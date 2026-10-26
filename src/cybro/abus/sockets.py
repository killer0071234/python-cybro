"""Cybro sockets: the variable lists controllers send and receive.

A socket frame (to_nad 0, ACK, type = socket id) carries the values of a
socket definition, packed little-endian in this order: all BIT slots (1 byte),
all INT slots (2 bytes), all LONG slots (4 bytes, also REAL). The frame has no
layout information, so the definition must match the PLC program. Verified
in both directions on hardware (docs/abus-notes.md, S9).

Definitions use the Edge Toolkit syntax (`[ETH] socket =` in config.ini)::

    id; bits; ints; longs        e.g. "1; socket_req, my_bit0; my_int0; my_long0"
"""

from __future__ import annotations

import re
import struct
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from dataclasses import dataclass

from ..exceptions import AbusError
from .alc import AlcVar
from .alc import DataType
from .alc import parse_value
from .frame import TYPE_BROADCAST
from .frame import TYPE_COMMAND

_NAME = re.compile(r"^[\w.\[\]]+$")
# ALC types that fit each slot (a REAL travels in a LONG slot)
_SLOT_TYPES = {
    DataType.BIT: (DataType.BIT,),
    DataType.INT: (DataType.INT,),
    DataType.LONG: (DataType.LONG, DataType.REAL),
}


@dataclass(frozen=True)
class SocketDef:
    """Socket id and its variables (names without the c<nad>. prefix)."""

    socket_id: int
    bits: tuple[str, ...] = ()
    ints: tuple[str, ...] = ()
    longs: tuple[str, ...] = ()

    @classmethod
    def parse(cls, text: str) -> SocketDef:
        """Parse "id; bits; ints; longs", raise ValueError if invalid.

        Missing and empty groups are empty. A trailing ";" is allowed.
        """
        socket_id, *groups = text.split(";")
        while len(groups) > 3 and not groups[-1].strip():
            groups.pop()
        if len(groups) > 3:
            raise ValueError(f"socket {text!r}: more than 3 variable groups")
        try:
            number = int(socket_id)
        except ValueError:
            raise ValueError(f"socket {text!r}: invalid id {socket_id!r}") from None
        try:
            check_socket_id(number)
        except ValueError as err:
            raise ValueError(f"socket {text!r}: {err}") from None
        names = [_parse_group(text, group) for group in groups]
        definition = cls(number, *names)
        all_names = [name for name, _ in definition.slots()]
        if not all_names:
            raise ValueError(f"socket {text!r}: no variables")
        duplicates = sorted({name for name in all_names if all_names.count(name) > 1})
        if duplicates:
            raise ValueError(f"socket {text!r}: duplicate {', '.join(duplicates)}")
        return definition

    @property
    def size(self) -> int:
        """Return the size of the body in bytes."""
        return sum(slot.size for _, slot in self.slots())

    def slots(self) -> Iterator[tuple[str, DataType]]:
        """Yield (name, slot type) in body order."""
        for names, slot in (
            (self.bits, DataType.BIT),
            (self.ints, DataType.INT),
            (self.longs, DataType.LONG),
        ):
            for name in names:
                yield name, slot


@dataclass(frozen=True)
class SocketMessage:
    """Decoded socket frame of a controller."""

    nad: int
    socket_id: int
    values: dict[str, int | float]  # names without the c<nad>. prefix
    addr: tuple[str, int]  # where the frame came from


def decode_socket(
    definition: SocketDef, body: bytes, alc: Mapping[str, AlcVar] | None
) -> dict[str, int | float]:
    """Return the values of a socket body by name.

    Each value has the type of its ALC variable; names missing from the ALC
    get the slot type. Raises AbusError if the body length doesn't match or
    an ALC variable doesn't fit its slot.
    """
    if len(body) != definition.size:
        raise AbusError(
            f"socket {definition.socket_id}: body has {len(body)} bytes, "
            f"definition {definition.size} bytes"
        )
    values: dict[str, int | float] = {}
    pos = 0
    for name, data_type in _types(definition, alc):
        (values[name],) = struct.unpack_from(data_type.fmt, body, pos)
        pos += data_type.size
    return values


def encode_socket(
    definition: SocketDef,
    values: Mapping[str, str | int | float],
    alc: Mapping[str, AlcVar] | None,
) -> bytes:
    """Return the socket body for values by name (all slots are required).

    Values are checked like a written variable (`parse_value`). Raises
    ValueError for missing, unknown or invalid values, AbusError if an ALC
    variable doesn't fit its slot.
    """
    types = list(_types(definition, alc))
    unknown = sorted(set(values) - {name for name, _ in types})
    if unknown:
        raise ValueError(f"{', '.join(unknown)} not in socket {definition.socket_id}")
    missing = [name for name, _ in types if name not in values]
    if missing:
        raise ValueError(
            f"socket {definition.socket_id}: missing values for {', '.join(missing)}"
        )
    body = b""
    for name, data_type in types:
        try:
            value = parse_value(str(values[name]), data_type)
        except ValueError as err:
            raise ValueError(f"{name}: {err}") from err
        body += struct.pack(data_type.fmt, value)
    return body


def encode_socket_values(
    bits: Iterable[str | int] = (),
    ints: Iterable[str | int] = (),
    longs: Iterable[str | int] = (),
    reals: Iterable[str | float] = (),
) -> bytes:
    """Return a socket body for values by type, when the names are unknown.

    The body order is bits, ints, longs, reals; it must match the receive
    socket of the PLC program. Values are checked like a written variable.
    Raises ValueError for an empty socket or an invalid value.
    """
    body = b""
    for group, data_type in (
        (bits, DataType.BIT),
        (ints, DataType.INT),
        (longs, DataType.LONG),
        (reals, DataType.REAL),
    ):
        for index, value in enumerate(group):
            label = f"{data_type.name.lower()}s[{index}]"
            try:
                number = parse_value(str(value), data_type)
            except ValueError as err:
                raise ValueError(f"{label}: {err}") from err
            body += struct.pack(data_type.fmt, number)
    if not body:
        raise ValueError("socket has no values")
    return body


def check_socket_id(socket_id: int) -> None:
    """Raise ValueError unless socket_id is 1..255 (100 is the broadcast type)."""
    if not 0 < socket_id <= 0xFF or socket_id in (TYPE_COMMAND, TYPE_BROADCAST):
        raise ValueError(f"socket id must be 1..255, except 100 (got {socket_id})")


def _parse_group(text: str, group: str) -> tuple[str, ...]:
    names = [name.strip() for name in group.split(",")]
    if names == [""]:
        return ()
    for name in names:
        if not _NAME.match(name):
            raise ValueError(f"socket {text!r}: invalid name {name!r}")
    return tuple(names)


def _types(
    definition: SocketDef, alc: Mapping[str, AlcVar] | None
) -> Iterator[tuple[str, DataType]]:
    """Yield (name, type to decode with) in body order."""
    for name, slot in definition.slots():
        var = alc.get(name) if alc is not None else None
        if var is None:
            yield name, slot
        elif var.data_type in _SLOT_TYPES[slot]:
            yield name, var.data_type
        else:
            found = var.data_type.name if var.data_type else f"{var.size}-byte type"
            raise AbusError(
                f"socket {definition.socket_id}: {name} is {found}, "
                f"not allowed in a {slot.name} slot"
            )
