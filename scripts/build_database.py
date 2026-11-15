"""Сборка базы профилей profiles.json. Запускается вручную, в программу не входит.

Что делает:
1. Берёт пакет Vodafone_hu 69.1 с серверов Apple (файл .ipcc, скачанный заранее
   командой `fetch` или вручную по адресу из APPLE_URL).
2. Берёт профили из локальных копий публичных репозиториев на закреплённых коммитах:
   дамп (DUMP_COMMIT), снимок Vodafone_hu 72.0 для платы D23 (DUMP_OLD_COMMIT) и
   снимок Vodafone_hu 72.0 для плат V53_V54_V57 (VCT_COMMIT).
3. Проверяет подписи .der.pri3 каждого пакета (ECDSA P-384, SHA-384) закреплённым
   открытым ключом. Если хоть одна подпись не сошлась, сборка останавливается.
4. Записывает profiles.json (схема 1) в указанный файл.

Подписи проверяются только здесь. Программа при установке сверяет SHA-256 из базы.
Подпись common.plist не проверяется: что именно она подписывает, не установлено.
Поле tested остаётся пустым, пока профиль не проверен записью на реальный iPhone.

Нужен пакет cryptography (он уже ставится вместе с pymobiledevice3).

Пример:
    python scripts/build_database.py --apple-ipcc Vodafone_hu_69.1.ipcc \\
        --dump-dir путь/к/копии/дампа/на/DUMP_COMMIT \\
        --dump-old-dir путь/к/копии/дампа/на/DUMP_OLD_COMMIT \\
        --vct-dir путь/к/копии/второго/репозитория/на/VCT_COMMIT \\
        --out src/carrier_profile_tool/profiles.json
"""
import argparse
import hashlib
import io
import json
import plistlib
import subprocess
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
# Тот же репозиторий, снимок с Vodafone_hu 72.0 для платы D23 (iPhone Air).
DUMP_OLD_COMMIT = "56497ccb845ad1b06a26b9290f82741d058fc650"
# Vodafone_hu 72.0 для плат V53_V54_V57 (iPhone 17, 17 Pro, 17 Pro Max), оригинальные
# бинарные plist из прошивки iOS 27.0 (24A437).
VCT_REPO = "VCTGomes/ios-carrier-bundle"
VCT_COMMIT = "b4566ef306ea93ccdd3668970ea85944a9af8b79"
VCT_BUNDLE_PATH = "carrier-bundles/27.0-24A437.V53_V54_V57OS/Vodafone_hu.bundle"

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
APPLE_GROUPS = ["G1", "G2", "G3", "G4", "G7"]
DUMP_DONORS = ["Vodafone_hu", "Vodafone_ro", "MTS_ua"]
DUMP_STOCK = ["MTS_ru", "MegaFon_ru", "Tele2_ru", "VimpelCom_ru"]

# Короткие пояснения для меню, простыми словами.
NOTES = {
    "Vodafone_hu": "Профиль оператора из Венгрии.",
    "Vodafone_ro": "Профиль оператора из Румынии. Экспериментальный.",
    "MTS_ua": "Профиль оператора из Украины. Экспериментальный.",
    "MTS_ru": "Стандартный профиль оператора.",
    "MegaFon_ru": "Стандартный профиль оператора.",
    "Tele2_ru": "Стандартный профиль оператора.",
    "VimpelCom_ru": "Стандартный профиль оператора.",
}


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
        "note": NOTES["Vodafone_hu"],
        "source": {
            "type": "ipcc",
            "url": APPLE_URL,
            "size": len(data),
            "sha256": digest(data),
            "sha1": digest(data, "sha1"),
            "sha384": digest(data, "sha384"),
        },
    }


def check_commit(folder: Path, commit: str):
    """Если папка - копия git-репозитория, проверяет, что она стоит на нужном коммите."""
    if not (folder / ".git").exists():
        return
    head = subprocess.run(
        ["git", "-C", str(folder), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    if head != commit:
        raise SystemExit(f"Папка {folder} стоит на коммите {head}, нужен {commit}.")


def files_profile(key, bundle: Path, repo: str, commit: str, repo_path: str, kind: str,
                  groups: list[str], note: str, profile_id: str | None = None,
                  require_signature: bool = False) -> dict:
    """Профиль из файлов папки bundle; repo_path - путь этой папки внутри репозитория."""
    name = bundle.name.removesuffix(".bundle")
    entries, files = [], {}
    for path in sorted(bundle.rglob("*")):
        if path.is_file():
            rel = path.relative_to(bundle).as_posix()
            data = path.read_bytes()
            files[rel] = data
            entries.append({
                "path": f"{repo_path}/{rel}",
                "size": len(data),
                "sha256": digest(data),
            })
    checked = verify_signatures(key, files)
    info = plistlib.loads(files["Info.plist"])
    version = info["CFBundleVersion"]
    print(f"{name} {version}: подписей проверено {checked}")
    if require_signature and checked == 0:
        raise SystemExit(f"{name} {version}: нет ни одной проверенной подписи.")
    return {
        "id": profile_id or f"{name.lower()}-{version}",
        "operator": name,
        "bundle_id": info["CFBundleIdentifier"],
        "version": version,
        "kind": kind,
        "groups": groups,
        "note": note,
        "source": {
            "type": "files",
            "repo": repo,
            "commit": commit,
            "bundle_name": f"{name}.bundle",
            "files": entries,
        },
    }


def dump_profile(key, dump_dir: Path, name: str, kind: str, groups: list[str], note: str) -> dict:
    return files_profile(
        key, dump_dir / "Carrier Bundles" / f"{name}.bundle", DUMP_REPO, DUMP_COMMIT,
        f"Carrier Bundles/{name}.bundle", kind, groups, note,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Сборка profiles.json")
    parser.add_argument("--apple-ipcc", type=Path, required=True,
                        help="файл Vodafone_hu .ipcc с серверов Apple")
    parser.add_argument("--dump-dir", type=Path, required=True,
                        help="папка с копией дампа на коммите DUMP_COMMIT")
    parser.add_argument("--dump-old-dir", type=Path, required=True,
                        help="папка с копией того же дампа на коммите DUMP_OLD_COMMIT")
    parser.add_argument("--vct-dir", type=Path, required=True,
                        help="папка с копией второго репозитория на коммите VCT_COMMIT")
    parser.add_argument("--out", type=Path, required=True, help="куда записать profiles.json")
    args = parser.parse_args(argv)

    check_commit(args.dump_dir, DUMP_COMMIT)
    check_commit(args.dump_old_dir, DUMP_OLD_COMMIT)
    check_commit(args.vct_dir, VCT_COMMIT)
    key = load_key()
    all_groups = list(GROUPS)
    profiles = [apple_profile(key, args.apple_ipcc)]
    profiles.append(files_profile(
        key, args.vct_dir / VCT_BUNDLE_PATH, VCT_REPO, VCT_COMMIT, VCT_BUNDLE_PATH, "donor",
        ["G5"], NOTES["Vodafone_hu"], "vodafone_hu-72.0-g5", require_signature=True,
    ))
    profiles.append(files_profile(
        key, args.dump_old_dir / "Carrier Bundles" / "Vodafone_hu.bundle", DUMP_REPO,
        DUMP_OLD_COMMIT, "Carrier Bundles/Vodafone_hu.bundle", "donor",
        ["G6"], NOTES["Vodafone_hu"], "vodafone_hu-72.0-g6", require_signature=True,
    ))
    new_groups = ["G8", "G9"]
    for name in DUMP_DONORS:
        profiles.append(dump_profile(key, args.dump_dir, name, "donor", new_groups, NOTES[name]))
    for name in DUMP_STOCK:
        profiles.append(dump_profile(key, args.dump_dir, name, "stock", all_groups, NOTES[name]))

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
