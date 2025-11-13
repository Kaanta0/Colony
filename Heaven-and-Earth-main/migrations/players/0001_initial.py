"""Initial migration for player saves."""

from __future__ import annotations


FROM_VERSION = 0
TO_VERSION = 1
DESCRIPTION = "Bootstrap player save directories"


def apply(context) -> None:  # type: ignore[override]
    guild_id = context.guild_id
    if guild_id is None:
        return

    target_dir = context.collection.record_directory(context.base, guild_id=guild_id)
    target_dir.mkdir(parents=True, exist_ok=True)
