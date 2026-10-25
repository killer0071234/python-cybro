"""Allocation table (ALC): where each variable lives in controller memory.

Ported from the Cybro Edge Toolkit (`alc_parser.py`, `var_info.py`,
`unzip.py`), with permission of Cybrotech.
"""

from __future__ import annotations

import io
import re
import zipfile
import zlib
from dataclasses import dataclass
from enum import Enum

from ..exceptions import AbusError

ENCODING = "latin-1"

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
