"""Tests for the fake controller itself (`cybro.testing`)."""

from __future__ import annotations

import asyncio
import struct
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from src.cybro import CybroDirect
from src.cybro.abus.client import AbusClient
from src.cybro.abus.client import AbusResponseError
from src.cybro.abus.frame import Command
from src.cybro.abus.frame import Direction
from src.cybro.abus.frame import Frame
from src.cybro.abus.frame import encode
from src.cybro.abus.sockets import SocketDef
from src.cybro.abus.transport import AbusTransport
from src.cybro.exceptions import CybroConnectionTimeoutError
from src.cybro.testing import FakeAbusController
from src.cybro.testing import FakeVar

NAD = 10000


@pytest_asyncio.fixture
async def fake() -> AsyncIterator[FakeAbusController]:
    """Return a fake controller."""
    controller = await FakeAbusController.start(
        NAD, [FakeVar("scan_time", "int", 5), FakeVar("power", "real", 1.5)]
    )
    yield controller
    controller.close()


@pytest_asyncio.fixture
async def transport(fake: FakeAbusController) -> AsyncIterator[AbusTransport]:
    """Return a transport with short timeouts."""
    abus = await AbusTransport.create(("127.0.0.1", 0), timeout=0.05, port=fake.port)
    yield abus
    abus.close()


@pytest.mark.asyncio
async def test_docstring_example(fake: FakeAbusController, transport) -> None:
    """The example of the module docstring works."""
    cybro = CybroDirect(transport, NAD, ip="127.0.0.1")
    await cybro.update()
    cybro.add_var(f"c{NAD}.scan_time")
    device = await cybro.update()
    assert device.vars[f"c{NAD}.scan_time"].value == "5"


@pytest.mark.asyncio
async def test_values(fake: FakeAbusController) -> None:
    """Values are stored in data memory with their type."""
    fake["scan_time"] = 7.9  # integer types are truncated
    assert fake["scan_time"] == 7
    fake["power"] = 2.25
    assert fake["power"] == 2.25
    with pytest.raises(KeyError):
        fake["missing"]  # noqa: B018


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["socket", "push", "broadcast"])
async def test_unsolicited_frames(fake, transport, kind: str) -> None:
    """Socket, push and broadcast frames reach the unsolicited callbacks."""
    seen: list[Frame] = []
    transport.on_unsolicited(lambda frame, _: seen.append(frame))
    if kind == "socket":
        fake.send_socket(transport.local_addr, 23, b"\x01")
    elif kind == "push":
        fake.send_push(transport.local_addr)
    else:
        fake.send_broadcast(transport.local_addr)
    await asyncio.sleep(0.02)
    assert [(f.kind, f.from_nad, f.to_nad) for f in seen] == [(kind, NAD, 0)]


@pytest.mark.asyncio
async def test_ignores_other_frames(fake, transport) -> None:
    """Frames for other NADs, answers and empty requests are not answered."""
    peer = fake.addr
    for frame in (
        Frame.request(1010, 4711, 1, Command.PING),
        Frame.request(1010, 0, 2, Command.READ_CODE, struct.pack("<2H", 0x200, 1)),
        Frame(1010, NAD, 3, Direction.ACK, 0, b"\x10"),
        Frame(1010, NAD, 4, Direction.REQ, 0, b""),
    ):
        transport._transport.sendto(encode(frame), peer)  # noqa: SLF001
    await asyncio.sleep(0.02)
    assert fake.requests == []


@pytest.mark.asyncio
async def test_drop_and_limits(fake, transport) -> None:
    """Dropped requests time out; too big READ_CODE blocks are refused."""
    client = AbusClient(transport, NAD, ip="127.0.0.1")
    fake.drop = 3
    with pytest.raises(CybroConnectionTimeoutError):
        await client.ping()
    await client.ping()
    with pytest.raises(AbusResponseError, match="BAD_PARA"):
        await client.call(Command.READ_CODE, struct.pack("<2H", 0x200, 0x101))


@pytest.mark.asyncio
async def test_lost_big_answers(fake, transport) -> None:
    """Answers above the UDP payload of a 1500 MTU are lost (spike S6)."""
    client = AbusClient(transport, NAD, ip="127.0.0.1")
    addresses = [0x100] * 400  # 400 x 4 bytes: reply 1618 bytes
    params = struct.pack("<3H", 0, 0, 400) + struct.pack("<400H", *addresses)
    with pytest.raises(CybroConnectionTimeoutError):
        await client.call(Command.READ_RANDOM, params)
    assert fake.reply_sizes[-1] == 1618


@pytest.mark.asyncio
async def test_receive_socket(fake, transport) -> None:
    """Receive sockets write values like the test controller does (S9)."""
    fake.receive_sockets[64] = SocketDef(64, ints=("scan_time",), longs=("power",))
    fake.receive_sockets[66] = SocketDef(66, bits=("scan_time",))  # wrong slot
    body = struct.pack("<hf", -7, 2.5)
    accepted = [
        Frame(1010, 0, 0, Direction.ACK, 64, body),
        Frame(4242, NAD, 9, Direction.ACK, 64, struct.pack("<hf", 8, 0.5)),
    ]
    ignored = [
        Frame(1010, 0, 0, Direction.REQ, 64, struct.pack("<hf", 1, 1)),
        Frame(1010, 0, 0, Direction.ACK, 65, struct.pack("<hf", 2, 2)),
        Frame(1010, 0, 0, Direction.ACK, 64, struct.pack("<hf", 3, 3) + b"\x00"),
        Frame(1010, 0, 0, Direction.ACK, 66, b"\x01"),
        Frame(1010, 4711, 0, Direction.ACK, 64, struct.pack("<hf", 4, 4)),
    ]
    transport.send_frame(accepted[0], fake.addr)
    await asyncio.sleep(0.01)
    assert (fake["scan_time"], fake["power"]) == (-7, 2.5)
    for frame in [*ignored, accepted[1]]:
        transport.send_frame(frame, fake.addr)
    await asyncio.sleep(0.01)
    assert (fake["scan_time"], fake["power"]) == (8, 0.5)
    # every socket frame for this NAD is recorded, also the ignored ones
    assert fake.sockets == [accepted[0], *ignored[1:4], accepted[1]]


def test_public_api() -> None:
    """Direct mode is importable from the package and from cybro.abus."""
    import src.cybro as cybro  # noqa: PLC0415
    import src.cybro.abus as abus  # noqa: PLC0415

    assert {"CybroDirect", "AbusError"} <= set(cybro.__all__)
    assert all(hasattr(abus, name) for name in abus.__all__)
