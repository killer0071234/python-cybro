"""Fake A-bus controller on UDP for tests (this library and its users).

fake = await FakeAbusController.start(10000, [FakeVar("scan_time", "int", 5)])
transport = await AbusTransport.create(("127.0.0.1", 0), port=fake.port)
client = AbusClient(transport, 10000, ip="127.0.0.1")
"""

from __future__ import annotations

import asyncio
import io
import struct
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from .abus.alc import DataType
from .abus.client import FILE_INFO_ADDR
from .abus.client import HEAD_ADDR
from .abus.client import SEGMENT_SIZE
from .abus.client import PlcStatus
from .abus.client import SystemStatus
from .abus.frame import Command
from .abus.frame import Direction
from .abus.frame import Frame
from .abus.frame import decode
from .abus.frame import encode

Addr = tuple[str, int]

_FILE_TABLE_ADDR = HEAD_ADDR + 0x100
_FILES_ADDR = HEAD_ADDR + 0x200
_FIRST_VAR_ADDR = 0x0100
_DATA_SIZE = 0x10000


@dataclass
class FakeVar:
    """A variable of the fake's program (data_type: bit, int, long or real)."""

    name: str
    data_type: str
    value: float = 0
    user: bool = False
    array: int = 1
    scope: str = "global"
    description: str = ""


def cybro_timestamp(value: datetime) -> int:
    """Convert a datetime to a controller timestamp (FAT style)."""
    return (
        (value.year - 1980) << 25
        | value.month << 21
        | value.day << 16
        | value.hour << 11
        | value.minute << 5
        | value.second // 2
    )


class FakeAbusController(asyncio.DatagramProtocol):
    """Answers A-bus requests like a controller with a loaded program."""

    def __init__(
        self,
        nad: int,
        variables: Iterable[FakeVar] = (),
        *,
        alc_text: str | None = None,
        password: int | None = None,
        timestamp: datetime = datetime(2026, 10, 10, 14, 19, 50),  # noqa: B008
        magic: int = 31416,
    ) -> None:
        """Initialize, use `start()` to open the socket.

        alc_text replaces the ALC generated from the variables.
        """
        self.nad = nad
        self.password = password
        self.system_status = SystemStatus.KERNEL_ACTIVE
        self.plc_status = PlcStatus.RUN
        self.silent = False  # True: never answer
        self.drop = 0  # number of requests to leave unanswered
        self.requests: list[Frame] = []
        self.code = bytearray()
        self.data = bytearray(_DATA_SIZE)
        self.alc_text = ""
        self.addresses: dict[str, tuple[int, DataType]] = {}
        self._transport: asyncio.DatagramTransport | None = None
        self.load_program(variables, timestamp, magic, alc_text)

    @classmethod
    async def start(
        cls, nad: int, *args, host: str = "127.0.0.1", port: int = 0, **kwargs
    ) -> FakeAbusController:
        """Create the fake and bind it to host:port."""
        loop = asyncio.get_running_loop()
        _, fake = await loop.create_datagram_endpoint(
            lambda: cls(nad, *args, **kwargs), local_addr=(host, port)
        )
        return fake

    @property
    def addr(self) -> Addr:
        """Return the bound address."""
        return self._transport.get_extra_info("sockname")[:2]

    @property
    def port(self) -> int:
        """Return the bound port."""
        return self.addr[1]

    def close(self) -> None:
        """Close the socket."""
        self._transport.close()

    def load_program(
        self,
        variables: Iterable[FakeVar],
        timestamp: datetime,
        magic: int = 31416,
        alc_text: str | None = None,
    ) -> None:
        """Replace the program: variables, head, file table and alc.zip."""
        lines = [
            f";CPU CyBro-2 {self.nad}",
            ";Addr Id    Array Offset Size Scope  Type  Name",
        ]
        self.addresses = {}
        address = _FIRST_VAR_ADDR
        for var in variables:
            data_type = DataType[var.data_type.upper()]
            lines.append(
                f"{address:04X}  {0x02 if var.user else 0:05X} {var.array:<5} {0:<6} "
                f"{data_type.size:<4} {var.scope:<6} {var.data_type.lower():<5} "
                f"{var.name:<32} {var.description}".rstrip()
            )
            names = (
                [var.name]
                if var.array <= 1
                else [f"{var.name}[{i}]" for i in range(var.array)]
            )
            for name in names:
                self.addresses[name] = (address, data_type)
                self[name] = var.value
                address += data_type.size
        self.alc_text = alc_text if alc_text is not None else "\r\n".join([*lines, ""])

        stamp = cybro_timestamp(timestamp)
        files = [
            ("cyp.zip", _zip("program.cyp", b"")),
            ("alc.zip", _zip_alc(self.alc_text)),
        ]
        self.code = bytearray(_FILES_ADDR)
        table = bytearray()
        for name, data in files:
            table += struct.pack(
                "<32sH3L", name.encode(), len(name), len(self.code), len(data), stamp
            )
            self.code += data
        self.code[_FILE_TABLE_ADDR : _FILE_TABLE_ADDR + len(table)] = table
        struct.pack_into("<6HL", self.code, HEAD_ADDR, 0, magic, 0, 0, 0, 0, stamp)
        struct.pack_into("<LH", self.code, FILE_INFO_ADDR, _FILE_TABLE_ADDR, len(files))

    def __getitem__(self, name: str) -> int | float:
        """Return the value of a variable."""
        address, data_type = self.addresses[name]
        return struct.unpack_from(data_type.fmt, self.data, address)[0]

    def __setitem__(self, name: str, value: float) -> None:
        """Set the value of a variable."""
        address, data_type = self.addresses[name]
        if data_type != DataType.REAL:
            value = int(value)
        struct.pack_into(data_type.fmt, self.data, address, value)

    def send_frame(self, frame: Frame, addr: Addr) -> None:
        """Send a frame on its own (push, socket or broadcast)."""
        self._transport.sendto(encode(frame), addr)

    # asyncio.DatagramProtocol

    def connection_made(self, transport) -> None:
        """Store the transport."""
        self._transport = transport

    def datagram_received(self, data: bytes, addr: Addr) -> None:
        """Answer a request addressed to this controller (or to NAD 0)."""
        frame = decode(data)
        if frame.direction != Direction.REQ or not frame.body:
            return
        command = frame.body[0]
        if frame.to_nad != self.nad and not (
            frame.to_nad == 0 and command in (Command.PING, Command.READ_STATUS)
        ):
            return
        self.requests.append(frame)
        if self.silent:
            return
        if self.drop:
            self.drop -= 1
            return
        if self.password is not None and frame.txn != self.password:
            direction, body = Direction.DENIED, b""
        else:
            direction, body = self._handle(command, frame.body[1:])
        answer = Frame(self.nad, frame.from_nad, frame.txn, direction, 0, body)
        self._transport.sendto(encode(answer), addr)

    def _handle(self, command: int, params: bytes) -> tuple[Direction, bytes]:
        if command == Command.PING:
            return Direction.ACK, b""
        if command == Command.READ_STATUS:
            return Direction.ACK, bytes([self.system_status, self.plc_status])
        if command == Command.READ_CODE:
            segment, size = struct.unpack("<2H", params)
            if size > SEGMENT_SIZE:
                return Direction.BAD_PARA, b""
            start = segment * SEGMENT_SIZE
            return Direction.ACK, bytes(self.code[start : start + size]).ljust(
                size, b"\xff"
            )
        return Direction.BAD_CMD, b""


def _zip(name: str, data: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, data)
    return buffer.getvalue()


def _zip_alc(text: str) -> bytes:
    return _zip("program.alc", text.encode("latin-1"))
