"""Add legacy fields to player records."""

from __future__ import annotations

from typing import MutableMapping

from bot.storage import _read_toml, _write_toml


FROM_VERSION = 1
TO_VERSION = 2
DESCRIPTION = "Ensure player saves track legacy techniques, traits, and heirs"


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
        for key, default in (
            ("legacy_techniques", []),
            ("legacy_traits", []),
            ("legacy_heirs", []),
        ):
            if key not in payload:
                payload[key] = list(default)
                changed = True
        if changed:
            _write_toml(path, payload)
            updated += 1

    if updated:
        context.log(f"added legacy fields to {updated} player save(s)")
