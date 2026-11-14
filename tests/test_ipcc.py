import hashlib
import io
import plistlib
import stat
import struct
import warnings
import zipfile

import pytest

from iphone_carrier_manager import ipcc


@pytest.mark.parametrize("fmt", [plistlib.FMT_XML, plistlib.FMT_BINARY])
def test_valid(zip_bytes, fmt):
    data = zip_bytes(fmt=fmt)
    result = ipcc.inspect_bytes(data)
    assert result.identifier == "example.target"
    assert result.version == "2.0"
    assert result.digest == hashlib.sha256(data).hexdigest()
    assert result.signature_status == "UNVERIFIED"
    assert result.signatures == ("signatures/common.plist",)


@pytest.mark.parametrize("name", ["../escape", "/absolute", "a/../b", "C:\\file", "a\x01b"])
def test_unsafe_paths(zip_bytes, name):
    with pytest.raises(ipcc.PackageError, match="путь"):
        ipcc.inspect_bytes(zip_bytes(extra={name: b"test"}))


def test_nul_and_duplicate():
    entry = zipfile.ZipInfo("x")
    entry.orig_filename = "x\0y"
    with pytest.raises(ipcc.PackageError):
        ipcc._check_entries([entry])
    with pytest.raises(ipcc.PackageError, match="Повторяющееся"):
        ipcc._check_entries([zipfile.ZipInfo("same"), zipfile.ZipInfo("same")])


@pytest.mark.parametrize("mode", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR])
def test_special(zip_bytes, mode):
    entry = zipfile.ZipInfo("Payload/Test.bundle/special")
    entry.create_system = 3
    entry.external_attr = (mode | 0o600) << 16
    with pytest.raises(ipcc.PackageError, match="специальная"):
        ipcc.inspect_bytes(zip_bytes(extra={entry: b"target"}))


@pytest.mark.parametrize("which", ["archive", "expanded", "entries"])
def test_limits(zip_bytes, monkeypatch, which):
    monkeypatch.setattr(
        ipcc,
        {"archive": "MAX_ARCHIVE", "expanded": "MAX_EXPANDED", "entries": "MAX_ENTRIES"}[which],
        1,
    )
    with pytest.raises(ipcc.PackageError) as error:
        ipcc.inspect_bytes(zip_bytes())
    assert error.value.code == 3


@pytest.mark.parametrize("mutation", ["encrypted", "compression"])
def test_unsupported_zip(zip_bytes, mutation):
    data = bytearray(zip_bytes())
    local = data.index(b"PK\x03\x04")
    central = data.index(b"PK\x01\x02")
    if mutation == "encrypted":
        struct.pack_into("<H", data, local + 6, 1)
        struct.pack_into("<H", data, central + 8, 1)
    else:
        struct.pack_into("<H", data, local + 8, 99)
        struct.pack_into("<H", data, central + 10, 99)
    with pytest.raises(ipcc.PackageError) as error:
        ipcc.inspect_bytes(data)
    assert error.value.code == 3


def test_crc():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Payload/Test.bundle/Info.plist", b"unique-content")
    data = buffer.getvalue().replace(b"unique-content", b"broken-content")
    with pytest.raises(ipcc.PackageError, match="CRC") as error:
        ipcc.inspect_bytes(data)
    assert error.value.code == 1


@pytest.mark.parametrize("data", [b"not zip", b"PK\x03\x04"])
def test_corrupt(data):
    with pytest.raises(ipcc.PackageError) as error:
        ipcc.inspect_bytes(data)
    assert error.value.code == 1


@pytest.mark.parametrize(
    "extra,omit,code",
    [
        ({"Other/file": b"x"}, (), 3),
        ({"Payload/Other.bundle/file": b"x"}, (), 3),
        ({}, ("Payload/Test.bundle/Info.plist",), 1),
        ({"Payload/Test.bundle/carrier.plist": b"broken"}, (), 1),
        ({"Payload/Test.bundle/signatures/common.plist": b"broken"}, (), 1),
        ({"Payload/Test.bundle/version.plist": plistlib.dumps({"CFBundleVersion": "9"})}, (), 1),
    ],
)
def test_structure(zip_bytes, extra, omit, code):
    with pytest.raises(ipcc.PackageError) as error:
        ipcc.inspect_bytes(zip_bytes(extra=extra, omit=omit))
    assert error.value.code == code


def test_empty_and_no_payload():
    for files in ({}, {"outside": b"x"}):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        with pytest.raises(ipcc.PackageError) as error:
            ipcc.inspect_bytes(buffer.getvalue())
        assert error.value.code == 3


@pytest.mark.parametrize(
    "info",
    [
        {},
        {"CFBundleIdentifier": "x"},
        {"CFBundleVersion": "1"},
        {"CFBundleIdentifier": 1, "CFBundleVersion": "1"},
    ],
)
def test_metadata(zip_bytes, info):
    with pytest.raises(ipcc.PackageError):
        ipcc.inspect_bytes(zip_bytes(info=info))


def test_no_windows_filesystem_rules(zip_bytes):
    extra = {
        "Payload/Test.bundle/CON": b"x",
        "Payload/Test.bundle/a:b": b"x",
        "Payload/Test.bundle/Case": b"x",
        "Payload/Test.bundle/case": b"x",
        "Payload/Test.bundle/file": b"x",
        "Payload/Test.bundle/file/child": b"x",
    }
    assert ipcc.inspect_bytes(zip_bytes(extra=extra)).signature_status == "UNVERIFIED"


def test_duplicate_archive():
    buffer = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("duplicate", b"x")
            archive.writestr("duplicate", b"y")
    with pytest.raises(ipcc.PackageError, match="Повторяющееся"):
        ipcc.inspect_bytes(buffer.getvalue())
