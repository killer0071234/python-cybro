"""Tests for `cybro.CybroDirect` against the fake A-bus controller.

The first part mirrors the behaviour tests of test_http.py (Cybro against
a scgi server) with the same variable names, so both backends are held to
the same contract.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from datetime import datetime

import pytest
import pytest_asyncio

from src.cybro import CybroDirect
from src.cybro.abus.alc import parse_alc
from src.cybro.abus.client import AbusResponseError
from src.cybro.abus.client import PlcStatus
from src.cybro.abus.frame import Command
from src.cybro.abus.frame import Direction
from src.cybro.abus.frame import Frame
from src.cybro.abus.sockets import SocketDef
from src.cybro.abus.sockets import SocketMessage
from src.cybro.abus.sockets import encode_socket
from src.cybro.abus.transport import AbusTransport
from src.cybro.exceptions import CybroConnectionTimeoutError
from src.cybro.exceptions import CybroError
from src.cybro.models import VarType
from src.cybro.testing import FakeAbusController
from src.cybro.testing import FakeVar

NAD = 12762
PREFIX = f"c{NAD}."
LOCAL = "127.0.0.1"

VARIABLES = [
    FakeVar("scan_overrun", "bit", 0, description="Scan overrun."),
    FakeVar("scan_time", "int", 6, description="Last scan time [ms]."),
    FakeVar("scan_time_max", "int", 22, description="Max scan time [ms]."),
    FakeVar("cybro_qx00", "bit", 0, user=True, description="Binary output."),
    FakeVar("dummy_int", "int", 0, array=31, description="Array."),
    FakeVar("lc00_general_error", "bit", 0, description="Combined system error."),
    FakeVar("lc01_general_error", "bit", 1, description="Combined system error."),
    FakeVar("fPowerTotal", "real", -1512.10107421875),
    FakeVar("energy", "long", 123456),
]


@pytest_asyncio.fixture
async def fake() -> AsyncIterator[FakeAbusController]:
    """Return a fake controller."""
    controller = await FakeAbusController.start(NAD, VARIABLES)
    yield controller
    controller.close()


@pytest_asyncio.fixture
async def transport(fake: FakeAbusController) -> AsyncIterator[AbusTransport]:
    """Return a transport whose 'broadcast' reaches the fake."""
    abus = await AbusTransport.create(
        (LOCAL, 0), timeout=0.05, broadcast=LOCAL, port=fake.port
    )
    yield abus
    abus.close()


@pytest.fixture
def cybro(transport: AbusTransport) -> CybroDirect:
    """Return a CybroDirect with a static IP."""
    return CybroDirect(transport, NAD, ip=LOCAL)


# --- same contract as Cybro (see test_http.py) ---


@pytest.mark.asyncio
async def test_update(cybro: CybroDirect, fake: FakeAbusController) -> None:
    """The first update reads server and PLC information."""
    device = await cybro.update()

    assert device.server_info.server_version.startswith("direct ")
    assert device.info.startswith("CybrotechScgiServer v")
    assert device.server_info.nad_list == [str(NAD)]
    assert device.plc_info.plc_status == "ok"
    assert device.plc_info.ip_port == f"{LOCAL}:{fake.port}"
    assert device.plc_info.timestamp == "2026-10-10 14:19:50"
    assert device.plc_info.plc_vars == {
        f"{PREFIX}scan_overrun": "bit",
        f"{PREFIX}scan_time": "int",
        f"{PREFIX}scan_time_max": "int",
        f"{PREFIX}cybro_qx00": "bit",
        f"{PREFIX}dummy_int": "int",
        f"{PREFIX}lc00_general_error": "bit",
        f"{PREFIX}lc01_general_error": "bit",
        f"{PREFIX}fPowerTotal": "real",
        f"{PREFIX}energy": "long",
    }


@pytest.mark.asyncio
async def test_update_user_vars(cybro: CybroDirect, fake: FakeAbusController) -> None:
    """Variables added with add_var() are refreshed by every update."""
    names = [f"{PREFIX}scan_time", f"{PREFIX}scan_time_max"]
    await cybro.update()
    for name in names:
        cybro.add_var(name)
    cybro.add_var(f"{PREFIX}unknown_var")  # not in the ALC file, ignored
    device = await cybro.update()
    assert device.vars[names[0]].value_int() == 6
    assert device.vars[names[1]].value_int() == 22
    assert list(device.user_vars) == names

    fake["scan_time"] = 9
    requests_before = len(fake.requests)
    device = await cybro.update()

    assert device.vars[names[0]].value_int() == 9
    # head + status + one READ_RANDOM, the ALC is not downloaded again
    commands = [r.body[0] for r in fake.requests[requests_before:]]
    assert commands == [Command.READ_CODE, Command.READ_STATUS, Command.READ_RANDOM]


@pytest.mark.asyncio
async def test_update_many_user_vars_in_one_request(
    cybro: CybroDirect, fake: FakeAbusController
) -> None:
    """There's no chunking of 25 variables: all are read in one go."""
    await cybro.update()
    for i in range(31):
        cybro.add_var(f"{PREFIX}dummy_int[{i}]")
    requests_before = len(fake.requests)
    await cybro.update()
    assert len(fake.requests) - requests_before == 3


@pytest.mark.asyncio
async def test_update_hiq(cybro: CybroDirect) -> None:
    """device_type=1 reads the HIQ variables; missing ones are "?"."""
    device = await cybro.update(device_type=1)
    assert device.vars[f"{PREFIX}lc01_general_error"].value == "1"
    assert device.vars[f"{PREFIX}hvac_mode"].value == "?"


@pytest.mark.asyncio
async def test_update_variable_list_info(cybro: CybroDirect) -> None:
    """Type and description of all PLC variables are kept in var_info."""
    device = await cybro.update(device_type=1)
    info = device.var_info[f"{PREFIX}dummy_int[3]"]
    assert info.type == "datatype.int"
    assert info.description == "Array."
    assert len(device.var_info) == 8 + 31
    assert device.vars[f"{PREFIX}lc00_general_error"].description == (
        "Combined system error."
    )
    assert f"{PREFIX}scan_time" not in device.vars  # listed, but not read


@pytest.mark.asyncio
async def test_read_var(cybro: CybroDirect) -> None:
    """Single variables are read immediately, also typed."""
    await cybro.update()
    assert await cybro.read_var(f"{PREFIX}scan_time_max") == "22"
    assert await cybro.read_var_int(f"{PREFIX}scan_time") == 6
    assert await cybro.read_var_float(f"{PREFIX}scan_time") == 6.0
    assert await cybro.read_var_bool(f"{PREFIX}lc01_general_error") is True
    assert await cybro.read_var(f"{PREFIX}fPowerTotal") == "-1512.10107421875"
    assert await cybro.read_var(f"{PREFIX}energy") == "123456"


@pytest.mark.asyncio
async def test_read_unknown_var(cybro: CybroDirect) -> None:
    """Unknown variables are "?", also typed and from other controllers."""
    await cybro.update()
    assert await cybro.read_var(f"{PREFIX}does_not_exist") == "?"
    assert await cybro.read_var_int(f"{PREFIX}does_not_exist") == "?"
    assert await cybro.read_var("c1000.scan_time") == "?"
    assert await cybro.read_var(f"{PREFIX}sys.unknown") == "?"
    assert await cybro.read_var("sys.unknown") == "?"


@pytest.mark.asyncio
async def test_write_var(cybro: CybroDirect, fake: FakeAbusController) -> None:
    """A write converts the string to the ALC type and returns the new value."""
    await cybro.update()
    assert await cybro.write_var(f"{PREFIX}cybro_qx00", "1") == "1"
    assert fake["cybro_qx00"] == 1
    assert await cybro.write_var(f"{PREFIX}fPowerTotal", "21.5", VarType.FLOAT) == 21.5
    assert await cybro.write_var(f"{PREFIX}energy", "-5") == "-5"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        (f"{PREFIX}cybro_qx00", "2", "out of range"),
        (f"{PREFIX}scan_time", "abc", "invalid value"),
        (f"{PREFIX}does_not_exist", "1", "unknown"),
        (f"{PREFIX}sys.plc_status", "1", "read-only"),
        ("c1000.scan_time", "1", "unknown"),
    ],
)
async def test_write_var_invalid(
    cybro: CybroDirect, fake: FakeAbusController, name: str, value: str, message: str
) -> None:
    """Invalid writes raise CybroError and write nothing."""
    await cybro.update()
    requests_before = len(fake.requests)
    with pytest.raises(CybroError, match=message):
        await cybro.write_var(name, value)
    assert all(
        r.body[0] != Command.WRITE_RANDOM for r in fake.requests[requests_before:]
    )


@pytest.mark.asyncio
async def test_array_element_read_write(
    cybro: CybroDirect, fake: FakeAbusController
) -> None:
    """Array elements are read and written by index."""
    name = f"{PREFIX}dummy_int[28]"
    await cybro.update()
    assert await cybro.read_var(name) == "0"
    assert await cybro.write_var(name, "123") == "123"
    assert fake["dummy_int[28]"] == 123
    assert fake["dummy_int[27]"] == 0
    cybro.add_var(name)
    device = await cybro.update()
    assert device.vars[name].value_int() == 123


@pytest.mark.asyncio
async def test_request_shapes(cybro: CybroDirect) -> None:
    """request() answers like xmltodict parses the scgi server's XML."""
    assert await cybro.request() is None
    assert await cybro.request({}) is None
    single = await cybro.request(f"{PREFIX}scan_time")
    assert single == {
        "var": {
            "name": f"{PREFIX}scan_time",
            "value": "6",
            "description": "Last scan time [ms].",
        }
    }
    both = await cybro.request(f"{PREFIX}scan_time&{PREFIX}cybro_qx00=1")
    assert [v["value"] for v in both["var"]] == ["6", "1"]
    unknown = await cybro.request({f"{PREFIX}nope": ""})
    assert unknown["var"]["value"] == "?"
    assert unknown["var"]["error_code"] == "2"


@pytest.mark.asyncio
async def test_server_vars(cybro: CybroDirect, transport: AbusTransport) -> None:
    """Server variables are created locally."""
    await cybro.update()
    data = await cybro.request("sys.server_uptime&sys.udp_tx_count&sys.push_count")
    uptime, tx_count, push_count = (v["value"] for v in data["var"])
    assert re.fullmatch(r"\d+ days, \d\d:\d\d:\d\d", uptime)
    assert int(tx_count) == transport.tx_count > 0
    assert push_count == "0"


@pytest.mark.asyncio
async def test_plc_sys_vars(cybro: CybroDirect, fake: FakeAbusController) -> None:
    """c<nad>.sys.* values come from the client's counters and the ALC."""
    await cybro.update()
    data = await cybro.request(
        f"{PREFIX}sys.bytes_transferred&{PREFIX}sys.com_error_count"
        f"&{PREFIX}sys.response_time&{PREFIX}sys.alc_file"
    )
    values = [v["value"] for v in data["var"]]
    assert int(values[0]) > 0
    assert values[1] == "0"
    assert int(values[2]) >= 0
    assert values[3] == f"\n{fake.alc_text}\n"  # like the scgi server


# --- direct mode only ---


@pytest.mark.asyncio
async def test_program_change_reloads_alc(
    cybro: CybroDirect, fake: FakeAbusController
) -> None:
    """A new program timestamp downloads the ALC again, values follow."""
    cybro_vars = [FakeVar("new_var", "int", 5), *VARIABLES]  # addresses move
    await cybro.update()
    cybro.add_var(f"{PREFIX}scan_time")
    await cybro.update()
    fake.load_program(cybro_vars, datetime(2026, 11, 1, 8, 0, 0))
    fake["scan_time"] = 77

    device = await cybro.update()
    assert device.vars[f"{PREFIX}scan_time"].value == "77"
    device = await cybro.update(full_update=True)
    assert device.plc_info.timestamp == "2026-11-01 08:00:00"
    assert f"{PREFIX}new_var" in device.plc_info.plc_vars


@pytest.mark.asyncio
async def test_alc_downloaded_once(
    cybro: CybroDirect, fake: FakeAbusController
) -> None:
    """The ALC is kept in memory while the program doesn't change."""
    await cybro.update(full_update=True)
    requests_before = len(fake.requests)
    await cybro.update(full_update=True)
    reads = [
        r for r in fake.requests[requests_before:] if r.body[0] == Command.READ_CODE
    ]
    assert len(reads) == 1  # only the head


@pytest.mark.asyncio
async def test_program_not_running(
    cybro: CybroDirect, fake: FakeAbusController
) -> None:
    """A stopped PLC reports "pgm missing" and "?" values, like the scgi server."""
    fake.plc_status = PlcStatus.STOP
    device = await cybro.update()
    assert device.plc_info.plc_status == "pgm missing"
    assert await cybro.read_var(f"{PREFIX}scan_time") == "?"
    fake.plc_status = PlcStatus.PAUSE  # paused programs can be read
    assert await cybro.read_var(f"{PREFIX}scan_time") == "6"


@pytest.mark.asyncio
async def test_no_valid_program(cybro: CybroDirect, fake: FakeAbusController) -> None:
    """An empty head (no program loaded) reports "pgm missing"."""
    fake.load_program(VARIABLES, datetime(2026, 10, 10, 14, 19, 50), magic=0)
    device = await cybro.update()
    assert device.plc_info.plc_status == "pgm missing"
    assert device.plc_info.alc_file == ""


@pytest.mark.asyncio
async def test_alc_missing(cybro: CybroDirect, fake: FakeAbusController) -> None:
    """A broken alc.zip reports "alc missing"."""
    head = await cybro.client.read_head()
    files = await cybro.client.read_files(head)
    alc = files[1]
    fake.code[alc.address : alc.address + alc.size] = b"\x00" * alc.size
    device = await cybro.update()
    assert device.plc_info.plc_status == "alc missing"
    assert await cybro.read_var(f"{PREFIX}scan_time") == "?"


@pytest.mark.asyncio
async def test_offline(cybro: CybroDirect, fake: FakeAbusController) -> None:
    """No answer raises CybroConnectionTimeoutError, like an unreachable server."""
    await cybro.update()
    cybro.add_var(f"{PREFIX}scan_time")
    fake.silent = True
    with pytest.raises(CybroConnectionTimeoutError):
        await cybro.update()
    fake.silent = False
    await cybro.update()


@pytest.mark.asyncio
async def test_locate_and_relearn(transport: AbusTransport) -> None:
    """Without IP the controller is found by broadcast."""
    cybro = CybroDirect(transport, NAD)
    device = await cybro.update()
    assert device.plc_info.ip_port.startswith(f"{LOCAL}:")
    assert cybro.client.ip == LOCAL


@pytest.mark.asyncio
async def test_password(transport: AbusTransport, fake: FakeAbusController) -> None:
    """A wrong password raises CybroError (DENIED)."""
    fake.password = 1234
    with pytest.raises(CybroError, match="DENIED"):
        await CybroDirect(transport, NAD, ip=LOCAL).update()
    device = await CybroDirect(transport, NAD, ip=LOCAL, password=1234).update()
    assert device.plc_info.plc_status == "ok"


@pytest.mark.asyncio
async def test_context_manager(cybro: CybroDirect, transport: AbusTransport) -> None:
    """Leaving async with or disconnect() keeps the shared transport open."""
    async with cybro:
        await cybro.update()
    await cybro.disconnect()
    await cybro.update()


@pytest.mark.asyncio
async def test_write_while_stopped(
    cybro: CybroDirect, fake: FakeAbusController
) -> None:
    """Writes to a stopped PLC raise CybroError."""
    await cybro.update()
    fake.plc_status = PlcStatus.STOP
    with pytest.raises(CybroError, match="pgm missing"):
        await cybro.write_var(f"{PREFIX}scan_time", "1")


@pytest.mark.asyncio
async def test_variable_list_without_alc(
    cybro: CybroDirect, fake: FakeAbusController
) -> None:
    """Only sys.variables without an ALC is an empty answer."""
    fake.plc_status = PlcStatus.STOP
    assert await cybro.request(f"{PREFIX}sys.variables") is None


@pytest.mark.asyncio
async def test_refused_alc_download(
    cybro: CybroDirect, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused request during the ALC download is not "alc missing"."""

    async def refuse(head=None) -> str:
        raise AbusResponseError(NAD, Direction.NO_AUTH)

    monkeypatch.setattr(cybro.client, "fetch_alc", refuse)
    with pytest.raises(AbusResponseError, match="NO_AUTH"):
        await cybro.update()


# --- sockets ---

SOCKET = "7; cybro_qx00; scan_time; energy, fPowerTotal"
SOCKET_VALUES = {"cybro_qx00": 1, "scan_time": -42, "energy": 70000, "fPowerTotal": 1.5}


def socket_body(values: dict = SOCKET_VALUES) -> bytes:
    """Return the body of socket 7 (fPowerTotal is a REAL in the fake)."""
    alc = parse_alc("0000  00000 1     0      4    global real  fPowerTotal")
    return encode_socket(SocketDef.parse(SOCKET), values, alc)


async def deliver() -> None:
    """Let the transport receive frames sent on loopback."""
    await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_socket_updates_vars(
    cybro: CybroDirect, fake: FakeAbusController, transport: AbusTransport
) -> None:
    """A socket updates device.vars like a read and reaches the listeners."""
    device = await cybro.update()
    messages: list[SocketMessage] = []
    cybro.on_socket(messages.append)
    assert cybro.add_socket(SOCKET) == SocketDef.parse(SOCKET)

    fake.send_socket(transport.local_addr, 7, socket_body())
    await deliver()

    assert messages == [SocketMessage(NAD, 7, SOCKET_VALUES, (LOCAL, fake.port))]
    values = {name: device.vars[PREFIX + name].value for name in SOCKET_VALUES}
    assert values == {
        "cybro_qx00": "1",
        "scan_time": "-42",
        "energy": "70000",
        "fPowerTotal": "1.5",
    }
    assert device.vars[f"{PREFIX}scan_time"].description == "Last scan time [ms]."
    assert device.vars[f"{PREFIX}energy"].description is None
    # formatted exactly like a read of the same values
    for name, value in SOCKET_VALUES.items():
        fake[name] = value
    for name in SOCKET_VALUES:
        assert await cybro.read_var(PREFIX + name) == values[name]


@pytest.mark.asyncio
async def test_socket_dropped(
    cybro: CybroDirect,
    fake: FakeAbusController,
    transport: AbusTransport,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Frames that can't be decoded are dropped without an exception."""
    messages: list[SocketMessage] = []
    cybro.on_socket(messages.append)
    cybro.add_socket(SOCKET)
    with caplog.at_level(logging.DEBUG, logger="src.cybro.direct"):
        fake.send_socket(transport.local_addr, 7, socket_body())  # no ALC yet
        await deliver()
        assert "no allocation table" in caplog.text
        await cybro.update()
        fake.send_socket(transport.local_addr, 7, socket_body()[:-1])
        await deliver()
        assert "body has 10 bytes" in caplog.text
    fake.send_socket(transport.local_addr, 8, socket_body())  # unknown id
    other = Frame(4711, 0, 0, Direction.ACK, 7, socket_body())  # other NAD
    fake.send_frame(other, transport.local_addr)
    await deliver()
    assert messages == []
    assert f"{PREFIX}scan_time" not in cybro._device.vars  # noqa: SLF001


@pytest.mark.asyncio
async def test_socket_failing_listener(
    cybro: CybroDirect,
    fake: FakeAbusController,
    transport: AbusTransport,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing listener is logged, the others still run."""
    await cybro.update()
    messages: list[SocketMessage] = []

    def broken(message: SocketMessage) -> None:
        raise ValueError("boom")

    cybro.on_socket(broken)
    remove = cybro.on_socket(messages.append)
    cybro.add_socket(SOCKET)
    fake.send_socket(transport.local_addr, 7, socket_body())
    await deliver()
    assert len(messages) == 1
    assert "Error in socket callback" in caplog.text
    remove()
    fake.send_socket(transport.local_addr, 7, socket_body())
    await deliver()
    assert len(messages) == 1


@pytest.mark.asyncio
async def test_remove_socket_and_disconnect(
    cybro: CybroDirect, fake: FakeAbusController, transport: AbusTransport
) -> None:
    """remove_socket() and disconnect() stop the socket handling."""
    await cybro.update()
    messages: list[SocketMessage] = []
    cybro.on_socket(messages.append)
    cybro.add_socket(SOCKET)
    cybro.add_socket(SocketDef(9, bits=("cybro_qx00",)))
    assert len(transport._callbacks) == 1  # noqa: SLF001  subscribed once
    cybro.remove_socket(7)
    cybro.remove_socket(7)  # unknown id is fine
    fake.send_socket(transport.local_addr, 7, socket_body())
    fake.send_socket(transport.local_addr, 9, b"\x01")
    await deliver()
    assert [message.socket_id for message in messages] == [9]

    await cybro.disconnect()
    assert transport._callbacks == []  # noqa: SLF001
    fake.send_socket(transport.local_addr, 9, b"\x01")
    await deliver()
    assert len(messages) == 1

    cybro.add_socket(SOCKET)  # works again after disconnect
    fake.send_socket(transport.local_addr, 7, socket_body())
    await deliver()
    assert len(messages) == 2


@pytest.mark.asyncio
async def test_add_socket_invalid(cybro: CybroDirect) -> None:
    """An invalid definition raises ValueError."""
    with pytest.raises(ValueError, match="id must be"):
        cybro.add_socket("100; cybro_qx00")


@pytest.mark.asyncio
async def test_socket_updates_learned_ip(
    transport: AbusTransport, fake: FakeAbusController
) -> None:
    """A socket from a new address updates a learned IP, not a static one."""
    learned = CybroDirect(transport, NAD)
    static = CybroDirect(transport, NAD, ip="127.0.0.2")
    await learned.update()
    learned.client.ip = "127.0.0.3"  # e.g. the controller got a new address
    for cybro in (learned, static):
        cybro._alc = learned._alc  # noqa: SLF001
        cybro.add_socket(SOCKET)
    fake.send_socket(transport.local_addr, 7, socket_body())
    await deliver()
    assert learned.client.ip == LOCAL
    assert static.client.ip == "127.0.0.2"
