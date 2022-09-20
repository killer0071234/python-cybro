"""Tests for `cybro.Cybro`."""
import unittest
from typing import Any
from unittest import IsolatedAsyncioTestCase

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
        "sys.scgi_port_status": "active",
        "sys.server_uptime": "00 days, 01:02:03",
        "sys.scgi_request_pending": 12,
        "sys.scgi_request_count": 23,
        "sys.push_port_status": "inactive",
        "sys.push_count": 13,
        "sys.push_ack_errors": 14,
        "sys.push_list_count": 2,
        "sys.cache_request": 1,
        "sys.cache_valid": 2,
        "sys.server_version": "3.1.3",
        "sys.udp_rx_count": 123,
        "sys.udp_tx_count": 234,
        "sys.datalogger_status": "stopped",
        "c1000.sys.ip_port": "127.0.0.1:8442",
        "c1000.sys.timestamp": "2022-08-20 15:52:46",
        "c1000.sys.plc_program_status": "ok",
        "c1000.sys.response_time": 3,
        "c1000.sys.bytes_transferred": 200,
        "c1000.sys.comm_error_count": 22,
        "c1000.sys.alc_file": 22,
    }
    return _vars


def var_vars() -> dict[str, Var]:
    """Return variable values as Var structure."""
    _vars: dict[str, Var] = {
        "sys.scgi_port_status": Var("sys.scgi_port_status", "active", "Desc."),
        "sys.server_uptime": Var("sys.server_uptime", "00 days, 01:02:03", "Desc."),
        "sys.scgi_request_pending": Var("sys.scgi_request_pending", 12, "Desc."),
        "sys.scgi_request_count": Var("sys.scgi_request_count", 23, "Desc."),
        "sys.push_port_status": Var("sys.push_port_status", "inactive", "Desc."),
        "sys.push_count": Var("sys.push_count", 13, "Desc."),
        "sys.push_ack_errors": Var("sys.push_ack_errors", 14, "Desc."),
        "sys.push_list_count": Var("sys.push_list_count", 2, "Desc."),
        "sys.cache_request": Var("sys.cache_request", 1, "Desc."),
        "sys.cache_valid": Var("sys.cache_valid", 2, "Desc."),
        "sys.server_version": Var("sys.server_version", "3.1.3", "Desc."),
        "sys.udp_rx_count": Var("sys.udp_rx_count", 123, "Desc."),
        "sys.udp_tx_count": Var("sys.udp_tx_count", 234, "Desc."),
        "sys.datalogger_status": Var("sys.datalogger_status", "stopped", "Desc."),
        "c1000.sys.ip_port": Var("c1000.sys.ip_port", "127.0.0.1:8442", "Desc."),
        "c1000.sys.timestamp": Var(
            "c1000.sys.timestamp", "2022-08-20 15:52:46", "Desc."
        ),
        "c1000.sys.plc_program_status": Var(
            "c1000.sys.plc_program_status", "ok", "Desc."
        ),
        "c1000.sys.response_time": Var("c1000.sys.response_time", "3", "Desc."),
        "c1000.sys.bytes_transferred": Var(
            "c1000.sys.bytes_transferred", "200", "Desc."
        ),
        "c1000.sys.comm_error_count": Var("c1000.sys.comm_error_count", "22", "Desc."),
        "c1000.sys.alc_file": Var("c1000.sys.alc_file", "22", "Desc."),
    }
    return _vars


class TestCybro(IsolatedAsyncioTestCase):
    """Test class."""

    def test_server_info_from_dict(self) -> None:
        """Check for ServerInfo.from_dict()."""
        server_info = ServerInfo.from_dict(var_dict())
        assert server_info.scgi_port_status == "active"
        assert server_info.server_uptime == "00 days, 01:02:03"
        assert server_info.scgi_request_pending == 12
        assert server_info.scgi_request_count == 23
        assert server_info.push_port_status == "inactive"
        assert server_info.push_count == 13
        assert server_info.push_ack_errors == 14
        assert server_info.push_list_count == 2
        assert server_info.cache_request == 1
        assert server_info.cache_valid == 2
        assert server_info.server_version == "3.1.3"
        assert server_info.udp_rx_count == 123
        assert server_info.udp_tx_count == 234
        assert server_info.datalogger_status == "stopped"
        assert server_info.nad_list == ""
        assert server_info.push_list == ""
        assert server_info.abus_list == ""
        assert server_info.datalogger_list == ""
        assert server_info.push_list == ""

    def test_server_info_from_vars(self) -> None:
        """Check for ServerInfo.from_vars()."""
        server_info = ServerInfo.from_vars(var_vars())
        assert server_info.scgi_port_status == "active"
        assert server_info.server_uptime == "00 days, 01:02:03"
        assert server_info.scgi_request_pending == 12
        assert server_info.scgi_request_count == 23
        assert server_info.push_port_status == "inactive"
        assert server_info.push_count == 13
        assert server_info.push_ack_errors == 14
        assert server_info.push_list_count == 2
        assert server_info.cache_request == 1
        assert server_info.cache_valid == 2
        assert server_info.server_version == "3.1.3"
        assert server_info.udp_rx_count == 123
        assert server_info.udp_tx_count == 234
        assert server_info.datalogger_status == "stopped"
        assert server_info.nad_list == ""
        assert server_info.push_list == ""
        assert server_info.abus_list == ""
        assert server_info.datalogger_list == ""
        assert server_info.push_list == ""

    # def test_plc_info_from_dict(self) -> None:
    #    """Check for PlcInfo.from_dict()."""
    #    plc_info = PlcInfo.from_dict(var_dict(), 1000)
    #    assert plc_info.ip_port == "127.0.0.1:8442"
    #    assert plc_info.timestamp == "2022-08-20 15:52:46"
    #    assert plc_info.plc_program_status == "ok"
    #    assert plc_info.response_time == "3"
    #    assert plc_info.bytes_transferred == "200"
    #    assert plc_info.comm_error_count == "22"
    #    assert plc_info.alc_file == "22"

    def test_plc_info_from_vars(self) -> None:
        """Check for PlcInfo.from_vars()."""
        plc_info = PlcInfo.from_vars(var_vars(), 1000)
        assert plc_info.ip_port == "127.0.0.1:8442"
        assert plc_info.timestamp == "2022-08-20 15:52:46"
        assert plc_info.plc_program_status == "ok"
        assert plc_info.response_time == "3"
        assert plc_info.bytes_transferred == "200"
        assert plc_info.comm_error_count == "22"
        assert plc_info.alc_file == "22"

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
