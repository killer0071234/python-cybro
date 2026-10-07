"""Example: read and write variables of a Cybro PLC through a scgi server."""  # fmt: skip
import argparse
import asyncio

from cybro import Cybro
from cybro import CybroError

# Status variables available on every Cybro PLC, with the converter to use.
STATUS_VARS = {
    "scan_time": "int",
    "scan_time_max": "int",
    "scan_overrun": "bool",
    "retentive_fail": "bool",
    "general_error": "bool",
    "sys.response_time": "int",
    "sys.bytes_transferred": "int",
    "sys.com_error_count": "int",
}


async def main(host: str, port: int, nad: int, write: str | None) -> None:
    """Show server and PLC information, then read (and optionally write) variables.

    Args:
        host: scgi server host or IP address
        port: scgi server port
        nad: network address (NAD) of the PLC
        write: optional "NAME=VALUE" to write to the PLC

    Raises:
        SystemExit: The PLC is not known to the scgi server.
    """
    prefix = f"c{nad}."
    cybro = Cybro(host, port=port, nad=nad)
    try:
        # The first update reads server and PLC information.
        device = await cybro.update()
        print("Server version:", device.server_info.server_version)
        print("Server uptime: ", device.server_info.server_uptime)
        print("Controllers:   ", device.server_info.nad_list)

        nad_list = device.server_info.nad_list or []
        if isinstance(nad_list, str):  # a single controller is returned as a string
            nad_list = [nad_list]
        if f"c{nad}" not in nad_list:
            raise SystemExit(f"error: PLC with NAD {nad} is not known to the server")

        print("PLC address:   ", device.plc_info.ip_port)
        print("PLC status:    ", device.plc_info.plc_status)
        print("PLC program:   ", device.plc_info.alc_file)

        # Register variables, then refresh them with a second update.
        for name in STATUS_VARS:
            cybro.add_var(prefix + name)
        device = await cybro.update()

        print()
        for name, kind in STATUS_VARS.items():
            var = device.vars.get(prefix + name)
            if var is None:
                print(f"{name}: not available on this PLC")
                continue
            # Values are returned as strings; convert them with the Var helpers.
            value = var.value_int() if kind == "int" else var.value_bool()
            print(f"{name}: {value}")

        if write:
            name, value = write.split("=", 1)
            await cybro.write_var(prefix + name, value)
            print(f"\n{name} after write:", await cybro.read_var(prefix + name))
    finally:
        await cybro.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="scgi server host or IP address")
    parser.add_argument("nad", type=int, help="network address (NAD) of the PLC")
    parser.add_argument("--port", type=int, default=4000, help="scgi server port")
    parser.add_argument(
        "--write", metavar="NAME=VALUE", help="write a PLC variable, e.g. lc00_qx00=1"
    )
    args = parser.parse_args()

    if not args.host.strip():
        parser.error("host must not be empty")
    if args.nad <= 0:
        parser.error("nad must be a positive number")
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if args.write is not None:
        var_name, sep, _ = args.write.partition("=")
        if not sep or not var_name.strip():
            parser.error("--write must have the form NAME=VALUE, e.g. lc00_qx00=1")
        if var_name.startswith(f"c{args.nad}."):
            parser.error(f"--write NAME must not include the 'c{args.nad}.' prefix")

    try:
        asyncio.run(main(args.host, args.port, args.nad, args.write))
    except CybroError as err:
        raise SystemExit(f"error: {err}") from err
