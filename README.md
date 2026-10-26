# python-cybro

[![GitHub Release][releases-shield]][releases]
[![PyPI][pypi-shield]][pypi]
[![Python Versions][python-shield]][pypi]
[![License][license-shield]](LICENSE)

[![GitHub Activity][commits-shield]][commits]
[![Code Coverage][codecov-shield]][codecov]
[![pre-commit][pre-commit-shield]][pre-commit]
[![Ruff][ruff-shield]][ruff]

[![Project Maintenance][maintenance-shield]][user_profile]

Asynchronous Python client for Cybro / HIQ PLCs by [Cybrotech](https://www.cybrotech.com).
Read and write PLC variables through the scgi server's HTTP interface (`Cybro`), or
talk to the controllers directly over A-bus/UDP without any server (`CybroDirect`).

## Requirements

- Python 3.11 or newer
- For `Cybro`: a running Cybrotech scgi server **v3.2.6 or newer** (tested with v3.2.6
  and v3.3.1; earlier versions are not supported).
  You can install the server natively, or run it as a Docker container:
  [![dockerhub][scgi-docker-shield]][scgi-docker]
- For `CybroDirect`: the controllers in the same broadcast domain (UDP port 8442).
  In Docker, use host networking.

## Installation

```bash
pip install cybro
```

## Usage

```python
import asyncio

from cybro import Cybro


async def main() -> None:
    nad = 10000  # network address (NAD) of the PLC
    prefix = f"c{nad}."

    async with Cybro("192.168.1.100", port=4000, nad=nad) as cybro:
        # The first update reads server and PLC information.
        # It must run before variables can be added, read or written.
        device = await cybro.update()
        print("Server version:", device.server_info.server_version)
        print("Controllers:", device.server_info.nad_list)
        print("PLC status:", device.plc_info.plc_status)

        # Register variables that every following update() should refresh.
        cybro.add_var(f"{prefix}scan_time")
        cybro.add_var(f"{prefix}sys.response_time")

        device = await cybro.update()
        for name in device.user_vars:
            print(name, "->", device.vars[name].value)

        # Read and write single variables.
        print(await cybro.read_var(f"{prefix}lc00_qx00"))
        await cybro.write_var(f"{prefix}lc00_qx00", "1")


asyncio.run(main())
```

A longer example lives in [examples/control.py](examples/control.py).

### Connecting

`Cybro(host_str, port=4000, nad=0, session=None)`

- `host_str` can be a plain host name or IP address, or a URL with a path,
  e.g. `http://example.com/scgi`. The scheme is ignored; requests always use HTTP.
- `nad` is the network address of the PLC. It is needed for PLC information and for `add_var()`.
  With `nad=0` (the default), only server information is read. See [Server only (`nad=0`)](#server-only-nad0).
- `session` lets you pass your own `aiohttp.ClientSession`; you stay responsible
  for closing it. Otherwise one is created on the first request and closed when
  leaving `async with Cybro(...)` or calling `await cybro.disconnect()`.

### Reading data

`await cybro.update(full_update=False, plc_nad=0, device_type=0)` returns a `Device`:

| Attribute            | Content                                                                                 |
| -------------------- | --------------------------------------------------------------------------------------- |
| `device.server_info` | Server data: version, uptime, request counters, active NADs (`nad_list`)                |
| `device.plc_info`    | PLC data: IP/port, status, response time, ALC file, available variables                 |
| `device.vars`        | All read variables by name (`Var` objects); only variables that have been read          |
| `device.var_info`    | Type and description of every PLC variable (`VarInfo` objects), values are not included |
| `device.user_vars`   | Variables registered with `add_var()`                                                   |

- The first call (or `full_update=True`) reads server and PLC information.
  Later calls only refresh the variables registered with `add_var()`.
- Set `device_type=1` for HIQ controllers to also read their HIQ-specific variables.
- `plc_nad` sets the PLC address if none was given to `Cybro()`. It only takes effect
  on the very first `update()` call.

### Server only (`nad=0`)

Without a NAD, `update()` reads only the server information. Use this to check that the
server is reachable, or to list the controllers it knows (`device.server_info.nad_list`).
In this mode:

- `device.plc_info` is `None`.
- `cybro.add_var(name)` only accepts system variables (`c<nad>.sys.*`). Pass `allow_all=True` to register other variables.
- `read_var()` and `write_var()` work with full variable names, e.g. `c10000.scan_time`.

To work with a PLC, create the `Cybro` object with its NAD.

### Variables

- `cybro.add_var(name)` registers a variable for `update()`. Only variables listed
  in the PLC's ALC file and system variables (`c<nad>.sys.*`) are accepted;
  pass `allow_all=True` to add any name.
- `cybro.remove_var(name)` removes it again.
- `await cybro.read_var(name)` and `await cybro.write_var(name, value)` read or
  write a single variable immediately.
- `await cybro.read_var_int(name)`, `read_var_float(name)` and `read_var_bool(name)`
  return the value as `int`, `float` or `bool`. They return `"?"` if the variable is
  unknown or its value cannot be converted.

Values are returned as strings. Each `Var` in `device.vars` has helpers to convert them:
`value_int()`, `value_float()` and `value_bool()`.

### Errors

All exceptions derive from `CybroError`, so catching it is enough:

| Exception                     | Raised when                                                         |
| ----------------------------- | ------------------------------------------------------------------- |
| `CybroConnectionError`        | The scgi server cannot be reached                                   |
| `CybroConnectionTimeoutError` | The scgi server does not answer in time                             |
| `CybroEmptyResponseError`     | The scgi server returns an empty response                           |
| `CybroPlcNotFoundError`       | The PLC information for the NAD is missing                          |
| `CybroError`                  | Any other error, e.g. `add_var()` or `read_var()` before `update()` |

Connection errors, timeouts and server errors (HTTP 5xx) are retried up to three
times before an exception is raised. Client errors (HTTP 4xx) are raised immediately.
Writes are retried too, so after a timeout a write may reach the PLC twice.

## Direct mode (no scgi server)

`CybroDirect` has the same API as `Cybro` and returns the same `Device`, but talks
to the controller directly over A-bus (UDP port 8442). All controllers share one
`AbusTransport` (one UDP socket):

```python
import asyncio

from cybro import CybroDirect
from cybro.abus import AbusTransport
from cybro.abus import scan


async def main() -> None:
    transport = await AbusTransport.create()  # binds UDP 8442
    try:
        for found in await scan(transport):  # who is on the LAN?
            print(found.nad, found.ip, found.status.plc.name)

        cybro = CybroDirect(transport, nad=10000)  # IP found by broadcast
        device = await cybro.update()
        cybro.add_var("c10000.scan_time")
        device = await cybro.update()
        print(device.vars["c10000.scan_time"].value)
        await cybro.write_var("c10000.lc00_qx00", "1")
    finally:
        transport.close()


asyncio.run(main())
```

- `AbusTransport.create()` binds UDP 8442 and raises `OSError` if the port is in use
  (e.g. by an scgi server on the same host). `AbusTransport.create(("0.0.0.0", 0))`
  binds any free port: requests work, but frames the controllers send on their own
  are not received.
- `CybroDirect(transport, nad, ip=None, password=None)`: without `ip`, the controller
  is found by broadcast, and found again after a timeout. A static `ip` is always used.
  `password` is the A-bus password, if the controller has one.
- Every request reads the program head and status first. After a program change,
  the allocation table (ALC) is downloaded again; otherwise it is kept in memory.
- Values are formatted like the scgi server does (e.g. a REAL `-1512.10107421875`).
  `c<nad>.sys.*` values are created by the library; `sys.server_version` is
  `direct <version>`.
- Differences to `Cybro`: a controller that doesn't answer raises
  `CybroConnectionTimeoutError` (there's no server to answer instead). An invalid
  write (unknown variable, value out of range) raises `CybroError`. Requests are not
  retried with backoff; the transport resends each frame up to three times
  (200 ms timeout).
- `scan()` sends READ_STATUS to NAD 0; every controller answers with its NAD, IP and
  status. `locate(transport, nad)` returns the IP of one controller.
- Tests: `cybro.testing.FakeAbusController` is a fake controller on UDP with
  variables, an ALC, an optional password and socket/push frames. Its
  `receive_sockets` take sockets like a PLC program; `sockets` lists the socket
  frames it got.

### Sockets

Cybro sockets are variable lists a PLC program sends (e.g. on change) and
receives. The frame doesn't say which variables it holds, so you pass the
definition from the CyPro program, in the scgi server's syntax
`id; bits; ints; longs` (4-byte slots hold LONG and REAL variables):

```python
SOCKET = "63; test_sock_bool; test_sock_int; test_sock_long, test_sock_real"

cybro = CybroDirect(transport, nad=10000)
await cybro.update()  # loads the ALC: sockets before it are dropped
cybro.add_socket(SOCKET)
cybro.on_socket(lambda message: print(message.socket_id, message.values))
# message.values: {"test_sock_bool": 1, "test_sock_int": 4344, ...}

# send to a receive socket of the PLC program
await cybro.send_socket(
    "64; test_sock_bool; test_sock_int; test_sock_long, test_sock_real",
    {
        "test_sock_bool": 1,
        "test_sock_int": -7,
        "test_sock_long": 70000,
        "test_sock_real": 0.75,
    },
)
# or by type, when the names are unknown (order: bits, ints, longs, reals)
await cybro.send_socket_values(64, bits=[1], ints=[-7], longs=[70000], reals=[0.75])
```

- Received values update `device.vars` right away, formatted like a read, and go to
  the `on_socket()` callbacks as `SocketMessage` (NAD, socket id, values, address).
  Callbacks run in the receive handler and must not block.
- Sockets are only received while the transport is bound to UDP 8442. They are LAN
  broadcasts, so they don't reach a container without host networking.
- **Sockets can be missed** (the test controller skipped some on-change frames), so
  keep calling `update()`.
- Frames from other NADs, with an unknown id, a body that doesn't match the
  definition, or before the first `update()` are dropped.
- Sending checks every value like `write_var()` and needs every variable of the
  definition. It is sent once to the controller's IP (never broadcast); the PLC
  doesn't answer, so a lost frame isn't noticed. The PLC ignores a body whose
  length doesn't match its socket, but can't tell a LONG from a REAL slot.
- `remove_socket(id)` stops one socket, `disconnect()` all of them.
- `cybro.abus.SocketDef`, `decode_socket()`, `encode_socket()` and
  `encode_socket_values()` work on the frame bodies without a controller.
  [examples/sockets.py](examples/sockets.py) shows live socket values.

The A-bus protocol code is ported from the Cybro Edge Toolkit (scgi_server) with
the permission of Cybrotech.

## Development

The project uses [Poetry](https://python-poetry.org).
A ready-to-use [dev container](.devcontainer) is included for VS Code.

```bash
poetry install
poetry run pytest
poetry run pre-commit install  # run Ruff, prettier and the tests before every commit
```

## Contributing

Contributions are welcome! Please read the [contribution guidelines](CONTRIBUTING.md) first.

## License

[MIT](LICENSE)

---

[commits-shield]: https://img.shields.io/github/commit-activity/y/killer0071234/python-cybro.svg?style=for-the-badge
[commits]: https://github.com/killer0071234/python-cybro/commits/main
[codecov-shield]: https://img.shields.io/codecov/c/gh/killer0071234/python-cybro?style=for-the-badge&token=2VFGXXQ4N0
[codecov]: https://codecov.io/gh/killer0071234/python-cybro
[pre-commit]: https://github.com/pre-commit/pre-commit
[pre-commit-shield]: https://img.shields.io/badge/pre--commit-enabled-brightgreen?style=for-the-badge
[license-shield]: https://img.shields.io/github/license/killer0071234/python-cybro.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/badge/maintainer-@killer0071234-blue.svg?style=for-the-badge
[pypi]: https://pypi.org/project/cybro/
[pypi-shield]: https://img.shields.io/pypi/v/cybro.svg?style=for-the-badge
[python-shield]: https://img.shields.io/pypi/pyversions/cybro.svg?style=for-the-badge
[releases-shield]: https://img.shields.io/github/release/killer0071234/python-cybro.svg?style=for-the-badge
[releases]: https://github.com/killer0071234/python-cybro/releases
[ruff]: https://github.com/astral-sh/ruff
[ruff-shield]: https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json&style=for-the-badge
[user_profile]: https://github.com/killer0071234
[scgi-docker-shield]: https://img.shields.io/badge/dockerhub-cybroscgiserver-brightgreen.svg?style=for-the-badge
[scgi-docker]: https://hub.docker.com/r/killer007/cybroscgiserver
