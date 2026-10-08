# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Updates of development tools and GitHub Actions are not listed one by one; see the
[GitHub releases](https://github.com/killer0071234/python-cybro/releases) for all
merged pull requests.

## [Unreleased]

### Fixed

- `read_var_int()`, `read_var_float()` and `read_var_bool()` return `int`, `float`
  and `bool` instead of the raw string. With a `var_type`, `write_var()` returns the
  converted value too. Unknown variables and values that cannot be converted are
  returned as `"?"`. `Device.vars` still holds the raw string. ([#334])

### Changed

- Development tools and GitHub Actions updated; CI runs again on Python 3.11 to
  3.14. ([#330], [#335])

## [0.4.1] - 2026-10-08

### Fixed

- `update()` no longer fills `Device.vars` with `value=None` entries. The scgi server
  answers `c<nad>.sys.variables` with name, type and description of every PLC
  variable, but without a value. These entries replaced values read in the same
  request, e.g. `lc00_general_error`. ([#329])

### Added

- `Device.var_info` holds type and description of every PLC variable as `VarInfo`
  objects. A read variable without description takes it from there. ([#329])

### Changed

- A PLC variable that is listed by the scgi server but was never read is no longer
  in `Device.vars`; look it up in `Device.var_info` instead. ([#329])

## [0.4.0] - 2026-10-07

### Added

- Array elements, e.g. `c1000.dummy_int[28]`, can be read, written and registered
  with `add_var()`. ([#326])
- `Cybro` can be used with `async with`. A session passed to `Cybro()` is not closed
  by `disconnect()`. ([#327])
- `CybroEmptyResponseError` and `CybroPlcNotFoundError` are exported; all exceptions
  derive from `CybroError`. ([#311])
- Tested with scgi server v3.3.1. ([#328])

### Changed

- **Breaking:** Python 3.11 or newer is required (3.11 to 3.14 are supported).
  ([#308])
- Connection errors, timeouts and server errors (HTTP 5xx) are retried up to three
  times; client errors (HTTP 4xx) are not retried. ([#327])
- `add_var()` without NAD only accepts system variables (`c<nad>.sys.*`), unless
  `allow_all=True` is passed. Methods that need data from `update()` raise
  `CybroError` if it was not called yet. ([#311])
- `xmltodict` 1.x is allowed; `async-timeout` is no longer a dependency. ([#327],
  [#328])

### Fixed

- A single variable registered with `add_var()` (or one left in the last chunk of 25) got no value. ([#325])
- JSON error responses with a charset in the `Content-Type` header were not
  recognised. ([#325])
- `ServerInfo.nad_list` is always a list, also with one or no controllers. ([#325])
- Each `Device` has its own variable lists; they were shared between instances.
  A full update refreshes the PLC information. ([#310])

## [0.3.1] - 2025-01-06

### Changed

- Version bump only, no changes to the library. ([#286])

## [0.3.0] - 2025-01-06

### Changed

- **Breaking:** Requires scgi server v3.2.6; earlier versions are not supported.
  ([#279])
- **Breaking:** `ServerInfo`: `scgi_port_status`, `scgi_request_pending`,
  `datalogger_status` and `abus_list` are removed, and `nad_list` is a list.
  `PlcInfo`: `plc_program_status` is renamed to `plc_status` and
  `comm_error_count` to `com_error_count`. ([#279])

## [0.2.0] - 2024-05-16

### Added

- `add_var()` checks that the variable exists in the PLC's ALC file. Pass
  `allow_all=True` to add any name. ([#244])

## [0.1.3] - 2024-03-20

### Changed

- Dependency updates only.

## [0.1.2] - 2023-11-29

### Fixed

- Errors were caught with an exception class that does not derive from
  `BaseException`. ([#164]) This fix was first released as pre-release 0.1.1a.

## [0.1.0] - 2023-11-16

### Added

- HVAC tags for HIQ controllers. ([#146])

### Fixed

- System variables are requested in chunks; too many in one request gave no valid
  result. ([#142])

## [0.0.9] - 2023-05-17

### Added

- RGB mode tags for HIQ controllers. ([#97])

## [0.0.8] - 2022-09-27

### Added

- `power_meter_error` tag for HIQ controllers. ([#35])

### Fixed

- Parsing of `PlcInfo.from_dict()`. ([#22])

## [0.0.7] - 2022-09-15

### Added

- Default tags for HIQ controllers. ([#18])

## [0.0.6] - 2022-08-15

### Changed

- Variables are requested in chunks of 25.

[Unreleased]: https://github.com/killer0071234/python-cybro/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/killer0071234/python-cybro/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/killer0071234/python-cybro/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/killer0071234/python-cybro/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/killer0071234/python-cybro/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/killer0071234/python-cybro/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/killer0071234/python-cybro/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/killer0071234/python-cybro/compare/v0.1.0...v0.1.2
[0.1.0]: https://github.com/killer0071234/python-cybro/compare/v0.0.9...v0.1.0
[0.0.9]: https://github.com/killer0071234/python-cybro/compare/v0.0.8...v0.0.9
[0.0.8]: https://github.com/killer0071234/python-cybro/compare/v.0.0.7...v0.0.8
[0.0.7]: https://github.com/killer0071234/python-cybro/compare/v0.0.6...v.0.0.7
[0.0.6]: https://github.com/killer0071234/python-cybro/releases/tag/v0.0.6
[#18]: https://github.com/killer0071234/python-cybro/pull/18
[#22]: https://github.com/killer0071234/python-cybro/pull/22
[#35]: https://github.com/killer0071234/python-cybro/pull/35
[#97]: https://github.com/killer0071234/python-cybro/pull/97
[#142]: https://github.com/killer0071234/python-cybro/pull/142
[#146]: https://github.com/killer0071234/python-cybro/pull/146
[#164]: https://github.com/killer0071234/python-cybro/pull/164
[#244]: https://github.com/killer0071234/python-cybro/pull/244
[#279]: https://github.com/killer0071234/python-cybro/pull/279
[#286]: https://github.com/killer0071234/python-cybro/pull/286
[#308]: https://github.com/killer0071234/python-cybro/pull/308
[#310]: https://github.com/killer0071234/python-cybro/pull/310
[#311]: https://github.com/killer0071234/python-cybro/pull/311
[#325]: https://github.com/killer0071234/python-cybro/pull/325
[#326]: https://github.com/killer0071234/python-cybro/pull/326
[#327]: https://github.com/killer0071234/python-cybro/pull/327
[#328]: https://github.com/killer0071234/python-cybro/pull/328
[#329]: https://github.com/killer0071234/python-cybro/pull/329
[#330]: https://github.com/killer0071234/python-cybro/pull/330
[#334]: https://github.com/killer0071234/python-cybro/pull/334
[#335]: https://github.com/killer0071234/python-cybro/pull/335
