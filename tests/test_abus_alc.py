"""Tests for the allocation table (`cybro.abus.alc`) and its download."""

from __future__ import annotations

import io
import zipfile

import pytest

from src.cybro.abus.alc import AlcVar
from src.cybro.abus.alc import DataType
from src.cybro.abus.alc import parse_alc
from src.cybro.abus.alc import unzip_alc
from src.cybro.abus.client import AbusClient
from src.cybro.abus.transport import AbusTransport
from src.cybro.exceptions import AbusError
from src.cybro.models import PlcInfo
from src.cybro.testing import FakeAbusController
from src.cybro.testing import FakeVar

# lines in the format of real controllers (docs/abus-notes.md), CRLF
ALC_TEXT = (
    ";CPU CyBro-2 32215\r\n"
    ";Addr Id    Array Offset Size Scope  Type  Name                             \r\n"
    "3218  00004 1     0      4    global real  fPowerTotal\r\n"
    "0500  00002 1     0      1    global bit   cybro_qx00                       Output.\r\n"
    "3B76  00006 3     0      2    global int   dummy_int                        Array.\r\n"
    "0600  00000 1     2      4    main   long  main.timer01.et                  Timer.\r\n"
    "0700  00000 1     0      8    global timer main.timer02\r\n"
    "garbage line\r\n"
    "\r\n"
)


def test_parse_alc() -> None:
    """Addresses, types, arrays, user flag and descriptions are parsed."""
    alc = parse_alc(ALC_TEXT)
    assert alc["fPowerTotal"] == AlcVar(
        "fPowerTotal", 0x3218, 4, DataType.REAL, False, "global", ""
    )
    assert alc["cybro_qx00"] == AlcVar(
        "cybro_qx00", 0x0500, 1, DataType.BIT, True, "global", "Output."
    )
    assert [alc[f"dummy_int[{i}]"].address for i in range(3)] == [
        0x3B76,
        0x3B78,
        0x3B7A,
    ]
    assert "dummy_int" not in alc
    assert alc["main.timer01.et"].address == 0x0602  # address + offset
    assert alc["main.timer02"].data_type is None  # kept, but not readable
    assert alc["main.timer02"].size == 8
    assert len(alc) == 7


def test_parse_alc_like_plc_info() -> None:
    """The typed parser knows every name PlcInfo.parse_alc_file() lists."""
    plc_info = PlcInfo(32215, "", "", "", "", 0, 0, ALC_TEXT, {})
    names = {name.removeprefix("c32215.") for name in plc_info.parse_alc_file()}
    base_names = {name.split("[")[0] for name in parse_alc(ALC_TEXT)}
    assert names - {"garbage"} == base_names


def zip_bytes(**files: bytes) -> bytes:
    """Return a zip archive with the given files."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("folder/", b"")
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_unzip_alc() -> None:
    """The first file of the archive is decoded as latin-1."""
    assert unzip_alc(zip_bytes(a="Größe".encode("latin-1"))) == "Größe"


@pytest.mark.parametrize("data", [b"", b"PK\x03\x04broken", zip_bytes()])
def test_unzip_alc_invalid(data: bytes) -> None:
    """Broken or empty archives raise AbusError."""
    with pytest.raises(AbusError, match="alc.zip"):
        unzip_alc(data)


@pytest.mark.asyncio
async def test_fetch_alc_from_fake() -> None:
    """fetch_alc() downloads, unzips and returns the fake's ALC text."""
    variables = [
        FakeVar("program_id", "int", 3, user=True),
        FakeVar("fPowerTotal", "real", -1512.10107421875, description="Power."),
        FakeVar("lc00_qx", "bit", 1, array=4),
    ] + [FakeVar(f"filler_{i:04d}", "long") for i in range(300)]
    fake = await FakeAbusController.start(10000, variables)
    transport = await AbusTransport.create(("127.0.0.1", 0), port=fake.port)
    try:
        client = AbusClient(transport, 10000, ip="127.0.0.1")
        text = await client.fetch_alc()
        assert text == fake.alc_text
        assert len(fake.requests) > 3  # head, file table, several zip segments
        alc = parse_alc(text)
        assert len(alc) == 1 + 1 + 4 + 300
        assert alc["program_id"].user
        assert alc["fPowerTotal"].description == "Power."
        for name, (address, data_type) in fake.addresses.items():
            assert (alc[name].address, alc[name].data_type) == (address, data_type)
        # the format matches the fixed columns PlcInfo.parse_alc_file() uses
        plc_info = PlcInfo(10000, "", "", "", "", 0, 0, text, {})
        assert plc_info.parse_alc_file()["c10000.fPowerTotal"] == "real"
    finally:
        transport.close()
        fake.close()


@pytest.mark.asyncio
async def test_fetch_alc_without_alc_zip() -> None:
    """A controller without alc.zip raises AbusError."""
    fake = await FakeAbusController.start(10000)
    transport = await AbusTransport.create(("127.0.0.1", 0), port=fake.port)
    try:
        client = AbusClient(transport, 10000, ip="127.0.0.1")
        head = await client.read_head()
        for i in range(head.file_count):  # rename every file
            name_at = head.file_system_addr + i * 46
            fake.code[name_at : name_at + 32] = b"other.zip".ljust(32, b"\x00")
        with pytest.raises(AbusError, match="no alc.zip"):
            await client.fetch_alc(head)
    finally:
        transport.close()
        fake.close()
