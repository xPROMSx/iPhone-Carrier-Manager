"""Проверка правил хранения файлов в репозитории."""

import subprocess
from pathlib import Path, PurePosixPath

import pytest


def test_repo_hygiene():
    root = Path(__file__).resolve().parents[1]
    ignore_file = root / ".gitignore"
    assert ignore_file.is_file(), "В корне репозитория отсутствует .gitignore"
    patterns = set(ignore_file.read_text(encoding="utf-8").splitlines())
    required = {
        "*.ipcc", "*.bundle", "*.plist", "*.mobileprovision", "*.log",
        "state/", "tmp/", ".tmp/", "__pycache__/", ".venv/", "venv/",
        "dist/", "build/", "*.egg-info/", ".pytest_cache/", ".ruff_cache/",
        "Thumbs.db", "desktop.ini",
    }
    assert required <= patterns, f"В .gitignore нет шаблонов: {sorted(required - patterns)}"

    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        pytest.fail(f"Не удалось выполнить git ls-files из корня репозитория: {error}")

    forbidden = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = raw_path.decode("utf-8", errors="replace")
        parts = PurePosixPath(path.casefold()).parts
        if parts[-1].endswith((".ipcc", ".plist")) or any(
            part.endswith(".bundle") for part in parts
        ):
            forbidden.append(path)
    assert not forbidden, f"В Git отслеживаются операторские файлы Apple: {forbidden}"
