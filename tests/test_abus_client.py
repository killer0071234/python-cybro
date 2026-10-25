"""Tests for `cybro.abus.client` and `cybro.abus.locate` against the fake."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

import pytest
import pytest_asyncio

from src.cybro.abus.client import HEAD_ADDR
from src.cybro.abus.client import AbusClient
from src.cybro.abus.client import AbusResponseError
from src.cybro.abus.client import PlcHead
from src.cybro.abus.client import PlcStatus
from src.cybro.abus.client import Status
from src.cybro.abus.client import SystemStatus
from src.cybro.abus.client import cybro_datetime
from src.cybro.abus.frame import Command
from src.cybro.abus.frame import Direction
from src.cybro.abus.locate import ScanResult
from src.cybro.abus.locate import locate
from src.cybro.abus.locate import scan
from src.cybro.abus.transport import AbusTransport
from src.cybro.exceptions import AbusError
from src.cybro.exceptions import CybroConnectionTimeoutError
from src.cybro.testing import FakeAbusController
from src.cybro.testing import cybro_timestamp

NAD = 10000
LOCAL = "127.0.0.1"
NOWHERE = "127.0.0.3"  # loopback address nobody listens on

# head of c10000 read during the hardware spike (docs/abus-notes.md)
REAL_HEAD = bytes.fromhex(
    "0000b87a06fb04579d1db73279724a5d46350100230001007ce10000000001000100"
    "010000000000ec0efe0e0c0f740f00000000c00401000100010003000100483503000200"
)


@pytest_asyncio.fixture
async def fake() -> AsyncIterator[FakeAbusController]:
    """Return a fake controller on loopback."""
    controller = await FakeAbusController.start(NAD)
    yield controller
    controller.close()


@pytest_asyncio.fixture
async def transport(fake: FakeAbusController) -> AsyncIterator[AbusTransport]:
    """Return a transport that 'broadcasts' to the fake."""
    abus = await AbusTransport.create(
        (LOCAL, 0), timeout=0.05, broadcast=LOCAL, port=fake.port
    )
    yield abus
    abus.close()


@pytest.mark.asyncio
async def test_ping_learns_ip(fake, transport) -> None:
    """Without an IP the request is broadcast and the IP learned."""
    client = AbusClient(transport, NAD)
    await client.ping()
    assert client.ip == LOCAL
    assert fake.requests[0].from_nad == 1010
    assert client.bytes_transferred == 19 + 18
    assert client.response_time > 0


@pytest.mark.asyncio
async def test_read_status(fake, transport) -> None:
    """READ_STATUS returns system and PLC status."""
    client = AbusClient(transport, NAD, ip=LOCAL)
    status = await client.read_status()
    assert status == Status(SystemStatus.KERNEL_ACTIVE, PlcStatus.RUN)
    assert status.ok
    fake.plc_status = PlcStatus.STOP
    assert not (await client.read_status()).ok
    fake.system_status = SystemStatus.LOADER_ACTIVE
    fake.plc_status = PlcStatus.PAUSE
    assert not (await client.read_status()).ok


@pytest.mark.parametrize("data", [b"", b"\x01", b"\x01\x09", b"\x05\x02"])
def test_invalid_status(data: bytes) -> None:
    """Unknown status values raise AbusError."""
    with pytest.raises(AbusError, match="status"):
        Status.from_bytes(data)


def test_real_head() -> None:
    """The head read from c10000 decodes like the probe reported."""
    head = PlcHead.from_bytes(REAL_HEAD)
    assert head.magic == 31416
    assert head.model == "Cybro-3"
    assert head.program_timestamp == datetime(2026, 10, 10, 14, 19, 50)
    assert head.scan_overrun == 1
    assert (head.file_system_addr, head.file_count) == (210248, 2)
    assert head.valid
    with pytest.raises(AbusError, match="too short"):
        PlcHead.from_bytes(REAL_HEAD[:-1])


def test_head_validity() -> None:
    """A head without program, magic or files is not valid."""
    head = PlcHead.from_bytes(REAL_HEAD)
    assert not PlcHead(**(head.__dict__ | {"empty": 1})).valid
    assert not PlcHead(**(head.__dict__ | {"magic": 1})).valid
    assert PlcHead(**(head.__dict__ | {"magic": 1})).model is None
    assert not PlcHead(**(head.__dict__ | {"file_count": 0})).valid


def test_timestamps() -> None:
    """FAT-style timestamps convert both ways; invalid ones give None."""
    value = datetime(2026, 10, 10, 14, 19, 50)
    assert cybro_datetime(cybro_timestamp(value)) == value
    assert cybro_datetime(0) is None


@pytest.mark.asyncio
async def test_read_head_and_files(fake, transport) -> None:
    """The fake's head and file table read back correctly."""
    client = AbusClient(transport, NAD, ip=LOCAL)
    head = await client.read_head()
    assert head.valid
    assert head.program_timestamp == datetime(2026, 10, 10, 14, 19, 50)
    files = await client.read_files(head)
    assert [f.name for f in files] == ["cyp.zip", "alc.zip"]
    assert files == await client.read_files()
    assert cybro_datetime(files[1].timestamp) == head.program_timestamp


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("addr", "size"),
    [
        (HEAD_ADDR, 0x46),
        (HEAD_ADDR, 0x100),
        (HEAD_ADDR + 0xF0, 0x20),
        (HEAD_ADDR + 0x10, 0x300),
        (HEAD_ADDR + 1, 0),
    ],
)
async def test_read_code_segments(fake, transport, addr: int, size: int) -> None:
    """Code reads are split at 256-byte segment borders."""
    client = AbusClient(transport, NAD, ip=LOCAL)
    fake.code[HEAD_ADDR : HEAD_ADDR + 0x400] = bytes(range(256)) * 4
    assert await client.read_code(addr, size) == bytes(fake.code[addr : addr + size])
    expected = -(-(addr % 256 + size) // 256) if size else 0
    assert len(fake.requests) == expected


@pytest.mark.asyncio
async def test_read_code_short_answer(fake, transport) -> None:
    """An answer with fewer bytes than requested raises AbusError."""
    client = AbusClient(transport, NAD, ip=LOCAL)
    fake._handle = lambda command, params: (Direction.ACK, b"\x00")  # noqa: SLF001
    with pytest.raises(AbusError, match="need 70"):
        await client.read_head()


@pytest.mark.asyncio
async def test_negative_answer(fake, transport) -> None:
    """A refused request raises AbusResponseError with the direction."""
    client = AbusClient(transport, NAD, ip=LOCAL)
    with pytest.raises(AbusResponseError, match="BAD_CMD") as err:
        await client.call(Command.WRITE_DATA)
    assert err.value.direction == Direction.BAD_CMD


@pytest.mark.asyncio
async def test_password_is_txn(fake, transport) -> None:
    """With a password every request uses it as transaction id."""
    fake.password = 4321
    with pytest.raises(AbusResponseError, match="DENIED"):
        await AbusClient(transport, NAD, ip=LOCAL).ping()
    client = AbusClient(transport, NAD, ip=LOCAL, password=4321)
    await client.ping()
    await client.read_status()
    assert [r.txn for r in fake.requests[-2:]] == [4321, 4321]


@pytest.mark.asyncio
async def test_learned_ip_is_relearned(fake, transport) -> None:
    """After a timeout a learned IP is dropped and the request broadcast."""
    client = AbusClient(transport, NAD)
    client.ip = NOWHERE  # learned earlier, controller has moved
    await client.ping()
    assert client.ip == LOCAL
    assert client.com_error_count == 1


@pytest.mark.asyncio
async def test_static_ip_is_kept(fake, transport) -> None:
    """A static IP is not replaced after a timeout."""
    client = AbusClient(transport, NAD, ip=NOWHERE)
    with pytest.raises(CybroConnectionTimeoutError):
        await client.ping()
    assert client.ip == NOWHERE
    assert fake.requests == []


@pytest.mark.asyncio
async def test_timeout_without_ip(fake, transport) -> None:
    """No answer to a broadcast raises without a second round."""
    fake.silent = True
    client = AbusClient(transport, NAD)
    with pytest.raises(CybroConnectionTimeoutError):
        await client.ping()
    assert len(fake.requests) == 3
    assert client.com_error_count == 1


@pytest.mark.asyncio
async def test_locate(fake, transport) -> None:
    """locate() returns the IP that answers a broadcast PING."""
    assert await locate(transport, NAD) == LOCAL
    with pytest.raises(CybroConnectionTimeoutError):
        await locate(transport, 4711)


@pytest.mark.asyncio
async def test_scan(fake, transport) -> None:
    """A READ_STATUS to NAD 0 finds the controller with its status."""
    assert await scan(transport, window=0.05) == [
        ScanResult(NAD, LOCAL, Status(SystemStatus.KERNEL_ACTIVE, PlcStatus.RUN))
    ]
    assert fake.requests[-1].to_nad == 0
    fake.system_status = 9  # invalid status is skipped
    assert await scan(transport, window=0.05) == []
    fake.password = 1  # DENIED is skipped
    assert await scan(transport, window=0.05) == []
