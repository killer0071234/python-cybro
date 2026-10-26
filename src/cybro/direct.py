"""Cybro API without scgi server: direct A-bus (UDP) to one controller.

CybroDirect answers request() the way the scgi server does (as parsed by
xmltodict), so update(), read_var(), write_var() and add_var() of Cybro
work unchanged. Behaviour follows the Edge Toolkit's scgi_server:

- Every request that touches c<nad>.* reads the program head and status
  first. A new program timestamp downloads and parses the ALC again.
- Values are formatted with str(), unknown variables are "?".
- c<nad>.sys.plc_status: "ok", "pgm missing", "alc missing" or "offline".

Socket frames of definitions added with add_socket() update device.vars as
they arrive and go to the on_socket() callbacks (only while the transport is
bound to port 8442). They can be missed, so keep calling update().

Unlike the scgi server, a controller that doesn't answer raises
CybroConnectionTimeoutError (there is no server that could answer instead).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version
from typing import Any

from .abus.alc import AlcVar
from .abus.alc import format_value
from .abus.alc import parse_alc
from .abus.alc import parse_value
from .abus.client import SOURCE_NAD
from .abus.client import AbusClient
from .abus.client import AbusResponseError
from .abus.frame import Frame
from .abus.sockets import SocketDef
from .abus.sockets import SocketMessage
from .abus.sockets import decode_socket
from .abus.transport import AbusTransport
from .abus.transport import Addr
from .cybro import Cybro
from .exceptions import AbusError
from .exceptions import CybroConnectionTimeoutError
from .exceptions import CybroError
from .models import Var

try:
    __version__ = version("cybro")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "unknown"

_LOGGER = logging.getLogger(__name__)

SocketCallback = Callable[[SocketMessage], None]

STATUS_OK = "ok"
STATUS_NO_PROGRAM = "pgm missing"
STATUS_NO_ALC = "alc missing"
STATUS_OFFLINE = "offline"


class CybroDirect(Cybro):
    """Same API as Cybro, talking to the controller directly over A-bus."""

    _var_chunk_size = 1_000_000  # no chunking, reads are split into frames

    def __init__(
        self,
        transport: AbusTransport,
        nad: int,
        ip: str | None = None,
        password: int | None = None,
        source_nad: int = SOURCE_NAD,
        port: int | None = None,
    ) -> None:
        """Define a direct connection to controller NAD.

        Args:
            transport: shared A-bus transport; not closed by disconnect()
            nad: Cybro PLC NAD (serial number)
            ip: static IP of the controller (default: found by broadcast)
            password: A-bus password, if the controller has one
            source_nad: NAD this client uses on the bus
            port: controller UDP port (default: the transport's)
        """
        self.client = AbusClient(transport, nad, ip, password, source_nad, port)
        self.transport = transport
        self.host = ip or ""
        self.port = self.client.port
        self.path = ""
        self.nad = nad
        self._prefix = f"c{nad}."
        self._lock = asyncio.Lock()
        self._started = time.monotonic()
        self._request_count = 0
        self._plc_status = "?"
        self._timestamp: int | None = None  # program the ALC belongs to
        self._program_timestamp = "?"
        self._alc: dict[str, AlcVar] | None = None
        self._alc_text: str | None = None
        self._sockets: dict[int, SocketDef] = {}
        self._socket_callbacks: list[SocketCallback] = []
        self._remove_socket_handler: Callable[[], None] | None = None

    def add_socket(self, definition: SocketDef | str) -> SocketDef:
        """Handle socket frames of this definition from now on.

        definition is a SocketDef or its text ("id; bits; ints; longs").
        Frames before the first update() (no ALC yet) are dropped.
        Raises ValueError if the text is invalid.
        """
        if isinstance(definition, str):
            definition = SocketDef.parse(definition)
        self._sockets[definition.socket_id] = definition
        if self._remove_socket_handler is None:
            self._remove_socket_handler = self.transport.on_socket(
                self._socket_received
            )
        return definition

    def remove_socket(self, socket_id: int) -> None:
        """Stop handling a socket id."""
        self._sockets.pop(socket_id, None)

    def on_socket(self, callback: SocketCallback) -> Callable[[], None]:
        """Call callback(message) for each decoded socket frame.

        It runs inside the transport's receive handler, so it must not block.
        Returns a function that removes the callback again.
        """
        self._socket_callbacks.append(callback)
        return lambda: self._socket_callbacks.remove(callback)

    async def disconnect(self) -> None:
        """Stop the socket handling (definitions are removed)."""
        if self._remove_socket_handler is not None:
            self._remove_socket_handler()
            self._remove_socket_handler = None
        self._sockets.clear()
        await super().disconnect()

    async def request(self, data: dict | str | None = None) -> Any:
        """Read and write variables, answer like the scgi server.

        Raises:
            CybroConnectionTimeoutError: The controller doesn't answer.
            CybroError: A write is invalid, or the controller refused a request.
        """
        items = _parse_request(data)
        if not items:
            return None
        self._request_count += 1
        async with self._lock:
            if any(name.startswith(self._prefix) for name, _ in items):
                await self._check_program()
            writes = [self._write_item(name, text) for name, text in items if text]
            if writes:
                await self.client.write_random(writes)
            names = list(dict.fromkeys(name for name, _ in items))
            readable = [
                var for name in names if (var := self._readable(name)) is not None
            ]
            values = await self.client.read_random(readable) if readable else {}
        entries = [self._entry(name, values) for name in names]
        entries = [entry for entry in entries if entry is not None]
        if self._prefix + "sys.variables" in names:
            entries += self._variable_list()
        if not entries:
            return None
        return {"var": entries[0] if len(entries) == 1 else entries}

    async def _check_program(self) -> None:
        """Read head and status, reload the ALC after a program change."""
        try:
            head = await self.client.read_head()
            if not head.valid:
                self._set_program(STATUS_NO_PROGRAM, None)
                return
            status = await self.client.read_status()
        except CybroConnectionTimeoutError:
            self._plc_status = STATUS_OFFLINE
            raise
        self._program_timestamp = str(head.program_timestamp)
        if not status.ok:
            self._plc_status = STATUS_NO_PROGRAM
            return
        if head.timestamp != self._timestamp:
            try:
                text = await self.client.fetch_alc(head)
                alc = await asyncio.get_running_loop().run_in_executor(
                    None, parse_alc, text
                )
            except AbusResponseError:
                raise
            except AbusError:
                self._set_program(STATUS_NO_ALC, head.timestamp)
                return
            self._alc, self._alc_text, self._timestamp = alc, text, head.timestamp
        self._plc_status = STATUS_OK if self._alc is not None else STATUS_NO_ALC

    def _socket_received(self, frame: Frame, addr: Addr) -> None:
        """Decode a socket frame, update device.vars, call the callbacks."""
        definition = self._sockets.get(frame.msg_type)
        if frame.from_nad != self.nad or definition is None:
            return
        if self._alc is None:
            _LOGGER.debug(
                "Socket %s from c%s dropped: no allocation table yet",
                frame.msg_type,
                self.nad,
            )
            return
        try:
            values = decode_socket(definition, frame.body, self._alc)
        except AbusError as err:
            _LOGGER.debug("Socket from c%s dropped: %s", self.nad, err)
            return
        if not self.client.static_ip:
            self.client.ip = addr[0]
        if self._device is not None:
            for name, value in values.items():
                var = self._alc.get(name)
                full_name = self._prefix + name
                self._device.vars[full_name] = Var(
                    full_name, str(value), var.description or None if var else None
                )
        message = SocketMessage(self.nad, definition.socket_id, values, addr)
        for callback in list(self._socket_callbacks):
            try:
                callback(message)
            except Exception:
                _LOGGER.exception("Error in socket callback")

    def _set_program(self, status: str, timestamp: int | None) -> None:
        self._plc_status = status
        self._timestamp = timestamp
        self._alc = self._alc_text = None
        if timestamp is None:
            self._program_timestamp = "?"

    def _readable(self, name: str) -> AlcVar | None:
        """Return the ALC variable for a name, if it can be read now."""
        if self._plc_status != STATUS_OK or not name.startswith(self._prefix):
            return None
        var = self._alc.get(name.removeprefix(self._prefix))
        return var if var is not None and var.data_type is not None else None

    def _write_item(self, name: str, text: str) -> tuple[AlcVar, int | float]:
        """Return (variable, value) for a write, raise CybroError if invalid."""
        if name.startswith(self._prefix + "sys.") or name.startswith("sys."):
            raise CybroError(f"{name} is read-only")
        var = self._readable(name)
        if var is None:
            if name.startswith(self._prefix) and self._plc_status != STATUS_OK:
                raise CybroError(f"can't write {name}: plc status {self._plc_status}")
            raise CybroError(f"unknown variable {name}")
        try:
            return var, parse_value(text, var.data_type)
        except ValueError as err:
            raise CybroError(f"{name}: {err}") from err

    def _entry(
        self, name: str, values: dict[str, int | float]
    ) -> dict[str, Any] | None:
        """Return the response entry for a name (None for sys.variables)."""
        if name == self._prefix + "sys.variables":
            return None
        if name.startswith("sys."):
            value = self._server_values().get(name)
        elif name.startswith(self._prefix + "sys."):
            value = self._plc_values().get(name.removeprefix(self._prefix))
        else:
            var = self._readable(name)
            if var is not None:
                return {
                    "name": name,
                    "value": format_value(values[var.name], var.data_type),
                    "description": var.description or None,
                }
            value = None
        if value is None:
            return {"name": name, "value": "?", "description": None, "error_code": "2"}
        return {"name": name, "value": value, "description": None}

    def _variable_list(self) -> list[dict[str, Any]]:
        """Return the c<nad>.sys.variables entries (name, type, description)."""
        return [
            {
                "name": self._prefix + var.name,
                "type": f"datatype.{var.data_type.name.lower() if var.data_type else 'none'}",
                "description": var.description or None,
            }
            for var in (self._alc or {}).values()
        ]

    def _plc_values(self) -> dict[str, str]:
        client = self.client
        return {
            "sys.ip_port": f"{client.ip}:{client.port}",  # known after the check
            "sys.timestamp": self._program_timestamp,
            "sys.plc_status": self._plc_status,
            "sys.response_time": str(int(client.response_time * 1000)),
            "sys.bytes_transferred": str(client.bytes_transferred),
            "sys.com_error_count": str(client.com_error_count),
            "sys.alc_file": f"\n{self._alc_text}\n" if self._alc_text else "",
        }

    def _server_values(self) -> dict[str, Any]:
        uptime = int(time.monotonic() - self._started)
        days, rest = divmod(uptime, 86400)
        hours, rest = divmod(rest, 3600)
        minutes, seconds = divmod(rest, 60)
        return {
            "sys.server_uptime": f"{days} days, {hours:02}:{minutes:02}:{seconds:02}",
            "sys.scgi_request_count": str(self._request_count),
            "sys.push_port_status": "inactive",
            "sys.push_count": "0",
            "sys.push_ack_errors": "0",
            "sys.push_list_count": "0",
            "sys.cache_request": "0",
            "sys.cache_valid": "0",
            "sys.server_version": f"direct {__version__}",
            "sys.udp_rx_count": str(self.transport.rx_count),
            "sys.udp_tx_count": str(self.transport.tx_count),
            "sys.nad_list": {"item": str(self.nad)},
        }


def _parse_request(data: dict | str | None) -> list[tuple[str, str | None]]:
    """Return (name, value to write or None) like Cybro builds the query."""
    if not data:
        return []
    if isinstance(data, str):
        parts = (part.partition("=") for part in data.split("&") if part)
        return [(name, value or None) for name, _, value in parts]
    return [(name, None if value == "" else str(value)) for name, value in data.items()]
