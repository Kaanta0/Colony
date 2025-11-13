"""Migrate player saves from innate soul schema to martial soul schema."""

from __future__ import annotations

from collections.abc import MutableMapping, Sequence
from typing import Any

from bot.models.soul_land import MartialSoul
from bot.storage import _read_toml, _write_toml

FROM_VERSION = 4
TO_VERSION = 5
DESCRIPTION = "Consolidate martial soul fields and drop innate soul payloads"


def _ensure_sequence(value: Any) -> list[Any]:
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

        for key in (
            "innate_souls",
            "innate_soul",
            "active_innate_soul_mutations",
            "innate_soul_mutation_history",
            "pending_innate_soul_mutations",
            "innate_soul_hybridized",
        ):
            if key in payload:
                payload.pop(key, None)
                changed = True

        souls_raw = payload.get("martial_souls")
        if isinstance(souls_raw, MutableMapping):
            souls = _ensure_sequence(souls_raw)
            payload["martial_souls"] = souls
            changed = True
        elif not isinstance(souls_raw, list):
            payload["martial_souls"] = _ensure_sequence(souls_raw)
            changed = True
        if not payload["martial_souls"]:
            payload["martial_souls"] = [MartialSoul.default(category="any").to_mapping()]
            changed = True

        soul_names = [str(entry.get("name", "")).strip() for entry in payload["martial_souls"] if isinstance(entry, MutableMapping)]
        soul_names = [name for name in soul_names if name]
        if soul_names and payload.get("primary_martial_soul") not in soul_names:
            payload["primary_martial_soul"] = soul_names[0]
            changed = True
        if soul_names:
            active = payload.get("active_martial_soul_names")
            active_list = _ensure_sequence(active)
            normalized_active: list[str] = []
            for entry in active_list:
                name = str(entry).strip()
                if name and name in soul_names and name not in normalized_active:
                    normalized_active.append(name)
                if len(normalized_active) >= 2:
                    break
            if not normalized_active:
                normalized_active = soul_names[:1]
            payload["active_martial_soul_names"] = normalized_active
            changed = True

        rings_key = None
        if "soul_rings" in payload:
            rings_key = "soul_rings"
        elif "spirit_rings" in payload:
            rings_key = "spirit_rings"
        if rings_key is not None:
            rings = _ensure_sequence(payload.pop(rings_key))
            payload["soul_rings"] = rings
            changed = True
        bones_key = None
        if "soul_bones" in payload:
            bones_key = "soul_bones"
        elif "spirit_bones" in payload:
            bones_key = "spirit_bones"
        if bones_key is not None:
            payload["soul_bones"] = _ensure_sequence(payload.pop(bones_key))
            changed = True

        if "soul_power_level" in payload:
            try:
                level = int(payload["soul_power_level"])
            except (TypeError, ValueError):
                level = 1
            if level < 1:
                level = 1
            if payload["soul_power_level"] != level:
                payload["soul_power_level"] = level
                changed = True

        if changed:
            _write_toml(path, payload)
            updated += 1

    if updated:
        context.log(f"updated {updated} player save(s) to martial soul schema")
