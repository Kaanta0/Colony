"""Initial migration for static definitions."""

from __future__ import annotations

FROM_VERSION = 0
TO_VERSION = 1
DESCRIPTION = "Move static content into data/static"


def apply(context) -> None:  # type: ignore[override]
    target_dir = context.collection.record_directory(context.base, guild_id=None)
    target_dir.mkdir(parents=True, exist_ok=True)
