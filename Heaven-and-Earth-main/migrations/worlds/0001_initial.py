"""Initial world data migration."""

from __future__ import annotations

from bot.storage import _read_toml, _write_toml

FROM_VERSION = 0
TO_VERSION = 1
DESCRIPTION = "Consolidate guild collections into world.toml"


def apply(context) -> None:  # type: ignore[override]
    guild_id = context.guild_id
    if guild_id is None:
        return

    world_path = context.collection.resolve_path(context.base, guild_id=guild_id)
    document = _read_toml(world_path)
    if not isinstance(document, dict):
        document = {}

    changed = False
    for collection in context.config.values():
        if collection.migration_key != context.collection.migration_key:
            continue
        section = collection.section
        if not section:
            continue
        if section not in document:
            document[section] = {}
            changed = True

    if changed or world_path.exists():
        _write_toml(world_path, document)
