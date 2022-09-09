"""Asynchronous Python client for Cybro scgi server."""
from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import dataclass
from itertools import islice
from typing import Any

import aiohttp
import async_timeout
import backoff
import xmltodict
from cachetools import TTLCache
from yarl import URL

from .exceptions import CybroConnectionError
from .exceptions import CybroConnectionTimeoutError
from .exceptions import CybroEmptyResponseError
from .exceptions import CybroError
from .models import Device
from .models import VarType

VERSION_CACHE: TTLCache = TTLCache(maxsize=16, ttl=7200)

VAR_CHUNK_SIZE: int = 25


@dataclass
class Cybro:
    """Main class for handling connections with Cybro scgi server."""

    host: str
    port: int = 4000
    nad: int = 0
    path: str = ""
    request_timeout: float = 8.0
    session: aiohttp.client.ClientSession | None = None

    _device: Device | None = None

    def __init__(
        self,
        host_str: str,
        port: int = 4000,
        nad: int = 0,
        session: aiohttp.client.ClientSession | None = None,
    ) -> None:
        """Define a new Cybro scgi server session.

        Args:
            host_str: Cybro scgi server connection string
            port: Cybro scgi server port (Default: 4000)
            nad: Cybro PLC NAD (Network address)
            session: optional a aiohttp session
        """
        new_host = host_str
        new_path = ""
        if new_host.find("//") >= 0:
            new_host = new_host.split("//")[1]
        if new_host.find("/") >= 0:
            new_path = "/" + "/".join(new_host.split("/")[1:])
            new_host = new_host.split("/")[0]
        url = URL.build(scheme="http", host=new_host, path=new_path)
        self.host = url.host
        self.path = url.path
        self.port = port
        self.nad = nad
        if session is not None:
            self.session = session

    async def disconnect(self) -> None:
        """Disconnect from cybro scgi server object."""
        if self.session is not None:
            await self.session.close()
            self.session = None

    @backoff.on_exception(
        backoff.expo,
        {CybroConnectionError, CybroConnectionTimeoutError, CybroError},
        max_tries=3,
        logger=None,
    )
    async def request(
        self,
        data: dict | str | None = None,
    ) -> Any:
        """Handle a request to a scgi server.

        A generic method for sending/handling HTTP requests done gainst
        the scgi server.

        Args:
            data: string / Dictionary of data to send to the scgi server.

        Returns:
            A Python dictionary with the response from the scgi server.

        Raises:
            CybroConnectionError: An error occurred while communitcation with
                the scgi server.
            CybroConnectionTimeoutError: A timeout occurred while communicating
                with the scgi server.
            CybroError: Received an unexpected response from Cybro scgi server.
        """
        if isinstance(data, str):
            url = URL.build(
                scheme="http",
                host=self.host,
                port=self.port,
                path=self.path,
                query_string=data,
            )
        else:
            url = URL.build(
                scheme="http",
                host=self.host,
                port=self.port,
                path=self.path,
                query=data,
            )

        # some fix of query data
        url_fixed = str(url).replace("=&", "&").removesuffix("=")
        url = url_fixed

        headers = {
            "Accept": "text/plain, */*",
        }

        if self.session is None:
            self.session = aiohttp.client.ClientSession()

        try:
            async with async_timeout.timeout(self.request_timeout):
                response = await self.session.get(
                    url=url_fixed,
                    allow_redirects=False,
                    ssl=False,
                    headers=headers,
                )

            content_type = response.headers.get("Content-Type", "")

            if response.status // 100 in [4, 5]:
                contents = await response.read()
                response.close()

                if content_type == "application/json":
                    raise CybroError(
                        response.status, json.loads(contents.decode("utf8"))
                    )
                raise CybroError(response.status, {"message": contents.decode("utf8")})

            response_data = xmltodict.parse(await response.text())

        except asyncio.TimeoutError as exception:
            raise CybroConnectionTimeoutError(
                f"Timeout occurred while connecting to server at {self.host}:{self.port}"
            ) from exception
        except (aiohttp.ClientError, socket.gaierror) as exception:
            print(exception)
            raise CybroConnectionError(
                f"Error occurred while communicating with server at {self.host}:{self.port}"
            ) from exception

        return response_data.get("data")

    @backoff.on_exception(
        backoff.expo, CybroEmptyResponseError, max_tries=3, logger=None
    )
    async def update(
        self, full_update: bool = False, plc_nad: int = 0, device_type: int = 0
    ) -> Device:
        """Get all variables in a single call.

        This method updates all variable information with a single call.

        Args:
            full_update: Force a full update from the device Device.
            plc_nad: Address of PLC to read
            device_type: 0 = undefined / generic PLC, 1 = HIQ-controller

        Returns:
            Cybro Device data.

        Raises:
            CybroEmptyResponseError: The Cybro scgi server returned an empty response.
        """
        if self._device is None or full_update:
            # read all relevant server vars
            _vars: dict[str, str] = {
                "sys.scgi_port_status": "",
                "sys.server_uptime": "",
                "sys.scgi_request_pending": "",
                "sys.scgi_request_count": "",
                "sys.push_port_status": "",
                "sys.push_count": "",
                "sys.push_ack_errors": "",
                "sys.push_list_count": "",
                "sys.cache_request": "",
                "sys.cache_valid": "",
                "sys.server_version": "",
                "sys.udp_rx_count": "",
                "sys.udp_tx_count": "",
                "sys.datalogger_status": "",
            }
            if plc_nad != 0 and self.nad == 0:
                self.nad = plc_nad
            if self.nad != 0:
                # read also specific plc variables
                _controller = "c" + str(self.nad) + "."
                _vars[_controller + "sys.ip_port"] = ""
                _vars[_controller + "sys.timestamp"] = ""
                _vars[_controller + "sys.plc_program_status"] = ""
                _vars[_controller + "sys.response_time"] = ""
                _vars[_controller + "sys.bytes_transferred"] = ""
                _vars[_controller + "sys.comm_error_count"] = ""
                _vars[_controller + "sys.alc_file"] = ""
                # read / prepare specific vars for HIQ-controller
                if device_type == 1:
                    _vars = _add_hiq_tags(_vars, _controller)

            if not (data := await self.request(data=_vars)):
                raise CybroEmptyResponseError(
                    f"Cybro scgi server at {self.host}:{self.port} returned an empty API"
                    " response on full update"
                )

            self._device = Device(data, plc_nad=self.nad)

            if len(self._device.user_vars) > 0:
                _user_vars = _get_chunk(self._device.user_vars, VAR_CHUNK_SIZE)
                for _vars in _user_vars:
                    if not (data := await self.request(data=_vars)):
                        raise CybroEmptyResponseError(
                            f"Cybro scgi server at {self.host}:{self.port} returned an empty"
                            " response on full update"
                        )
                    self._device.update_user_var_from_dict(data=data)

            return self._device

        if len(self._device.user_vars) > 0:
            _user_vars = _get_chunk(self._device.user_vars, VAR_CHUNK_SIZE)
            for _vars in _user_vars:
                if not (data := await self.request(data=_vars)):
                    raise CybroEmptyResponseError(
                        f"Cybro scgi server at {self.host}:{self.port} returned an empty"
                        " response on user update"
                    )
                self._device.update_user_var_from_dict(data=data)

            return self._device

        return self._device

    @backoff.on_exception(
        backoff.expo, CybroEmptyResponseError, max_tries=3, logger=None
    )
    async def write_var(
        self, name: str, value: str, var_type: VarType = VarType.STR
    ) -> str | int | float | bool:
        """Write a single variable to scgi server."""
        data = await self.request(data={name: value})
        return self._device.update_var(data, var_type=var_type)

    @backoff.on_exception(
        backoff.expo, CybroEmptyResponseError, max_tries=3, logger=None
    )
    async def read_var(
        self, name: str, var_type: VarType = VarType.STR
    ) -> str | int | float | bool:
        """Read a single variable from scgi server."""
        if not (data := await self.request(data=name)):
            raise CybroEmptyResponseError(
                f"Cybro scgi server at {self.host}:{self.port} returned an empty"
                " response on read of {name}"
            )
        return self._device.update_var(data, var_type=var_type)

    @backoff.on_exception(
        backoff.expo, CybroEmptyResponseError, max_tries=3, logger=None
    )
    async def read_var_int(
        self,
        name: str,
    ) -> int:
        """Read a single variable from scgi server as int."""
        return await self.read_var(name, VarType.INT)

    @backoff.on_exception(
        backoff.expo, CybroEmptyResponseError, max_tries=3, logger=None
    )
    async def read_var_float(
        self,
        name: str,
    ) -> float:
        """Read a single variable from scgi server as float."""
        return await self.read_var(name, VarType.FLOAT)

    @backoff.on_exception(
        backoff.expo, CybroEmptyResponseError, max_tries=3, logger=None
    )
    async def read_var_bool(
        self,
        name: str,
    ) -> bool:
        """Read a single variable from scgi server as float."""
        return await self.read_var(name, VarType.BOOL)

    def add_var(self, name: str) -> None:
        """Add a variable into update buffer."""
        self._device.add_var(name)

    def remove_var(self, name: str) -> None:
        """Remove a variable from update buffer."""
        self._device.remove_var(name)

    async def __aenter__(self) -> Cybro:
        """Async enter.

        Returns:
            The Cybro object.
        """
        return self

    async def __aexit__(self, *_exc_info) -> None:
        """Async exit.

        Args:
            _exc_info: Exec type.
        """


def _get_chunk(data, chunk_size):
    """Split dictionary into smaller chunks."""
    data_it = iter(data)
    for i in range(0, len(data), chunk_size):
        yield {k: data[k] for k in islice(data_it, chunk_size)}


def _add_hiq_tags(
    variables: dict[str, str], controller: str = "c10000."
) -> dict[str, str]:
    """Adds controller specific general error tags."""
    _vars = variables
    _vars[controller + "lc00_general_error"] = ""
    _vars[controller + "lc01_general_error"] = ""
    _vars[controller + "lc02_general_error"] = ""
    _vars[controller + "lc03_general_error"] = ""
    _vars[controller + "lc04_general_error"] = ""
    _vars[controller + "lc05_general_error"] = ""
    _vars[controller + "lc06_general_error"] = ""
    _vars[controller + "lc07_general_error"] = ""
    _vars[controller + "ld00_general_error"] = ""
    _vars[controller + "ld01_general_error"] = ""
    _vars[controller + "ld02_general_error"] = ""
    _vars[controller + "ld03_general_error"] = ""
    _vars[controller + "bc00_general_error"] = ""
    _vars[controller + "bc01_general_error"] = ""
    _vars[controller + "bc02_general_error"] = ""
    _vars[controller + "bc03_general_error"] = ""
    _vars[controller + "bc04_general_error"] = ""
    _vars[controller + "bc05_general_error"] = ""
    _vars[controller + "sc00_general_error"] = ""
    _vars[controller + "sc01_general_error"] = ""
    _vars[controller + "sc02_general_error"] = ""
    _vars[controller + "sc03_general_error"] = ""
    _vars[controller + "th00_general_error"] = ""
    _vars[controller + "th01_general_error"] = ""
    _vars[controller + "th02_general_error"] = ""
    _vars[controller + "th03_general_error"] = ""
    _vars[controller + "th04_general_error"] = ""
    _vars[controller + "th05_general_error"] = ""
    _vars[controller + "fc00_general_error"] = ""
    _vars[controller + "fc01_general_error"] = ""
    _vars[controller + "fc02_general_error"] = ""
    _vars[controller + "fc03_general_error"] = ""
    _vars[controller + "fc04_general_error"] = ""
    _vars[controller + "fc05_general_error"] = ""
    return _vars
