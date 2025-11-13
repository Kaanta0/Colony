from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_BASE = Path(__file__).resolve().parents[1]
if str(PROJECT_BASE) not in sys.path:
    sys.path.insert(0, str(PROJECT_BASE))

from bot.storage import _write_toml


def test_write_toml_preserves_original_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "data.toml"
    _write_toml(target, {"alpha": 1})
    original_contents = target.read_text(encoding="utf8")

    def _boom(src: Path, dst: Path) -> None:
        raise RuntimeError("simulated failure")

    monkeypatch.setattr("bot.storage.os.replace", _boom)

    with pytest.raises(RuntimeError):
        _write_toml(target, {"alpha": 2})

    assert target.read_text(encoding="utf8") == original_contents
    leftovers = [p for p in target.parent.iterdir() if p.name != "data.toml"]
    assert leftovers == []
