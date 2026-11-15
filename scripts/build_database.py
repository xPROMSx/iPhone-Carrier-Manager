"""Сборка базы профилей profiles.json. Запускается вручную, в программу не входит.

Что делает:
1. Берёт пакет Vodafone_hu 69.1 с серверов Apple (файл .ipcc, скачанный заранее
   командой `fetch` или вручную по адресу из APPLE_URL).
2. Берёт профили из локальной копии публичного дампа на закреплённом коммите.
3. Проверяет подписи .der.pri3 каждого пакета (ECDSA P-384, SHA-384) закреплённым
   открытым ключом. Если хоть одна подпись не сошлась, сборка останавливается.
4. Записывает profiles.json (схема 1) в указанный файл.

Подписи проверяются только здесь. Программа при установке сверяет SHA-256 из базы.
Подпись common.plist не проверяется: что именно она подписывает, не установлено.
Поле tested остаётся пустым, пока профиль не проверен записью на реальный iPhone.

Нужен пакет cryptography (он уже ставится вместе с pymobiledevice3).

Пример:
    python scripts/build_database.py --apple-ipcc Vodafone_hu_69.1.ipcc \\
        --dump-dir путь/к/ios-carrier-bundles \\
        --out src/iphone_carrier_manager/profiles.json
"""
import argparse
import hashlib
import io
import json
import plistlib
import sys
import zipfile
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

# Открытый ключ (SPKI, DER), которым Apple подписывает .der.pri3.
# SHA-256 от этих байт: e11b9fbd35c8f9798a9a038606ffd0cea24b8fe7daa3c917916892f7ba602112
KEY_SPKI_HEX = (
    "3076301006072a8648ce3d020106052b8104002203620004de7893741a21ef21e5fe21fc58"
    "38034fed5ba8666e83bc29a29501da497727f32ed7796dd138d3b8550e4eef9cf53490059e"
    "615cd260ed0d53f37c781ebd92efdd7c6779afa27cf617269d603a2eb544a61c45dfcd3376"
    "43ebb4f28b548eaa97"
)
KEY_SPKI_SHA256 = "e11b9fbd35c8f9798a9a038606ffd0cea24b8fe7daa3c917916892f7ba602112"

APPLE_URL = (
    "https://updates.cdn-apple.com/20260324/carrierbundles/032-21361/"
    "97A3F062-376F-444C-AFCA-72ABB6109E84/Vodafone_hu_iPhone.ipcc"
)
APPLE_BUNDLE = "Vodafone_hu"
DUMP_REPO = "dwilliamsuk/ios-carrier-bundles"
DUMP_COMMIT = "a434a25c4be1f4319569ccc80f9e70c5be6072aa"

GROUPS = {
    "G1": ("iPhone 14, 14 Plus, 14 Pro, 14 Pro Max",
           ["iPhone14,7", "iPhone14,8", "iPhone15,2", "iPhone15,3"]),
    "G2": ("iPhone 15, 15 Plus, 15 Pro, 15 Pro Max",
           ["iPhone15,4", "iPhone15,5", "iPhone16,1", "iPhone16,2"]),
    "G3": ("iPhone 16, 16 Plus, 16 Pro, 16 Pro Max",
           ["iPhone17,1", "iPhone17,2", "iPhone17,3", "iPhone17,4"]),
    "G4": ("iPhone 16e", ["iPhone17,5"]),
    "G5": ("iPhone 17, 17 Pro, 17 Pro Max",
           ["iPhone18,1", "iPhone18,2", "iPhone18,3"]),
    "G6": ("iPhone Air", ["iPhone18,4"]),
    "G7": ("iPhone 17e", ["iPhone18,5"]),
    "G8": ("iPhone 18 Pro, 18 Pro Max (Global), Duo",
           ["iPhone19,2", "iPhone19,4", "iPhone19,7"]),
    "G9": ("iPhone 18 Pro Max (US)", ["iPhone19,3"]),
}
APPLE_GROUPS = ["G1", "G2", "G3", "G4", "G5", "G6", "G7"]
DUMP_DONORS = ["Vodafone_hu", "Vodafone_ro", "MTS_ua"]
DUMP_STOCK = ["MTS_ru", "MegaFon_ru", "Tele2_ru", "VimpelCom_ru"]


def digest(data: bytes, name: str = "sha256") -> str:
    return hashlib.new(name, data).hexdigest()


def load_key():
    spki = bytes.fromhex(KEY_SPKI_HEX)
    if digest(spki) != KEY_SPKI_SHA256:
        raise SystemExit("Встроенный ключ не совпадает с отпечатком.")
    return serialization.load_der_public_key(spki)


def verify_signatures(key, files: dict[str, bytes]) -> int:
    """Проверяет все .der.pri3 из signatures/overrides_*.plist. Возвращает их число."""
    checked = 0
    for name, data in files.items():
        if not (name.startswith("signatures/overrides_") and name.endswith(".plist")):
            continue
        for entry, signature in plistlib.loads(data).items():
            if not entry.endswith(".der.pri3"):
                continue
            target = files[entry[:-1]]  # соседний файл .der.pri
            try:
                key.verify(signature, target, ec.ECDSA(hashes.SHA384()))
            except InvalidSignature:
                raise SystemExit(f"Подпись не сошлась: {name} / {entry}")
            checked += 1
    return checked


def apple_profile(key, ipcc: Path) -> dict:
    data = ipcc.read_bytes()
    archive = zipfile.ZipFile(io.BytesIO(data))
    prefix = f"Payload/{APPLE_BUNDLE}.bundle/"
    files = {
        n[len(prefix):]: archive.read(n)
        for n in archive.namelist()
        if n.startswith(prefix) and not n.endswith("/")
    }
    checked = verify_signatures(key, files)
    if checked == 0:
        raise SystemExit("В пакете Apple не нашлось ни одной подписи .der.pri3.")
    info = plistlib.loads(files["Info.plist"])
    version = info["CFBundleVersion"]
    print(f"Apple {APPLE_BUNDLE} {version}: подписей проверено {checked}")
    return {
        "id": f"{APPLE_BUNDLE.lower()}-{version}",
        "operator": APPLE_BUNDLE,
        "bundle_id": info["CFBundleIdentifier"],
        "version": version,
        "kind": "donor",
        "groups": APPLE_GROUPS,
        "note": "Пакет с серверов Apple (iOS 26.4+). Для российских SIM не проверялся.",
        "source": {
            "type": "ipcc",
            "url": APPLE_URL,
            "size": len(data),
            "sha256": digest(data),
            "sha1": digest(data, "sha1"),
            "sha384": digest(data, "sha384"),
        },
    }


def dump_profile(key, dump_dir: Path, name: str, kind: str, groups: list[str], note: str) -> dict:
    bundle = dump_dir / "Carrier Bundles" / f"{name}.bundle"
    entries, files = [], {}
    for path in sorted(bundle.rglob("*")):
        if path.is_file():
            rel = path.relative_to(bundle).as_posix()
            data = path.read_bytes()
            files[rel] = data
            entries.append({
                "path": f"Carrier Bundles/{name}.bundle/{rel}",
                "size": len(data),
                "sha256": digest(data),
            })
    checked = verify_signatures(key, files)
    info = plistlib.loads(files["Info.plist"])
    version = info["CFBundleVersion"]
    print(f"{name} {version}: подписей проверено {checked}")
    return {
        "id": f"{name.lower()}-{version}",
        "operator": name,
        "bundle_id": info["CFBundleIdentifier"],
        "version": version,
        "kind": kind,
        "groups": groups,
        "note": note,
        "source": {
            "type": "files",
            "repo": DUMP_REPO,
            "commit": DUMP_COMMIT,
            "bundle_name": f"{name}.bundle",
            "files": entries,
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Сборка profiles.json")
    parser.add_argument("--apple-ipcc", type=Path, required=True,
                        help="файл Vodafone_hu .ipcc с серверов Apple")
    parser.add_argument("--dump-dir", type=Path, required=True,
                        help="папка с копией дампа на закреплённом коммите")
    parser.add_argument("--out", type=Path, required=True, help="куда записать profiles.json")
    args = parser.parse_args(argv)

    key = load_key()
    all_groups = list(GROUPS)
    profiles = [apple_profile(key, args.apple_ipcc)]
    new_groups = ["G8", "G9"]
    for name in DUMP_DONORS:
        note = ("Из публичного дампа iOS 27.2. Для российских SIM не проверялся."
                if name == "Vodafone_hu" else "Экспериментальный. Из публичного дампа iOS 27.2.")
        profiles.append(dump_profile(key, args.dump_dir, name, "donor", new_groups, note))
    for name in DUMP_STOCK:
        profiles.append(dump_profile(
            key, args.dump_dir, name, "stock", all_groups,
            "Штатный профиль оператора из публичного дампа iOS 27.2."))

    database = {
        "schema": 1,
        "groups": {
            gid: {"label": label, "product_types": types, "tested": []}
            for gid, (label, types) in GROUPS.items()
        },
        "profiles": profiles,
    }
    args.out.write_text(json.dumps(database, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Записано профилей: {len(profiles)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
