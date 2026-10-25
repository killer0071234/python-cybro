"""A-bus transport and command frames.

Ported from the Cybro Edge Toolkit (`abus/transport_frame.py`,
`abus/command_frame.py`), with permission of Cybrotech.

Layout (little-endian)::

    signature 0x55AA | length | from_nad | to_nad    header <2H2L
    direction | type | body                           command frame
    transaction id | crc                              <2H

`length` counts the command frame and the transaction id. The CRC covers
everything before it.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

from ..exceptions import AbusError

ABUS_PORT = 8442
MAX_FRAME_BYTES = 1000
SIGNATURE = 0x55AA
TYPE_COMMAND = 0x00
TYPE_BROADCAST = 0x64

_HEADER = struct.Struct("<2H2L")
_COMMAND_HEAD = struct.Struct("<2B")
_TAIL = struct.Struct("<2H")
_TXN = struct.Struct("<H")
_MIN_SIZE = _HEADER.size + _COMMAND_HEAD.size + _TAIL.size

# bytes of a request frame that are not command parameters
OVERHEAD = _MIN_SIZE + 1  # + command byte

_CRC_TABLE = (
    0x049D, 0x0C07, 0x1591, 0x1ACF, 0x1D4B, 0x202D, 0x2507, 0x2B4B,
    0x34A5, 0x38C5, 0x3D3F, 0x4445, 0x4D0F, 0x538F, 0x5FB3, 0x6BBF,
)  # fmt: skip


class Command(IntEnum):
    """First body byte of a request."""

    PING = 0x10
    READ_STATUS = 0x11
    READ_CODE = 0x21
    WRITE_DATA = 0x32
    READ_RANDOM = 0x33
    WRITE_RANDOM = 0x34


class Direction(IntEnum):
    """Request, acknowledge or the reason a request was refused."""

    REQ = 0
    ACK = 1
    INTERNAL = 2
    BAD_CMD = 3
    BAD_PARA = 4
    NOT_READY = 5
    NO_PROG = 6
    NO_KERN = 7
    NO_SIGN = 8
    NO_AUTH = 9
    FORCED = 10
    BAD_FLASH = 11
    DENIED = 12


def direction_name(direction: int) -> str:
    """Return the name of a direction value, also for unknown values."""
    try:
        return Direction(direction).name
    except ValueError:
        return str(direction)


def crc(data: bytes) -> int:
    """Return the A-bus checksum of data."""
    return sum((b ^ 0x5A) * _CRC_TABLE[i & 0x0F] for i, b in enumerate(data)) & 0xFFFF


@dataclass(frozen=True)
class Frame:
    """One A-bus message (transport frame + command frame)."""

    from_nad: int
    to_nad: int
    txn: int
    direction: int
    msg_type: int
    body: bytes

    @classmethod
    def request(
        cls, from_nad: int, to_nad: int, txn: int, command: Command, params: bytes = b""
    ) -> Frame:
        """Return a request frame for a command."""
        return cls(
            from_nad,
            to_nad,
            txn,
            Direction.REQ,
            TYPE_COMMAND,
            bytes([command]) + params,
        )

    @property
    def size(self) -> int:
        """Return the encoded size in bytes."""
        return _MIN_SIZE + len(self.body)

    @property
    def kind(self) -> str:
        """Classify the frame: push, broadcast, socket, request or response."""
        if self.to_nad == 0 and self.direction == Direction.ACK:
            if self.msg_type == TYPE_COMMAND:
                return "push"
            if self.msg_type == TYPE_BROADCAST:
                return "broadcast"
            return "socket"
        return "request" if self.direction == Direction.REQ else "response"

    def __str__(self) -> str:
        """Return a one-line description."""
        return (
            f"{self.from_nad} -> {self.to_nad} txn={self.txn} "
            f"{direction_name(self.direction)} type={self.msg_type} "
            f"body={self.body.hex() or '-'}"
        )


def encode(frame: Frame) -> bytes:
    """Return the bytes of a frame."""
    data = _COMMAND_HEAD.pack(frame.direction, frame.msg_type) + frame.body
    head = _HEADER.pack(SIGNATURE, len(data) + _TXN.size, frame.from_nad, frame.to_nad)
    txn = _TXN.pack(frame.txn)
    return head + data + txn + _TXN.pack(crc(head + data + txn))


def decode(raw: bytes) -> Frame:
    """Parse and check a received frame, raise AbusError if it is invalid."""
    if len(raw) < _MIN_SIZE:
        raise AbusError(f"frame too short ({len(raw)} bytes)")
    signature, length, from_nad, to_nad = _HEADER.unpack_from(raw)
    if signature != SIGNATURE:
        raise AbusError(f"invalid signature {signature:#06x}")
    data_size = len(raw) - _MIN_SIZE + _COMMAND_HEAD.size
    if length != data_size + _TXN.size:
        raise AbusError(f"length field {length} != {data_size + _TXN.size}")
    end = _HEADER.size + data_size
    txn, checksum = _TAIL.unpack_from(raw, end)
    if checksum != crc(raw[: end + _TXN.size]):
        raise AbusError("invalid crc")
    direction, msg_type = _COMMAND_HEAD.unpack_from(raw, _HEADER.size)
    body = raw[_HEADER.size + _COMMAND_HEAD.size : end]
    return Frame(from_nad, to_nad, txn, direction, msg_type, bytes(body))
