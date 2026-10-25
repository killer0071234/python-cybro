"""Tests for the shared A-bus UDP transport (`cybro.abus.transport`)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from collections.abc import Callable

import pytest
import pytest_asyncio

from src.cybro.abus.frame import Command
from src.cybro.abus.frame import Direction
from src.cybro.abus.frame import Frame
from src.cybro.abus.frame import decode
from src.cybro.abus.frame import encode
from src.cybro.abus.transport import AbusTransport
from src.cybro.exceptions import CybroConnectionError
from src.cybro.exceptions import CybroConnectionTimeoutError

LOCAL = "127.0.0.1"
SOURCE = 1010

Addr = tuple[str, int]
Handler = Callable[[Frame, Addr], list[bytes]]


class Peer(asyncio.DatagramProtocol):
    """UDP peer on loopback that answers with whatever the handler returns."""

    def __init__(self, handler: Handler) -> None:
        """Initialize."""
        self.handler = handler
        self.received: list[Frame] = []
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:
        """Store the transport."""
        self.transport = transport

    def datagram_received(self, data: bytes, addr: Addr) -> None:
        """Answer a request."""
        frame = decode(data)
        self.received.append(frame)
        for raw in self.handler(frame, addr):
            self.transport.sendto(raw, addr)

    @property
    def addr(self) -> Addr:
        """Return the local address."""
        return self.transport.get_extra_info("sockname")[:2]

    def send(self, raw: bytes, addr: Addr) -> None:
        """Send raw bytes."""
        self.transport.sendto(raw, addr)


def ack(request: Frame, body: bytes = b"", **changes) -> bytes:
    """Return the encoded ACK for a request."""
    fields = {
        "from_nad": request.to_nad,
        "to_nad": request.from_nad,
        "txn": request.txn,
        "direction": Direction.ACK,
        "msg_type": 0,
        "body": body,
    } | changes
    return encode(Frame(**fields))


async def start_peer(handler: Handler) -> Peer:
    """Start a peer on a free loopback port."""
    loop = asyncio.get_running_loop()
    _, peer = await loop.create_datagram_endpoint(
        lambda: Peer(handler), local_addr=(LOCAL, 0)
    )
    return peer


@pytest_asyncio.fixture
async def transport() -> AsyncIterator[AbusTransport]:
    """Return a transport on a free loopback port with short timeouts."""
    abus = await AbusTransport.create(
        local_addr=(LOCAL, 0), timeout=0.05, attempts=3, broadcast=LOCAL
    )
    yield abus
    abus.close()


def ping(nad: int, txn: int) -> Frame:
    """Return a PING request from the source NAD."""
    return Frame.request(SOURCE, nad, txn, Command.PING)


@pytest.mark.asyncio
async def test_exchange_matches_response(transport: AbusTransport) -> None:
    """The response with the same NAD pair and txn resolves the request."""
    peer = await start_peer(lambda f, _: [ack(f, b"\x01\x02")])
    frame, addr = await transport.exchange(ping(10000, 7), peer.addr)
    assert frame == Frame(10000, SOURCE, 7, Direction.ACK, 0, b"\x01\x02")
    assert addr == peer.addr
    assert len(peer.received) == 1
    assert (transport.tx_count, transport.rx_count) == (1, 1)


@pytest.mark.asyncio
async def test_exchange_returns_negative_answers(transport: AbusTransport) -> None:
    """A refused request is a response too; the caller checks the direction."""
    peer = await start_peer(lambda f, _: [ack(f, direction=Direction.DENIED)])
    frame, _ = await transport.exchange(ping(10000, 7), peer.addr)
    assert frame.direction == Direction.DENIED


@pytest.mark.asyncio
async def test_foreign_frames_are_ignored(transport: AbusTransport) -> None:
    """Frames for another NAD pair or txn, requests and garbage don't match."""

    def handler(f: Frame, _) -> list[bytes]:
        return [
            b"garbage",
            ack(f, txn=f.txn + 1),
            ack(f, from_nad=8372),
            ack(f, to_nad=1003),
            ack(f, direction=Direction.REQ),
            ack(f, body=b"ok"),
        ]

    peer = await start_peer(handler)
    unsolicited: list[Frame] = []
    transport.on_unsolicited(lambda frame, _: unsolicited.append(frame))
    frame, _ = await transport.exchange(ping(10000, 7), peer.addr)
    assert frame.body == b"ok"
    await asyncio.sleep(0.01)
    assert len(unsolicited) == 4  # garbage is dropped


@pytest.mark.asyncio
async def test_timeout_and_retry(transport: AbusTransport) -> None:
    """A lost answer is retried; the request is sent again unchanged."""
    peer = await start_peer(lambda f, _: [] if len(peer.received) < 3 else [ack(f)])
    frame, _ = await transport.exchange(ping(10000, 7), peer.addr)
    assert frame.direction == Direction.ACK
    assert peer.received == [ping(10000, 7)] * 3


@pytest.mark.asyncio
async def test_timeout_after_all_attempts(transport: AbusTransport) -> None:
    """No answer after all attempts raises CybroConnectionTimeoutError."""
    peer = await start_peer(lambda f, _: [])
    with pytest.raises(CybroConnectionTimeoutError, match="c10000"):
        await transport.exchange(ping(10000, 7), peer.addr)
    assert len(peer.received) == 3
    # the next exchange with the same key works
    peer.handler = lambda f, _: [ack(f)]
    await transport.exchange(ping(10000, 7), peer.addr)


@pytest.mark.asyncio
async def test_two_controllers_in_parallel(transport: AbusTransport) -> None:
    """Requests to different controllers run at the same time."""

    def slow(f: Frame, _) -> list[bytes]:
        asyncio.get_running_loop().call_later(
            0.03, peer1.send, ack(f, b"slow"), transport.local_addr
        )
        return []

    peer1 = await start_peer(slow)
    peer2 = await start_peer(lambda f, _: [ack(f, b"fast")])
    order: list[bytes] = []

    async def run(nad: int, peer: Peer) -> None:
        frame, _ = await transport.exchange(ping(nad, 1), peer.addr)
        order.append(frame.body)

    await asyncio.gather(run(10000, peer1), run(8352, peer2))
    assert order == [b"fast", b"slow"]


@pytest.mark.asyncio
async def test_same_key_is_rejected(transport: AbusTransport) -> None:
    """Two requests with the same NAD pair and txn can't be told apart."""
    peer = await start_peer(lambda f, _: [])
    task = asyncio.create_task(transport.exchange(ping(10000, 7), peer.addr))
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="pending"):
        await transport.exchange(ping(10000, 7), peer.addr)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not transport._pending  # noqa: SLF001


@pytest.mark.asyncio
async def test_default_address_is_broadcast(transport: AbusTransport) -> None:
    """Without an address the request goes to the broadcast address."""
    peer = await start_peer(lambda f, _: [ack(f)])
    transport.port = peer.addr[1]
    _, addr = await transport.exchange(ping(10000, 7))
    assert addr == peer.addr


@pytest.mark.asyncio
async def test_collect_returns_all_answers(transport: AbusTransport) -> None:
    """A request to NAD 0 collects the answers of every controller."""

    def handler(f: Frame, _) -> list[bytes]:
        return [ack(f, from_nad=nad) for nad in (10000, 8352)] + [
            ack(f, txn=99, from_nad=1)
        ]

    peer = await start_peer(handler)
    answers = await transport.collect(ping(0, 5), peer.addr, window=0.03)
    assert sorted(frame.from_nad for frame, _ in answers) == [8352, 10000]
    assert not transport._collectors  # noqa: SLF001


@pytest.mark.asyncio
async def test_unsolicited_callback(transport: AbusTransport) -> None:
    """Socket frames go to the callback until it is removed."""
    peer = await start_peer(lambda f, _: [])
    seen: list[tuple[Frame, Addr]] = []
    remove = transport.on_unsolicited(lambda frame, addr: seen.append((frame, addr)))
    socket = encode(Frame(8372, 0, 0, Direction.ACK, 23, b"\x01"))
    peer.send(socket, transport.local_addr)
    await asyncio.sleep(0.01)
    assert seen == [(decode(socket), peer.addr)]
    remove()
    peer.send(socket, transport.local_addr)
    await asyncio.sleep(0.01)
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_failing_callback_does_not_break_transport(
    transport: AbusTransport,
) -> None:
    """An exception in a callback is logged, other callbacks still run."""
    peer = await start_peer(lambda f, _: [])
    seen: list[Frame] = []

    def broken(frame: Frame, addr: Addr) -> None:
        raise ValueError("boom")

    transport.on_unsolicited(broken)
    transport.on_unsolicited(lambda frame, _: seen.append(frame))
    peer.send(encode(Frame(8372, 0, 0, Direction.ACK, 3, b"")), transport.local_addr)
    await asyncio.sleep(0.01)
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_close_fails_pending_requests() -> None:
    """Closing the transport ends waiting requests with an error."""
    abus = await AbusTransport.create(local_addr=(LOCAL, 0), timeout=1)
    peer = await start_peer(lambda f, _: [])
    task = asyncio.create_task(abus.exchange(ping(10000, 7), peer.addr))
    await asyncio.sleep(0.01)
    abus.close()
    with pytest.raises(CybroConnectionError, match="closed"):
        await task
    with pytest.raises(CybroConnectionError, match="closed"):
        await abus.exchange(ping(10000, 8), peer.addr)
    abus.close()  # twice is fine


@pytest.mark.asyncio
async def test_txn_counter(transport: AbusTransport) -> None:
    """Transaction ids count 1..0xFFFF and wrap around."""
    assert [transport.next_txn() for _ in range(3)] == [1, 2, 3]
    transport._txn = 0xFFFE  # noqa: SLF001
    assert [transport.next_txn() for _ in range(3)] == [0xFFFF, 1, 2]


@pytest.mark.asyncio
async def test_socket_error_is_logged(
    transport: AbusTransport, caplog: pytest.LogCaptureFixture
) -> None:
    """ICMP errors (e.g. port unreachable) don't break the transport."""
    with caplog.at_level(logging.DEBUG, logger="src.cybro.abus.transport"):
        transport.error_received(OSError("unreachable"))
    assert "unreachable" in caplog.text


@pytest.mark.asyncio
async def test_port_in_use(transport: AbusTransport) -> None:
    """Binding a port that is in use raises OSError (caller may use port 0)."""
    with pytest.raises(OSError):
        await AbusTransport.create(local_addr=transport.local_addr)
