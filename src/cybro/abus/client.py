"""Operations on one controller over a shared AbusTransport.

Ported from the Cybro Edge Toolkit (`plc_client.py`, `plc_head.py`,
`status.py`, `file_descriptor.py`), with permission of Cybrotech.
"""

from __future__ import annotations

import asyncio
import struct
import time
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum

from ..exceptions import AbusError
from ..exceptions import CybroConnectionTimeoutError
from .alc import unzip_alc
from .frame import Command
from .frame import Direction
from .frame import Frame
from .frame import direction_name
from .transport import AbusTransport

SOURCE_NAD = 1010

SEGMENT_SIZE = 0x100
HEAD_ADDR = 0x20000
HEAD_SIZE = 0x46
FILE_INFO_ADDR = HEAD_ADDR + 0x40
MAGICS = {31415: "Cybro-2", 31416: "Cybro-3"}

_HEAD = struct.Struct("<6HL")
_FILE_INFO = struct.Struct("<LH")
_SCAN_OVERRUN = struct.Struct("<H")
_SCAN_OVERRUN_OFFSET = 0x38
_FILE_DESCRIPTOR = struct.Struct("<32sH3L")


class AbusResponseError(AbusError):
    """The controller refused a request (DENIED, NO_PROG, ...)."""

    def __init__(self, nad: int, direction: int) -> None:
        """Initialize with the direction of the answer."""
        super().__init__(f"c{nad} answered {direction_name(direction)}")
        self.direction = direction


class SystemStatus(IntEnum):
    """Which firmware part runs."""

    LOADER_ACTIVE = 0
    KERNEL_ACTIVE = 1


class PlcStatus(IntEnum):
    """Run state of the PLC program."""

    STOP = 0
    PAUSE = 1
    RUN = 2
    NO_VALID_PROGRAM = 3
    SCAN_OVERRUN = 4


@dataclass(frozen=True)
class Status:
    """Answer to READ_STATUS."""

    system: SystemStatus
    plc: PlcStatus

    @classmethod
    def from_bytes(cls, data: bytes) -> Status:
        """Decode the 2 status bytes."""
        try:
            return cls(SystemStatus(data[0]), PlcStatus(data[1]))
        except (IndexError, ValueError) as err:
            raise AbusError(f"invalid status {data.hex()}") from err

    @property
    def ok(self) -> bool:
        """Return True if variables can be read (kernel active, run or pause)."""
        return self.system == SystemStatus.KERNEL_ACTIVE and self.plc in (
            PlcStatus.RUN,
            PlcStatus.PAUSE,
        )


def cybro_datetime(value: int) -> datetime | None:
    """Convert a controller timestamp (FAT style) to a datetime."""
    try:
        return datetime(
            ((value >> 25) & 0x7F) + 1980,
            (value >> 21) & 0x0F,
            (value >> 16) & 0x1F,
            (value >> 11) & 0x1F,
            (value >> 5) & 0x3F,
            (value & 0x1F) * 2,
        )
    except ValueError:
        return None


@dataclass(frozen=True)
class PlcHead:
    """Program head in code memory (0x20000)."""

    empty: int
    magic: int
    crc: int
    code_crc: int
    alloc_crc: int
    retentive_crc: int
    timestamp: int
    scan_overrun: int
    file_system_addr: int
    file_count: int

    @classmethod
    def from_bytes(cls, data: bytes) -> PlcHead:
        """Decode the head."""
        if len(data) < HEAD_SIZE:
            raise AbusError(f"plc head too short ({len(data)} bytes)")
        return cls(
            *_HEAD.unpack_from(data),
            *_SCAN_OVERRUN.unpack_from(data, _SCAN_OVERRUN_OFFSET),
            *_FILE_INFO.unpack_from(data, FILE_INFO_ADDR - HEAD_ADDR),
        )

    @property
    def program_timestamp(self) -> datetime | None:
        """Return the time the program was transferred."""
        return cybro_datetime(self.timestamp)

    @property
    def model(self) -> str | None:
        """Return the controller generation from the magic number."""
        return MAGICS.get(self.magic)

    @property
    def valid(self) -> bool:
        """Return True if a program with a file system is loaded."""
        return (
            self.empty == 0
            and self.magic in MAGICS
            and self.file_system_addr > 0
            and self.file_count > 0
        )


@dataclass(frozen=True)
class FileDescriptor:
    """Entry of the file table in code memory."""

    name: str
    address: int
    size: int
    timestamp: int

    @classmethod
    def from_bytes(cls, data: bytes, offset: int = 0) -> FileDescriptor:
        """Decode one 46-byte descriptor."""
        name, name_len, address, size, timestamp = _FILE_DESCRIPTOR.unpack_from(
            data, offset
        )
        return cls(name[:name_len].decode("latin-1"), address, size, timestamp)


class AbusClient:
    """Request/response with one controller.

    Without an IP the requests are broadcast, and the IP is learned from the
    first answer. A learned IP is dropped after a timeout and the request is
    broadcast once more (the controller may have a new address). A static IP
    is always used as given.
    """

    def __init__(
        self,
        transport: AbusTransport,
        nad: int,
        ip: str | None = None,
        password: int | None = None,
        source_nad: int = SOURCE_NAD,
        port: int | None = None,
    ) -> None:
        """Initialize (port: controller UDP port, default from the transport)."""
        self.transport = transport
        self.nad = nad
        self.ip = ip
        self.static_ip = ip is not None
        self.password = password
        self.source_nad = source_nad
        self.port = port or transport.port
        self.bytes_transferred = 0
        self.com_error_count = 0
        self.response_time = 0.0  # seconds, last exchange
        self._lock = asyncio.Lock()

    async def call(self, command: Command, params: bytes = b"") -> bytes:
        """Send a command and return the body of the ACK."""
        async with self._lock:
            try:
                response = await self._exchange(command, params)
            except CybroConnectionTimeoutError:
                if self.static_ip or self.ip is None:
                    raise
                self.ip = None
                response = await self._exchange(command, params)
        if response.direction != Direction.ACK:
            raise AbusResponseError(self.nad, response.direction)
        return response.body

    async def _exchange(self, command: Command, params: bytes) -> Frame:
        txn = self.password if self.password is not None else self.transport.next_txn()
        request = Frame.request(self.source_nad, self.nad, txn, command, params)
        addr = (self.ip, self.port) if self.ip else None
        start = time.monotonic()
        try:
            response, source = await self.transport.exchange(request, addr)
        except CybroConnectionTimeoutError:
            self.com_error_count += 1
            raise
        self.response_time = time.monotonic() - start
        self.bytes_transferred += request.size + response.size
        self.ip = source[0]
        return response

    async def ping(self) -> None:
        """Ping the controller (finds its IP if unknown)."""
        await self.call(Command.PING)

    async def read_status(self) -> Status:
        """Return the system and PLC status."""
        return Status.from_bytes(await self.call(Command.READ_STATUS))

    async def read_code(self, addr: int, size: int) -> bytes:
        """Read code memory, one segment per exchange."""
        result = bytearray()
        while size > 0:
            segment, offset = divmod(addr, SEGMENT_SIZE)
            count = min(SEGMENT_SIZE - offset, size)
            body = await self.call(
                Command.READ_CODE, struct.pack("<2H", segment, offset + count)
            )
            chunk = body[offset : offset + count]
            if len(chunk) != count:
                raise AbusError(
                    f"read_code {addr:#x}: got {len(body)} bytes, need {offset + count}"
                )
            result += chunk
            addr += count
            size -= count
        return bytes(result)

    async def read_head(self) -> PlcHead:
        """Read and decode the program head."""
        return PlcHead.from_bytes(await self.read_code(HEAD_ADDR, HEAD_SIZE))

    async def read_files(self, head: PlcHead | None = None) -> list[FileDescriptor]:
        """Read the file table (from the given head or a fresh one)."""
        if head is None:
            head = await self.read_head()
        raw = await self.read_code(
            head.file_system_addr, head.file_count * _FILE_DESCRIPTOR.size
        )
        return [
            FileDescriptor.from_bytes(raw, i * _FILE_DESCRIPTOR.size)
            for i in range(head.file_count)
        ]

    async def fetch_alc(self, head: PlcHead | None = None) -> str:
        """Download alc.zip and return the allocation table text."""
        files = await self.read_files(head)
        alc = next((f for f in files if f.name == "alc.zip"), None)
        if alc is None:
            raise AbusError(f"c{self.nad} has no alc.zip")
        data = await self.read_code(alc.address, alc.size)
        return await asyncio.get_running_loop().run_in_executor(None, unzip_alc, data)
