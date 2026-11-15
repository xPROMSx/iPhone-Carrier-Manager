"""Проверка схемы только на выдуманной базе."""

import copy
import json

import pytest

from iphone_carrier_manager import profiles
from iphone_carrier_manager.ipcc import MAX_ARCHIVE


def test_valid_database(profile_data, files_data):
    for data in (profile_data, files_data[0]):
        database = profiles.parse_database(data)
        assert profiles.get_profile(database, "synthetic-stock").version == "2.0"
        assert profiles.group_for_product(database, "iPhone999,1") == "synthetic"
        assert len(profiles.profiles_for_product(database, "iPhone999,2")) == 1
        assert profiles.profiles_for_product(database, "iPhone998,1") == ()
        with pytest.raises(profiles.ProfileError, match="не найден"):
            profiles.get_profile(database, "missing")


def test_packaged_database():
    # Боевая база проходит ту же проверку схемы, что и выдуманная.
    database = profiles.load_database()
    assert database.groups
    assert database.profiles
    used = {group for item in database.profiles for group in item.groups}
    assert used == set(database.groups)


# Каждый элемент задаёт отдельное нарушение схемы, без изменения исходной fixture.
@pytest.mark.parametrize(
    "path,value",
    [
        ((), []),
        (("schema",), 2),
        (("schema",), True),
        (("schema",), "1"),
        (("groups",), []),
        (("profiles",), {}),
        (("groups", "synthetic"), []),
        (("groups", "synthetic", "label"), 1),
        (("groups", "synthetic", "product_types"), "iPhone999,1"),
        (("groups", "synthetic", "product_types"), ["SyntheticPhone"]),
        (("groups", "synthetic", "product_types"), ["iPhone999,1", "iPhone999,1"]),
        (("groups", "synthetic", "tested"), ["iPhone998,1"]),
        (("groups", "synthetic", "tested"), ["iPhone999,1", "iPhone999,1"]),
        (("profiles", 0), []),
        (("profiles", 0, "id"), ""),
        (("profiles", 0, "operator"), None),
        (("profiles", 0, "bundle_id"), 2),
        (("profiles", 0, "version"), ""),
        (("profiles", 0, "kind"), "other"),
        (("profiles", 0, "note"), 1),
        (("profiles", 0, "groups"), []),
        (("profiles", 0, "groups"), ["missing"]),
        (("profiles", 0, "source"), []),
        (("profiles", 0, "source", "type"), "other"),
        (("profiles", 0, "source", "sha256"), "a" * 63),
        (("profiles", 0, "source", "sha256"), "g" * 64),
        (("profiles", 0, "source", "sha1"), "0" * 39),
        (("profiles", 0, "source", "sha384"), "0" * 95),
        *[
            (("profiles", 0, "source", "size"), value)
            for value in (0, -1, MAX_ARCHIVE + 1, 1.5, "1", True)
        ],
        *[
            (("profiles", 0, "source", "url"), value)
            for value in (
                "http://updates.cdn-apple.com/test",
                "https://foreign.invalid/test",
                "https://updates.cdn-apple.com.foreign.invalid/test",
                "https://updates.cdn-apple.com@foreign.invalid/test",
                "https://user@updates.cdn-apple.com/test",
                "https://updates.cdn-apple.com:444/test",
                "https://[broken/test",
                " https://updates.cdn-apple.com/test",
                "https://updates.cdn-apple.com/\ntest",
            )
        ],
    ],
)
def test_invalid_database(profile_data, path, value):
    data = copy.deepcopy(profile_data)
    if not path:
        data = value
    else:
        target = data
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    with pytest.raises(profiles.ProfileError):
        profiles.parse_database(data)


@pytest.mark.parametrize(
    "field", ["id", "operator", "bundle_id", "version", "kind", "groups", "source"]
)
def test_required_profile_fields(profile_data, field):
    del profile_data["profiles"][0][field]
    with pytest.raises(profiles.ProfileError):
        profiles.parse_database(profile_data)


def test_duplicate_profile_ids(profile_data):
    profile_data["profiles"].append(copy.deepcopy(profile_data["profiles"][0]))
    with pytest.raises(profiles.ProfileError, match="уникальны"):
        profiles.parse_database(profile_data)


def test_duplicate_models_between_groups(profile_data):
    profile_data["groups"]["other"] = copy.deepcopy(profile_data["groups"]["synthetic"])
    with pytest.raises(profiles.ProfileError, match="между группами"):
        profiles.parse_database(profile_data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("repo", "single"),
        ("repo", "../repo"),
        ("repo", "owner/repo/extra"),
        ("commit", "0" * 39),
        ("commit", "z" * 40),
        ("bundle_name", "Synthetic"),
        ("bundle_name", "nested/Synthetic.bundle"),
        ("bundle_name", "../Synthetic.bundle"),
        ("bundle_name", ".bundle"),
        ("files", []),
        ("files", {}),
    ],
)
def test_invalid_files_source(files_data, field, value):
    data, _ = files_data
    data["profiles"][0]["source"][field] = value
    with pytest.raises(profiles.ProfileError):
        profiles.parse_database(data)


@pytest.mark.parametrize(
    "path,match",
    [
        ("../Synthetic.bundle/Info.plist", "небезопасный"),
        ("/Synthetic.bundle/Info.plist", "небезопасный"),
        ("Synthetic.bundle/../Info.plist", "небезопасный"),
        ("Synthetic.bundle/\\Info.plist", "небезопасный"),
        ("Synthetic.bundle/\x00Info.plist", "управляющих"),
        ("Synthetic.bundle/\x85Info.plist", "управляющих"),
        ("Synthetic.bundle//Info.plist", "сегмент"),
        ("Synthetic.bundle/./Info.plist", "сегмент"),
        ("Other.bundle/Info.plist", "ровно один сегмент"),
        ("Synthetic.bundle/Synthetic.bundle/Info.plist", "ровно один сегмент"),
        ("Other/Synthetic.bundle/Info.plist", "разные префиксы"),
        ("Carrier Bundles/Synthetic.bundle", "внутри bundle"),
    ],
)
def test_invalid_file_paths(files_data, path, match):
    data, _ = files_data
    data["profiles"][0]["source"]["files"][0]["path"] = path
    with pytest.raises(profiles.ProfileError, match=match):
        profiles.parse_database(data)


@pytest.mark.parametrize(
    "field,value",
    [("sha256", "not-a-hash"), ("size", 0), ("size", True), ("size", MAX_ARCHIVE + 1)],
)
def test_invalid_file_metadata(files_data, field, value):
    data, _ = files_data
    data["profiles"][0]["source"]["files"][0][field] = value
    with pytest.raises(profiles.ProfileError):
        profiles.parse_database(data)


@pytest.mark.parametrize("conflict", [False, True])
def test_duplicate_or_conflicting_file_paths(files_data, conflict):
    data, _ = files_data
    source = data["profiles"][0]["source"]
    entry = copy.deepcopy(source["files"][0])
    if conflict:
        entry["path"] += "/nested.bin"
    source["files"].append(entry)
    with pytest.raises(profiles.ProfileError, match="каталогом" if conflict else "повторяющийся"):
        profiles.parse_database(data)


@pytest.mark.parametrize("content", ["{", json.dumps({"schema": 2}), None])
def test_invalid_packaged_database(tmp_path, monkeypatch, content):
    if content is not None:
        (tmp_path / "profiles.json").write_text(content)
    monkeypatch.setattr(profiles, "files", lambda name: tmp_path)
    with pytest.raises(profiles.ProfileError):
        profiles.load_database()
