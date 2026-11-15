"""Пакетная база профилей и её полная проверка до обращения к сети."""

import json
import re
from dataclasses import dataclass
from importlib.resources import files
from urllib.parse import urlsplit

from carrier_profile_tool.ipcc import MAX_ARCHIVE, PackageError

ALLOWED_HOSTS = frozenset(("updates.cdn-apple.com", "raw.githubusercontent.com"))


class ProfileError(PackageError):
    pass


@dataclass(frozen=True)
class Group:
    label: str
    product_types: tuple[str, ...]
    tested: tuple[str, ...]


@dataclass(frozen=True)
class IpccSource:
    url: str
    size: int
    hashes: dict[str, str]


@dataclass(frozen=True)
class SourceFile:
    path: str
    relative_path: str
    size: int
    hashes: dict[str, str]


@dataclass(frozen=True)
class FilesSource:
    repo: str
    commit: str
    bundle_name: str
    files: tuple[SourceFile, ...]


@dataclass(frozen=True)
class Profile:
    id: str
    operator: str
    bundle_id: str
    version: str
    kind: str
    groups: tuple[str, ...]
    note: str | None
    source: IpccSource | FilesSource


@dataclass(frozen=True)
class ProfileDatabase:
    groups: dict[str, Group]
    profiles: tuple[Profile, ...]


def _fail(message):
    raise ProfileError("База профилей: " + message)


def _text(value, field):
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)
    ):
        _fail(f"поле {field} должно быть непустой строкой без управляющих символов.")
    return value


def _object(value, field):
    if not isinstance(value, dict):
        _fail(f"поле {field} должно быть объектом.")
    return value


def _strings(value, field):
    if not isinstance(value, list):
        _fail(f"поле {field} должно быть списком строк.")
    result = tuple(_text(item, field) for item in value)
    if len(set(result)) != len(result):
        _fail(f"поле {field} содержит повторы.")
    return result


def _hex(value, length, field):
    if not isinstance(value, str) or not re.fullmatch(rf"[0-9a-fA-F]{{{length}}}", value):
        _fail(f"поле {field} должно содержать {length} hex-символов.")
    return value.lower()


def _size(value):
    if type(value) is not int or not 0 < value <= MAX_ARCHIVE:
        _fail("size должно быть целым числом от 1 до 64 MiB.")
    return value


def _hashes(source, optional=False):
    result = {"sha256": _hex(source.get("sha256"), 64, "sha256")}
    if optional:
        for name, length in (("sha1", 40), ("sha384", 96)):
            if name in source:
                result[name] = _hex(source[name], length, name)
    return result


def validate_url(url):
    """Проверить также адрес редиректа, не раскрывая его в ошибке."""
    try:
        if (
            not isinstance(url, str)
            or not url.startswith("https://")
            or any(c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 for c in url)
        ):
            raise ValueError
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in ALLOWED_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
        ):
            raise ValueError
    except ValueError:
        raise ProfileError("Разрешены только HTTPS и хосты из белого списка.") from None
    return parsed.hostname


def _path(value, field):
    value = _text(value, field)
    if ".." in value or "\\" in value or value.startswith("/"):
        _fail(f"небезопасный путь в поле {field}.")
    if any(part in ("", ".") for part in value.split("/")):
        _fail(f"небезопасный сегмент пути в поле {field}.")
    return value


def _source(value):
    value = _object(value, "source")
    if value.get("type") == "ipcc":
        url = _text(value.get("url"), "url")
        validate_url(url)
        return IpccSource(url, _size(value.get("size")), _hashes(value, optional=True))
    if value.get("type") != "files":
        _fail("неизвестный тип source.")
    repo = _text(value.get("repo"), "repo")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or any(
        part in (".", "..") for part in repo.split("/")
    ):
        _fail("repo должен иметь вид владелец/имя.")
    commit = _hex(value.get("commit"), 40, "commit")
    bundle = _path(value.get("bundle_name"), "bundle_name")
    if "/" in bundle or not bundle.endswith(".bundle") or bundle == ".bundle":
        _fail("bundle_name должен быть одним именем с окончанием .bundle.")
    entries = value.get("files")
    if not isinstance(entries, list) or not entries:
        _fail("files должен быть непустым списком.")
    prefix = None
    result = []
    names = set()
    for entry in entries:
        entry = _object(entry, "files[]")
        path = _path(entry.get("path"), "path")
        parts = path.split("/")
        if parts.count(bundle) != 1:
            _fail("в path должен быть ровно один сегмент, равный bundle_name.")
        index = parts.index(bundle)
        current = tuple(parts[:index])
        if prefix is not None and current != prefix:
            _fail("пути файлов имеют разные префиксы перед bundle_name.")
        prefix = current
        relative = "/".join(parts[index + 1 :])
        if not relative:
            _fail("path должен указывать на файл внутри bundle.")
        if relative in names:
            _fail("повторяющийся путь файла.")
        names.add(relative)
        result.append(SourceFile(path, relative, _size(entry.get("size")), _hashes(entry)))
    for name in names:
        parts = name.split("/")
        if any("/".join(parts[:i]) in names for i in range(1, len(parts))):
            _fail("путь файла совпадает с каталогом.")
    return FilesSource(repo, commit, bundle, tuple(result))


def parse_database(data):
    data = _object(data, "корень")
    if type(data.get("schema")) is not int or data["schema"] != 1:
        _fail("поддерживается только schema 1.")
    groups = {}
    products = set()
    for key, value in _object(data.get("groups"), "groups").items():
        _text(key, "id группы")
        value = _object(value, "группа")
        label = _text(value.get("label"), "label")
        models = _strings(value.get("product_types"), "product_types")
        if any(not re.fullmatch(r"iPhone[0-9]+,[0-9]+", model) for model in models):
            _fail("product_types должен содержать ProductType вида iPhone<число>,<число>.")
        if products.intersection(models):
            _fail("product_types повторяются между группами.")
        products.update(models)
        tested = _strings(value.get("tested"), "tested")
        if not set(tested) <= set(models):
            _fail("tested содержит модель вне product_types своей группы.")
        groups[key] = Group(label, models, tested)
    entries = data.get("profiles")
    if not isinstance(entries, list):
        _fail("profiles должен быть списком.")
    profiles = []
    ids = set()
    for entry in entries:
        entry = _object(entry, "профиль")
        fields = {
            key: _text(entry.get(key), key)
            for key in ("id", "operator", "bundle_id", "version", "kind")
        }
        if fields["id"] in ids:
            _fail("id профилей должны быть уникальны.")
        ids.add(fields["id"])
        if fields["kind"] not in ("stock", "donor"):
            _fail("kind должен быть stock или donor.")
        targets = _strings(entry.get("groups"), "groups профиля")
        if not targets or any(group not in groups for group in targets):
            _fail("профилю нужна хотя бы одна группа; все группы должны существовать.")
        note = entry.get("note")
        if "note" in entry:
            if not isinstance(note, str) or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in note):
                _fail("note должен быть строкой без управляющих символов.")
        profiles.append(
            Profile(**fields, groups=targets, note=note, source=_source(entry.get("source")))
        )
    return ProfileDatabase(groups, tuple(profiles))


def load_database():
    try:
        data = json.loads(
            files("carrier_profile_tool").joinpath("profiles.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        raise ProfileError(
            "База профилей отсутствует, недоступна или содержит неверный JSON."
        ) from None
    return parse_database(data)


def group_for_product(database, product):
    return next(
        (key for key, group in database.groups.items() if product in group.product_types), None
    )


def profiles_for_product(database, product):
    group = group_for_product(database, product)
    return tuple(profile for profile in database.profiles if group in profile.groups)


def get_profile(database, profile_id):
    for profile in database.profiles:
        if profile.id == profile_id:
            return profile
    raise ProfileError("Профиль с указанным id не найден в базе.")
