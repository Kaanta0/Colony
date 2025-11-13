"""Player data schema definitions for static analysis and migration helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Sequence


@dataclass(slots=True)
class StoredSpiritRing:
    """Serialized representation of a spirit ring bound to a martial soul."""

    color: Literal["white", "yellow", "purple", "black", "red", "gold"]
    age: int
    slot_index: int
    martial_soul: str | None = None
    ability_keys: Sequence[str] = field(default_factory=tuple)
    domain_effect: str | None = None
    source: str | None = None


@dataclass(slots=True)
class StoredSpiritBone:
    """Serialized representation of a spirit bone attachment."""

    slot: Literal[
        "head",
        "torso",
        "left_arm",
        "right_arm",
        "left_leg",
        "right_leg",
        "external",
    ]
    age: int
    abilities: Sequence[str] = field(default_factory=tuple)
    passive_bonuses: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class PlayerPayload:
    """Top-level TOML payload stored on disk for each player."""

    user_id: int
    name: str
    martial_souls: Sequence[dict]
    primary_martial_soul: str | None = None
    active_martial_soul_names: Sequence[str] = field(default_factory=tuple)
    soul_rings: Sequence[dict] = field(default_factory=tuple)
    soul_bones: Sequence[dict] = field(default_factory=tuple)
    soul_power_level: int = 1


__all__ = [
    "PlayerPayload",
    "StoredSpiritRing",
    "StoredSpiritBone",
]
