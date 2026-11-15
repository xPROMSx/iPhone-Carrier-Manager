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


@pytest.fixture
def profile_data(zip_bytes):
    import hashlib

    payload = zip_bytes()
    return {
        "schema": 1,
        "groups": {"synthetic": {
            "label": "Выдуманная группа",
            "product_types": ["iPhone999,1", "iPhone999,2"],
            "tested": ["iPhone999,1"],
        }},
        "profiles": [{
            "id": "synthetic-stock", "operator": "Выдуманный оператор",
            "bundle_id": "example.target", "version": "2.0", "kind": "stock",
            "groups": ["synthetic"], "note": "Выдуманное примечание",
            "source": {
                "type": "ipcc", "url": "https://updates.cdn-apple.com/synthetic.ipcc",
                "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
            },
        }],
    }


@pytest.fixture
def files_data(profile_data):
    import hashlib

    content = {
        "Info.plist": plistlib.dumps({"CFBundleIdentifier": "example.target",
                                      "CFBundleVersion": "2.0"}),
        "carrier.plist": plistlib.dumps({}),
        "nested/deeper/test.bin": b"synthetic bytes",
    }
    profile_data["profiles"][0]["source"] = {
        "type": "files", "repo": "synthetic-owner/synthetic-repo", "commit": "a" * 40,
        "bundle_name": "Synthetic.bundle", "files": [
            {"path": "Carrier Bundles/Synthetic.bundle/" + name, "size": len(data),
             "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in content.items()
        ],
    }
    return profile_data, content


@pytest.fixture
def network(monkeypatch):
    """Подстановка транспорта: тесты не выполняют реальные сетевые операции."""
    from types import SimpleNamespace

    from carrier_profile_tool import fetch

    state = SimpleNamespace(now=0, urls=[], timeouts=[], bodies={}, step=0, block=65536,
                            error=None, closed=0, opens=0)
    monkeypatch.setattr(fetch.time, "monotonic", lambda: state.now)

    class Response:
        def __init__(self, data):
            self.data = data
            self.position = 0
            self.download_socket = SimpleNamespace(settimeout=state.timeouts.append)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            state.closed += 1

        def isclosed(self):
            return False

        def read1(self, size):
            state.now += state.step
            size = min(size, state.block)
            result = self.data[self.position:self.position + size]
            self.position += len(result)
            return result

    def open_url(url, timeout):
        state.opens += 1
        state.urls.append(url)
        state.timeouts.append(timeout)
        if state.error:
            raise state.error
        return Response(state.bodies[url])

    monkeypatch.setattr(fetch, "_opener", lambda budget: SimpleNamespace(open=open_url))
    return state
