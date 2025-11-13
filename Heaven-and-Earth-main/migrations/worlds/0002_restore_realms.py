"""Restore default cultivation realm names in stored stage definitions."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from bot.storage import _read_toml, _write_toml

FROM_VERSION = 1
TO_VERSION = 2
DESCRIPTION = "Rename Soul Land realm titles in cultivation stage records"


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


def _rename_label(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    replacement = _REALM_RENAMES.get(value)
    if replacement:
        return replacement
    if "(" in value and value.endswith(")"):
        base, rest = value.split("(", 1)
        stripped = base.strip()
        if stripped in _REALM_RENAMES:
            return f"{_REALM_RENAMES[stripped]} ({rest}"
    return value


def apply(context) -> None:  # type: ignore[override]
    guild_id = context.guild_id
    if guild_id is None:
        return

    world_path = context.collection.resolve_path(context.base, guild_id=guild_id)
    document = _read_toml(world_path)
    if not isinstance(document, MutableMapping):
        return

    section = context.collection.section
    if not section:
        return

    stages = document.get(section)
    if not isinstance(stages, MutableMapping):
        return

    changed = False
    new_stages: dict[str, Any] = {}
    for key, payload in stages.items():
        if not isinstance(payload, MutableMapping):
            new_stages[key] = payload
            continue
        stage_data = dict(payload)
        realm = stage_data.get("realm")
        renamed_realm = _rename_label(realm)
        if renamed_realm != realm:
            stage_data["realm"] = renamed_realm
            changed = True
        name = stage_data.get("name")
        renamed_name = _rename_label(name)
        if renamed_name != name:
            stage_data["name"] = renamed_name
            changed = True
        new_stages[key] = stage_data

    if not changed:
        return

    document[section] = new_stages
    _write_toml(world_path, document)
    context.log("renamed cultivation stage realm labels")
