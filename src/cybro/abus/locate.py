"""Find controllers on the LAN by broadcast.

Results of the hardware spike (docs/abus-notes.md, S2): a broadcast PING
or READ_STATUS to NAD 0 is answered by every controller with its own NAD.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..exceptions import AbusError
from .client import SOURCE_NAD
from .client import AbusClient
from .client import Status
from .frame import Command
from .frame import Direction
from .frame import Frame
from .transport import AbusTransport

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanResult:
    """A controller that answered the scan."""

    nad: int
    ip: str
    status: Status


async def locate(
    transport: AbusTransport, nad: int, password: int | None = None
) -> str:
    """Return the IP of a controller (broadcast PING to its NAD)."""
    client = AbusClient(transport, nad, password=password)
    await client.ping()
    return client.ip


async def scan(
    transport: AbusTransport, window: float = 1.0, source_nad: int = SOURCE_NAD
) -> list[ScanResult]:
    """Broadcast READ_STATUS to NAD 0 and return every controller that answers."""
    request = Frame.request(source_nad, 0, transport.next_txn(), Command.READ_STATUS)
    found: dict[int, ScanResult] = {}
    for frame, (ip, _) in await transport.collect(request, window=window):
        if frame.direction != Direction.ACK:
            continue
        try:
            found[frame.from_nad] = ScanResult(
                frame.from_nad, ip, Status.from_bytes(frame.body)
            )
        except AbusError as err:
            _LOGGER.debug("Scan answer from c%s ignored: %s", frame.from_nad, err)
    return sorted(found.values(), key=lambda result: result.nad)
