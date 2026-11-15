"""Новые команды не меняют установку и не требуют реального телефона или сети."""

import asyncio
import builtins
import hashlib
from types import SimpleNamespace

import pytest

from iphone_carrier_manager import cli, device, fetch, profiles


def use_database(monkeypatch, data):
    database = profiles.parse_database(data)
    monkeypatch.setattr(profiles, "load_database", lambda: database)
    return database


@pytest.mark.parametrize("product,tested", [("iPhone999,1", True), ("iPhone999,2", False)])
def test_profiles_group(profile_data, monkeypatch, capsys, product, tested):
    use_database(monkeypatch, profile_data)
    assert cli.main(["profiles", "--product", product]) == 0
    output = capsys.readouterr().out
    for text in ("synthetic-stock", "Выдуманный оператор", "2.0", "stock", "Выдуманное примечание"):
        assert text in output
    assert ("не проверялось на этой модели" in output) is not tested
    assert "проверялось на этой модели" in output


def test_profiles_unknown_model(profile_data, monkeypatch, capsys):
    use_database(monkeypatch, profile_data)
    assert cli.main(["profiles", "--product", "iPhone998,1"]) == 0
    assert "профилей нет" in capsys.readouterr().out


@pytest.mark.parametrize("args", [["profiles"], ["profiles", "--product", "iPhone999,1"]])
def test_empty_database_no_device(args, monkeypatch, capsys):
    async def forbidden():
        pytest.fail("пустая база не требует телефона")

    monkeypatch.setattr(device, "read_device", forbidden)
    use_database(monkeypatch, {"schema": 1, "groups": {}, "profiles": []})
    assert cli.main(args) == 0
    assert capsys.readouterr().out == "База профилей пуста\n"


def test_profiles_device_read(profile_data, monkeypatch, capsys):
    use_database(monkeypatch, profile_data)

    async def read():
        return SimpleNamespace(product="iPhone999,1")

    monkeypatch.setattr(device, "read_device", read)
    assert cli.main(["profiles"]) == 0
    assert "synthetic-stock" in capsys.readouterr().out


def test_offline_profiles_and_fetch_no_device_import(profile_data, monkeypatch, capsys, tmp_path):
    use_database(monkeypatch, profile_data)
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith("pymobiledevice3") or name == "iphone_carrier_manager.device":
            pytest.fail("команда импортирует модуль устройства")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert cli.main(["profiles", "--product", "iPhone999,1"]) == 0
    assert cli.main(["fetch", "missing", "--out", str(tmp_path / "test.ipcc")]) == 1
    assert "Traceback" not in str(capsys.readouterr())


def test_fetch_success(profile_data, zip_bytes, network, monkeypatch, tmp_path, capsys):
    use_database(monkeypatch, profile_data)
    source = profile_data["profiles"][0]["source"]
    data = zip_bytes()
    network.bodies[source["url"]] = data
    path = tmp_path / "synthetic.ipcc"
    assert cli.main(["fetch", "synthetic-stock", "--out", str(path)]) == 0
    assert path.read_bytes() == data
    output = capsys.readouterr()
    assert "synthetic-stock" in output.out
    assert "2.0" in output.out
    assert hashlib.sha256(data).hexdigest() in output.out
    assert not output.err


def test_fetch_existing_before_download(profile_data, monkeypatch, tmp_path, capsys):
    use_database(monkeypatch, profile_data)
    path = tmp_path / "synthetic.ipcc"
    path.write_bytes(b"keep")
    monkeypatch.setattr(fetch, "fetch_profile", lambda item: pytest.fail("не скачивать"))
    assert cli.main(["fetch", "synthetic-stock", "--out", str(path)]) == 1
    assert "уже существует" in capsys.readouterr().err
    assert path.read_bytes() == b"keep"


@pytest.mark.parametrize(
    "failure,code",
    [
        (profiles.ProfileError("Синтетическая ошибка"), 1),
        (profiles.PackageError("Неизвестная структура", 3), 3),
        (RuntimeError("private-secret"), 1),
        (asyncio.CancelledError(), 1),
    ],
)
def test_fetch_error_codes_no_file(profile_data, monkeypatch, tmp_path, capsys, failure, code):
    use_database(monkeypatch, profile_data)

    def fail(item):
        raise failure

    monkeypatch.setattr(fetch, "fetch_profile", fail)
    path = tmp_path / "synthetic.ipcc"
    assert cli.main(["fetch", "synthetic-stock", "--out", str(path)]) == code
    assert not path.exists()
    output = capsys.readouterr()
    assert "Traceback" not in str(output)
    assert "private-secret" not in str(output)


@pytest.mark.parametrize(
    "args", [["fetch"], ["fetch", "synthetic-stock"], ["profiles", "--unknown"]]
)
def test_argument_errors_keep_code_two(args, capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2
    assert "Traceback" not in capsys.readouterr().err


def test_bad_database_error_before_device(monkeypatch, capsys):
    def broken():
        raise profiles.ProfileError("База профилей: неверная схема.")

    monkeypatch.setattr(profiles, "load_database", broken)
    assert cli.main(["profiles"]) == 1
    output = capsys.readouterr()
    assert "неверная схема" in output.err
    assert "Traceback" not in output.err


def test_fetch_race_keeps_existing_file(profile_data, monkeypatch, tmp_path, capsys):
    use_database(monkeypatch, profile_data)
    path = tmp_path / "synthetic.ipcc"

    def fetched(item):
        path.write_bytes(b"created concurrently")
        return b"new", SimpleNamespace(version="2.0", digest="0" * 64)

    monkeypatch.setattr(fetch, "fetch_profile", fetched)
    assert cli.main(["fetch", "synthetic-stock", "--out", str(path)]) == 1
    assert path.read_bytes() == b"created concurrently"
    assert "уже существует" in capsys.readouterr().err
