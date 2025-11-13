"""Remap Soul Land realm titles back to the core cultivation ladder."""

from __future__ import annotations

from collections.abc import MutableMapping, Sequence
from typing import Any

from bot.storage import _read_toml, _write_toml

FROM_VERSION = 3
TO_VERSION = 4
DESCRIPTION = "Rename Soul Land realm labels in player saves"


_REALM_RENAMES: dict[str, str] = {
    "Spirit Channeling": "Mortal",
    "Spirit Scholar": "Qi Condensation",
    "Spirit Master": "Foundation Establishment",
    "Spirit Grandmaster": "Core Formation",
    "Spirit Elder": "Nascent Soul",
    "Spirit Ancestor": "Soul Transformation",
    "Spirit King": "Ascendant",
    "Spirit Emperor": "Illusory Yin",
    "Spirit Sage": "Corporeal Yang",
    "Spirit Douluo": "Nirvana Scryer",
    "Titled Douluo": "Nirvana Cleanser",
    "Limit Douluo": "Nirvana Shatterer",
    "Godhood": "Heaven's Blight",
}


def _rename_value(value: Any) -> tuple[Any, bool]:
    if isinstance(value, str):
        replacement = _REALM_RENAMES.get(value)
        if replacement is None:
            return value, False
        return replacement, True
    if isinstance(value, list):
        changed = False
        result: list[Any] = []
        for item in value:
            new_item, item_changed = _rename_value(item)
            changed |= item_changed
            result.append(new_item)
        return result, changed
    if isinstance(value, tuple):
        changed = False
        result = []
        for item in value:
            new_item, item_changed = _rename_value(item)
            changed |= item_changed
            result.append(new_item)
        return tuple(result), changed
    if isinstance(value, set):
        changed = False
        result = set()
        for item in value:
            new_item, item_changed = _rename_value(item)
            changed |= item_changed
            result.add(new_item)
        return result, changed
    if isinstance(value, MutableMapping):
        changed = False
        result: dict[Any, Any] = {}
        for key, raw in value.items():
            new_value, value_changed = _rename_value(raw)
            changed |= value_changed
            result[key] = new_value
        return result, changed
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        changed = False
        result = []
        for item in value:
            new_item, item_changed = _rename_value(item)
            changed |= item_changed
            result.append(new_item)
        return type(value)(result), changed  # type: ignore[arg-type]
    return value, False


def apply(context) -> None:  # type: ignore[override]
    guild_id = context.guild_id
    if guild_id is None:
        return

    directory = context.collection.record_directory(context.base, guild_id=guild_id)
    if not directory.exists():
        return

    updated = 0
    for path in sorted(directory.glob("*.toml")):
        payload = _read_toml(path)
        if not isinstance(payload, MutableMapping):
            continue

        rewritten, changed = _rename_value(dict(payload))
        if not changed:
            continue

        _write_toml(path, rewritten)
        updated += 1

    if updated:
        context.log(f"updated realm labels in {updated} player save(s)")
