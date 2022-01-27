"""Asynchronous Python client for Cybro scgi server."""
from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import dataclass
from typing import Any

import aiohttp
import async_timeout
import backoff
from cachetools import TTLCache
from yarl import URL
import xmltodict

from .exceptions import (
    CybroConnectionError,
    CybroConnectionTimeoutError,
    CybroEmptyResponseError,
    CybroError,
)
from .models import Device, VarType

VERSION_CACHE: TTLCache = TTLCache(maxsize=16, ttl=7200)


@dataclass
class Cybro:
    """Main class for handling connections with WLED."""

    host: str
    port: int = 4000
    request_timeout: float = 8.0
    session: aiohttp.client.ClientSession | None = None

    _device: Device | None = None

    async def disconnect(self) -> None:
        """disconnect from cybro scgi server object"""
        if self.session is not None:
            await self.session.close()
            self.session = None

    @backoff.on_exception(backoff.expo, CybroConnectionError, max_tries=3, logger=None)
    async def request(
        self,
        uri: str = "",
        data: dict | None = None,
    ) -> Any:
        """Handle a request to a WLED device.

        A generic method for sending/handling HTTP requests done gainst
        the WLED device.

        Args:
            uri: Request URI, for example `/json/si`.
            method: HTTP method to use for the request.E.g., "GET" or "POST".
            data: Dictionary of data to send to the WLED device.

        Returns:
            A Python dictionary (JSON decoded) with the response from the
            WLED device.

        Raises:
            WLEDConnectionError: An error occurred while communitcation with
                the WLED device.
            WLEDConnectionTimeoutError: A timeout occurred while communicating
                with the WLED device.
            WLEDError: Received an unexpected response from the WLED device.
        """
        if data is not None:
            url = URL.build(
                scheme="http", host=self.host, port=self.port, path="", query=data
            )
        else:
            url = URL.build(
                scheme="http", host=self.host, port=self.port, path="", query_string=uri
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
                print(url_fixed)
                response = await self.session.get(
                    url=url_fixed,
                    # method="GET",
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
            # print(response_data.get("data"))

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
    async def update(self, full_update: bool = False) -> Device:
        """Get all information about the device in a single call.

        This method updates all WLED information available with a single API
        call.

        Args:
            full_update: Force a full update from the WLED Device.

        Returns:
            WLED Device data.

        Raises:
            WLEDEmptyResponseError: The WLED device returned an empty response.
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
            if not (data := await self.request(data=_vars)):
                raise CybroEmptyResponseError(
                    f"Cybro scgi server at {self.host}:{self.port} returned an empty API"
                    " response on full update"
                )

            self._device = Device(data)

            if len(self._device.user_vars) > 0:
                cnt = len(self._device.user_vars)
                print(f"Updating {cnt} user var(s)")
                if not (data := await self.request(data=self._device.user_vars)):
                    raise CybroEmptyResponseError(
                        f"Cybro scgi server at {self.host}:{self.port} returned an empty"
                        " response on full update"
                    )

                self._device.update_user_var_from_dict(data=data)

            return self._device

        if len(self._device.user_vars) > 0:
            cnt = len(self._device.user_vars)
            print(f"Updating {cnt} user var(s)")
            if not (data := await self.request(data=self._device.user_vars)):
                raise CybroEmptyResponseError(
                    f"Cybro scgi server at {self.host}:{self.port} returned an empty"
                    " response on full update"
                )

            self._device.update_user_var_from_dict(data=data)

            return self._device

        return self._device

    @backoff.on_exception(
        backoff.expo, CybroEmptyResponseError, max_tries=3, logger=None
    )
    async def write_var(self, name: str, value: str) -> None:
        """write a single variable to scgi server"""
        await self.request(data={name: value})

    @backoff.on_exception(
        backoff.expo, CybroEmptyResponseError, max_tries=3, logger=None
    )
    async def read_var(
        self, name: str, var_type: VarType = VarType.STR
    ) -> str | int | float | bool:
        """read a single variable from scgi server"""
        if not (data := await self.request(uri=name)):
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
        """read a single variable from scgi server as int"""
        return await self.read_var(name, VarType.INT)

    @backoff.on_exception(
        backoff.expo, CybroEmptyResponseError, max_tries=3, logger=None
    )
    async def read_var_float(
        self,
        name: str,
    ) -> float:
        """read a single variable from scgi server as float"""
        return await self.read_var(name, VarType.FLOAT)

    @backoff.on_exception(
        backoff.expo, CybroEmptyResponseError, max_tries=3, logger=None
    )
    async def read_var_bool(
        self,
        name: str,
    ) -> bool:
        """read a single variable from scgi server as float"""
        return await self.read_var(name, VarType.BOOL)

    def add_var(self, name: str) -> None:
        """add a variable into update buffer"""
        self._device.add_var(name)

    def remove_var(self, name: str) -> None:
        """remove a variable from update buffer"""
        self._device.remove_var(name)

    async def reset(self) -> None:
        """Reboot WLED device."""
        await self.request("/reset")

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
