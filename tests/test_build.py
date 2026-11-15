import asyncio
from dataclasses import replace

import pytest

from carrier_profile_tool import build, device, ipcc
from carrier_profile_tool.cli import main


@pytest.fixture
def local_device(monkeypatch, rows):
    lines, problem = device.parse_lines(rows)
    result = device.Device("SyntheticPhone", "27.2.1", "TestBuild", lines, problem)

    async def read(selector=None):
        return result

    monkeypatch.setattr(build, "read_device", read)
    return result


@pytest.mark.parametrize("matching", [False, True])
def test_build_in_memory(source, local_device, monkeypatch, tmp_path, matching):
    if matching:
        local_device.lines[0].identifier = "example.target"
    before = set(tmp_path.rglob("*"))
    original_inspect = build.inspect_bytes
    inspected = []

    def inspect(data):
        result = original_inspect(data)
        inspected.append(result)
        return result

    monkeypatch.setattr(build, "inspect_bytes", inspect)
    package, _, line = asyncio.run(build.build_file(source, "slot:kOne"))
    assert inspected
    output = inspected[0]
    assert output.bundle == (
        f"Payload/{line.international_mobile_subscriber_identity}_ID-"
        f"{line.integrated_circuit_card_identity}_ID-.bundle/"
    )
    assert {n[len(package.bundle) :]: b for n, b in package.files.items()} == {
        n[len(output.bundle) :]: b for n, b in output.files.items()
    }
    assert set(tmp_path.rglob("*")) == before


def test_cli_privacy(source, local_device, capsys):
    assert main(["build", "--source", str(source), "--line", "slot:kOne"]) == 0
    output = capsys.readouterr()
    assert "Пробная подготовка выполнена" in output.out
    assert "UNVERIFIED" in output.out
    assert "15 цифр" in output.out and "19 цифр" in output.out
    assert build.WARNING in output.out
    assert "SHA-256" not in output.out
    for line in local_device.lines:
        for secret in (
            line.international_mobile_subscriber_identity,
            line.integrated_circuit_card_identity,
        ):
            assert secret not in output.out + output.err
            # Отличимые фрагменты идентификаторов также не выводятся.
            assert secret[-4:] not in output.out + output.err
            assert secret[:6] not in output.out + output.err


@pytest.mark.parametrize("bad", ["slot:missing", "slot:0", "kOne"])
def test_bad_selector(source, local_device, bad):
    with pytest.raises(device.DeviceError):
        asyncio.run(build.build_file(source, bad))


def test_failure_does_not_leave_output(source, local_device, monkeypatch, tmp_path):
    before = set(tmp_path.rglob("*"))

    def fail(data):
        raise ipcc.PackageError("Синтетический отказ повторной проверки.")

    monkeypatch.setattr(build, "inspect_bytes", fail)
    with pytest.raises(ipcc.PackageError):
        asyncio.run(build.build_file(source, "slot:kOne"))
    assert set(tmp_path.rglob("*")) == before


def test_internal_mismatch(source, local_device, monkeypatch):
    original = build.inspect_bytes

    def changed(data):
        return replace(original(data), version="changed")

    monkeypatch.setattr(build, "inspect_bytes", changed)
    with pytest.raises(ipcc.PackageError, match="не совпали"):
        asyncio.run(build.build_file(source, "slot:kOne"))


@pytest.mark.parametrize("case,code", [("unknown", 3), ("multiple", 3), ("broken", 1)])
def test_build_input_exit_codes(source, zip_bytes, monkeypatch, case, code, capsys):
    async def forbidden(*args):
        pytest.fail("Устройство не должно читаться при отказе исходного IPCC")

    monkeypatch.setattr(build, "read_device", forbidden)
    source.write_bytes(
        b"broken"
        if case == "broken"
        else zip_bytes(
            extra={"Outside/file" if case == "unknown" else "Payload/Second.bundle/file": b"test"}
        )
    )
    assert main(["build", "--source", str(source), "--line", "slot:kOne"]) == code
    assert "Пробная подготовка не выполнена" in capsys.readouterr().err


def test_source_name_never_printed(source, local_device, capsys):
    from carrier_profile_tool.cli import main

    name = "0010-1000-0000-0000-1234.ipcc"  # Выдуманное имя.
    renamed = source.with_name(name)
    source.rename(renamed)
    for args, code in (
        (["check", str(renamed)], 3),
        (["check", str(renamed.parent)], 3),
        (["build", "--source", str(renamed), "--line", "slot:kOne"], 0),
    ):
        assert main(args) == code
        output = capsys.readouterr()
        for fragment in (name, *name.removesuffix(".ipcc").split("-")):
            assert fragment not in output.out + output.err


def test_corrupt_overrides_rejected(source, zip_bytes, monkeypatch, capsys):
    source.write_bytes(zip_bytes(extra={"Payload/Test.bundle/overrides_TEST.plist": b"broken"}))

    async def forbidden(*args):
        pytest.fail("Устройство не должно читаться при битом plist")

    monkeypatch.setattr(build, "read_device", forbidden)
    assert main(["check", str(source)]) == 1
    assert main(["build", "--source", str(source), "--line", "slot:kOne"]) == 1
    assert "plist" in str(capsys.readouterr())


@pytest.mark.parametrize("explicit_directories", [False, True])
def test_prepared_directories_load_with_strict_afc(
    zip_bytes, rows, explicit_directories, monkeypatch, tmp_path
):
    import io
    import posixpath
    import zipfile

    from pymobiledevice3.services.installation_proxy import (
        InstallationProxyService,
        classify_zip_file,
    )

    extra = {"Payload/Test.bundle/signatures/nested/file": b"synthetic"}
    if explicit_directories:
        extra.update(
            {"Payload/": b"", "Payload/Test.bundle/": b"", "Payload/Test.bundle/empty/": b""}
        )
    package = ipcc.inspect_bytes(zip_bytes(extra=extra))
    lines, _ = device.parse_lines(rows)
    before = set(tmp_path.rglob("*"))
    data = build.prepare(package, lines[0])
    assert set(tmp_path.rglob("*")) == before
    assert classify_zip_file(data).is_ipcc()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        assert names[0].endswith(".bundle/")
        assert names[0] != "Payload/"
        assert all(name.endswith("/") for name in names[: names.index(names[0] + "Info.plist")])

    class StrictAFC:
        def __init__(self):
            self.directories = set()
            self.files = {}

        async def makedirs(self, name):
            while name and name not in self.directories:
                self.directories.add(name)
                name = posixpath.dirname(name)

        async def set_file_contents(self, name, data, progress=None):
            assert posixpath.dirname(name) in self.directories
            self.files[name] = data

    afc = StrictAFC()
    import logging

    service = object.__new__(InstallationProxyService)
    service.logger = logging.getLogger("synthetic-loader")
    asyncio.run(service.upload_ipcc_from_bytes(data, "/synthetic-stage", afc))
    uploaded = {name.split(".bundle/", 1)[1]: content for name, content in afc.files.items()}
    assert uploaded == {
        name[len(package.bundle) :]: content for name, content in package.files.items()
    }
