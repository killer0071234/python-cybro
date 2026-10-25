"""Allocation table (ALC): where each variable lives in controller memory.

Ported from the Cybro Edge Toolkit (`alc_parser.py`, `var_info.py`,
`unzip.py`), with permission of Cybrotech.
"""

from __future__ import annotations

import io
import math
import re
import zipfile
import zlib
from dataclasses import dataclass
from enum import Enum

from ..exceptions import AbusError

ENCODING = "latin-1"
FLOAT32_MAX = 3.4028234663852886e38

_LINE = re.compile(
    r"^(\w*)\s*(\w*)\s*(\w*)\s*(\w*)\s*(\w*)\s*(\w*)\s*(\w*)\s*([\w.]*)\s*(.*)"
)


class DataType(Enum):
    """Type of a controller variable with its size and struct format."""

    BIT = (1, "<B")
    INT = (2, "<h")
    LONG = (4, "<l")
    REAL = (4, "<f")

    def __init__(self, size: int, fmt: str) -> None:
        """Store size and format."""
        self.size = size
        self.fmt = fmt


_INT_RANGES = {
    DataType.BIT: (0, 1),
    DataType.INT: (-0x8000, 0x7FFF),
    DataType.LONG: (-0x80000000, 0x7FFFFFFF),
}


@dataclass(frozen=True)
class AlcVar:
    """One variable (or array element) of the allocation table."""

    name: str
    address: int
    size: int
    data_type: DataType | None  # None: type the library can't read
    user: bool
    scope: str
    description: str


def parse_alc(text: str) -> dict[str, AlcVar]:
    """Parse the ALC text; arrays are expanded to name[i].

    Comment lines (";"), empty lines and lines that don't parse are skipped.
    """
    result: dict[str, AlcVar] = {}
    for line in text.splitlines():
        line = line.rstrip()
        if not line or line[0] == ";":
            continue
        match = _LINE.search(line)
        try:
            address = int(match[1], 16) + int(match[4])  # + offset (timers, counters)
            ident = int(match[2], 16)
            array = int(match[3])
            size = int(match[5])
        except ValueError:
            continue
        data_type = DataType.__members__.get(match[7].upper())
        if data_type is not None:
            size = data_type.size
        name = match[8]
        names = [name] if array <= 1 else [f"{name}[{i}]" for i in range(array)]
        for i, item in enumerate(names):
            result[item] = AlcVar(
                item,
                address + i * size,
                size,
                data_type,
                bool(ident & 0x02),
                match[6],
                match[9],
            )
    return result


def unzip_alc(data: bytes) -> str:
    """Return the text of the first file in alc.zip (blocking)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            member = next(i for i in archive.infolist() if not i.is_dir())
            return archive.read(member).decode(ENCODING)
    except (
        zipfile.BadZipFile,
        StopIteration,
        RuntimeError,
        OSError,
        zlib.error,
    ) as err:
        raise AbusError("invalid alc.zip") from err


def format_value(value: float, data_type: DataType) -> str:
    """Format a value like the scgi server: str() of the read value.

    A REAL is the float32 widened to a double, e.g. -1512.10107421875.
    """
    return str(float(value) if data_type == DataType.REAL else int(value))


def parse_value(text: str, data_type: DataType) -> int | float:
    """Convert a written string to the type, raise ValueError if it doesn't fit.

    Integer types are truncated like the scgi server does (int(float(text))).
    """
    try:
        number = float(text)
    except ValueError:
        raise ValueError(f"invalid value {text!r} for {data_type.name}") from None
    if data_type == DataType.REAL:
        if math.isfinite(number) and abs(number) > FLOAT32_MAX:
            raise ValueError(f"{text} is out of range for REAL")
        return number
    low, high = _INT_RANGES[data_type]
    if not math.isfinite(number) or not low <= int(number) <= high:
        raise ValueError(f"{text} is out of range for {data_type.name}")
    return int(number)
