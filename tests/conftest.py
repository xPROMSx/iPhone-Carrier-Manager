"""Все SIM и пакеты здесь выдуманы; файлы создаются только тестами."""

import copy
import io
import plistlib
import zipfile

import pytest


@pytest.fixture
def rows():
    return [
        {
            "Slot": slot,
            "MCC": "001",
            "MNC": "01",
            "InternationalMobileSubscriberIdentity": "00101000000000" + str(index),
            "IntegratedCircuitCardIdentity": "0" * (18 + index) + str(index),
            "CFBundleIdentifier": "example.current",
            "CFBundleVersion": "1.0",
            "kCTPostponementInfoAvailable": "test",
        }
        for index, slot in enumerate(("kOne", "kTwo"))
    ]


@pytest.fixture
def values(rows):
    return {
        "DeviceClass": "iPhone",
        "ProductType": "SyntheticPhone",
        "ProductVersion": "27.2.1",
        "BuildVersion": "SyntheticBuild",
        "CarrierBundleInfoArray": copy.deepcopy(rows),
    }


@pytest.fixture
def zip_bytes():
    def make(extra=None, info=None, fmt=plistlib.FMT_XML, omit=()):
        files = {
            "Payload/Test.bundle/Info.plist": plistlib.dumps(
                info
                if info is not None
                else {
                    "CFBundleIdentifier": "example.target",
                    "CFBundleVersion": "2.0",
                },
                fmt=fmt,
            ),
            "Payload/Test.bundle/carrier.plist": plistlib.dumps({}, fmt=fmt),
            "Payload/Test.bundle/signatures/common.plist": plistlib.dumps(
                {"CBSignature3": b"test"}
            ),
            "Payload/Test.bundle/overrides_TEST.der.pri": b"synthetic bytes",
        }
        files.update(extra or {})
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in files.items():
                if name not in omit:
                    archive.writestr(name, content)
        return buffer.getvalue()

    return make


@pytest.fixture
def source(tmp_path, zip_bytes):
    path = tmp_path / "synthetic.ipcc"
    path.write_bytes(zip_bytes())
    return path
