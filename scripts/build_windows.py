"""Windows onedir, offline smoke и ZIP. Запускать из установленного окружения."""

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from importlib.metadata import distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "CarrierProfileTool"


def smoke(executable):
    """Только EXE, вне checkout; ожидаемый код check равен 3."""
    with tempfile.TemporaryDirectory(prefix="icm-smoke-") as temporary:
        folder = Path(temporary)
        source = folder / "synthetic.ipcc"
        with zipfile.ZipFile(source, "w") as archive:
            for name, data in {
                "Info.plist": {"CFBundleIdentifier": "example.synthetic", "CFBundleVersion": "1.0"},
                "carrier.plist": {},
                "signatures/common.plist": {"CBSignature3": b"synthetic-unverified"},
            }.items():
                archive.writestr("Payload/Test.bundle/" + name, plistlib.dumps(data))
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment["PYTHONUTF8"] = "1"
        environment["ICM_IMPORT_SMOKE"] = "1"
        for args, code in (
            ([], 0), (["--help"], 0), (["--version"], 0), (["check", str(source)], 3),
            (["profiles", "--product", "iPhone18,1"], 0),
        ):
            result = subprocess.run(
                [str(executable), *args],
                cwd=folder,
                env=environment,
                capture_output=True,
                input=b"0\n",
                timeout=60,
            )
            if result.returncode != code or (code == 3 and b"UNVERIFIED" not in result.stdout):
                raise RuntimeError("Offline smoke EXE не пройден.")
            if not args and "1 - продолжить" not in result.stdout.decode("utf-8"):
                raise RuntimeError("Меню EXE не открылось на экране приветствия.")
            if args and args[0] == "profiles" and "Группа:" not in result.stdout.decode("utf-8"):
                raise RuntimeError("База профилей отсутствует в EXE или smoke не пройден.")


def licenses(folder):
    """Сохраняет доступные notices окружения; это не комплект исходников GPL."""
    destination = folder / "licenses"
    destination.mkdir(exist_ok=True)
    inventory = []
    for distribution in distributions():
        name = distribution.metadata.get("Name", "unknown")
        inventory.append(f"{name}=={distribution.version}")
        for entry in distribution.files or ():
            if any(word in entry.name.lower() for word in ("license", "copying", "notice")):
                source = Path(distribution.locate_file(entry))
                if source.is_file():
                    target = destination / name / str(entry).replace("..", "_")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
    (destination / "environment.txt").write_text("\n".join(sorted(inventory)), encoding="utf-8")
    for source in Path(sys.base_prefix).glob("LICENSE*"):
        if source.is_file():
            shutil.copyfile(source, destination / ("Python-" + source.name))


def main():
    if sys.platform != "win32":
        raise RuntimeError("Onedir собирается только на Windows.")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--console",
            "--collect-data=carrier_profile_tool",
            "--runtime-hook",
            str(ROOT / "scripts" / "import_services.py"),
            "--hidden-import=pymobiledevice3.services.installation_proxy",
            "--hidden-import=pymobiledevice3.services.diagnostics",
            "--hidden-import=pymobiledevice3.services.afc",
            "--name",
            NAME,
            "--specpath",
            str(ROOT / "build"),
            "--workpath",
            str(ROOT / "build" / "pyinstaller"),
            "--distpath",
            str(ROOT / "dist"),
            str(ROOT / "src" / "carrier_profile_tool" / "__main__.py"),
        ],
        cwd=ROOT,
        check=True,
    )
    folder = ROOT / "dist" / NAME
    for name in ("LICENSE", "NOTICE", "README.md", "README_EN.md"):
        shutil.copyfile(ROOT / name, folder / name)
    shutil.copyfile(ROOT / "docs" / "VERIFICATION.md", folder / "VERIFICATION.md")
    licenses(folder)
    smoke(folder / (NAME + ".exe"))
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    (folder / "SOURCE-COMMIT.txt").write_text(sha + "\n", encoding="ascii")
    shutil.make_archive(
        str(ROOT / "dist" / f"{NAME}-{sha}"), "zip", root_dir=ROOT / "dist", base_dir=NAME
    )


if __name__ == "__main__":
    main()
