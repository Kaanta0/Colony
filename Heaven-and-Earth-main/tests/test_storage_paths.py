from __future__ import annotations

from pathlib import Path

import sys
import pytest

PROJECT_BASE = Path(__file__).resolve().parents[1]
if str(PROJECT_BASE) not in sys.path:
    sys.path.insert(0, str(PROJECT_BASE))

from bot.storage import resolve_storage_root


def test_resolve_storage_root_prefers_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    override = tmp_path / "custom"
    monkeypatch.setenv("HEAVEN_DATA_ROOT", str(override))
    monkeypatch.delenv("HEAVEN_STORAGE_ROOT", raising=False)

    result = resolve_storage_root(Path("/ignored/base"))

    assert result == override.resolve()


def test_resolve_storage_root_handles_site_packages(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("HEAVEN_DATA_ROOT", raising=False)
    monkeypatch.delenv("HEAVEN_STORAGE_ROOT", raising=False)
    package_root = tmp_path / "lib" / "python3.12" / "site-packages" / "heaven"
    package_root.mkdir(parents=True)

    working_dir = tmp_path / "runtime"
    working_dir.mkdir()
    monkeypatch.chdir(working_dir)

    result = resolve_storage_root(package_root)

    assert result == working_dir.resolve()


def test_resolve_storage_root_defaults_to_package_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("HEAVEN_DATA_ROOT", raising=False)
    monkeypatch.delenv("HEAVEN_STORAGE_ROOT", raising=False)
    package_root = tmp_path / "heaven"
    package_root.mkdir()

    result = resolve_storage_root(package_root)

    assert result == package_root.resolve()
