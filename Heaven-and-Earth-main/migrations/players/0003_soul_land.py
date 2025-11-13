"""Backfill Soul Land martial soul data for existing player saves."""

from __future__ import annotations

from collections.abc import MutableMapping, Sequence
from typing import Any

from bot.storage import _read_toml, _write_toml

FROM_VERSION = 2
TO_VERSION = 3
DESCRIPTION = "Ensure players have martial souls, rings, bones, and soul power level fields"


_DEF_SEQUENCE_KEYS: tuple[str, ...] = ("martial_souls", "spirit_rings", "spirit_bones")


def _normalize_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    if isinstance(value, MutableMapping):
        return [dict(value)]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    return [value]


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

        changed = False

        for key in _DEF_SEQUENCE_KEYS:
            value = payload.get(key)
            normalized = _normalize_sequence(value)
            if value != normalized:
                payload[key] = normalized
                changed = True

        raw_level = payload.get("soul_power_level")
        try:
            level = int(raw_level)
        except (TypeError, ValueError):
            level = 1
        if level < 1:
            level = 1
        if raw_level != level:
            payload["soul_power_level"] = level
            changed = True

        if changed:
            _write_toml(path, payload)
            updated += 1

    if updated:
        context.log(f"normalized martial soul fields in {updated} player save(s)")
