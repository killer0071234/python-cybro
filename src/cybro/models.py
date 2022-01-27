"""Models for Cybro scgi server."""
from __future__ import annotations

from dataclasses import dataclass

from typing import Any
from enum import IntEnum

from .exceptions import CybroError


@dataclass
class ServerInfo:  # pylint:
    """Cybro scgi server informations"""

    scgi_port_status: str
    """- no response, server is down
    - "active", server is up and running"""
    server_uptime: str
    """server uptime, returned as "dd days, hh:mm:ss"""
    scgi_request_pending: int
    """number of requests waiting to be served"""
    scgi_request_count: int
    """total number of requests served since server is started"""
    push_port_status: str
    """- "error", port already used by another application
    - "inactive", push disabled by configuration file
    - "active", push server is up and running"""
    push_count: int
    """total number of push messages received from controllers"""
    push_ack_errors: int
    """total number of push acknowledge errors"""
    push_list_count: int
    """total number of controllers in push list"""
    cache_request: int
    """configured read request time [s]"""
    cache_valid: int
    """configured cache validity time [s]"""
    server_version: str
    """returns "major.minor.release" """
    udp_rx_count: int
    """total number of messages received through UDP proxy"""
    udp_tx_count: int
    """total number of messages transmitted through UDP proxy"""
    datalogger_status: str
    """- "active", process running
    - "stopped", process stopped"""
    nad_list: str = ""
    """list of controllers in push list"""
    push_list: str = ""
    """list of controllers with status
    (nad, ip:port, plc status, plc program, alc file, resp time)"""
    abus_list: str = ""
    """status of communication between server and controllers"""
    datalogger_list: str = ""
    """list of variables for sample and alarm"""
    proxy_activity_list: str = ""
    """data for proxy activity"""

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ServerInfo:
        """generate ServerInfo object out of name / value dictionary"""
        return ServerInfo(
            scgi_port_status=data["sys.scgi_port_status"],
            server_uptime=data["sys.server_uptime"],
            scgi_request_pending=data["sys.scgi_request_pending"],
            scgi_request_count=data["sys.scgi_request_count"],
            push_port_status=data["sys.push_port_status"],
            push_count=data["sys.push_count"],
            push_ack_errors=data["sys.push_ack_errors"],
            push_list_count=data["sys.push_list_count"],
            cache_request=data["sys.cache_request"],
            cache_valid=data["sys.cache_valid"],
            server_version=data["sys.server_version"],
            udp_rx_count=data["sys.udp_rx_count"],
            udp_tx_count=data["sys.udp_tx_count"],
            datalogger_status=data["sys.datalogger_status"],
        )

    @staticmethod
    def from_vars(variables: dict[str, Var]) -> ServerInfo:
        """generate ServerInfo object out of name / Var object dictionary"""
        return ServerInfo(
            scgi_port_status=variables.get("sys.scgi_port_status").value,
            server_uptime=variables.get("sys.server_uptime").value,
            scgi_request_pending=variables.get("sys.scgi_request_pending").value,
            scgi_request_count=variables.get("sys.scgi_request_count").value,
            push_port_status=variables.get("sys.push_port_status").value,
            push_count=variables.get("sys.push_count").value,
            push_ack_errors=variables.get("sys.push_ack_errors").value,
            push_list_count=variables.get("sys.push_list_count").value,
            cache_request=variables.get("sys.cache_request").value,
            cache_valid=variables.get("sys.cache_valid").value,
            server_version=variables.get("sys.server_version").value,
            udp_rx_count=variables.get("sys.udp_rx_count").value,
            udp_tx_count=variables.get("sys.udp_tx_count").value,
            datalogger_status=variables.get("sys.datalogger_status").value,
        )


@dataclass
class Var:
    """object representing a scgi server variable"""

    name: str
    """name of variable"""
    value: str
    """value as string of variable"""
    description: str
    """description of the variable"""
    # var_type: int
    # """variable type"""

    def __init__(self, name: str, value: str, description: str) -> None:
        self.name = name
        self.value = value
        self.description = description
        # self.var_type = var_type

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Var:
        """split a dict with "name", "value" and "description" into a Var object"""
        return Var(
            name=data.get("name"),
            value=data.get("value"),
            description=data.get("description"),
            # var_type=var_type,
        )

    # @property
    # def value(self) -> str | int | float | bool:
    #    """return the current value"""
    #    if self.var_type == VarType.STR:
    #        return self.value_string
    #    if self.var_type == VarType.FLOAT:
    #        return self.value_float
    #    if self.var_type == VarType.BOOL:
    #        return self.value_bool
    #    return self.value_int

    def value_string(self) -> str:
        """get current value"""
        return self.value

    def value_int(self) -> int:
        """get current value"""
        return int(self.value)

    def value_bool(self) -> bool:
        """get current value"""
        return bool(self.value == "1")

    def value_float(self) -> float:
        """get current value"""
        return float(self.value)


class Device:
    """Object holding all information of Cybro scgi server."""

    server_info: ServerInfo
    vars: dict[str, Var] = {}
    """list of variable / value / descriptions (after read/write)"""
    user_vars: dict[str, str] = {}
    """list of all variables to periodically update"""
    vars_types: dict[str, int] = {}

    def __init__(self, data: dict) -> None:
        """Initialize an empty Cybro scgi server device class.

        Args:
            data: The full API response from a Cybro scgi server device.

        Raises:
            CybroError: In case the given API response is incomplete in a way
                that a Device object cannot be constructed from it.
        """
        # Check if all elements are in the passed dict, else raise an Error
        if data["var"] is None:
            raise CybroError("Cybro data is incomplete, cannot construct device object")

        # if any(
        #    k not in data and data[k] is not None
        #    for k in ("var")
        # ):
        #    raise CybroError("Cybro data is incomplete, cannot construct device object")
        self.update_from_dict(data)

    def update_from_dict(self, data: dict) -> Device:
        """Return Device object from Cybro scgi server response.

        Args:
            data: Update the device object with the data received from a
                Cybro scgi server request.

        Returns:
            The updated Device object.
        """
        # update server info
        for var in data["var"]:
            self.vars.update({var["name"]: Var.from_dict(var)})
            self.vars_types.update({var["name"]: 0})
        self.server_info = ServerInfo.from_vars(self.vars)

        # print(self.vars)

        return self

    def update_user_var_from_dict(self, data: dict) -> None:
        """Parses user variables from Cybro scgi server response.

        Args:
            data: object with the data received from a
                Cybro scgi server request.

        Returns:
            None.
        """
        # update user variable
        for _var in data["var"]:
            self.vars.update({_var["name"]: Var.from_dict(_var)})
            # self.user_vars.update({_var["name"]: ""})

        return self

    def update_var(self, data: dict, var_type: VarType = 0) -> str | bool | int | float:
        """Return Value of a single var response.

        Args:
            data: Update the variable object with the data received from a
                Cybro scgi request.
            var_type: Type of read variable (default = str)

        Returns:
            The value of the var
        """
        if len(data) == 1:
            self.vars.update({data["var"]["name"]: Var.from_dict(data["var"])})
            self.vars_types.update({data["var"]["name"]: var_type})
            self.user_vars.update({data["var"]["name"]: ""})
            return self.vars[data["var"]["name"]].value

        for _var in data["var"]:
            self.vars.update({_var["name"]: Var.from_dict(_var)})
            self.vars_types.update({_var["name"]: var_type})
            self.user_vars.update({_var["name"]: ""})
            return self.vars[_var["name"]].value
        return "?"

    def add_var(self, name, var_type: VarType = 0) -> None:
        """Adds a variable to the update list.

        Args:
            name: Variable name to read eg: c1000.scan_time"""
        self.user_vars.update({name: ""})
        self.vars_types.update({name: var_type})

    def remove_var(self, name) -> None:
        """Removes a variable from the read list"""
        self.user_vars.pop(name)
        self.vars_types.pop(name)


class VarType(IntEnum):
    """Enumeration representing variable types"""

    STR = 0
    INT = 1
    FLOAT = 2
    BOOL = 3
