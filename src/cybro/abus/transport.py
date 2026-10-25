"""Shared UDP endpoint for A-bus request/response exchanges.

One socket serves every controller. Responses are matched to requests on
(NAD pair, transaction id), like the Edge Toolkit's `abus_exchanger.py`.
Frames that match no request go to the unsolicited callbacks (push,
socket and broadcast frames, requests of other A-bus masters).
"""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Callable

from ..exceptions import AbusError
from ..exceptions import CybroConnectionError
from ..exceptions import CybroConnectionTimeoutError
from .frame import ABUS_PORT
from .frame import Direction
from .frame import Frame
from .frame import decode
from .frame import encode

_LOGGER = logging.getLogger(__name__)

Addr = tuple[str, int]
Key = tuple[int, int, int]
UnsolicitedCallback = Callable[[Frame, Addr], None]

DEFAULT_TIMEOUT = 0.2
DEFAULT_ATTEMPTS = 3


def _response_key(frame: Frame) -> Key:
    return frame.from_nad, frame.to_nad, frame.txn


def _request_key(frame: Frame) -> Key:
    return frame.to_nad, frame.from_nad, frame.txn


class AbusTransport(asyncio.DatagramProtocol):
    """UDP socket that exchanges A-bus frames with controllers."""

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        attempts: int = DEFAULT_ATTEMPTS,
        broadcast: str = "255.255.255.255",
        port: int = ABUS_PORT,
    ) -> None:
        """Initialize, use `create()` to open the socket."""
        self.timeout = timeout
        self.attempts = attempts
        self.broadcast = broadcast
        self.port = port
        self._transport: asyncio.DatagramTransport | None = None
        self._closed = False
        self._pending: dict[Key, asyncio.Future] = {}
        self._collectors: list[tuple[Key, list[tuple[Frame, Addr]]]] = []
        self._callbacks: list[UnsolicitedCallback] = []
        self._txn = 0
        self.rx_count = 0
        self.tx_count = 0

    @classmethod
    async def create(
        cls,
        local_addr: Addr = ("0.0.0.0", ABUS_PORT),  # noqa: S104
        **kwargs,
    ) -> AbusTransport:
        """Open a broadcast-enabled socket on local_addr (OSError if busy).

        Bind port 0 to get a free port: requests work, but frames that
        controllers send to port 8442 on their own are not received.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(local_addr)
        except OSError:
            sock.close()
            raise
        loop = asyncio.get_running_loop()
        _, protocol = await loop.create_datagram_endpoint(
            lambda: cls(**kwargs), sock=sock
        )
        return protocol

    @property
    def local_addr(self) -> Addr:
        """Return the bound address."""
        return self._transport.get_extra_info("sockname")[:2]

    def next_txn(self) -> int:
        """Return the next transaction id (1..0xFFFF)."""
        self._txn = self._txn % 0xFFFF + 1
        return self._txn

    def on_unsolicited(self, callback: UnsolicitedCallback) -> Callable[[], None]:
        """Call callback(frame, addr) for frames that match no request.

        Returns a function that removes the callback again.
        """
        self._callbacks.append(callback)
        return lambda: self._callbacks.remove(callback)

    async def exchange(
        self, request: Frame, addr: Addr | None = None
    ) -> tuple[Frame, Addr]:
        """Send a request and return (response, source address).

        Without addr the request is broadcast. The request is sent again
        after each timeout. Raises CybroConnectionTimeoutError when all
        attempts are used up. Negative answers (DENIED, ...) are returned.
        """
        self._check_open()
        key = _request_key(request)
        if key in self._pending:
            raise RuntimeError(f"request {key} is already pending")
        target = addr or (self.broadcast, self.port)
        raw = encode(request)
        future = asyncio.get_running_loop().create_future()
        self._pending[key] = future
        try:
            for _ in range(self.attempts):
                self._send(raw, target)
                try:
                    return await asyncio.wait_for(asyncio.shield(future), self.timeout)
                except TimeoutError:
                    continue
        finally:
            del self._pending[key]
            future.cancel()
        raise CybroConnectionTimeoutError(
            f"no answer from c{request.to_nad} at {target[0]}:{target[1]}"
        )

    async def collect(
        self, request: Frame, addr: Addr | None = None, window: float = 1.0
    ) -> list[tuple[Frame, Addr]]:
        """Send a request once and return every answer within the window.

        Answers match on the source NAD and txn only, so a request to NAD 0
        returns one answer per controller.
        """
        self._check_open()
        answers: list[tuple[Frame, Addr]] = []
        collector = ((0, request.from_nad, request.txn), answers)
        self._collectors.append(collector)
        try:
            self._send(encode(request), addr or (self.broadcast, self.port))
            await asyncio.sleep(window)
        finally:
            self._collectors.remove(collector)
        return answers

    def close(self) -> None:
        """Close the socket and fail waiting requests."""
        if self._closed:
            return
        self._closed = True
        for future in self._pending.values():
            if not future.done():
                future.set_exception(CybroConnectionError("transport closed"))
        if self._transport is not None:
            self._transport.close()

    def _send(self, raw: bytes, addr: Addr) -> None:
        self.tx_count += 1
        self._transport.sendto(raw, addr)

    def _check_open(self) -> None:
        if self._closed:
            raise CybroConnectionError("transport closed")

    # asyncio.DatagramProtocol

    def connection_made(self, transport) -> None:
        """Store the transport."""
        self._transport = transport

    def connection_lost(self, exc: Exception | None) -> None:
        """Fail waiting requests when the socket goes away."""
        self.close()

    def error_received(self, exc: Exception) -> None:
        """Log socket errors (e.g. ICMP port unreachable)."""
        _LOGGER.debug("A-bus socket error: %s", exc)

    def datagram_received(self, data: bytes, addr: Addr) -> None:
        """Hand a received frame to its request, collector or the callbacks."""
        self.rx_count += 1
        try:
            frame = decode(data)
        except AbusError as err:
            _LOGGER.debug("Dropped frame from %s: %s (%s)", addr, err, data.hex())
            return
        if frame.direction != Direction.REQ:
            future = self._pending.get(_response_key(frame))
            if future is not None and not future.done():
                future.set_result((frame, addr[:2]))
                return
            for (_, to_nad, txn), answers in self._collectors:
                if frame.to_nad == to_nad and frame.txn == txn:
                    answers.append((frame, addr[:2]))
                    return
        for callback in list(self._callbacks):
            try:
                callback(frame, addr[:2])
            except Exception:
                _LOGGER.exception("Error in A-bus unsolicited callback")
