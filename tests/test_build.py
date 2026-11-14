import asyncio
from dataclasses import replace

import pytest

from iphone_carrier_manager import build, device, ipcc
from iphone_carrier_manager.cli import main


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
