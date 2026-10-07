# python-cybro

[![GitHub Release][releases-shield]][releases]
[![PyPI][pypi-shield]][pypi]
[![Python Versions][python-shield]][pypi]
[![License][license-shield]](LICENSE)

[![GitHub Activity][commits-shield]][commits]
[![Code Coverage][codecov-shield]][codecov]
[![pre-commit][pre-commit-shield]][pre-commit]
[![Black][black-shield]][black]

[![Project Maintenance][maintenance-shield]][user_profile]

Asynchronous Python client for the [Cybrotech](https://www.cybrotech.com) scgi server.
Use it to read and write variables of Cybro / HIQ PLCs through the scgi server's HTTP interface.

## Requirements

- Python 3.11 or newer
- A running Cybrotech scgi server **v3.2.6** (earlier versions are not supported).
  You can install the server natively, or run it as a Docker container:
  [![dockerhub][scgi-docker-shield]][scgi-docker]

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

    cybro = Cybro("192.168.1.100", port=4000, nad=nad)
    try:
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
    finally:
        await cybro.disconnect()


asyncio.run(main())
```

A longer example lives in [examples/control.py](examples/control.py).

### Connecting

`Cybro(host, port=4000, nad=0, session=None)`

- `host` can be a plain host name or IP address, or a URL with a path,
  e.g. `http://example.com/scgi`. The scheme is ignored; requests always use HTTP.
- `nad` is the network address of the PLC. With `nad=0`, only server information is read.
- `session` lets you pass your own `aiohttp.ClientSession`. Otherwise one is created
  on the first request. Call `await cybro.disconnect()` to close it.
  Leaving an `async with Cybro(...)` block does **not** close the session.

### Reading data

`await cybro.update(full_update=False, plc_nad=0, device_type=0)` returns a `Device`:

| Attribute            | Content                                                                  |
| -------------------- | ------------------------------------------------------------------------ |
| `device.server_info` | Server data: version, uptime, request counters, active NADs (`nad_list`) |
| `device.plc_info`    | PLC data: IP/port, status, response time, ALC file, available variables  |
| `device.vars`        | All read variables by name (`Var` objects)                               |
| `device.user_vars`   | Variables registered with `add_var()`                                    |

- The first call (or `full_update=True`) reads server and PLC information.
  Later calls only refresh the variables registered with `add_var()`.
- Set `device_type=1` for HIQ controllers to also read their HIQ-specific variables.

### Variables

- `cybro.add_var(name)` registers a variable for `update()`. Only variables listed
  in the PLC's ALC file and system variables (`c<nad>.sys.*`) are accepted;
  pass `allow_all=True` to add any name.
- `cybro.remove_var(name)` removes it again.
- `await cybro.read_var(name)` and `await cybro.write_var(name, value)` read or
  write a single variable immediately.

Values are returned as strings. Each `Var` in `device.vars` has helpers to convert them:
`value_int()`, `value_float()` and `value_bool()`.

### Errors

The library raises `CybroError` and its subclasses `CybroConnectionError` and
`CybroConnectionTimeoutError`. Failed requests are retried up to three times
before an exception is raised.

## Development

The project uses [Poetry](https://python-poetry.org).
A ready-to-use [dev container](.devcontainer) is included for VS Code.

```bash
poetry install
poetry run pytest
poetry run pre-commit install  # run linters before every commit
```

## Contributing

Contributions are welcome! Please read the [contribution guidelines](CONTRIBUTING.md) first.

## License

[MIT](LICENSE)

---

[black]: https://github.com/psf/black
[black-shield]: https://img.shields.io/badge/code%20style-black-000000.svg?style=for-the-badge
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
[user_profile]: https://github.com/killer0071234
[scgi-docker-shield]: https://img.shields.io/badge/dockerhub-cybroscgiserver-brightgreen.svg?style=for-the-badge
[scgi-docker]: https://hub.docker.com/r/killer007/cybroscgiserver
