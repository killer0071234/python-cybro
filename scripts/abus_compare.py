#!/usr/bin/env python3
"""Compare direct A-bus reads with the scgi server (T4/T5 live check).

Run on the host (not in the devcontainer), with the scgi server running.
It uses the library, so install it with its dependencies first:

  python -m pip install -e .        # or: poetry install; poetry run python ...

  abus_compare.py --nad 10000 --scgi http://192.168.10.201:4000
  abus_compare.py --nad 10000 --scgi http://... --all          # all variables
  abus_compare.py --nad 10000 --scgi http://... --write test_var=5

Checks:
  1. The ALC parses and its variable count matches c<nad>.sys.variables.
  2. Every user variable (or all, or the given names) has the same text
     directly and from the scgi server.
  3. --write NAME=VALUE: writes directly, reads back directly and via scgi,
     then restores the old value.

Exit code 0 if everything matches.
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET  # noqa: N817
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cybro.abus.alc import format_value  # noqa: E402
from cybro.abus.alc import parse_alc  # noqa: E402
from cybro.abus.alc import parse_value  # noqa: E402
from cybro.abus.client import AbusClient  # noqa: E402
from cybro.abus.transport import AbusTransport  # noqa: E402

CHUNK = 50  # variables per scgi request


def scgi_get(base: str, names: list[str]) -> dict[str, str | None]:
    """Return name -> value (None if no value) for the names from the scgi server."""
    result: dict[str, str | None] = {}
    for start in range(0, len(names), CHUNK):
        query = "&".join(urllib.parse.quote(n) for n in names[start : start + CHUNK])
        with urllib.request.urlopen(f"{base}/?{query}", timeout=10) as response:  # noqa: S310
            root = ET.fromstring(response.read())  # noqa: S314 - own scgi server
        for var in root.iter("var"):
            result[var.findtext("name")] = var.findtext("value")
    return result


def scgi_count(base: str, nad: int) -> int:
    """Return the number of entries in c<nad>.sys.variables."""
    with urllib.request.urlopen(f"{base}/?c{nad}.sys.variables", timeout=30) as resp:  # noqa: S310
        root = ET.fromstring(resp.read())  # noqa: S314
    return sum(1 for var in root.iter("var") if var.findtext("value") is None)


async def run(args: argparse.Namespace) -> int:
    """Run the checks."""
    transport = await AbusTransport.create(("0.0.0.0", 0), port=args.port)  # noqa: S104
    client = AbusClient(transport, args.nad, ip=args.ip, password=args.password)
    errors = 0
    try:
        status = await client.read_status()
        head = await client.read_head()
        print(f"c{args.nad} at {client.ip}: {status.system.name}, {status.plc.name}")
        print(f"program {head.program_timestamp}, {head.model}, valid={head.valid}")

        start = time.monotonic()
        alc = parse_alc(await client.fetch_alc(head))
        print(f"ALC: {len(alc)} variables in {time.monotonic() - start:.2f} s")
        count = scgi_count(args.scgi, args.nad)
        ok = count == len(alc)
        errors += not ok
        print(f"  scgi sys.variables: {count} -> {'OK' if ok else 'MISMATCH'}")

        if args.names:
            wanted = [alc[name] for name in args.names]
        elif args.all:
            wanted = [var for var in alc.values() if var.data_type is not None]
        else:
            wanted = [var for var in alc.values() if var.user]
        start = time.monotonic()
        direct = await client.read_random(wanted)
        elapsed = (time.monotonic() - start) * 1000
        scgi = scgi_get(args.scgi, [f"c{args.nad}.{var.name}" for var in wanted])
        diffs = 0
        for var in wanted:
            mine = format_value(direct[var.name], var.data_type)
            theirs = scgi.get(f"c{args.nad}.{var.name}")
            if mine != theirs:
                diffs += 1
                print(f"  DIFF {var.name} ({var.data_type.name}): {mine} != {theirs}")
        print(
            f"Read {len(wanted)} variables in {elapsed:.0f} ms: "
            f"{len(wanted) - diffs} equal, {diffs} different"
        )
        errors += diffs

        if args.write:
            name, text = args.write.split("=", 1)
            var = alc[name]
            old = (await client.read_random([var]))[name]
            await client.write_random([(var, parse_value(text, var.data_type))])
            mine = format_value((await client.read_random([var]))[name], var.data_type)
            theirs = scgi_get(args.scgi, [f"c{args.nad}.{name}"])[f"c{args.nad}.{name}"]
            await client.write_random([(var, old)])
            ok = (
                mine
                == theirs
                == format_value(parse_value(text, var.data_type), var.data_type)
            )
            errors += not ok
            print(f"Write {name}={text}: direct {mine}, scgi {theirs}, restored {old}")
            print(f"  -> {'OK' if ok else 'MISMATCH'}")
        print(f"bytes {client.bytes_transferred}, timeouts {client.com_error_count}")
    finally:
        transport.close()
    return 1 if errors else 0


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--nad", type=int, required=True)
    parser.add_argument("--ip", help="controller IP (default: broadcast)")
    parser.add_argument("--password", type=int)
    parser.add_argument("--port", type=int, default=8442, help="controller UDP port")
    parser.add_argument("--scgi", required=True, help="e.g. http://host:4000")
    parser.add_argument("--all", action="store_true", help="all readable variables")
    parser.add_argument("--write", metavar="NAME=VALUE", help="write round trip")
    parser.add_argument("names", nargs="*", help="variable names without cNAD.")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
