"""HTTP tests for `cybro.Cybro` against a mocked scgi server."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from unittest.mock import patch
from urllib.parse import unquote
from xml.sax.saxutils import escape

import aiohttp
import backoff._async
import pytest
from aiohttp import web
from aresponses import ResponsesMockServer

from src.cybro.cybro import VAR_CHUNK_SIZE
from src.cybro.cybro import Cybro
from src.cybro.cybro import _build_query
from src.cybro.exceptions import CybroConnectionError
from src.cybro.exceptions import CybroConnectionTimeoutError
from src.cybro.exceptions import CybroEmptyResponseError
from src.cybro.exceptions import CybroError

HOST = "scgi.example.com"
NAD = 12762
PREFIX = f"c{NAD}."

ALC_FILE = (
    f";CPU CyBro-2 {NAD} \n"
    ";Addr Id    Array Offset Size Scope  Type  Name                             \n"
    "0011  00000 1     0      1    global bit   scan_overrun                     Scan overrun.\n"
    "0400  00000 1     0      2    global int   scan_time                        Last scan time [ms].\n"
    "0402  00000 1     0      2    global int   scan_time_max                    Max scan time [ms].\n"
    "0500  00000 1     0      1    global bit   cybro_qx00                       Binary output.\n"
    "3B76  00004 31    0      2    global int   dummy_int                        Array.\n"
)

# Values as returned by a real scgi server (v3.3.1)
SERVER_VALUES: dict[str, str] = {
    "sys.server_uptime": "0 days, 03:45:01",
    "sys.scgi_request_count": "100",
    "sys.push_port_status": "active",
    "sys.push_count": "0",
    "sys.push_ack_errors": "0",
    "sys.push_list_count": "0",
    "sys.cache_request": "10",
    "sys.cache_valid": "5",
    "sys.server_version": "3.3.1",
    "sys.udp_rx_count": "10",
    "sys.udp_tx_count": "10",
    f"{PREFIX}sys.ip_port": "192.168.1.20:8442",
    f"{PREFIX}sys.timestamp": "2026-10-01 12:00:00",
    f"{PREFIX}sys.plc_status": "ok",
    f"{PREFIX}sys.response_time": "7",
    f"{PREFIX}sys.bytes_transferred": "5585774",
    f"{PREFIX}sys.com_error_count": "0",
    f"{PREFIX}sys.alc_file": ALC_FILE,
    f"{PREFIX}scan_overrun": "0",
    f"{PREFIX}scan_time": "6",
    f"{PREFIX}scan_time_max": "22",
    f"{PREFIX}cybro_qx00": "0",
    f"{PREFIX}dummy_int[28]": "0",
    f"{PREFIX}lc00_general_error": "0",
    f"{PREFIX}lc01_general_error": "1",
}

# Like a real scgi server, PLC variables are returned without description
NO_DESCRIPTION: set[str] = {
    f"{PREFIX}lc00_general_error",
    f"{PREFIX}lc01_general_error",
}

# Answer to "c<nad>.sys.variables": name, type and description, but no value
VARIABLE_LIST: dict[str, tuple[str, str]] = {
    f"{PREFIX}cybro_ix00": ("datatype.bit", "Binary input (0-open, 1-closed)."),
    f"{PREFIX}cybro_ix01": ("datatype.bit", "Binary input (0-open, 1-closed)."),
    f"{PREFIX}scan_time": ("datatype.int", "Last scan time [ms]."),
    f"{PREFIX}lc00_general_error": ("datatype.bit", "Combined system error."),
    f"{PREFIX}lc01_general_error": ("datatype.bit", "Combined system error."),
    f"{PREFIX}general_error": ("datatype.bit", ""),
}


def _xml_var(name: str, value: str, description: str = "Desc.") -> str:
    return (
        f"<var><name>{name}</name><value>{value}</value>"
        f"<description>{description}</description></var>"
    )


def _xml_var_info(name: str, var_type: str, description: str) -> str:
    return (
        f"<var><name>{name}</name><type>{var_type}</type>"
        f"<description>{description}</description></var>"
    )


def _xml_unknown_var(name: str) -> str:
    return (
        f"<var><name>{name}</name><value>?</value><description />"
        "<error_code>2</error_code></var>"
    )


class FakeScgiServer:
    """Answers scgi requests from a dictionary and records every query."""

    def __init__(self, nad_list: list[int] | None = None) -> None:
        """Initialize the server with its values and controller list."""
        self.values = dict(SERVER_VALUES)
        self.nad_list = [NAD] if nad_list is None else nad_list
        self.queries: list[dict[str, str]] = []
        self.raw_queries: list[str] = []

    async def handler(self, request: web.Request) -> web.Response:
        """Read or write the requested variables.

        Like the real scgi server, variable names are not URL-decoded.
        """
        raw_query = request.rel_url.raw_query_string
        query = {}
        for part in raw_query.split("&") if raw_query else []:
            name, _, value = part.partition("=")
            query[name] = unquote(value)
        self.queries.append(query)
        self.raw_queries.append(raw_query)
        out = []
        var_list = []
        for name, value in query.items():
            if name == f"{PREFIX}sys.variables":
                # the real server appends the list after all requested values
                var_list = [
                    _xml_var_info(var, var_type, description)
                    for var, (var_type, description) in VARIABLE_LIST.items()
                ]
                continue
            if name == "sys.nad_list":
                items = "".join(f"<item>{nad}</item>" for nad in self.nad_list)
                out.append(_xml_var(name, items))
                continue
            if name not in self.values:
                out.append(_xml_unknown_var(escape(name)))
                continue
            if value:
                self.values[name] = value
            description = "" if name in NO_DESCRIPTION else "Desc."
            out.append(_xml_var(name, escape(self.values[name]), description))
        body = '<?xml version="1.0" encoding="ISO-8859-1"?><data>'
        body += "".join(out + var_list) + "</data>"
        return web.Response(text=body, content_type="text/xml", charset="iso-8859-1")


@pytest.fixture(autouse=True)
def no_backoff_wait() -> Iterator[None]:
    """Keep the retry count, but don't wait between retries."""
    next_wait = backoff._async._next_wait

    def _no_wait(*args, **kwargs):
        next_wait(*args, **kwargs)
        return 0

    with patch.object(backoff._async, "_next_wait", _no_wait):
        yield


@pytest.fixture
def server(aresponses: ResponsesMockServer) -> FakeScgiServer:
    """Fake scgi server answering all requests to HOST:4000."""
    fake = FakeScgiServer()
    aresponses.add(f"{HOST}:4000", response=fake.handler, repeat=aresponses.INFINITY)
    return fake


# --- request() ---


@pytest.mark.asyncio
async def test_request_parses_xml(server: FakeScgiServer) -> None:
    """A response is parsed into a dictionary."""
    cybro = Cybro(HOST, nad=NAD)
    data = await cybro.request(data=f"{PREFIX}scan_time")
    await cybro.disconnect()

    assert data == {
        "var": {"name": f"{PREFIX}scan_time", "value": "6", "description": "Desc."}
    }


@pytest.mark.asyncio
async def test_request_query_without_empty_values(server: FakeScgiServer) -> None:
    """Variables to read are sent without "=", variables to write with their value."""
    cybro = Cybro(HOST, nad=NAD)
    await cybro.request(data={"a": "", "b": "1", "c": ""})
    await cybro.disconnect()

    assert server.raw_queries == ["a&b=1&c"]


@pytest.mark.asyncio
async def test_request_uses_path_from_host(aresponses: ResponsesMockServer) -> None:
    """A path in the host string is used for every request."""
    fake = FakeScgiServer()
    aresponses.add(f"{HOST}:8080", "/scgi", "GET", fake.handler)
    cybro = Cybro(f"http://{HOST}/scgi", port=8080, nad=NAD)
    await cybro.request(data="sys.server_version")
    await cybro.disconnect()

    aresponses.assert_plan_strictly_followed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "attempts"), [(400, 1), (404, 1), (500, 3), (503, 3)]
)
async def test_request_http_error(
    aresponses: ResponsesMockServer, status: int, attempts: int
) -> None:
    """Server errors are retried, client errors are not."""
    aresponses.add(
        f"{HOST}:4000",
        response=aresponses.Response(status=status, text="failure"),
        repeat=attempts,
    )
    cybro = Cybro(HOST, nad=NAD)
    with pytest.raises(CybroError):
        await cybro.request(data="sys.server_version")
    await cybro.disconnect()

    aresponses.assert_plan_strictly_followed()


@pytest.mark.asyncio
async def test_request_http_error_json(aresponses: ResponsesMockServer) -> None:
    """A JSON error body is passed on in the exception."""
    aresponses.add(
        f"{HOST}:4000",
        response=aresponses.Response(
            status=500,
            text='{"message": "broken"}',
            content_type="application/json",
        ),
        repeat=3,
    )
    cybro = Cybro(HOST, nad=NAD)
    with pytest.raises(CybroError) as err:
        await cybro.request(data="sys.server_version")
    await cybro.disconnect()

    assert err.value.args == (500, {"message": "broken"})


@pytest.mark.asyncio
async def test_request_timeout(aresponses: ResponsesMockServer) -> None:
    """A slow server raises CybroConnectionTimeoutError."""

    async def slow(_request: web.Request) -> web.Response:
        await asyncio.sleep(1)
        return web.Response(text="<data></data>")

    aresponses.add(f"{HOST}:4000", response=slow, repeat=3)
    cybro = Cybro(HOST, nad=NAD)
    cybro.request_timeout = 0.05
    with pytest.raises(CybroConnectionTimeoutError):
        await cybro.request(data="sys.server_version")
    await cybro.disconnect()

    aresponses.assert_plan_strictly_followed()


@pytest.mark.asyncio
async def test_request_timeout_while_reading_body(
    aresponses: ResponsesMockServer,
) -> None:
    """The timeout also covers reading the response body."""

    async def slow_body(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={"Content-Type": "text/xml"})
        await response.prepare(request)
        await response.write(b"<data>")
        await asyncio.sleep(1)
        await response.write(b"</data>")
        return response

    aresponses.add(f"{HOST}:4000", response=slow_body, repeat=3)
    cybro = Cybro(HOST, nad=NAD)
    cybro.request_timeout = 0.05
    with pytest.raises(CybroConnectionTimeoutError):
        await cybro.request(data="sys.server_version")
    await cybro.disconnect()


@pytest.mark.asyncio
async def test_request_connection_error() -> None:
    """A connection failure raises CybroConnectionError after three attempts."""
    session = aiohttp.ClientSession()
    cybro = Cybro(HOST, nad=NAD, session=session)
    with (
        patch.object(
            session, "get", side_effect=aiohttp.ClientConnectionError("refused")
        ) as get,
        pytest.raises(CybroConnectionError),
    ):
        await cybro.request(data="sys.server_version")
    await session.close()

    assert get.call_count == 3


# --- update() ---


@pytest.mark.asyncio
async def test_update(server: FakeScgiServer) -> None:
    """The first update reads server and PLC information."""
    cybro = Cybro(HOST, nad=NAD)
    device = await cybro.update()
    await cybro.disconnect()

    assert device.server_info.server_version == "3.3.1"
    assert device.plc_info.plc_status == "ok"
    assert device.plc_info.ip_port == "192.168.1.20:8442"
    assert device.plc_info.plc_vars == {
        f"{PREFIX}scan_overrun": "bit",
        f"{PREFIX}scan_time": "int",
        f"{PREFIX}scan_time_max": "int",
        f"{PREFIX}cybro_qx00": "bit",
        f"{PREFIX}dummy_int": "int",
    }


@pytest.mark.asyncio
async def test_update_single_controller(server: FakeScgiServer) -> None:
    """nad_list is a list, also with a single controller."""
    cybro = Cybro(HOST, nad=NAD)
    device = await cybro.update()
    await cybro.disconnect()

    assert device.server_info.nad_list == [str(NAD)]


@pytest.mark.asyncio
async def test_update_no_controller(server: FakeScgiServer) -> None:
    """nad_list is empty when the server knows no controllers."""
    server.nad_list = []
    cybro = Cybro(HOST)
    device = await cybro.update()
    await cybro.disconnect()

    assert device.server_info.nad_list == []


@pytest.mark.asyncio
async def test_update_server_only(server: FakeScgiServer) -> None:
    """Without NAD, only server variables are requested."""
    server.nad_list = [NAD, 1000]
    cybro = Cybro(HOST)
    device = await cybro.update()
    await cybro.disconnect()

    assert device.plc_info is None
    assert device.server_info.nad_list == [str(NAD), "1000"]
    assert all(name.startswith("sys.") for name in server.queries[0])


@pytest.mark.asyncio
async def test_update_user_vars(server: FakeScgiServer) -> None:
    """Variables added with add_var() are refreshed by every update."""
    names = [f"{PREFIX}scan_time", f"{PREFIX}scan_time_max"]
    cybro = Cybro(HOST, nad=NAD)
    await cybro.update()
    for name in names:
        cybro.add_var(name)
    cybro.add_var(f"{PREFIX}unknown_var")  # not in the ALC file, ignored
    device = await cybro.update()
    assert device.vars[names[0]].value_int() == 6
    assert device.vars[names[1]].value_int() == 22
    assert list(device.user_vars) == names

    server.values[names[0]] = "9"
    requests_before = len(server.queries)
    device = await cybro.update()
    await cybro.disconnect()

    assert device.vars[names[0]].value_int() == 9
    assert server.queries[requests_before:] == [dict.fromkeys(names, "")]


@pytest.mark.asyncio
async def test_update_single_user_var(server: FakeScgiServer) -> None:
    """A single registered variable gets its value."""
    cybro = Cybro(HOST, nad=NAD)
    await cybro.update()
    cybro.add_var(f"{PREFIX}scan_time")
    device = await cybro.update()
    await cybro.disconnect()

    assert device.vars[f"{PREFIX}scan_time"].value == "6"


@pytest.mark.asyncio
async def test_update_in_chunks(server: FakeScgiServer) -> None:
    """Many variables are requested in chunks of VAR_CHUNK_SIZE."""
    names = [f"{PREFIX}sys.var{i:02}" for i in range(VAR_CHUNK_SIZE + 5)]
    cybro = Cybro(HOST, nad=NAD)
    await cybro.update()
    for name in names:
        cybro.add_var(name)
    requests_before = len(server.queries)
    await cybro.update()
    await cybro.disconnect()

    sizes = [len(query) for query in server.queries[requests_before:]]
    assert sizes == [VAR_CHUNK_SIZE, 5]


@pytest.mark.asyncio
async def test_update_hiq(server: FakeScgiServer) -> None:
    """device_type=1 also requests the HIQ-controller variables."""
    cybro = Cybro(HOST, nad=NAD)
    await cybro.update(device_type=1)
    await cybro.disconnect()

    requested = {name for query in server.queries for name in query}
    assert f"{PREFIX}hvac_mode" in requested
    assert f"{PREFIX}lc00_general_error" in requested


@pytest.mark.asyncio
async def test_update_variable_list_keeps_values(server: FakeScgiServer) -> None:
    """The "sys.variables" list never replaces or adds variable values."""
    cybro = Cybro(HOST, nad=NAD)
    for full_update in (False, True):
        device = await cybro.update(full_update=full_update, device_type=1)
        assert device.vars[f"{PREFIX}lc00_general_error"].value == "0"
        assert device.vars[f"{PREFIX}lc01_general_error"].value == "1"
        assert [name for name, var in device.vars.items() if var.value is None] == []
        # listed, but not read: no entry in vars
        assert f"{PREFIX}cybro_ix00" not in device.vars
        assert f"{PREFIX}general_error" not in device.vars
    await cybro.disconnect()


@pytest.mark.asyncio
async def test_update_variable_list_info(server: FakeScgiServer) -> None:
    """Type and description of all PLC variables are kept in var_info."""
    cybro = Cybro(HOST, nad=NAD)
    device = await cybro.update(device_type=1)

    info = device.var_info[f"{PREFIX}cybro_ix00"]
    assert info.name == f"{PREFIX}cybro_ix00"
    assert info.type == "datatype.bit"
    assert info.description == "Binary input (0-open, 1-closed)."
    assert device.var_info[f"{PREFIX}general_error"].description is None
    # variables read without description get the one from the list
    error = device.vars[f"{PREFIX}lc00_general_error"]
    assert error.description == "Combined system error."
    # a description sent with the value is kept
    assert device.vars["sys.server_version"].description == "Desc."

    cybro.add_var(f"{PREFIX}scan_time")
    await cybro.update()
    await cybro.read_var(f"{PREFIX}lc01_general_error")
    await cybro.disconnect()
    assert device.vars[f"{PREFIX}scan_time"].value == "6"
    assert device.vars[f"{PREFIX}scan_time"].description == "Desc."
    assert (
        device.vars[f"{PREFIX}lc01_general_error"].description
        == "Combined system error."
    )


@pytest.mark.asyncio
async def test_update_empty_response(aresponses: ResponsesMockServer) -> None:
    """An empty response raises CybroEmptyResponseError after three attempts."""
    aresponses.add(
        f"{HOST}:4000",
        response=aresponses.Response(text="<data></data>", content_type="text/xml"),
        repeat=3,
    )
    cybro = Cybro(HOST, nad=NAD)
    with pytest.raises(CybroEmptyResponseError):
        await cybro.update()
    await cybro.disconnect()

    aresponses.assert_plan_strictly_followed()


# --- read_var() / write_var() ---


@pytest.mark.asyncio
async def test_read_var(server: FakeScgiServer) -> None:
    """A single variable is read immediately."""
    cybro = Cybro(HOST, nad=NAD)
    await cybro.update()
    value = await cybro.read_var(f"{PREFIX}scan_time_max")
    await cybro.disconnect()

    assert value == "22"
    assert server.queries[-1] == {f"{PREFIX}scan_time_max": ""}


@pytest.mark.asyncio
async def test_read_var_typed(server: FakeScgiServer) -> None:
    """The typed read methods return int, float and bool."""
    cybro = Cybro(HOST, nad=NAD)
    await cybro.update()
    try:
        value_int = await cybro.read_var_int(f"{PREFIX}scan_time")
        value_float = await cybro.read_var_float(f"{PREFIX}scan_time")
        value_bool = await cybro.read_var_bool(f"{PREFIX}scan_overrun")
    finally:
        await cybro.disconnect()

    assert value_int == 6
    assert isinstance(value_int, int)
    assert value_float == 6.0
    assert isinstance(value_float, float)
    assert value_bool is False


@pytest.mark.asyncio
async def test_read_var_typed_true(server: FakeScgiServer) -> None:
    """read_var_bool returns True for "1"."""
    server.values[f"{PREFIX}scan_overrun"] = "1"
    cybro = Cybro(HOST, nad=NAD)
    await cybro.update()
    value = await cybro.read_var_bool(f"{PREFIX}scan_overrun")
    await cybro.disconnect()

    assert value is True


@pytest.mark.asyncio
async def test_read_var_typed_unknown(server: FakeScgiServer) -> None:
    """A typed read of an unknown variable returns "?" instead of raising."""
    name = f"{PREFIX}does_not_exist"
    cybro = Cybro(HOST, nad=NAD)
    await cybro.update()
    values = [
        await cybro.read_var_int(name),
        await cybro.read_var_float(name),
        await cybro.read_var_bool(name),
    ]
    await cybro.disconnect()

    assert values == ["?", "?", "?"]


@pytest.mark.asyncio
async def test_read_var_typed_retries(aresponses: ResponsesMockServer) -> None:
    """A typed read is attempted three times on an empty response, not nine."""
    fake = FakeScgiServer()
    aresponses.add(f"{HOST}:4000", response=fake.handler)
    aresponses.add(
        f"{HOST}:4000",
        response=aresponses.Response(text="<data></data>", content_type="text/xml"),
        repeat=3,
    )
    cybro = Cybro(HOST, nad=NAD)
    await cybro.update()
    with pytest.raises(CybroEmptyResponseError):
        await cybro.read_var_int(f"{PREFIX}scan_time")
    await cybro.disconnect()

    aresponses.assert_plan_strictly_followed()


@pytest.mark.asyncio
async def test_write_var(server: FakeScgiServer) -> None:
    """A write sends name=value and returns the new value."""
    cybro = Cybro(HOST, nad=NAD)
    await cybro.update()
    value = await cybro.write_var(f"{PREFIX}cybro_qx00", "1")
    await cybro.disconnect()

    assert value == "1"
    assert server.queries[-1] == {f"{PREFIX}cybro_qx00": "1"}
    assert server.values[f"{PREFIX}cybro_qx00"] == "1"


@pytest.mark.asyncio
async def test_read_unknown_var(server: FakeScgiServer) -> None:
    """An unknown variable is returned as "?"."""
    cybro = Cybro(HOST, nad=NAD)
    await cybro.update()
    value = await cybro.read_var(f"{PREFIX}does_not_exist")
    await cybro.disconnect()

    assert value == "?"


# --- array elements ---


@pytest.mark.asyncio
async def test_array_element_read_write(server: FakeScgiServer) -> None:
    """Array elements are sent with unencoded brackets."""
    name = f"{PREFIX}dummy_int[28]"
    cybro = Cybro(HOST, nad=NAD)
    await cybro.update()
    assert await cybro.read_var(name) == "0"
    assert await cybro.write_var(name, "123") == "123"
    await cybro.disconnect()

    assert server.values[name] == "123"
    assert server.raw_queries[-2:] == [name, f"{name}=123"]


@pytest.mark.asyncio
async def test_array_element_add_var(server: FakeScgiServer) -> None:
    """add_var() accepts elements of arrays listed in the ALC file."""
    name = f"{PREFIX}dummy_int[28]"
    cybro = Cybro(HOST, nad=NAD)
    await cybro.update()
    cybro.add_var(name)
    cybro.add_var(f"{PREFIX}unknown_array[1]")  # not in the ALC file, ignored
    server.values[name] = "7"
    device = await cybro.update()
    await cybro.disconnect()

    assert list(device.user_vars) == [name]
    assert device.vars[name].value_int() == 7


@pytest.mark.parametrize(
    ("data", "query"),
    [
        (None, ""),
        ({}, ""),
        ("c1.a", "c1.a"),
        ("c1.a&c1.b=1", "c1.a&c1.b=1"),
        ("c1.dummy_int[28]", "c1.dummy_int[28]"),
        ({"c1.a": "", "c1.b": "1"}, "c1.a&c1.b=1"),
        ({"c1.dummy_int[28]": 123}, "c1.dummy_int[28]=123"),
        ({"c1.text": "a b&c"}, "c1.text=a%20b%26c"),
    ],
)
def test_build_query(data: dict | str | None, query: str) -> None:
    """The query keeps names and brackets readable and encodes values."""
    assert _build_query(data) == query


# --- session handling ---


@pytest.mark.asyncio
async def test_own_session_is_used(server: FakeScgiServer) -> None:
    """A session passed in is used for requests."""
    async with aiohttp.ClientSession() as session:
        cybro = Cybro(HOST, nad=NAD, session=session)
        await cybro.request(data="sys.server_version")

        assert cybro.session is session


@pytest.mark.asyncio
async def test_own_session_is_not_closed(server: FakeScgiServer) -> None:
    """A session passed in is left open by disconnect() and async with."""
    async with aiohttp.ClientSession() as session:
        async with Cybro(HOST, nad=NAD, session=session) as cybro:
            await cybro.request(data="sys.server_version")
        await cybro.disconnect()

        assert not session.closed
        assert cybro.session is session


@pytest.mark.asyncio
async def test_async_with_closes_session(server: FakeScgiServer) -> None:
    """Leaving async with closes the session created by the library."""
    async with Cybro(HOST, nad=NAD) as cybro:
        await cybro.request(data="sys.server_version")
        session = cybro.session

    assert session.closed
    assert cybro.session is None


@pytest.mark.asyncio
async def test_new_session_after_disconnect(server: FakeScgiServer) -> None:
    """After disconnect(), the next request opens a new session."""
    cybro = Cybro(HOST, nad=NAD)
    await cybro.request(data="sys.server_version")
    first = cybro.session
    await cybro.disconnect()
    await cybro.request(data="sys.server_version")
    second = cybro.session
    await cybro.disconnect()

    assert first is not second
    assert first.closed
    assert second.closed


@pytest.mark.asyncio
async def test_disconnect_closes_session(server: FakeScgiServer) -> None:
    """disconnect() closes the session created by the library."""
    cybro = Cybro(HOST, nad=NAD)
    await cybro.request(data="sys.server_version")
    session = cybro.session
    await cybro.disconnect()

    assert session.closed
    assert cybro.session is None
