"""Tests for `cybro.Cybro`."""  # fmt: skip
import unittest
from typing import Any
from unittest import IsolatedAsyncioTestCase

from src.cybro.cybro import _add_hiq_tags
from src.cybro.cybro import _get_chunk
from src.cybro.exceptions import CybroError
from src.cybro.exceptions import CybroPlcNotFoundError
from src.cybro.models import Device
from src.cybro.models import PlcInfo
from src.cybro.models import ServerInfo
from src.cybro.models import Var


# This method will be used by the mock to replace requests.get
def mocked_requests_get(*args, **kwargs):
    """Mocked data request."""

    class MockResponse:
        """Mocked response."""

        def __init__(self, xml_data, status_code):
            self.xml_data = xml_data
            self.status = status_code
            self.read = xml_data

        def xml(self):
            """get only xml."""
            return self.xml_data

    if args[0] == "example.com":
        return MockResponse(
            "<data><var><name>c10000.scan_time</name><value>1</value><description>Last scan execution time [ms].</description></var></data>",
            200,
        )
    elif args[0] == "http://someotherurl.com/anothertest.json":
        return MockResponse({"key2": "value2"}, 200)

    return MockResponse(None, 404)


def var_dict() -> dict[str, str]:
    """Return variable values as ditionary."""
    _vars: dict[str, Any] = {
        "sys.server_uptime": "00 days, 01:02:03",
        "sys.scgi_request_count": 23,
        "sys.push_port_status": "inactive",
        "sys.push_count": 13,
        "sys.push_ack_errors": 14,
        "sys.push_list_count": 2,
        "sys.cache_request": 1,
        "sys.cache_valid": 2,
        "sys.server_version": "3.2.6",
        "sys.udp_rx_count": 123,
        "sys.udp_tx_count": 234,
        "sys.nad_list": {"item": [123, 1000]},
        "c1000.sys.ip_port": "127.0.0.1:8442",
        "c1000.sys.timestamp": "2022-08-20 15:52:46",
        "c1000.sys.plc_status": "ok",
        "c1000.sys.response_time": 3,
        "c1000.sys.bytes_transferred": 200,
        "c1000.sys.com_error_count": 22,
        "c1000.sys.alc_file": ";CPU CyBro-2 10000 \n;Addr Id    Array Offset Size Scope  Type  Name                             \n0050  00000 1     0      1    global bit   lc00_general_error               Combined system error (timeout or program error), module is not operational.\n0070  00000 1     0      1    global bit   lc01_general_error               Combined system error (timeout or program error), module is not operational.\n0090  00000 1     0      1    global bit   lc02_general_error               Combined system error (timeout or program error), module is not operational.\n00B0  00000 1     0      1    global bit   lc03_general_error               Combined system error (timeout or program error), module is not operational.\n00D0  00000 1     0      1    global bit   lc04_general_error               Combined system error (timeout or program error), module is not operational.\n00F0  00000 1     0      1    global bit   lc05_general_error               Combined system error (timeout or program error), module is not operational.\n0110  00000 1     0      1    global bit   lc06_general_error               Combined system error (timeout or program error), module is not operational.\n0130  00000 1     0      1    global bit   lc07_general_error               Combined system error (timeout or program error), module is not operational.\n0150  00000 1     0      1    global bit   ld00_general_error               Combined system error (timeout or program error), module is not operational.\n0170  00000 1     0      1    global bit   ld01_general_error               Combined system error (timeout or program error), module is not operational.\n0190  00000 1     0      1    global bit   ld02_general_error               Combined system error (timeout or program error), module is not operational.\n01B0  00000 1     0      1    global bit   ld03_general_error               Combined system error (timeout or program error), module is not operational.\n01D0  00000 1     0      1    global bit   bc00_general_error               Combined system error (timeout or program error), module is not operational.\n01F0  00000 1     0      1    global bit   bc01_general_error               Combined system error (timeout or program error), module is not operational.\n0210  00000 1     0      1    global bit   bc02_general_error               Combined system error (timeout or program error), module is not operational.",
    }
    return _vars


def api_resp() -> dict:
    """Return a full response."""
    _dict = var_dict()
    _all1 = {
        "var": [
            {"name": k, "value": v, "description": "Desc."} for k, v in _dict.items()
        ]
    }
    return _all1


def var_vars() -> dict[str, Var]:
    """Return variable values as Var structure."""
    _dict = var_dict()
    _vars = {k: Var(k, v, "Desc.") for k, v in _dict.items()}
    return _vars


class TestCybro(IsolatedAsyncioTestCase):
    """Test class."""

    def test_server_info_from_dict(self) -> None:
        """Check for ServerInfo.from_dict()."""
        server_info = ServerInfo.from_dict(var_dict())
        self.assertEqual(server_info.server_uptime, "00 days, 01:02:03")
        self.assertEqual(server_info.scgi_request_count, 23)
        self.assertEqual(server_info.push_port_status, "inactive")
        self.assertEqual(server_info.push_count, 13)
        self.assertEqual(server_info.push_ack_errors, 14)
        self.assertEqual(server_info.push_list_count, 2)
        self.assertEqual(server_info.cache_request, 1)
        self.assertEqual(server_info.cache_valid, 2)
        self.assertEqual(server_info.server_version, "3.2.6")
        self.assertEqual(server_info.udp_rx_count, 123)
        self.assertEqual(server_info.udp_tx_count, 234)
        self.assertEqual(server_info.nad_list, [123, 1000])
        self.assertEqual(server_info.push_list, "")
        self.assertEqual(server_info.datalogger_list, "")
        self.assertEqual(server_info.push_list, "")

    def test_server_info_from_vars(self) -> None:
        """Check for ServerInfo.from_vars()."""
        server_info = ServerInfo.from_vars(var_vars())
        self.assertEqual(server_info.server_uptime, "00 days, 01:02:03")
        self.assertEqual(server_info.scgi_request_count, 23)
        self.assertEqual(server_info.push_port_status, "inactive")
        self.assertEqual(server_info.push_count, 13)
        self.assertEqual(server_info.push_ack_errors, 14)
        self.assertEqual(server_info.push_list_count, 2)
        self.assertEqual(server_info.cache_request, 1)
        self.assertEqual(server_info.cache_valid, 2)
        self.assertEqual(server_info.server_version, "3.2.6")
        self.assertEqual(server_info.udp_rx_count, 123)
        self.assertEqual(server_info.udp_tx_count, 234)
        self.assertEqual(server_info.nad_list, [123, 1000])
        self.assertEqual(server_info.push_list, "")
        self.assertEqual(server_info.datalogger_list, "")
        self.assertEqual(server_info.push_list, "")

    def test_server_info_from_dict_exception(self) -> None:
        """Check for ServerInfo.from_vars()."""
        self.assertRaises(
            CybroError,
            ServerInfo.from_dict,
            {},
        )

    def test_server_info_from_vars_exception(self) -> None:
        """Check for ServerInfo.from_vars()."""
        self.assertRaises(
            CybroError,
            ServerInfo.from_vars,
            {},
        )

    def test_plc_info_from_dict(self) -> None:
        """Check for PlcInfo.from_dict()."""
        plc_info = PlcInfo.from_dict(var_dict(), 1000)
        self.assertEqual(plc_info.ip_port, "127.0.0.1:8442")
        self.assertEqual(plc_info.timestamp, "2022-08-20 15:52:46")
        self.assertEqual(plc_info.plc_status, "ok")
        self.assertEqual(plc_info.response_time, 3)
        self.assertEqual(plc_info.bytes_transferred, 200)
        self.assertEqual(plc_info.com_error_count, 22)

    def test_plc_info_from_vars(self) -> None:
        """Check for PlcInfo.from_vars()."""
        plc_info = PlcInfo.from_vars(var_vars(), 1000)
        self.assertEqual(plc_info.ip_port, "127.0.0.1:8442")
        self.assertEqual(plc_info.timestamp, "2022-08-20 15:52:46")
        self.assertEqual(plc_info.plc_status, "ok")
        self.assertEqual(plc_info.response_time, 3)
        self.assertEqual(plc_info.bytes_transferred, 200)
        self.assertEqual(plc_info.com_error_count, 22)

    def test_plc_info_from_dict_exception(self) -> None:
        """Check for ServerInfo.from_dict()."""
        self.assertRaises(CybroPlcNotFoundError, PlcInfo.from_dict, {}, 1)

    def test_plc_info_from_vars_exception(self) -> None:
        """Check for ServerInfo.from_vars()."""
        self.assertRaises(CybroPlcNotFoundError, PlcInfo.from_vars, api_resp, 1)

    def test_plc_info_parse_alc_file(self) -> None:
        """Check for parsing of alc-file."""
        plc_info = PlcInfo.from_vars(var_vars(), 1000)
        res = plc_info.parse_alc_file()
        self.assertCountEqual(res, plc_info.plc_vars)

    def test_plc_info_parse_alc_file_empty(self) -> None:
        """Check for parsing of alc-file."""
        plc_info = PlcInfo.from_vars(var_vars(), 1000)
        plc_info.alc_file = None
        res = plc_info.parse_alc_file()
        self.assertCountEqual(res, {})

    def test_var_from_dict(self) -> None:
        """Check for Var parsing"""
        var = Var.from_dict(
            {"name": "sys.server_version", "value": "3.2.6", "description": "Desc."}
        )
        self.assertIsInstance(var, Var)

    def test_var_value_string(self) -> None:
        """Get value of Var."""
        var = Var.from_dict(
            {"name": "sys.server_version", "value": "3.2.6", "description": "Desc."}
        )
        value = var.value_string()
        self.assertEqual(value, "3.2.6")

    def test_var_value_int(self) -> None:
        """Get value of Var."""
        var = Var.from_dict(
            {"name": "sys.server_version", "value": "3", "description": "Desc."}
        )
        value = var.value_int()
        self.assertEqual(value, 3)

    def test_var_value_bool(self) -> None:
        """Get value of Var."""
        var = Var.from_dict(
            {"name": "sys.server_version", "value": "1", "description": "Desc."}
        )
        value = var.value_bool()
        self.assertEqual(value, True)

    def test_var_value_float(self) -> None:
        """Get value of Var."""
        var = Var.from_dict(
            {"name": "sys.server_version", "value": "3.3", "description": "Desc."}
        )
        value = var.value_float()
        self.assertEqual(value, 3.3)

    def test_device_empty(self) -> None:
        """Check for empty / None response."""
        self.assertRaises(CybroError, Device, None, 1000)

    def test_device_incomplete(self) -> None:
        """Check for incomplete response."""
        self.assertRaises(CybroError, Device, {}, 1000)

    def test_device(self) -> None:
        """Check for a normal response."""
        device = Device(api_resp(), 1000)
        self.assertIsInstance(device, Device)

    def test_device_update_user_var_from_dict(self) -> None:
        """Update a variable from user var."""
        device = Device(api_resp(), 1000)
        device.update_user_var_from_dict(api_resp())
        self.assertIsInstance(device, Device)

    def test_device_update_user_var_from_dict_one(self) -> None:
        """Update a variable from user var."""
        device = Device(api_resp(), 1000)
        device.update_user_var_from_dict(
            {"var": {"name": "c1000.scan_time", "value": "2", "description": "Desc."}}
        )
        self.assertIsInstance(device, Device)

    def test_device_update_from_dict(self) -> None:
        """Update a variable from user var."""
        device = Device(api_resp(), 1000)
        device.update_from_dict(api_resp())
        self.assertIsInstance(device, Device)

    def test_device_update_var_one(self) -> None:
        """Update a single var."""
        device = Device(api_resp(), 1000)
        ret_val = device.update_var(
            {"var": {"name": "c1000.scan_time", "value": "2", "description": "Desc."}}
        )
        self.assertEqual(ret_val, "2")

    def test_device_update_var(self) -> None:
        """Update a single var."""
        device = Device(api_resp(), 1000)
        ret_val = device.update_var(
            {
                "var": [
                    {
                        "name": "c1000.scan_time",
                        "value": "2",
                        "description": "Desc.",
                    },
                    {
                        "name": "c1000.scan_time_max",
                        "value": "5",
                        "description": "Desc.",
                    },
                ]
            }
        )
        self.assertEqual(ret_val, "2")

    def test_device_update_var_invalid(self) -> None:
        """Update a single var."""
        device = Device(api_resp(), 1000)
        ret_val = device.update_var({""})
        self.assertEqual(ret_val, "?")

    def test_device_add_var(self) -> None:
        """Add a single var."""
        device = Device(api_resp(), 1000)
        device.add_var("c1000.scan_time", 0)
        self.assertIsInstance(device, Device)

    def test_device_remove_var(self) -> None:
        """Remove a single var."""
        device = Device(api_resp(), 1000)
        device.add_var("c1000.scan_time", 0)
        device.remove_var("c1000.scan_time")
        self.assertIsInstance(device, Device)

    # def test_cybro_add_var(self) -> None:
    #    """Test to add a var."""
    #    _dev = Cybro("localhost", 4000, 1)
    #    _dev.add_var("c1.test")
    #    self.assertIsNotNone(_dev)

    def test_cybro_get_chunk(self) -> None:
        """Get a single chunk."""
        _vars_check = {}
        for dev in range(8):
            _vars_check.update({f"c1.lc{dev:02.0f}_general_error": ""})
        _chunk = _get_chunk(_vars_check, 50)
        for chunk in _chunk:
            self.assertCountEqual(_vars_check, chunk)

    def test_cybro_add_hiq_tags(self) -> None:
        """Check to add hiq-tags."""
        _vars_check = {}
        for dev in range(8):
            _vars_check.update({f"c1.lc{dev:02.0f}_general_error": ""})
        for dev in range(10):
            _vars_check.update({f"c1.ld{dev:02.0f}_general_error": ""})
            _vars_check.update({f"c1.ld{dev:02.0f}_rgb_mode": ""})
            _vars_check.update({f"c1.ld{dev:02.0f}_rgb_mode_2": ""})
        for dev in range(4):
            _vars_check.update({f"c1.sc{dev:02.0f}_general_error": ""})
        for dev in range(6):
            _vars_check.update({f"c1.bc{dev:02.0f}_general_error": ""})
            _vars_check.update({f"c1.fc{dev:02.0f}_general_error": ""})
        for dev in range(10):
            _vars_check.update({f"c1.th{dev:02.0f}_general_error": ""})
            _vars_check.update({f"c1.th{dev:02.0f}_window_enable": ""})
            _vars_check.update({f"c1.th{dev:02.0f}_fan_limit": ""})
            _vars_check.update({f"c1.th{dev:02.0f}_demand_enable": ""})
        _vars_check.update({"c1.power_meter_error": ""})
        _vars_check.update({"c1.outdoor_temperature_enable": ""})
        _vars_check.update({"c1.wall_temperature_enable": ""})
        _vars_check.update({"c1.water_temperature_enable": ""})
        _vars_check.update({"c1.auxilary_temperature_enable": ""})
        _vars_check.update({"c1.hvac_mode": ""})
        _vars = {}
        _vars = _add_hiq_tags(_vars, "c1.")
        self.assertCountEqual(_vars, _vars_check)

    # We patch 'aiohttp.client.ClientSession' with our own method. The mock object is passed in to our test case method.
    # @patch("aiohttp.client.ClientSession", spec=True)
    # @mock.patch("aiohttp.client.ClientSession", side_effect=mocked_requests_get)
    # async def test_update(self, mock_get) -> None:
    #    """Test update."""
    #    mock_response = MagicMock()
    #    mock_response.status = 200
    #    mock_response.read.return_value = "<data><var><name>c10000.scan_time</name><value>1</value><description>Last scan execution time [ms].</description></var></data>"
    #    mock_response.get.return_value = "<data><var><name>c10000.scan_time</name><value>1</value><description>Last scan execution time [ms].</description></var></data>"
    #    mock_response.text.return_value = "<data><var><name>c10000.scan_time</name><value>1</value><description>Last scan execution time [ms].</description></var></data>"
    #    mock_get.return_value = mock_response
    #    async with aiohttp.ClientSession() as session:
    #        cybro = Cybro("example.com", 80, session=session)
    #        print(cybro)
    #        response = await cybro.update()
    #        assert response is None


if __name__ == "__main__":
    unittest.main()
