"""Все устройства, линии и пакеты синтетические. USB и службы подменены."""

import asyncio
import copy
import logging
from dataclasses import replace
from types import SimpleNamespace

import pytest

from iphone_carrier_manager import cli, device, install, timeouts


@pytest.fixture
def rig(monkeypatch, values, rows, tmp_path, zip_bytes, capsys):
    import pymobiledevice3.lockdown as lockdown
    import pymobiledevice3.services.diagnostics as diagnostics
    import pymobiledevice3.services.installation_proxy as proxy
    import pymobiledevice3.usbmux as usbmux

    source = tmp_path / "source.ipcc"
    previous = tmp_path / "previous.ipcc"
    source.write_bytes(zip_bytes())
    previous.write_bytes(
        zip_bytes(
            info={
                "CFBundleIdentifier": "example.current",
                "CFBundleVersion": "1.0",
            }
        )
    )
    state = SimpleNamespace(
        install_calls=0,
        restart_calls=0,
        post_reads=0,
        clients=[],
        caches=[],
        events=[],
        install_error=None,
        restart_error=None,
        close_error=False,
        phase="before",
        probes=0,
        detach=True,
        list_errors=False,
        locked=0,
        after=copy.deepcopy(values),
        before=copy.deepcopy(values),
        now=0,
        word="INSTALL",
        tty=True,
        recovery_error=False,
        read_error=False,
        refresh_changed=False,
    )
    serial = "synthetic-serial-secret"
    state.after["CarrierBundleInfoArray"][0].update(
        CFBundleIdentifier="example.target", CFBundleVersion="2.0"
    )
    monkeypatch.setattr(device.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(install.sys, "stdin", SimpleNamespace(isatty=lambda: state.tty))
    monkeypatch.setattr("builtins.input", lambda prompt: state.word)
    monkeypatch.setattr(install, "monotonic", lambda: state.now)

    async def pause(seconds):
        state.now += seconds

    monkeypatch.setattr(install, "pause", pause)
    # Deadlines progress only through the fake clock; wait_for still uses the real loop.
    monkeypatch.setattr(timeouts, "DETACH", 3)
    monkeypatch.setattr(timeouts, "READBACK", 8)

    class Client:
        paired = True

        def __init__(self):
            self.initial = state.phase == "before"
            self.all_values = copy.deepcopy(state.before if self.initial else state.after)

        async def get_value(self, domain=None, key=None):
            state.events.append("read")
            if state.refresh_changed and self.initial:
                self.all_values["CarrierBundleInfoArray"][0]["CFBundleVersion"] = "changed"
            return self.all_values

        async def close(self):
            state.events.append("client.close")
            if state.close_error:
                raise OSError(serial)

    async def factory(**kwargs):
        assert kwargs["autopair"] is False
        assert kwargs["serial"] == serial
        assert kwargs["connection_type"] == "USB"
        assert kwargs["usbmux_address"] == device.ENDPOINT
        cache = kwargs["pairing_records_cache_folder"]
        assert cache.is_dir()
        state.caches.append(cache)
        if state.phase != "before":
            state.post_reads += 1
            if state.locked:
                from pymobiledevice3.exceptions import PasscodeRequiredError, PasswordRequiredError

                state.locked -= 1
                if getattr(state, "password_error", False):
                    raise PasswordRequiredError(serial)
                raise PasscodeRequiredError("locked", serial, "27.2.1")
            if state.read_error:
                raise OSError(serial)
        client = Client()
        state.clients.append(client)
        return client

    async def listing(**kwargs):
        assert kwargs == {"usbmux_address": device.ENDPOINT}
        if state.phase == "restarted":
            state.probes += 1
            if state.list_errors:
                raise OSError(serial)
            if state.probes == 1 and state.detach:
                # A same-serial network entry must not count as USB presence.
                return [SimpleNamespace(serial=serial, is_usb=False)]
        if state.phase == "failed" and state.recovery_error:
            raise OSError(serial)
        return [SimpleNamespace(serial=serial, is_usb=True)]

    class Installation:
        def __init__(self, client):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            state.events.append("installation.close")

        async def install_from_bytes(self, data):
            from pymobiledevice3.services.installation_proxy import classify_zip_file

            assert classify_zip_file(data).is_ipcc()
            assert logging.root.manager.disable == logging.CRITICAL
            state.install_calls += 1
            state.events.append("install")
            state.phase = "failed"
            if state.install_error == "stall":
                await asyncio.Event().wait()
            if state.install_error:
                raise state.install_error
            state.phase = "installed"

    class Diagnostics:
        def __init__(self, client):
            pass

        async def __aenter__(self):
            return self

        async def restart(self):
            state.restart_calls += 1
            state.events.append("restart")
            if state.restart_error:
                raise state.restart_error
            state.phase = "restarted"

        async def close(self):
            state.events.append("diagnostics.close")
            if state.close_error:
                raise OSError(serial)

    monkeypatch.setattr(lockdown, "create_using_usbmux", factory)
    monkeypatch.setattr(usbmux, "list_devices", listing)
    monkeypatch.setattr(proxy, "InstallationProxyService", Installation)
    monkeypatch.setattr(diagnostics, "DiagnosticsService", Diagnostics)

    def run():
        state.code = cli.main(
            [
                "install",
                "--source",
                str(source),
                "--line",
                "slot:kOne",
                "--previous",
                str(previous),
            ]
        )
        state.output = capsys.readouterr()
        text = state.output.out + state.output.err
        for secret in (serial, str(source), str(previous)):
            assert secret not in text
        for row in rows:
            for key in (device.IMSI_KEY, device.ICCID_KEY):
                assert row[key] not in text
        for cache in state.caches:
            assert not cache.exists() or cache == tmp_path / ".pymobiledevice3"
        assert len(set(state.caches)) <= 1
        assert state.install_calls <= 1 and state.restart_calls <= 1
        if state.code == 0:
            assert state.install_calls == state.restart_calls == 1
            assert state.probes > 1 and state.detach and not state.list_errors
        else:
            assert "ПОДТВЕРЖДЕНО (перезагрузка" not in text
        return state.code

    state.run, state.source, state.previous = run, source, previous
    return state


@pytest.mark.parametrize("case", ["success", "same", "approximate", "close", "existing_cache"])
def test_confirmed(rig, case, zip_bytes, tmp_path):
    if case == "same":
        rig.source.write_bytes(rig.previous.read_bytes())
        rig.after = copy.deepcopy(rig.before)
    elif case == "approximate":
        rig.previous.write_bytes(
            zip_bytes(
                info={
                    "CFBundleIdentifier": "example.current",
                    "CFBundleVersion": "0.9",
                }
            )
        )
    elif case == "close":
        rig.close_error = True
    elif case == "existing_cache":
        (tmp_path / ".pymobiledevice3").mkdir()
    assert rig.run() == 0
    assert "ПОДТВЕРЖДЕНО (перезагрузка подтверждена косвенно)" in rig.output.out
    if case == "approximate":
        assert "приблизительный возврат, версия другая" in rig.output.out
    assert rig.events.index("installation.close") < rig.events.index("restart")
    index = rig.events.index("restart")
    assert rig.events[index + 1 : index + 3] == ["diagnostics.close", "client.close"]


@pytest.mark.parametrize("case", ["missing_return", "word", "spaces", "not_tty", "changed"])
def test_preflight_refuses(rig, case):
    if case == "missing_return":
        rig.previous.write_bytes(rig.source.read_bytes())
    elif case == "word":
        rig.word = "install"
    elif case == "spaces":
        rig.word = " INSTALL "
    elif case == "not_tty":
        rig.tty = False
    elif case == "changed":
        rig.refresh_changed = True
    assert rig.run() == 1
    assert rig.install_calls == rig.restart_calls == 0


@pytest.mark.parametrize("which", ["source", "previous"])
@pytest.mark.parametrize("case,code", [("broken", 1), ("unknown", 3)])
def test_package_refusal_before_usb(rig, which, case, code, zip_bytes):
    getattr(rig, which).write_bytes(
        b"broken" if case == "broken" else zip_bytes(extra={"outside/file": b"x"})
    )
    assert rig.run() == code
    assert rig.clients == []


@pytest.mark.parametrize("case", ["prohibited", "other", "timeout", "disconnect", "cancel"])
def test_install_failure_one_read_no_restart(rig, case, rows, monkeypatch):
    from pymobiledevice3.exceptions import AppInstallError

    secret = rows[0][device.ICCID_KEY]
    rig.install_error = {
        "prohibited": AppInstallError("InstallProhibited: /" + secret),
        "other": AppInstallError("Other: /" + secret),
        "timeout": "stall",
        "disconnect": OSError(secret),
        "cancel": asyncio.CancelledError(secret),
    }[case]
    if case == "timeout":
        monkeypatch.setattr(timeouts, "INSTALL", 0.01)
    assert rig.run() == 4
    assert rig.post_reads == 1 and rig.restart_calls == 0
    expected = {
        "prohibited": "телефон запретил установку",
        "other": "телефон отказал в установке",
    }.get(case, "результат установки неизвестен")
    assert expected in rig.output.out


def test_failed_recovery_is_not_retried(rig):
    rig.install_error = OSError("secret")
    rig.recovery_error = True
    assert rig.run() == 4
    assert rig.restart_calls == 0
    assert "Однократное чтение" in rig.output.out


def test_restart_failure(rig):
    rig.restart_error = OSError("secret")
    assert rig.run() == 4
    assert rig.post_reads == rig.probes == 0
    assert "вручную" in rig.output.out and "info" in rig.output.out


@pytest.mark.parametrize(
    "case",
    [
        "no_detach",
        "list_errors",
        "other_line",
        "composition",
        "sim_pair",
        "version",
        "profile",
        "incomplete",
        "unreadable",
    ],
)
def test_unconfirmed(rig, case):
    if case == "no_detach":
        rig.detach = False
    elif case == "list_errors":
        rig.list_errors = True
    elif case == "other_line":
        rig.after["CarrierBundleInfoArray"][1]["CFBundleVersion"] = "changed"
    elif case == "composition":
        rig.after["CarrierBundleInfoArray"].pop()
    elif case == "sim_pair":
        rig.after["CarrierBundleInfoArray"][0][device.ICCID_KEY] = "0" * 22
    elif case == "version":
        rig.after["CarrierBundleInfoArray"][0]["CFBundleVersion"] = "other"
    elif case == "profile":
        rig.after["CarrierBundleInfoArray"][0]["CFBundleIdentifier"] = "example.other"
    elif case == "incomplete":
        del rig.after["CarrierBundleInfoArray"][0]["MNC"]
    elif case == "unreadable":
        rig.read_error = True
    assert rig.run() == 4
    if case in ("no_detach", "list_errors"):
        assert "перезагрузка не обнаружена" in rig.output.out
    if case in ("composition", "sim_pair"):
        assert "состав линий изменился" in rig.output.out
    if case == "version":
        assert "другая версия того же профиля" in rig.output.out
    if case == "other_line":
        assert "ВНИМАНИЕ" in rig.output.out


@pytest.mark.parametrize("password_error", [False, True])
def test_unlock_and_shared_cache(rig, password_error):
    rig.password_error = password_error
    rig.locked = 2
    assert rig.run() == 0
    assert "разблокируйте iPhone" in rig.output.out
    assert rig.post_reads == 3 and len(rig.caches) == 4
    assert len(set(rig.caches)) == 1


@pytest.mark.parametrize("case", ["restart_cancel", "wait_cancel"])
def test_cancel_after_install(rig, monkeypatch, case):
    if case == "restart_cancel":
        rig.restart_error = asyncio.CancelledError()
    else:

        async def cancel(*args):
            raise asyncio.CancelledError()

        monkeypatch.setattr(install, "wait_for_detach", cancel)
    assert rig.run() == 4


def test_reordered_lines_do_not_change_result(rig):
    rig.after["CarrierBundleInfoArray"].reverse()
    assert rig.run() == 0


def test_safe_fields_and_warnings(rig, rows):
    rig.before["ProductType"] = rows[0][device.IMSI_KEY]
    rig.before["ProductVersion"] = "other"
    assert rig.run() == 0
    assert "<скрыто>" in rig.output.out and "iOS отличается" in rig.output.out
    for warning in ("не удаляет", "не гарантирован", "UNVERIFIED", "не трогайте телефон"):
        assert warning in rig.output.out


def test_verify_changed_other_subscriber(rows):
    lines, problem = device.parse_lines(rows)
    before = device.Device("Synthetic", "27.2.1", "Test", lines, problem)
    after = copy.deepcopy(before)
    after.lines[1] = replace(after.lines[1], international_mobile_subscriber_identity="0" * 15)
    package = SimpleNamespace(identifier=lines[0].identifier, version=lines[0].version)
    assert "другая линия изменилась" in install.verify_result(before, after, "slot:kOne", package)


def test_confirmation_eof(rig, monkeypatch):
    def eof(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert rig.run() == 1
    assert rig.install_calls == 0


@pytest.mark.parametrize("entries", ["none", "multiple", "foreign"])
def test_read_same_never_uses_another_phone(monkeypatch, entries, tmp_path):
    async def listing():
        if entries == "none":
            return []
        if entries == "multiple":
            return [SimpleNamespace(serial="original"), SimpleNamespace(serial="another")]
        return [SimpleNamespace(serial="another")]

    async def forbidden(*args):
        pytest.fail("Не должен открываться lockdown")

    monkeypatch.setattr(install, "usb_devices", listing)
    monkeypatch.setattr(device, "open_client", forbidden)
    with pytest.raises(device.DeviceError):
        asyncio.run(install.read_same("original", tmp_path))


def test_real_sigint_during_install(rig, monkeypatch):
    import os
    import signal
    import subprocess
    import sys

    marker = "ICM_SYNTHETIC_SIGINT_TEST"
    if os.environ.get(marker) != "1":
        environment = os.environ.copy()
        environment[marker] = "1"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_install.py::test_real_sigint_during_install",
            ],
            capture_output=True,
            env=environment,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
        return
    # Give asyncio.run its standard SIGINT handler, independent of pytest's handler.
    signal.signal(signal.SIGINT, signal.default_int_handler)

    async def interrupted(client, data):
        rig.install_calls += 1
        rig.phase = "failed"
        signal.raise_signal(signal.SIGINT)
        await asyncio.sleep(0)

    monkeypatch.setattr(install, "install_once", interrupted)
    assert rig.run() == 4
    assert rig.restart_calls == 0 and rig.post_reads == 1
    assert "результат установки неизвестен" in rig.output.out


def test_logs_restored_on_preflight_failure(rig):
    disabled = logging.root.manager.disable
    rig.word = "wrong"
    assert rig.run() == 1
    assert logging.root.manager.disable == disabled
