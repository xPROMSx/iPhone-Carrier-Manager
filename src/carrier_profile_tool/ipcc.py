"""Локальное чтение IPCC без извлечения на диск."""

import hashlib
import io
import os
import plistlib
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

MAX_ARCHIVE = 64 * 1024 * 1024
MAX_EXPANDED = 64 * 1024 * 1024
MAX_ENTRIES = 10_000
SIGNATURE_REASON = "правила подписываемых данных и происхождение ключа не подтверждены"


class PackageError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


@dataclass
class Package:
    identifier: str
    version: str
    digest: str
    bundle: str = field(repr=False)
    files: dict[str, bytes] = field(repr=False)
    directories: tuple[str, ...] = field(repr=False)
    signatures: tuple[str, ...] = field(repr=False)
    signature_status: str = "UNVERIFIED"


def _check_entries(entries):
    if len(entries) > MAX_ENTRIES:
        raise PackageError("Превышен предел числа записей ZIP.", 3)
    names = set()
    size = 0
    for entry in entries:
        name = entry.orig_filename
        if (
            not name
            or name.startswith("/")
            or ".." in name.split("/")
            or "\\" in name
            or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in name)
        ):
            raise PackageError("Небезопасный путь в ZIP.")
        if name in names:
            raise PackageError("Повторяющееся имя в ZIP.")
        names.add(name)
        mode = entry.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise PackageError("Ссылка или специальная запись в ZIP.")
        if entry.flag_bits & (1 | 64):
            raise PackageError("Шифрование ZIP не поддерживается.", 3)
        if entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise PackageError("Метод сжатия ZIP не поддерживается.", 3)
        size += entry.file_size
        if size > MAX_EXPANDED:
            raise PackageError("Превышен суммарный распакованный объём ZIP.", 3)


def _plist(data):
    try:
        result = plistlib.loads(data)
        if type(result) is not dict:
            raise ValueError
        return result
    except Exception:
        raise PackageError("Повреждённый plist или неверный тип его корня.") from None


def _metadata(info, key):
    value = info.get(key)
    if type(value) is not str or not value.strip():
        raise PackageError("Отсутствует или неверно задан идентификатор либо версия профиля.")
    return value


def inspect_bytes(data):
    if len(data) > MAX_ARCHIVE:
        raise PackageError("Превышен предел размера IPCC.", 3)
    files = {}
    directories = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            _check_entries(entries)
            total = 0
            for entry in entries:
                chunks = []
                with archive.open(entry) as stream:
                    while chunk := stream.read(min(65536, MAX_EXPANDED - total + 1)):
                        total += len(chunk)
                        if total > MAX_EXPANDED:
                            raise PackageError("Превышен распакованный объём ZIP.", 3)
                        chunks.append(chunk)
                content = b"".join(chunks)
                if entry.is_dir():
                    if content:
                        raise PackageError("Повреждённая запись каталога ZIP.")
                    directories.append(entry.filename)
                else:
                    files[entry.filename] = content
    except PackageError:
        raise
    except Exception:
        raise PackageError("ZIP повреждён или не читается; проверка CRC не завершена.") from None

    names = list(files) + directories
    bundles = {
        n.split("/")[1]
        for n in names
        if n.startswith("Payload/")
        and len(n.split("/")) >= 2
        and n.split("/")[1].endswith(".bundle")
    }
    if len(bundles) != 1:
        raise PackageError("Не поддерживается структура: нужен один bundle в Payload.", 3)
    bundle = "Payload/" + next(iter(bundles)) + "/"
    if any(n != "Payload/" and not n.startswith(bundle) for n in names):
        raise PackageError("Неизвестная структура IPCC вне целевого bundle.", 3)
    for required in ("Info.plist", "carrier.plist"):
        if bundle + required not in files:
            raise PackageError("Нет обязательного Info.plist или carrier.plist.")
    info = _plist(files[bundle + "Info.plist"])
    _plist(files[bundle + "carrier.plist"])
    identifier = _metadata(info, "CFBundleIdentifier")
    version = _metadata(info, "CFBundleVersion")
    if bundle + "version.plist" in files:
        other = _plist(files[bundle + "version.plist"])
        for key in ("CFBundleIdentifier", "CFBundleVersion", "CFBundleShortVersionString"):
            if key in other:
                _metadata(other, key)
                if key in info and other[key] != info[key]:
                    raise PackageError("Несогласованные метаданные профиля.")
    signatures = []
    for name, content in files.items():
        if name.startswith(bundle + "signatures/"):
            signatures.append(name[len(bundle) :])
        if name.startswith(bundle) and name.endswith(".plist"):
            try:
                plistlib.loads(content)
            except Exception:
                raise PackageError("Повреждённый plist внутри bundle.") from None
    return Package(
        identifier,
        version,
        hashlib.sha256(data).hexdigest(),
        bundle,
        files,
        tuple(directories),
        tuple(sorted(signatures)),
    )


def check_file(path):
    try:
        with Path(path).open("rb") as stream:
            before = os.fstat(stream.fileno())
            data = stream.read(MAX_ARCHIVE + 1)
            after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise PackageError("Исходный файл изменился во время чтения.")
    except OSError:
        raise PackageError("IPCC отсутствует или недоступен для чтения.") from None
    try:
        return inspect_bytes(data)
    except PackageError as error:
        if len(data) <= MAX_ARCHIVE:
            error.digest = hashlib.sha256(data).hexdigest()
        raise
