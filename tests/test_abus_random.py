"""Tests for READ_RANDOM / WRITE_RANDOM and value conversion."""

from __future__ import annotations

import math
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from src.cybro.abus.alc import AlcVar
from src.cybro.abus.alc import DataType
from src.cybro.abus.alc import format_value
from src.cybro.abus.alc import parse_alc
from src.cybro.abus.alc import parse_value
from src.cybro.abus.client import AbusClient
from src.cybro.abus.frame import MAX_FRAME_BYTES
from src.cybro.abus.frame import Command
from src.cybro.abus.transport import AbusTransport
from src.cybro.exceptions import AbusError
from src.cybro.testing import FakeAbusController
from src.cybro.testing import FakeVar

NAD = 10000

VARIABLES = [
    FakeVar("bits", "bit", 1, array=700),
    FakeVar("ints", "int", -1234, array=400),
    FakeVar("longs", "long", 70000, array=300),
    FakeVar("fPowerTotal", "real", -1512.10107421875),
    FakeVar("program_id", "int", 3, user=True),
]


@pytest_asyncio.fixture
async def fake() -> AsyncIterator[FakeAbusController]:
    """Return a fake controller with many variables."""
    controller = await FakeAbusController.start(NAD, VARIABLES)
    yield controller
    controller.close()


@pytest_asyncio.fixture
async def client(fake: FakeAbusController) -> AsyncIterator[AbusClient]:
    """Return a client for the fake."""
    transport = await AbusTransport.create(("127.0.0.1", 0), port=fake.port)
    yield AbusClient(transport, NAD, ip="127.0.0.1")
    transport.close()


@pytest.fixture
def alc(fake: FakeAbusController) -> dict[str, AlcVar]:
    """Return the parsed ALC of the fake."""
    return parse_alc(fake.alc_text)


def assert_frames_fit(fake: FakeAbusController) -> None:
    """Every request and its answer stay within MAX_FRAME_BYTES."""
    for request in fake.requests:
        assert request.size <= MAX_FRAME_BYTES
    assert max(fake.reply_sizes) <= MAX_FRAME_BYTES


@pytest.mark.asyncio
async def test_read_one_real(client, alc) -> None:
    """A REAL is read as the float32 value (widened to double)."""
    values = await client.read_random([alc["fPowerTotal"]])
    assert values == {"fPowerTotal": -1512.10107421875}


@pytest.mark.asyncio
async def test_read_many_split_frames(fake, client, alc) -> None:
    """1,402 variables of mixed size are read in frames of max. 1000 bytes."""
    values = await client.read_random(list(alc.values()))
    assert values == {name: fake[name] for name in alc}
    assert len(values) == 1402
    assert all(fake.requests[i].body[0] == Command.READ_RANDOM for i in range(3))
    assert len(fake.requests) == 4  # 981 parameter bytes per frame
    assert_frames_fit(fake)


@pytest.mark.asyncio
async def test_read_empty(fake, client) -> None:
    """Nothing to read sends nothing."""
    assert await client.read_random([]) == {}
    assert fake.requests == []


@pytest.mark.asyncio
async def test_read_untyped_variable(client) -> None:
    """A variable without a known type can't be read."""
    timer = AlcVar("t", 0x100, 8, None, False, "global", "")
    with pytest.raises(AbusError, match="type"):
        await client.read_random([timer])


@pytest.mark.asyncio
async def test_read_short_answer(fake, client, alc) -> None:
    """An answer with too few bytes raises AbusError."""
    fake.truncate_replies = True
    with pytest.raises(AbusError, match="bytes"):
        await client.read_random([alc["fPowerTotal"]])


@pytest.mark.asyncio
async def test_write_round_trip(fake, client, alc) -> None:
    """Written values read back; one frame per write if it fits."""
    await client.write_random(
        [
            (alc["bits[3]"], 0),
            (alc["ints[0]"], 32767),
            (alc["longs[9]"], -2147483648),
            (alc["fPowerTotal"], 21.5),
        ]
    )
    assert len(fake.requests) == 1
    assert fake["bits[3]"] == 0
    assert fake["ints[0]"] == 32767
    assert fake["longs[9]"] == -2147483648
    assert fake["fPowerTotal"] == 21.5
    assert fake["bits[2]"] == 1  # neighbours untouched


@pytest.mark.asyncio
async def test_write_many_split_frames(fake, client, alc) -> None:
    """Large writes are split so that frames stay within 1000 bytes."""
    items = [(var, 0) for var in alc.values()]
    await client.write_random(items)
    assert all(fake[name] == 0 for name in alc)
    assert len(fake.requests) > 1
    assert_frames_fit(fake)


@pytest.mark.asyncio
async def test_password_used_for_random_access(fake, client, alc) -> None:
    """Random access uses the password as transaction id too."""
    fake.password = 77
    client.password = 77
    await client.read_random([alc["program_id"]])
    await client.write_random([(alc["program_id"], 5)])
    assert [r.txn for r in fake.requests] == [77, 77]
    assert fake["program_id"] == 5


@pytest.mark.parametrize(
    ("value", "data_type", "text"),
    [
        (1, DataType.BIT, "1"),
        (-1234, DataType.INT, "-1234"),
        (70000, DataType.LONG, "70000"),
        (-1512.10107421875, DataType.REAL, "-1512.10107421875"),
        (0.0, DataType.REAL, "0.0"),
        (math.inf, DataType.REAL, "inf"),
    ],
)
def test_format_value(value: float, data_type: DataType, text: str) -> None:
    """Values are formatted with str() like the scgi server (spike S8)."""
    assert format_value(value, data_type) == text


@pytest.mark.parametrize(
    ("text", "data_type", "value"),
    [
        ("1", DataType.BIT, 1),
        ("0", DataType.BIT, 0),
        ("1.0", DataType.BIT, 1),
        ("-32768", DataType.INT, -32768),
        ("12.9", DataType.INT, 12),  # truncated like the scgi server
        ("2147483647", DataType.LONG, 2147483647),
        ("-1512.1", DataType.REAL, -1512.1),
        (" 5 ", DataType.INT, 5),
    ],
)
def test_parse_value(text: str, data_type: DataType, value: float) -> None:
    """Strings convert to the ALC type."""
    assert parse_value(text, data_type) == value


@pytest.mark.parametrize(
    ("text", "data_type"),
    [
        ("2", DataType.BIT),
        ("-1", DataType.BIT),
        ("32768", DataType.INT),
        ("-2147483649", DataType.LONG),
        ("1e39", DataType.REAL),
        ("abc", DataType.INT),
        ("nan", DataType.INT),
        ("", DataType.REAL),
    ],
)
def test_parse_value_invalid(text: str, data_type: DataType) -> None:
    """Values out of range or not numeric raise ValueError."""
    with pytest.raises(ValueError, match="for"):
        parse_value(text, data_type)
