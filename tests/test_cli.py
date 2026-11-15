import asyncio
import shutil
import subprocess
import sys

import pytest

from carrier_profile_tool import cli, device


def test_offline_check(source, capsys):
    assert cli.main(["check", str(source)]) == 3
    output = capsys.readouterr()
    assert "UNVERIFIED" in output.out
    assert "Структура: проверена" in output.out


@pytest.mark.parametrize(
    "args,code", [(["--help"], 0), (["--version"], 0), (["unknown"], 2), (["build"], 2)]
)
def test_parser(args, code, capsys):
    with pytest.raises(SystemExit) as result:
        cli.main(args)
    assert result.value.code == code
    assert "Traceback" not in capsys.readouterr().err


def test_missing_invalid_and_directory(tmp_path, source, capsys):
    assert cli.main(["check", str(tmp_path / "missing.ipcc")]) == 1
    assert cli.main(["check", "\0invalid"]) == 1
    assert cli.main(["check", str(tmp_path)]) == 3
    (tmp_path / "broken.ipcc").write_bytes(b"broken")
    assert cli.main(["check", str(tmp_path)]) == 1
    other = tmp_path / "empty"
    other.mkdir()
    assert cli.main(["check", str(other)]) == 1
    assert "Traceback" not in capsys.readouterr().err


def test_module_and_console_script(source):
    executable = shutil.which("carrier-profile-tool")
    assert executable is not None, (
        "Console script carrier-profile-tool не установлен или не в PATH"
    )
    for prefix in ([sys.executable, "-m", "carrier_profile_tool"], [executable]):
        result = subprocess.run(prefix + ["check", str(source)], capture_output=True)
        assert result.returncode == 3
        assert b"UNVERIFIED" in result.stdout


def test_info_privacy(monkeypatch, values, capsys):
    lines, problem = device.parse_lines(values["CarrierBundleInfoArray"])

    async def fake():
        return device.Device("SyntheticPhone", "27.2.1", "Test", lines, problem)

    monkeypatch.setattr(device, "read_device", fake)
    assert cli.main(["info"]) == 0
    output = capsys.readouterr()
    assert "MNC 01" in output.out
    for row in values["CarrierBundleInfoArray"]:
        assert row[device.IMSI_KEY] not in output.out + output.err
        assert row[device.ICCID_KEY][-4:] not in output.out + output.err


def test_cancel_and_secret_error(monkeypatch, capsys):
    async def cancel(args):
        raise asyncio.CancelledError()

    monkeypatch.setattr(cli, "device_command", cancel)
    assert cli.main(["info"]) == 1

    async def failure(args):
        raise RuntimeError("synthetic-secret-do-not-print")

    monkeypatch.setattr(cli, "device_command", failure)
    assert cli.main(["info"]) == 1
    assert "synthetic-secret-do-not-print" not in str(capsys.readouterr())


def test_offline_check_does_not_import_device(source, monkeypatch):
    import builtins

    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith("pymobiledevice3"):
            pytest.fail("Offline check импортирует библиотеку устройства")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert cli.main(["check", str(source)]) == 3


def test_utf8_with_cp1252_environment(source):
    import os

    environment = os.environ.copy()
    environment.update(PYTHONIOENCODING="cp1252", PYTHONUTF8="0")
    result = subprocess.run(
        [sys.executable, "-m", "carrier_profile_tool", "check", str(source)],
        capture_output=True,
        env=environment,
    )
    assert result.returncode == 3, result.stderr.decode("utf-8", errors="replace")
    assert "UNVERIFIED" in result.stdout.decode("utf-8")
    result.stderr.decode("utf-8")
