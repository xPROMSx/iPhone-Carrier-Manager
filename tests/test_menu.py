"""Меню работает с выдуманным телефоном и пакетами без сети и USB."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from carrier_profile_tool import cli, device, menu, profiles
from carrier_profile_tool.ipcc import inspect_bytes


@pytest.fixture
def setup_menu(monkeypatch, rows, zip_bytes):
    lines, problem = device.parse_lines(rows)
    phone = device.Device("iPhone18,1", "27.2.1", "Synthetic", lines[:1], problem)
    target = inspect_bytes(zip_bytes())
    previous = inspect_bytes(zip_bytes(info={
        "CFBundleIdentifier": "example.current", "CFBundleVersion": "1.0",
    }))
    def entry(identifier, version):
        return SimpleNamespace(
            bundle_id=identifier, version=version, operator="Тестовый оператор",
            kind="stock", note="Синтетический", groups=("test",),
        )
    entries = (
        entry(target.identifier, target.version),
        entry(previous.identifier, previous.version),
    )
    database = profiles.ProfileDatabase({
        "test": profiles.Group("Тест", ("iPhone18,1",), ()),
    }, entries)
    reader = AsyncMock(return_value=phone)
    installer = AsyncMock(return_value=0)
    downloader = Mock(side_effect=lambda item: (
        b"", target if item is entries[0] else previous
    ))
    monkeypatch.setattr(menu, "read_device", reader)
    monkeypatch.setattr(menu, "load_database", lambda: database)
    monkeypatch.setattr(menu, "fetch_profile", downloader)
    monkeypatch.setattr(menu, "install_packages", installer)
    return SimpleNamespace(
        phone=phone, lines=lines, target=target, previous=previous,
        database=database, entries=entries, reader=reader,
        installer=installer, downloader=downloader,
    )


def run(answers):
    output = []
    iterator = iter(answers)
    def read(question):
        output.append(question)
        value = next(iterator)
        if isinstance(value, BaseException):
            raise value
        return value
    result = asyncio.run(menu.run_menu(read, output.append))
    return result, "\n".join(output)


def test_success(setup_menu):
    s = setup_menu
    code, output = run(["1", "1", "1", ""])
    assert code == 0
    s.installer.assert_awaited_once_with(s.target, s.previous, "slot:kOne")
    for line in s.lines:
        assert line.international_mobile_subscriber_identity not in output
        assert line.integrated_circuit_card_identity not in output
    assert "не проверялось на этой модели" in output
    assert "Нажмите Enter" in output


def test_multiple_lines(setup_menu):
    s = setup_menu
    s.phone.lines = s.lines
    run(["1", "2", "1", ""])
    s.installer.assert_awaited_once_with(s.target, s.previous, "slot:kTwo")


@pytest.mark.parametrize("answers", [
    ["0"], [""], ["x", "x", "x"], ["1", "0"], ["1", "1", "0"],
    ["1", "1", "3", "0"],
])
def test_exit_before_install(setup_menu, answers):
    run(answers)
    setup_menu.installer.assert_not_awaited()


@pytest.mark.parametrize("error", [EOFError(), KeyboardInterrupt()])
@pytest.mark.parametrize("prefix", [[], ["1"], ["1", "1"]])
def test_cancel(setup_menu, error, prefix):
    code, output = run([*prefix, error])
    assert code == 1
    assert "Операция отменена" in output
    setup_menu.installer.assert_not_awaited()


def test_usb_retry(setup_menu):
    s = setup_menu
    s.reader.side_effect = [RuntimeError("001010000000001"), s.phone]
    _, output = run(["1", "1", "1", "1", ""])
    assert s.reader.await_count == 2
    assert "Apple Devices" in output
    assert "001010000000001" not in output
    s.installer.assert_awaited_once()


def test_download_error(setup_menu):
    s = setup_menu
    s.downloader.side_effect = RuntimeError("secret")
    _, output = run(["1", "1", "1", ""])
    assert "Не удалось скачать" in output
    assert "secret" not in output
    s.installer.assert_not_awaited()


def test_already_installed(setup_menu):
    s = setup_menu
    _, output = run(["1", "1", "2", ""])
    assert "уже установлен" in output
    s.downloader.assert_not_called()
    s.installer.assert_not_awaited()


def test_missing_previous(setup_menu, monkeypatch):
    s = setup_menu
    monkeypatch.setattr(menu, "load_database", lambda: profiles.ProfileDatabase(
        s.database.groups, s.entries[:1],
    ))
    _, output = run(["1", "1", "1", ""])
    assert "Без пакета для возврата" in output
    s.installer.assert_not_awaited()


@pytest.mark.parametrize("bundle", [False, True])
def test_own_packages_without_database(setup_menu, monkeypatch, tmp_path, zip_bytes, bundle):
    import io
    import zipfile

    s = setup_menu
    monkeypatch.setattr(menu, "load_database", lambda: profiles.ProfileDatabase({}, ()))
    if bundle:
        path = tmp_path / "Test.bundle"
        path.mkdir()
        with zipfile.ZipFile(io.BytesIO(zip_bytes())) as archive:
            for name in ("Info.plist", "carrier.plist"):
                (path / name).write_bytes(archive.read("Payload/Test.bundle/" + name))
    else:
        path = tmp_path / "synthetic.ipcc"
        path.write_bytes(zip_bytes())
    previous = tmp_path / "previous.ipcc"
    previous.write_bytes(zip_bytes(info={
        "CFBundleIdentifier": "example.current", "CFBundleVersion": "1.0",
    }))
    code, output = run(["1", "1", "1", str(path), str(previous), ""])
    assert code == 0
    assert "Указать свой файл" in output
    args = s.installer.await_args.args
    assert args[0].identifier == "example.target"
    assert args[1].identifier == "example.current"
    assert args[2] == "slot:kOne"
    s.downloader.assert_not_called()


def test_ambiguous_line(setup_menu):
    setup_menu.phone.line_problem = True
    run(["1", ""])
    setup_menu.installer.assert_not_awaited()


def test_default_and_explicit_menu(monkeypatch):
    mocked = AsyncMock(return_value=0)
    monkeypatch.setattr(menu, "run_menu", mocked)
    assert cli.main([]) == 0
    assert cli.main(["menu"]) == 0
    assert mocked.await_count == 2


def test_welcome_does_not_read_phone(setup_menu):
    assert run(["0"])[0] == 0
    setup_menu.reader.assert_not_awaited()

def test_previous_download_error(setup_menu):
    s = setup_menu
    s.downloader.side_effect = [(b"", s.target), RuntimeError("private")]
    code, output = run(["1", "1", "1", ""])
    assert code == 1
    assert "Не удалось скачать: пакет для возврата" in output
    assert "private" not in output
    s.installer.assert_not_awaited()


def test_invalid_local_package(setup_menu, tmp_path):
    s = setup_menu
    code, output = run(["1", "1", "3", str(tmp_path / "absent.ipcc"), ""])
    assert code == 1
    assert "Операция не выполнена" in output
    s.installer.assert_not_awaited()
