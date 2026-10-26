"""Example: show socket values of a Cybro PLC live, without a scgi server.

Binds UDP 8442 (stop a scgi server on this host first) and prints every
socket frame of the given definitions. Sockets can be missed, so the
variables are also polled every --poll seconds.

    python examples/sockets.py --nad 10000 \
        --socket "63; test_sock_bool; test_sock_int; test_sock_long, test_sock_real"
"""

import argparse
import asyncio
import contextlib
from datetime import datetime

from cybro import CybroDirect
from cybro.abus import AbusTransport


async def main(
    nad: int, ip: str | None, sockets: list[str], seconds: float, poll: float
) -> None:
    """Print socket frames as they arrive and poll the same variables.

    Args:
        nad: network address (NAD) of the PLC
        ip: static IP of the PLC (default: found by broadcast)
        sockets: socket definitions ("id; bits; ints; longs")
        seconds: how long to run
        poll: seconds between polls (0: don't poll)
    """
    transport = await AbusTransport.create()  # sockets arrive on UDP 8442
    try:
        cybro = CybroDirect(transport, nad, ip=ip)
        await cybro.update()  # loads the ALC; sockets need it
        names = []
        for text in sockets:
            definition = cybro.add_socket(text)
            slot_names = [name for name, _ in definition.slots()]
            names += [f"c{nad}.{name}" for name in slot_names]
            print(f"socket {definition.socket_id}: {', '.join(slot_names)}")
        for name in names:
            cybro.add_var(name)

        def show(message) -> None:
            stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            values = ", ".join(f"{k}={v}" for k, v in message.values.items())
            print(
                f"{stamp} socket {message.socket_id} from {message.addr[0]}: {values}"
            )

        cybro.on_socket(show)
        print(f"Listening for {seconds} s (Ctrl+C to stop)")
        end = asyncio.get_running_loop().time() + seconds
        while (left := end - asyncio.get_running_loop().time()) > 0:
            await asyncio.sleep(min(poll, left) if poll else left)
            if poll and left > poll:
                device = await cybro.update()
                stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                values = ", ".join(
                    f"{name.split('.', 1)[1]}={device.vars[name].value}"
                    for name in names
                )
                print(f"{stamp} poll: {values}")
        await cybro.disconnect()
    finally:
        transport.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--nad", type=int, required=True, help="PLC network address")
    parser.add_argument("--ip", help="PLC IP (default: found by broadcast)")
    parser.add_argument(
        "--socket",
        action="append",
        required=True,
        help='socket definition "id; bits; ints; longs" (repeatable)',
    )
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--poll", type=float, default=10, help="0: sockets only")
    args = parser.parse_args()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main(args.nad, args.ip, args.socket, args.seconds, args.poll))
