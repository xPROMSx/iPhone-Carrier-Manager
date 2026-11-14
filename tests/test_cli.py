import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from iphone_carrier_manager import cli, device


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
    for prefix in (
        [sys.executable, "-m", "iphone_carrier_manager"],
        [
            str(
                Path(sys.executable).parent
                / (
                    "iphone-carrier-manager.exe"
                    if sys.platform == "win32"
                    else "iphone-carrier-manager"
                )
            )
        ],
    ):
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
