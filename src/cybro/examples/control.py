# pylint: disable=W0621
"""Asynchronous Python client for Cybro."""

import asyncio
from src.cybro.models import VarType

from src.cybro.cybro import Cybro


async def main():
    """Show example on controlling a Cybro PLC."""
    async with Cybro("192.168.1.60") as cybro:
        device = await cybro.update()
        print(device.server_info.server_version)

        device.add_var("c12762.lc00_qx00", VarType.BOOL)
        device.add_var("c12762.lc00_qx01", VarType.BOOL)
        device.add_var("c12762.lc00_qx02", VarType.BOOL)
        device.add_var("c12762.lc00_qx03", VarType.BOOL)
        device.add_var("c12762.lc00_qx04", VarType.BOOL)
        device.add_var("c12762.lc00_qx05", VarType.BOOL)
        device.add_var("c12762.lc00_qx06", VarType.BOOL)
        device.add_var("c12762.lc00_qx07", VarType.BOOL)
        device.add_var("c12762.lc00_qx08", VarType.BOOL)
        device.add_var("c12762.lc00_qx09", VarType.BOOL)
        device.add_var("c12762.scan_time", VarType.INT)
        device.add_var("c12762.sys.ip_port")
        device.add_var("c12762.sys.timestamp")
        device.add_var("c12762.sys.plc_program_status")
        device.add_var("c12762.sys.response_time", VarType.INT)
        device.add_var("c12762.sys.bytes_transferred", VarType.INT)
        device.add_var("c12762.sys.comm_error_count", VarType.INT)
        await cybro.update()
        for var in device.user_vars:
            print(var + " -> " + device.vars[var].value)

        await cybro.write_var("c12762.lc00_qx00", "0")
        await cybro.update()
        # await cybro.write_var("c12762.cybro_qx05", "1")

        # print(await cybro.read_var("c12762.cybro_qx04"))
        # print(await cybro.read_var("c12762.cybro_qx05"))
        # print(await cybro.read_var("c12762.cybro_qx06"))
        # print(await cybro.read_var("sys.abus_list"))

        # await cybro.update()
        # if isinstance(device.state.preset, Preset):
        #    print(f"Preset active! Name: {device.state.preset.name}")

        # if isinstance(device.state.playlist, Playlist):
        #    print(f"Playlist active! Name: {device.state.playlist.name}")

        ## Turn strip on, full brightness
        # await led.master(on=True, brightness=255)
        # close connection and release resources
        await cybro.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
