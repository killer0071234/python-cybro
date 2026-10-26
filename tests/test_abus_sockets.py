"""Tests for Cybro socket definitions and bodies (`cybro.abus.sockets`)."""

from __future__ import annotations

import pytest

from src.cybro.abus.alc import DataType
from src.cybro.abus.alc import parse_alc
from src.cybro.abus.sockets import SocketDef
from src.cybro.abus.sockets import check_socket_id
from src.cybro.abus.sockets import decode_socket
from src.cybro.abus.sockets import encode_socket
from src.cybro.abus.sockets import encode_socket_values
from src.cybro.exceptions import AbusError

# test program on c10000 (docs/abus-notes.md, S9)
ALC_TEXT = (
    ";CPU CyBro-2 10000\r\n"
    "249D  00000 1     0      1    global bit   test_sock_bool\r\n"
    "316E  00000 1     0      2    global int   test_sock_int\r\n"
    "84C0  00000 1     0      4    global long  test_sock_long\r\n"
    "8704  00000 1     0      4    global real  test_sock_real\r\n"
    "8708  00000 1     0      14   global timer test_sock_timer\r\n"
)
ALC = parse_alc(ALC_TEXT)
TEST_SOCKET = "63; test_sock_bool; test_sock_int; test_sock_long, test_sock_real"
# socket 63 received from c10000 at 10:32:32
TEST_BODY = bytes.fromhex("01f810a8a0060000003242")
TEST_VALUES = {
    "test_sock_bool": 1,
    "test_sock_int": 4344,
    "test_sock_long": 434344,
    "test_sock_real": 44.5,
}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "1; socket_req, my_bit0, my_bit1; my_int0, my_int1; my_long0, my_long1",
            SocketDef(
                1,
                ("socket_req", "my_bit0", "my_bit1"),
                ("my_int0", "my_int1"),
                ("my_long0", "my_long1"),
            ),
        ),
        (
            "10; socket_req; event_device, event_number, event_status;",
            SocketDef(
                10, ("socket_req",), ("event_device", "event_number", "event_status")
            ),
        ),
        ("5;;;a", SocketDef(5, longs=("a",))),
        ("5; a", SocketDef(5, bits=("a",))),
        ("5; a; ; ;", SocketDef(5, bits=("a",))),
        ("255; arr[0], arr[1]", SocketDef(255, bits=("arr[0]", "arr[1]"))),
    ],
)
def test_parse(text: str, expected: SocketDef) -> None:
    """The toolkit syntax is parsed, empty and missing groups are empty."""
    assert SocketDef.parse(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "x; a",
        "0; a",
        "100; a",  # 0x64 is the broadcast type
        "256; a",
        "1",
        "1;;;",
        "1; a; b; c; d",
        "1; a, a",
        "1; a; a",
        "1; a b",
        "1; a,, b",
    ],
)
def test_parse_invalid(text: str) -> None:
    """Bad ids, empty definitions, extra groups and bad names are refused."""
    with pytest.raises(ValueError):  # noqa: PT011
        SocketDef.parse(text)


def test_size_and_slots() -> None:
    """Slots are in body order: bits, ints, longs."""
    definition = SocketDef(1, ("b0", "b1"), ("i0",), ("l0",))
    assert definition.size == 1 + 1 + 2 + 4
    assert list(definition.slots()) == [
        ("b0", DataType.BIT),
        ("b1", DataType.BIT),
        ("i0", DataType.INT),
        ("l0", DataType.LONG),
    ]


def test_decode_test_socket() -> None:
    """A socket from the test controller decodes with the ALC types."""
    assert decode_socket(SocketDef.parse(TEST_SOCKET), TEST_BODY, ALC) == TEST_VALUES


def test_decode_c8372_socket_3() -> None:
    """3 bits + 3 REALs captured from c8372 (spike S3)."""
    alc = parse_alc(
        "0000  00000 1     0      1    global bit   b0\r\n"
        "0001  00000 1     0      1    global bit   b1\r\n"
        "0002  00000 1     0      1    global bit   b2\r\n"
        "0004  00000 1     0      4    global real  r0\r\n"
        "0008  00000 1     0      4    global real  r1\r\n"
        "000C  00000 1     0      4    global real  r2\r\n"
    )
    definition = SocketDef.parse("3; b0, b1, b2;; r0, r1, r2")
    body = bytes.fromhex("010100499357c5ff9c9746fec1df47")
    assert decode_socket(definition, body, alc) == {
        "b0": 1,
        "b1": 1,
        "b2": 0,
        "r0": -3449.205322265625,
        "r1": 19406.498046875,
        "r2": 114563.984375,
    }


def test_decode_without_alc() -> None:
    """Names missing from the ALC get the slot type (signed)."""
    definition = SocketDef(1, ("b",), ("i",), ("l",))
    body = bytes.fromhex("01 ffff feffffff")
    assert decode_socket(definition, body, None) == {"b": 1, "i": -1, "l": -2}
    assert decode_socket(definition, body, ALC) == {"b": 1, "i": -1, "l": -2}


@pytest.mark.parametrize("body", [TEST_BODY[:-1], TEST_BODY + b"\x00", b""])
def test_decode_wrong_length(body: bytes) -> None:
    """A body that doesn't match the definition is refused (like the PLC)."""
    with pytest.raises(AbusError, match="11 bytes"):
        decode_socket(SocketDef.parse(TEST_SOCKET), body, ALC)


@pytest.mark.parametrize(
    "text",
    [
        "1; test_sock_int",  # INT in a BIT slot
        "1;; test_sock_real",  # REAL in an INT slot
        "1;;; test_sock_bool",  # BIT in a LONG slot
        "1;;; test_sock_timer",  # type the library can't read
    ],
)
def test_decode_type_mismatch(text: str) -> None:
    """An ALC variable that doesn't fit its slot is refused."""
    definition = SocketDef.parse(text)
    with pytest.raises(AbusError, match="test_sock_"):
        decode_socket(definition, bytes(definition.size), ALC)


def test_encode_test_socket() -> None:
    """Encoding gives the bytes the controller sends and accepts."""
    definition = SocketDef.parse(TEST_SOCKET)
    assert encode_socket(definition, TEST_VALUES, ALC) == TEST_BODY
    texts = {name: str(value) for name, value in TEST_VALUES.items()}
    assert encode_socket(definition, texts, ALC) == TEST_BODY


def test_encode_round_trip() -> None:
    """Decode(encode(values)) returns the values, also without an ALC."""
    definition = SocketDef(7, ("b",), ("i",), ("l",))
    values = {"b": 0, "i": -32768, "l": 2147483647}
    body = encode_socket(definition, values, None)
    assert decode_socket(definition, body, None) == values


@pytest.mark.parametrize(
    ("values", "match"),
    [
        ({**TEST_VALUES, "test_sock_int": 40000}, "out of range"),
        ({**TEST_VALUES, "test_sock_bool": 2}, "out of range"),
        ({**TEST_VALUES, "test_sock_real": "abc"}, "invalid value"),
        ({k: v for k, v in TEST_VALUES.items() if k != "test_sock_long"}, "missing"),
        ({**TEST_VALUES, "other": 1}, "not in socket 63"),
    ],
)
def test_encode_invalid(values: dict, match: str) -> None:
    """Values out of range, missing or unknown names are refused."""
    with pytest.raises(ValueError, match=match):
        encode_socket(SocketDef.parse(TEST_SOCKET), values, ALC)


def test_encode_type_mismatch() -> None:
    """Encoding checks the ALC types like decoding."""
    with pytest.raises(AbusError, match="test_sock_real"):
        encode_socket(SocketDef.parse("1;; test_sock_real"), {"test_sock_real": 1}, ALC)


def test_encode_values() -> None:
    """Values by type give the same body as the named definition."""
    body = encode_socket_values(bits=[1], ints=[4344], longs=[434344], reals=[44.5])
    assert body == TEST_BODY
    texts = encode_socket_values(
        bits=["1"], ints=["4344"], longs=["434344"], reals=["44.5"]
    )
    assert texts == TEST_BODY
    assert encode_socket_values(reals=[1]) == bytes.fromhex("0000803f")
    assert encode_socket_values(longs=[1]) == bytes.fromhex("01000000")


@pytest.mark.parametrize(
    ("values", "match"),
    [
        ({}, "no values"),
        ({"bits": [], "reals": []}, "no values"),
        ({"bits": [0, 2]}, r"bits\[1\]: 2 is out of range for BIT"),
        ({"ints": [40000]}, r"ints\[0\]: 40000 is out of range for INT"),
        ({"longs": [2**31]}, r"longs\[0\]: .* out of range for LONG"),
        ({"reals": ["x"]}, r"reals\[0\]: invalid value 'x' for REAL"),
    ],
)
def test_encode_values_invalid(values: dict, match: str) -> None:
    """Empty sockets and invalid values are refused with their position."""
    with pytest.raises(ValueError, match=match):
        encode_socket_values(**values)


@pytest.mark.parametrize("socket_id", [0, 100, 256, -1])
def test_check_socket_id(socket_id: int) -> None:
    """Socket ids are 1..255 without the broadcast type 100."""
    with pytest.raises(ValueError, match="id must be"):
        check_socket_id(socket_id)
    check_socket_id(64)
