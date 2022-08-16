# pylint: disable=W0621
"""Asynchronous Python client for Cybro."""
import asyncio

from src.cybro.cybro import Cybro
from src.cybro.models import VarType


async def main():
    """Show example on controlling a Cybro PLC."""
    nad = 10000
    prefix = f"c{nad}."
    async with Cybro("192.168.10.62", 4000, nad) as cybro:
        device = await cybro.update()
        print("server_version -> " + device.server_info.server_version)
        print("ip_port -> " + device.plc_info.ip_port)
        print("timestamp -> " + device.plc_info.timestamp)
        print("plc_program_status -> " + device.plc_info.plc_program_status)
        print("response_time -> " + device.plc_info.response_time)
        print("bytes_transferred -> " + device.plc_info.bytes_transferred)
        print("comm_error_count -> " + device.plc_info.comm_error_count)
        # print("alc_file -> " + device.plc_info.alc_file)
        # print(device.plc_info.plc_vars["c12762.lc00_qx00"])

        device.add_var(f"{prefix}scan_overrun", VarType.BOOL)
        device.add_var(f"{prefix}retentive_fail", VarType.BOOL)
        device.add_var(f"{prefix}general_error", VarType.BOOL)
        device.add_var(f"{prefix}lc00_general_error", VarType.BOOL)
        device.add_var(f"{prefix}lc01_general_error", VarType.BOOL)
        device.add_var(f"{prefix}lc02_general_error", VarType.BOOL)
        device.add_var(f"{prefix}ld00_general_error", VarType.BOOL)
        device.add_var(f"{prefix}ld01_general_error", VarType.BOOL)
        device.add_var(f"{prefix}ld02_general_error", VarType.BOOL)
        device.add_var(f"{prefix}ld03_general_error", VarType.BOOL)
        device.add_var(f"{prefix}lc00_qx00", VarType.BOOL)
        device.add_var(f"{prefix}lc00_qx01", VarType.BOOL)
        device.add_var(f"{prefix}lc00_qx02", VarType.BOOL)
        device.add_var(f"{prefix}lc00_qx03", VarType.BOOL)
        device.add_var(f"{prefix}lc00_qx04", VarType.BOOL)
        device.add_var(f"{prefix}lc00_qx05", VarType.BOOL)
        device.add_var(f"{prefix}lc00_qx06", VarType.BOOL)
        device.add_var(f"{prefix}lc00_qx07", VarType.BOOL)
        device.add_var(f"{prefix}lc00_qx08", VarType.BOOL)
        device.add_var(f"{prefix}lc00_qx09", VarType.BOOL)
        device.add_var(f"{prefix}ld00_qx00", VarType.BOOL)
        device.add_var(f"{prefix}ld00_qx01", VarType.BOOL)
        device.add_var(f"{prefix}ld00_qx02", VarType.BOOL)
        device.add_var(f"{prefix}ld00_qx03", VarType.BOOL)
        device.add_var(f"{prefix}ld00_qw00", VarType.INT)
        device.add_var(f"{prefix}ld00_qw01", VarType.INT)
        device.add_var(f"{prefix}ld00_qw02", VarType.INT)
        device.add_var(f"{prefix}ld00_qw03", VarType.INT)
        device.add_var(f"{prefix}scan_time", VarType.INT)
        device.add_var(f"{prefix}scan_time_max", VarType.INT)
        device.add_var(f"{prefix}sys.ip_port")
        device.add_var(f"{prefix}sys.timestamp")
        device.add_var(f"{prefix}sys.plc_program_status")
        device.add_var(f"{prefix}sys.response_time", VarType.INT)
        device.add_var(f"{prefix}sys.bytes_transferred", VarType.INT)
        device.add_var(f"{prefix}sys.comm_error_count", VarType.INT)
        await cybro.update()
        for var in device.user_vars:
            print(var + " -> " + device.vars[var].value)

        # print(await cybro.write_var("c12762.lc00_qx00", "0"))
        # await cybro.update()
        # await cybro.write_var("c12762.cybro_qx05", "1")

        # print(await cybro.read_var("c12762.cybro_qx04"))
        # print(await cybro.read_var("c12762.cybro_qx05"))
        # print(await cybro.read_var("c12762.cybro_qx06"))
        # print(await cybro.read_var("sys.abus_list"))

        # await cybro.update()
        # if isinstance(device.state.preset, Preset):
        #    print(f"Preset active! Name: {device.state.preset.name}")

        await cybro.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
