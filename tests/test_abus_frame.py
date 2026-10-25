"""Tests for the A-bus frame codec (`cybro.abus.frame`)."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from src.cybro.abus.frame import Command
from src.cybro.abus.frame import Direction
from src.cybro.abus.frame import Frame
from src.cybro.abus.frame import crc
from src.cybro.abus.frame import decode
from src.cybro.abus.frame import encode
from src.cybro.exceptions import AbusError
from src.cybro.exceptions import CybroError

FIXTURES = Path(__file__).parent / "fixtures" / "abus"

# Golden frames from docs/abus-notes.md (captured from c32215)
HEAD_REQUEST = "aa550900f2030000d77d0000000021000246000100bbe8"
READ_RANDOM_REQUEST = "aa550d00f2030000d77d000000003300000000010018322a009940"
READ_RANDOM_RESPONSE = "aa550800d77d0000f2030000010021dbd1c42a009e48"


def captured_frames() -> list[str]:
    """Return the hex of every captured frame."""
    return [
        json.loads(line)["hex"]
        for name in ("frames.jsonl", "frames1.jsonl")
        for line in (FIXTURES / name).read_text().splitlines()
    ]


def test_capture_is_complete() -> None:
    """All 390 frames of the hardware spike are available."""
    assert len(captured_frames()) == 390


@pytest.mark.parametrize("raw", captured_frames())
def test_captured_frames_round_trip(raw: str) -> None:
    """Every captured frame decodes and re-encodes to identical bytes."""
    data = bytes.fromhex(raw)
    assert encode(decode(data)) == data


def test_decode_head_request() -> None:
    """The READ_CODE head request decodes to its fields."""
    frame = decode(bytes.fromhex(HEAD_REQUEST))
    assert frame == Frame(
        from_nad=1010,
        to_nad=32215,
        txn=1,
        direction=Direction.REQ,
        msg_type=0,
        body=bytes([Command.READ_CODE]) + struct.pack("<2H", 0x200, 0x46),
    )


def test_decode_read_random_response() -> None:
    """The READ_RANDOM response carries one REAL value."""
    frame = decode(bytes.fromhex(READ_RANDOM_RESPONSE))
    assert (frame.from_nad, frame.to_nad, frame.txn) == (32215, 1010, 42)
    assert frame.direction == Direction.ACK
    assert struct.unpack("<f", frame.body) == (-1678.8477783203125,)


def test_encode_request() -> None:
    """A request built from its fields gives the captured bytes."""
    body = bytes([Command.READ_RANDOM]) + struct.pack("<3HH", 0, 0, 1, 0x3218)
    frame = Frame(1010, 32215, 42, Direction.REQ, 0, body)
    assert encode(frame).hex() == READ_RANDOM_REQUEST
    assert frame.size == len(bytes.fromhex(READ_RANDOM_REQUEST))


def test_crc() -> None:
    """CRC = sum of (byte ^ 0x5A) * PRIM[i & 15], cut to 16 bits."""
    assert crc(b"") == 0
    assert crc(b"\x5a") == 0
    assert crc(b"\x5b") == 0x049D
    assert crc(b"\x5a" * 15 + b"\x5b") == 0x6BBF
    assert crc(b"\x5a" * 16 + b"\x5b") == 0x049D
    data = bytes.fromhex(HEAD_REQUEST)
    assert crc(data[:-2]) == struct.unpack("<H", data[-2:])[0]


def test_frame_kind() -> None:
    """Frames are classified like the scgi server router does."""
    assert Frame(8372, 0, 0, Direction.ACK, 0, b"").kind == "push"
    assert Frame(8372, 0, 0, Direction.ACK, 0x64, b"").kind == "broadcast"
    assert Frame(8372, 0, 0, Direction.ACK, 23, b"").kind == "socket"
    assert Frame(1003, 8372, 5, Direction.REQ, 0, b"\x10").kind == "request"
    assert Frame(8372, 1010, 5, Direction.ACK, 0, b"").kind == "response"
    assert Frame(8372, 1010, 5, Direction.DENIED, 0, b"").kind == "response"


def test_unknown_direction_is_kept() -> None:
    """A direction value without a name still decodes and round-trips."""
    frame = Frame(8372, 1010, 5, 99, 0, b"")
    assert decode(encode(frame)) == frame


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"", "too short"),
        (bytes.fromhex(HEAD_REQUEST)[:15], "too short"),
        (b"\x55\xaa" + bytes.fromhex(HEAD_REQUEST)[2:], "signature"),
        (bytes.fromhex(HEAD_REQUEST)[:-1] + b"\x00", "crc"),
        (bytes.fromhex(HEAD_REQUEST) + b"\x00", "length"),
        (bytes.fromhex(HEAD_REQUEST)[:-3] + bytes.fromhex(HEAD_REQUEST)[-2:], "length"),
    ],
)
def test_decode_rejects_invalid_frames(raw: bytes, message: str) -> None:
    """Short frames, a wrong signature, length or CRC raise AbusError."""
    with pytest.raises(AbusError, match=message):
        decode(raw)


def test_length_field_must_match() -> None:
    """A frame whose length field is too small is rejected (toolkit accepts it)."""
    frame = bytearray(encode(Frame(1010, 32215, 1, Direction.REQ, 0, b"\x10\x00")))
    struct.pack_into("<H", frame, 2, 3)  # real length is 4
    struct.pack_into("<H", frame, len(frame) - 2, crc(bytes(frame[:-2])))
    with pytest.raises(AbusError, match="length"):
        decode(bytes(frame))


def test_abus_error_is_cybro_error() -> None:
    """Callers can catch every library error as CybroError."""
    assert issubclass(AbusError, CybroError)


def test_request_and_str() -> None:
    """Frame.request builds a command request; str() is one readable line."""
    frame = Frame.request(1010, 10000, 1, Command.PING)
    assert frame == Frame(1010, 10000, 1, Direction.REQ, 0, b"\x10")
    assert str(frame) == "1010 -> 10000 txn=1 REQ type=0 body=10"
    assert (
        str(Frame(10000, 1010, 1, 99, 0, b"")) == "10000 -> 1010 txn=1 99 type=0 body=-"
    )
