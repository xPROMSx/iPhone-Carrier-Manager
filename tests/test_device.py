import ast
import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from iphone_carrier_manager import device


@pytest.fixture
def connected(monkeypatch, values, tmp_path):
    import pymobiledevice3.lockdown as lockdown
    import pymobiledevice3.usbmux as usbmux

    monkeypatch.setattr(device.Path, "home", lambda: tmp_path)

    class Client:
        paired = True
        closed = False
        all_values = copy.deepcopy(values)

        async def close(self):
            self.closed = True

        async def get_value(self, domain=None, key=None):
            if key is None:
                self.all_values = copy.deepcopy(self.all_values)
                return self.all_values
            return self.all_values.get(key)

    client = Client()
    arguments = {}

    async def factory(**kwargs):
        arguments.update(kwargs)
        assert kwargs["autopair"] is False
        assert kwargs["connection_type"] == "USB"
        assert kwargs["usbmux_address"] == device.ENDPOINT
        return client

    async def listing(**kwargs):
        assert kwargs == {"usbmux_address": device.ENDPOINT}
        return [
            SimpleNamespace(serial="synthetic-only", is_usb=True),
            SimpleNamespace(serial="synthetic-network", is_usb=False),
        ]

    async def forbidden(*args, **kwargs):
        pytest.fail("Вызван изменяющий метод")

    for method in ("pair", "save_pair_record", "set_value", "unpair"):
        monkeypatch.setattr(lockdown.LockdownClient, method, forbidden)
    monkeypatch.setattr(lockdown, "create_using_usbmux", factory)
    monkeypatch.setattr(usbmux, "list_devices", listing)
    return client, arguments


def test_usb_read_and_build_selection(connected):
    client, arguments = connected
    result = asyncio.run(device.read_device("slot:kOne"))
    assert result.lines[1].mnc == "01"
    assert device.choose_line(result, "slot:kOne").slot == "kOne"
    assert client.closed
    assert arguments["pairing_records_cache_folder"].is_dir() is False


def test_existing_cache(connected, monkeypatch, tmp_path):
    (tmp_path / ".pymobiledevice3").mkdir()
    monkeypatch.setattr(device.Path, "home", lambda: tmp_path)
    asyncio.run(device.read_device())
    assert connected[1]["pairing_records_cache_folder"] == tmp_path / ".pymobiledevice3"
    assert (tmp_path / ".pymobiledevice3").is_dir()


@pytest.mark.parametrize(
    "kind", ["none", "multiple", "unpaired", "locked", "service", "cancel", "timeout", "changed"]
)
def test_failures(connected, monkeypatch, kind):
    import pymobiledevice3.lockdown as lockdown
    import pymobiledevice3.usbmux as usbmux
    from pymobiledevice3.exceptions import ConnectionFailedToUsbmuxdError, PasswordRequiredError

    client, _ = connected
    if kind in ("none", "multiple", "service"):

        async def listing(**kwargs):
            if kind == "service":
                raise ConnectionFailedToUsbmuxdError()
            return [] if kind == "none" else [SimpleNamespace(is_usb=True)] * 2

        monkeypatch.setattr(usbmux, "list_devices", listing)
    elif kind == "unpaired":
        client.paired = False
    elif kind in ("locked", "cancel", "timeout"):

        async def factory(**kwargs):
            if kind == "locked":
                raise PasswordRequiredError()
            if kind == "cancel":
                raise asyncio.CancelledError()
            raise TimeoutError()

        monkeypatch.setattr(lockdown, "create_using_usbmux", factory)
    else:

        async def changed(domain=None, key=None):
            client.all_values["CarrierBundleInfoArray"][0]["CFBundleVersion"] = "changed"
            return client.all_values

        client.get_value = changed
    with pytest.raises(device.DeviceError):
        asyncio.run(device.read_device("slot:kOne"))
    if kind in ("unpaired", "changed"):
        assert client.closed


@pytest.mark.parametrize(
    "mutation",
    ["numeric", "duplicate", "short", "empty", "nondigit", "integer", "unicode", "missing_version"],
)
def test_line_rejections(rows, mutation):
    if mutation == "numeric":
        rows[0]["Slot"] = 1
    elif mutation == "duplicate":
        rows[1]["Slot"] = rows[0]["Slot"]
    elif mutation == "short":
        rows[0]["IMSI"] = rows[0].pop(device.IMSI_KEY)
        rows[0]["ICCID"] = rows[0].pop(device.ICCID_KEY)
    elif mutation == "missing_version":
        del rows[0]["CFBundleVersion"]
    else:
        rows[0][device.IMSI_KEY] = {
            "empty": "",
            "nondigit": "test",
            "integer": 1,
            "unicode": "１" * 15,
        }[mutation]
    lines, problem = device.parse_lines(rows)
    with pytest.raises(device.DeviceError):
        device.choose_line(device.Device("x", "27.2.1", "x", lines, problem), "slot:kOne")


def test_no_forbidden_imports():
    root = Path(__file__).resolve().parents[1] / "src"
    forbidden = {"installation_proxy", "afc", "diagnostics"}
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [alias.name for alias in node.names]
            assert not any(forbidden.intersection(name.split(".")) for name in names)


def test_library_autopair_false_does_not_pair():
    from pymobiledevice3.lockdown import LockdownClient

    async def not_paired():
        return False

    async def forbidden(*args, **kwargs):
        pytest.fail("Библиотека попыталась выполнить сопряжение")

    client = SimpleNamespace(
        validate_pairing=not_paired, pair=forbidden, save_pair_record=forbidden
    )
    asyncio.run(LockdownClient._handle_autopair(client, False, None))


def test_wait_for_deadlines(connected, monkeypatch):
    import pymobiledevice3.lockdown as lockdown

    real_wait_for = asyncio.wait_for
    observed = []

    async def wait(awaitable, timeout):
        observed.append(timeout)
        return await real_wait_for(awaitable, timeout=0.001 if timeout == 15 else timeout)

    async def stalled(**kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(device.asyncio, "wait_for", wait)
    monkeypatch.setattr(lockdown, "create_using_usbmux", stalled)
    with pytest.raises(device.DeviceError):
        asyncio.run(device.read_device())
    assert observed == [5, 15]


def test_missing_carrier_and_safe_partial_info(connected):
    client, _ = connected
    del client.all_values["CarrierBundleInfoArray"]
    result = asyncio.run(device.read_device())
    assert result.line_problem and result.lines == []
    assert result.product == "SyntheticPhone"


def test_missing_version_still_shows_safe_information(connected):
    client, _ = connected
    del client.all_values["CarrierBundleInfoArray"][0]["CFBundleVersion"]
    result = asyncio.run(device.read_device())
    assert result.line_problem
    assert result.lines[0].version == "недоступно"


@pytest.mark.parametrize(
    "key,length,valid",
    [
        (device.IMSI_KEY, 9, False),
        (device.IMSI_KEY, 10, True),
        (device.IMSI_KEY, 15, True),
        (device.IMSI_KEY, 16, False),
        (device.ICCID_KEY, 17, False),
        (device.ICCID_KEY, 18, True),
        (device.ICCID_KEY, 22, True),
        (device.ICCID_KEY, 23, False),
    ],
)
def test_identifier_boundaries(rows, key, length, valid):
    rows[0][key] = "0" * length
    lines, problem = device.parse_lines(rows)
    snapshot = device.Device("x", "27.2.1", "x", lines, problem)
    if valid:
        assert device.choose_line(snapshot, "slot:kOne").slot == "kOne"
    else:
        with pytest.raises(device.DeviceError):
            device.choose_line(snapshot, "slot:kOne")
