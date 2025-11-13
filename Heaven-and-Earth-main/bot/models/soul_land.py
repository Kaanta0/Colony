"""Soul Land specific mechanics for martial souls and spirit cultivation."""

from __future__ import annotations

import json
import os
import math
import random
import re
from itertools import chain, islice, product
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Collection, Iterable, Mapping, Sequence

from .combat import (
    DamageType,
    Skill,
    SkillArchetype,
    SkillCategory,
    SpiritualAffinity,
    Stats,
    WeaponType,
    coerce_affinity,
    normalize_affinities,
)
if TYPE_CHECKING:
    from .innate_souls import InnateSoulMutation

__all__ = [
    "MartialSoulType",
    "MartialSoulRarity",
    "MartialSoul",
    "MartialSoulEvolution",
    "SpiritRingColor",
    "SPIRIT_RING_COLOR_AGE_RANGES",
    "SpiritRing",
    "SpiritBoneSlot",
    "SpiritBone",
    "SoulPowerRank",
    "SOUL_POWER_RANK_TABLE",
    "rank_for_level",
    "max_ring_slots_for_level",
    "required_ring_count_for_level",
    "SoulRingTier",
    "SOUL_RING_AGE_TIERS",
    "build_martial_soul_signature_skill",
]


_ANSI_RESET = "\u001b[0m"
_ANSI_DIM_GRAY = "\u001b[2;90m"
_AFFINITY_ANSI_CODES: dict[SpiritualAffinity, str] = {
    SpiritualAffinity.FIRE: "31",
    SpiritualAffinity.WATER: "34",
    SpiritualAffinity.WIND: "36",
    SpiritualAffinity.EARTH: "33",
    SpiritualAffinity.METAL: "90",
    SpiritualAffinity.ICE: "36",
    SpiritualAffinity.LIGHTNING: "33",
    SpiritualAffinity.LIGHT: "37",
    SpiritualAffinity.DARKNESS: "35",
    SpiritualAffinity.LIFE: "32",
    SpiritualAffinity.DEATH: "31",
    SpiritualAffinity.SAMSARA: "35",
    SpiritualAffinity.SPACE: "35",
    SpiritualAffinity.TIME: "33",
    SpiritualAffinity.GRAVITY: "34",
    SpiritualAffinity.POISON: "32",
    SpiritualAffinity.MUD: "33",
    SpiritualAffinity.TEMPERATURE: "36",
    SpiritualAffinity.LAVA: "31",
    SpiritualAffinity.TWILIGHT: "95",
    SpiritualAffinity.ENTROPY: "30",
    SpiritualAffinity.PERMAFROST: "96",
    SpiritualAffinity.DUSTSTORM: "33",
    SpiritualAffinity.PLASMA: "95",
    SpiritualAffinity.STEAM: "37",
    SpiritualAffinity.INFERNO: "91",
    SpiritualAffinity.FLASHFROST: "36",
    SpiritualAffinity.FROSTFLOW: "94",
    SpiritualAffinity.BLIZZARD: "97",
    SpiritualAffinity.TEMPEST: "34",
    SpiritualAffinity.MIST: "37",
}

_SIGNATURE_DAMAGE_BY_GRADE: dict[int, float] = {
    1: 0.10,
    2: 0.20,
    3: 0.30,
    4: 0.40,
    5: 0.50,
    6: 0.60,
    7: 0.70,
    8: 0.80,
    9: 0.90,
}

_SIGNATURE_TRIGGER_BY_GRADE: dict[int, float] = {
    1: 0.36,
    2: 0.34,
    3: 0.32,
    4: 0.30,
    5: 0.28,
    6: 0.26,
    7: 0.24,
    8: 0.22,
    9: 0.20,
}


def _load_martial_soul_catalog() -> tuple["MartialSoul", ...]:
    if os.environ.get("SOUL_LAND_SKIP_PRESETS"):
        return tuple()
    data_path = Path(__file__).resolve().parents[2] / "data" / "martial_souls.json"
    try:
        raw_entries = json.loads(data_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            "Missing martial soul data file at 'data/martial_souls.json'."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError("Martial soul data file contains invalid JSON.") from exc

    if not isinstance(raw_entries, Sequence):
        raise ValueError("Martial soul data must be a sequence of objects.")

    catalog: list[MartialSoul] = []
    for entry in raw_entries:
        if not isinstance(entry, Mapping):
            continue
        try:
            soul = MartialSoul.from_mapping(entry)
        except Exception:
            continue
        if soul.name:
            catalog.append(soul)

    if not catalog:
        raise ValueError("Martial soul data file contained no valid entries.")

    return tuple(catalog)


class MartialSoulType(str, Enum):
    """Classification for martial souls based on their manifestation."""

    BEAST = "beast"
    TOOL = "tool"
    BODY = "body"

    @classmethod
    def from_value(
        cls, value: str | "MartialSoulType" | None, *, default: "MartialSoulType" | None = None
    ) -> "MartialSoulType":
        if isinstance(value, cls):
            return value
        if default is None:
            default = cls.BEAST
        if not value:
            return default
        normalized = str(value).strip().lower()
        if normalized in {cls.BEAST.value, "beasts"}:
            return cls.BEAST
        if normalized in {cls.TOOL.value, "tools"}:
            return cls.TOOL
        if normalized in {"weapon", "weapons", "auxiliary", "support"}:
            return cls.TOOL
        if normalized in {cls.BODY.value, "avatars", "avatar", "totem", "soul", "body"}:
            return cls.BODY
        return default


class MartialSoulRarity(str, Enum):
    """Represents the scarcity of a martial soul."""

    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    EPIC = "epic"
    LEGENDARY = "legendary"
    MYTHIC = "mythic"

    @classmethod
    def from_value(
        cls, value: str | "MartialSoulRarity" | None, *, default: "MartialSoulRarity" = None
    ) -> "MartialSoulRarity":
        if isinstance(value, cls):
            return value
        if value is None:
            return default or cls.COMMON
        normalized = str(value).strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        return default or cls.COMMON


_MARTIAL_SOUL_CATALOG: tuple["MartialSoul", ...] = ()
_BEAST_CREATURE_TITLES: tuple[str, ...] = ()


@dataclass(slots=True)
class MartialSoulEvolution:
    """Represents an evolution milestone for a martial soul."""

    name: str
    required_level: int | None = None
    required_rings: int | None = None
    stat_bonus: Stats = field(default_factory=Stats)
    ability_unlocks: int = 0
    description: str | None = None

    def __post_init__(self) -> None:
        self.name = str(self.name).strip() or "Evolution"
        if self.required_level is not None:
            try:
                level = int(self.required_level)
            except (TypeError, ValueError):
                level = 0
            self.required_level = max(1, level)
        if self.required_rings is not None:
            try:
                rings = int(self.required_rings)
            except (TypeError, ValueError):
                rings = 0
            self.required_rings = max(0, rings)
        if not isinstance(self.stat_bonus, Stats):
            if isinstance(self.stat_bonus, Mapping):
                self.stat_bonus = Stats.from_mapping(self.stat_bonus)
            else:
                self.stat_bonus = Stats()
        try:
            ability_unlocks = int(self.ability_unlocks)
        except (TypeError, ValueError):
            ability_unlocks = 0
        self.ability_unlocks = max(0, ability_unlocks)
        if self.description:
            self.description = str(self.description).strip() or None

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "stat_bonus": self.stat_bonus.to_mapping(),
        }
        if self.required_level is not None:
            payload["required_level"] = int(self.required_level)
        if self.required_rings is not None:
            payload["required_rings"] = int(self.required_rings)
        if self.ability_unlocks:
            payload["ability_unlocks"] = int(self.ability_unlocks)
        if self.description:
            payload["description"] = self.description
        return payload

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "MartialSoulEvolution":
        name = payload.get("name", "Evolution")
        required_level = payload.get("required_level")
        required_rings = payload.get("required_rings")
        ability_unlocks = payload.get("ability_unlocks", 0)
        description = payload.get("description")
        stat_payload = payload.get("stat_bonus", {})
        if isinstance(stat_payload, Stats):
            stat_bonus = stat_payload
        elif isinstance(stat_payload, Mapping):
            stat_bonus = Stats.from_mapping(stat_payload)
        else:
            stat_bonus = Stats()
        return cls(
            name=str(name),
            required_level=required_level,
            required_rings=required_rings,
            stat_bonus=stat_bonus,
            ability_unlocks=ability_unlocks,
            description=str(description).strip() or None if description else None,
        )

    def is_unlocked(self, level: int, ring_count: int) -> bool:
        if self.required_level is not None and level < self.required_level:
            return False
        if self.required_rings is not None and ring_count < self.required_rings:
            return False
        return True


def _rarity_for_grade(grade: int) -> MartialSoulRarity:
    if grade >= 9:
        return MartialSoulRarity.MYTHIC
    if grade >= 8:
        return MartialSoulRarity.LEGENDARY
    if grade >= 6:
        return MartialSoulRarity.EPIC
    if grade >= 4:
        return MartialSoulRarity.RARE
    if grade >= 2:
        return MartialSoulRarity.UNCOMMON
    return MartialSoulRarity.COMMON


def _base_innate_attributes_for_grade(grade: int) -> Stats:
    tier = max(1, int(grade))
    base_strength = 1.2 + 0.6 * tier
    base_physique = 1.0 + 0.55 * tier
    base_agility = 1.1 + 0.5 * tier
    return Stats(strength=base_strength, physique=base_physique, agility=base_agility)


def _base_ability_slots_for_grade(grade: int) -> int:
    tier = max(1, int(grade))
    return max(1, min(5, 1 + tier // 3))


@dataclass(slots=True)
class MartialSoul:
    """Represents a martial soul awakened by a cultivator."""

    name: str
    category: MartialSoulType
    grade: int
    affinities: tuple[SpiritualAffinity, ...] = field(default_factory=tuple)
    description: str | None = None
    variant: str | None = None
    signature_abilities: tuple[str, ...] = field(default_factory=tuple)
    favoured_weapons: tuple[WeaponType, ...] = field(default_factory=tuple)
    rarity: MartialSoulRarity = MartialSoulRarity.COMMON
    innate_attributes: Stats = field(default_factory=Stats)
    base_ability_slots: int = 1
    evolution_paths: tuple[MartialSoulEvolution, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        self.name = str(self.name or "").strip()
        try:
            grade_value = int(self.grade)
        except (TypeError, ValueError):
            grade_value = 1
        self.grade = max(1, grade_value)

        if not isinstance(self.category, MartialSoulType):
            self.category = MartialSoulType.from_value(str(self.category))

        affinities: list[SpiritualAffinity] = []
        for entry in self.affinities:
            try:
                affinities.append(coerce_affinity(entry))
            except ValueError:
                continue
        unique_affinities = tuple(dict.fromkeys(affinities))

        target_affinity_count = _target_affinity_count_for_grade(self.grade)
        normalized_affinities = _normalize_affinities_for_grade(
            unique_affinities, self.grade
        )
        if normalized_affinities:
            if len(normalized_affinities) <= target_affinity_count:
                self.affinities = normalized_affinities
            else:
                self.affinities = normalized_affinities[:target_affinity_count]
        elif len(unique_affinities) > target_affinity_count:
            self.affinities = unique_affinities[:target_affinity_count]
        else:
            self.affinities = unique_affinities

        self.rarity = MartialSoulRarity.from_value(self.rarity, default=_rarity_for_grade(self.grade))

        if not isinstance(self.innate_attributes, Stats):
            if isinstance(self.innate_attributes, Mapping):
                self.innate_attributes = Stats.from_mapping(self.innate_attributes)
            else:
                self.innate_attributes = Stats()

        try:
            base_slots = int(self.base_ability_slots)
        except (TypeError, ValueError):
            base_slots = 1
        self.base_ability_slots = max(1, min(10, base_slots))

        evolution_entries: list[MartialSoulEvolution] = []
        if isinstance(self.evolution_paths, MartialSoulEvolution):
            evolution_entries = [self.evolution_paths]
        elif isinstance(self.evolution_paths, Mapping):
            evolution_entries = [MartialSoulEvolution.from_mapping(self.evolution_paths)]
        elif isinstance(self.evolution_paths, Sequence) and not isinstance(
            self.evolution_paths, (str, bytes)
        ):
            for entry in self.evolution_paths:
                if isinstance(entry, MartialSoulEvolution):
                    evolution_entries.append(entry)
                elif isinstance(entry, Mapping):
                    try:
                        evolution_entries.append(MartialSoulEvolution.from_mapping(entry))
                    except Exception:
                        continue
        self.evolution_paths = tuple(evolution_entries)

        signatures: list[str] = []
        for entry in self.signature_abilities:
            value = str(entry).strip()
            if value:
                signatures.append(value)
        self.signature_abilities = tuple(dict.fromkeys(signatures))

        favoured: list[WeaponType] = []
        raw_weapons = self.favoured_weapons
        if isinstance(raw_weapons, Mapping):
            candidates = raw_weapons.values()
        elif isinstance(raw_weapons, (WeaponType, str)):
            candidates = (raw_weapons,)
        elif isinstance(raw_weapons, Iterable) and not isinstance(
            raw_weapons, (str, bytes)
        ):
            candidates = raw_weapons
        else:
            candidates = ()
        for entry in candidates:
            if isinstance(entry, WeaponType):
                weapon = entry
            else:
                try:
                    weapon = WeaponType.from_value(entry)
                except (TypeError, ValueError):
                    continue
            favoured.append(weapon)
        if favoured:
            favoured = list(dict.fromkeys(favoured))
        self.favoured_weapons = tuple(favoured)

        if self.description:
            self.description = str(self.description).strip() or None
        if self.variant:
            self.variant = str(self.variant).strip() or None

        self.name = _sanitize_martial_soul_name(
            self.name,
            category=self.category,
            favoured_weapons=self.favoured_weapons,
            affinities=self.affinities,
        )

        if self.grade == 1 and _requires_humble_name(self.name):
            if self.category in {MartialSoulType.BEAST, MartialSoulType.BODY}:
                self.name = _simple_beast_name(self.affinities)
            else:
                self.name = _simple_tool_name(self.favoured_weapons, self.affinities)

        if not self.signature_abilities:
            self.signature_abilities = (
                _signature_skill_key_from_name(self.name),
            )

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "category": self.category.value,
            "grade": int(self.grade),
            "affinities": [aff.value for aff in self.affinities],
            "rarity": self.rarity.value,
            "base_ability_slots": int(self.base_ability_slots),
        }
        innate_payload = self.innate_attributes.to_mapping()
        if any(innate_payload.values()):
            payload["innate_attributes"] = innate_payload
        if self.favoured_weapons:
            payload["favoured_weapons"] = [
                weapon.value for weapon in self.favoured_weapons
            ]
        if self.description:
            payload["description"] = self.description
        if self.variant:
            payload["variant"] = self.variant
        if self.signature_abilities:
            payload["signature_abilities"] = list(self.signature_abilities)
        if self.evolution_paths:
            payload["evolution_paths"] = [
                evolution.to_mapping() for evolution in self.evolution_paths
            ]
        return payload

    def favours_weapon(self, weapon: WeaponType | None) -> bool:
        if not weapon or not self.favoured_weapons:
            return False
        return weapon in self.favoured_weapons

    def has_affinity(
        self, affinity: SpiritualAffinity | Sequence[SpiritualAffinity] | None
    ) -> bool:
        for candidate in normalize_affinities(affinity):
            if candidate in self.affinities:
                return True
        return False

    def manifest_label(self) -> str:
        return _infer_manifest_label(
            self.category,
            self.favoured_weapons,
            self.affinities,
            name=self.name,
        )

    def signature_skill_key(self) -> str:
        if self.signature_abilities:
            return self.signature_abilities[0]
        return _signature_skill_key_from_name(self.name)

    def legacy_aliases(self) -> tuple[str, ...]:
        manifest = self.manifest_label()
        tokens = self.name.split()
        if not tokens:
            return ()
        manifest_lower = manifest.lower()
        if tokens and tokens[-1].lower() == manifest_lower:
            base_tokens = tokens[:-1]
        else:
            base_tokens = tokens
        if not base_tokens:
            return ()
        base = " ".join(base_tokens).strip()
        aliases: list[str] = []
        seen: set[str] = set()
        for suffix in ("Soul", "Martial Soul"):
            candidate = f"{base} {suffix}".strip()
            key = candidate.lower()
            if candidate and key not in seen:
                aliases.append(candidate)
                seen.add(key)
        return tuple(aliases)

    def damage_multiplier(
        self,
        *,
        skill_weapon: WeaponType | None,
        equipped_weapons: Collection[WeaponType],
        skill_affinity: SpiritualAffinity | Sequence[SpiritualAffinity] | None,
        skill_archetype: SkillArchetype,
        ring_count: int = 0,
    ) -> float:
        bonus = 0.0
        rings = max(0, int(ring_count))
        applied = False

        if self.category in {MartialSoulType.BEAST, MartialSoulType.BODY}:
            if skill_archetype is SkillArchetype.BEAST:
                bonus += 0.08 + 0.02 * self.grade + 0.01 * rings
                applied = True
        else:
            if skill_archetype is SkillArchetype.WEAPON:
                bonus += 0.05 + 0.015 * self.grade + 0.005 * rings
                applied = True
            if self.favoured_weapons:
                if self.favours_weapon(skill_weapon):
                    bonus += 0.04 + 0.015 * self.grade + 0.01 * rings
                    applied = True
                elif not skill_weapon:
                    for weapon in equipped_weapons:
                        if self.favours_weapon(weapon):
                            bonus += 0.025 + 0.01 * self.grade + 0.005 * rings
                            applied = True
                            break

        if self.has_affinity(skill_affinity):
            bonus += 0.04 + 0.015 * self.grade + 0.005 * rings
            applied = True

        if not applied:
            return 1.0
        return max(1.0, 1.0 + bonus)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "MartialSoul":
        name_raw = payload.get("name", "Awakened Manifestation")
        grade = int(payload.get("grade", 1))
        category = MartialSoulType.from_value(payload.get("category"))
        affinities_raw = payload.get("affinities", ())
        affinities: list[SpiritualAffinity] = []
        if isinstance(affinities_raw, (list, tuple, set)):
            for entry in affinities_raw:
                try:
                    affinities.append(coerce_affinity(entry))
                except ValueError:
                    continue
        elif affinities_raw is not None:
            try:
                affinities.append(coerce_affinity(affinities_raw))
            except ValueError:
                pass
        description = payload.get("description")
        variant = payload.get("variant")
        signature_raw = payload.get("signature_abilities", ())
        if isinstance(signature_raw, (list, tuple, set)):
            signature_abilities = tuple(str(entry) for entry in signature_raw if entry)
        elif signature_raw:
            signature_abilities = (str(signature_raw),)
        else:
            signature_abilities = ()
        rarity_value = payload.get("rarity")
        innate_payload = payload.get("innate_attributes", {})
        if isinstance(innate_payload, Stats):
            innate_attributes = innate_payload
        elif isinstance(innate_payload, Mapping):
            innate_attributes = Stats.from_mapping(innate_payload)
        else:
            innate_attributes = Stats()
        base_slots = payload.get("base_ability_slots", 1)
        evolutions_raw = payload.get("evolution_paths", ())
        evolution_entries: list[MartialSoulEvolution] = []
        if isinstance(evolutions_raw, MartialSoulEvolution):
            evolution_entries.append(evolutions_raw)
        elif isinstance(evolutions_raw, Mapping):
            evolution_entries.append(MartialSoulEvolution.from_mapping(evolutions_raw))
        elif isinstance(evolutions_raw, Sequence) and not isinstance(
            evolutions_raw, (str, bytes)
        ):
            for entry in evolutions_raw:
                if isinstance(entry, MartialSoulEvolution):
                    evolution_entries.append(entry)
                elif isinstance(entry, Mapping):
                    try:
                        evolution_entries.append(MartialSoulEvolution.from_mapping(entry))
                    except Exception:
                        continue
        favoured_raw = payload.get("favoured_weapons", ())
        if isinstance(favoured_raw, Mapping):
            favoured_weapons = tuple(favoured_raw.values())
        elif isinstance(favoured_raw, (WeaponType, str)):
            favoured_weapons = (favoured_raw,)
        elif isinstance(favoured_raw, Iterable) and not isinstance(
            favoured_raw, (str, bytes)
        ):
            favoured_weapons = tuple(favoured_raw)
        elif favoured_raw:
            favoured_weapons = (favoured_raw,)
        else:
            favoured_weapons = ()
        name = _sanitize_martial_soul_name(
            name_raw,
            category=category,
            favoured_weapons=favoured_weapons,
            affinities=affinities,
        )
        return cls(
            name=name,
            category=category,
            grade=grade,
            affinities=tuple(affinities),
            description=str(description).strip() or None if description else None,
            variant=str(variant).strip() or None if variant else None,
            signature_abilities=signature_abilities,
            rarity=rarity_value,
            innate_attributes=innate_attributes,
            base_ability_slots=base_slots,
            evolution_paths=tuple(evolution_entries),
            favoured_weapons=favoured_weapons,
        )

    @staticmethod
    def default(
        *,
        affinity: SpiritualAffinity | str | None = None,
        category: MartialSoulType | str = MartialSoulType.BEAST,
        preset_index: int | None = None,
        rng: random.Random | None = None,
        grade: int | None = None,
    ) -> "MartialSoul":
        normalized_affinity: SpiritualAffinity | None
        if affinity is None:
            normalized_affinity = None
        else:
            try:
                normalized_affinity = coerce_affinity(affinity)
            except ValueError:
                normalized_affinity = None

        normalized_category = _normalize_martial_soul_category(category)
        pool = list(_MARTIAL_SOUL_CATALOG)
        if not pool:
            raise RuntimeError("Martial soul catalog is empty.")

        filtered_pool = _filter_souls_by_category(pool, normalized_category)

        if normalized_affinity is not None:
            affinity_matches = _filter_souls_by_affinity(filtered_pool, normalized_affinity)
            if not affinity_matches:
                affinity_matches = _filter_souls_by_affinity(
                    pool, normalized_affinity, category_hint=normalized_category
                )
            if affinity_matches:
                filtered_pool = affinity_matches

        normalized_grade: int | None
        if grade is None:
            normalized_grade = None
        else:
            try:
                normalized_grade = max(1, int(grade))
            except (TypeError, ValueError):
                normalized_grade = None

        if normalized_grade is not None:
            grade_matches = [
                soul for soul in filtered_pool if soul.grade == normalized_grade
            ]
            if not grade_matches:
                # Drop the affinity filter but keep any category constraint so
                # cultivators can still draw the intended grade even when the
                # requested affinity lacks entries at that tier.
                category_pool = _filter_souls_by_category(pool, normalized_category)
                grade_matches = [
                    soul for soul in category_pool if soul.grade == normalized_grade
                ]
            if not grade_matches:
                # As a final fallback, ignore both affinity and category so the
                # requested grade can always be satisfied.
                grade_matches = [soul for soul in pool if soul.grade == normalized_grade]
            if grade_matches:
                filtered_pool = grade_matches

        if not filtered_pool:
            raise ValueError("No martial souls match the requested filters.")

        if preset_index is not None:
            template = filtered_pool[preset_index % len(filtered_pool)]
        else:
            chooser = rng.choice if rng is not None else random.choice
            template = chooser(filtered_pool)

        return MartialSoul.from_mapping(template.to_mapping())


    @staticmethod
    def default_pool() -> tuple["MartialSoul", ...]:
        return tuple(MartialSoul.from_mapping(soul.to_mapping()) for soul in _MARTIAL_SOUL_CATALOG)

    def unlocked_evolutions(self, level: int, ring_count: int) -> tuple[MartialSoulEvolution, ...]:
        return tuple(
            evolution
            for evolution in self.evolution_paths
            if evolution.is_unlocked(level, ring_count)
        )

    def total_innate_stats(self, level: int, ring_count: int) -> Stats:
        result = self.innate_attributes.copy()
        for evolution in self.unlocked_evolutions(level, ring_count):
            result.add_in_place(evolution.stat_bonus)
        return result

    def ability_slots(self, level: int, ring_count: int) -> int:
        slots = self.base_ability_slots
        for evolution in self.unlocked_evolutions(level, ring_count):
            slots += evolution.ability_unlocks
        return max(1, min(20, slots))

def _combine_affinities(
    *groups: Sequence[SpiritualAffinity], limit: int = 3
) -> tuple[SpiritualAffinity, ...]:
    result: list[SpiritualAffinity] = []
    for group in groups:
        for affinity in group:
            if affinity not in result:
                result.append(affinity)
                if len(result) >= limit:
                    return tuple(result)
    return tuple(result)


def _target_affinity_count_for_grade(grade: int) -> int:
    """Return the maximum affinity count permitted for the supplied grade."""

    if grade <= 3:
        return 1
    return 2


def _mixed_affinity_for_component(
    component: SpiritualAffinity,
) -> SpiritualAffinity | None:
    for affinity in SpiritualAffinity:
        if not affinity.is_mixed:
            continue
        if component in affinity.components:
            return affinity
    return None


def _normalize_affinities_for_grade(
    affinities: Sequence[SpiritualAffinity], grade: int
) -> tuple[SpiritualAffinity, ...]:
    if not affinities:
        return tuple()

    unique: list[SpiritualAffinity] = []
    for affinity in affinities:
        if affinity not in unique:
            unique.append(affinity)

    if grade <= 3:
        for affinity in unique:
            if not affinity.is_mixed:
                return (affinity,)
        for affinity in unique:
            for component in affinity.components:
                if not component.is_mixed:
                    return (component,)
        return tuple()

    if grade <= 6:
        mixed = [aff for aff in unique if aff.is_mixed]
        if mixed:
            return (mixed[0],)
        non_mixed = [aff for aff in unique if not aff.is_mixed]
        if len(non_mixed) >= 2:
            return tuple(non_mixed[:2])
        if non_mixed:
            base = non_mixed[0]
            upgraded = _mixed_affinity_for_component(base)
            if upgraded is not None:
                return (upgraded,)
            for candidate in SpiritualAffinity:
                if candidate.is_mixed or candidate is base:
                    continue
                return (base, candidate)
            return (base,)
        return tuple()

    mixed = [aff for aff in unique if aff.is_mixed]
    non_mixed = [aff for aff in unique if not aff.is_mixed]
    if len(mixed) >= 2:
        return tuple(mixed[:2])
    if mixed and non_mixed:
        return (mixed[0], non_mixed[0])
    return tuple()


def _affinity_display_label(affinities: Sequence[SpiritualAffinity]) -> str:
    for affinity in affinities:
        return affinity.display_name
    return "Elemental"


_BEAST_ACTION_PROFILES: dict[SpiritualAffinity, tuple[str, str]] = {
    SpiritualAffinity.FIRE: ("Blaze Pounce", "erupts in a burst of {element} flames that rake across the foe"),
    SpiritualAffinity.WATER: ("Tide Crash", "surges forward on {element} currents and crushes its target"),
    SpiritualAffinity.WIND: ("Gale Rush", "whips around the enemy in spiralling {element} gusts"),
    SpiritualAffinity.EARTH: ("Stone Slam", "hammers down with {element} weight, cracking the ground"),
    SpiritualAffinity.METAL: ("Edge Rake", "slashes with {element} blades that shear armour"),
    SpiritualAffinity.ICE: ("Frost Snap", "lashes out with {element} shards that freeze on impact"),
    SpiritualAffinity.LIGHTNING: ("Volt Blitz", "detonates in {element} arcs that jolt nerves"),
    SpiritualAffinity.LIGHT: ("Radiant Sweep", "floods the area in {element} brilliance and searing talons"),
    SpiritualAffinity.DARKNESS: ("Shadow Rend", "tears through defenders with {element} shadows"),
    SpiritualAffinity.LIFE: ("Bloom Pulse", "erupts in {element} vitality that overruns the foe"),
    SpiritualAffinity.DEATH: ("Grave Chill", "lets {element} chill gnaw at the foe's life force"),
    SpiritualAffinity.SAMSARA: ("Samsara Bloom", "erupts in alternating {element} life and dusk that lash out"),
    SpiritualAffinity.SPACE: ("Void Shear", "slices through space with {element} edges"),
    SpiritualAffinity.TIME: ("Chrono Break", "stutters time with {element} ripples before striking"),
    SpiritualAffinity.GRAVITY: ("Quell Crush", "pins the target beneath {element} weight"),
    SpiritualAffinity.POISON: ("Venom Lash", "lashes out with {element} toxins that seep in"),
    SpiritualAffinity.MUD: ("Mire Maul", "drags the foe through {element} sludge before the hit lands"),
    SpiritualAffinity.TEMPERATURE: ("Flux Flare", "whips the target with {element} heat whiplash"),
    SpiritualAffinity.LAVA: ("Magma Torrent", "spills {element} magma across the impact point"),
    SpiritualAffinity.TWILIGHT: ("Gloam Twist", "weaves {element} dusk around the foe before striking"),
    SpiritualAffinity.ENTROPY: ("Ruin Collapse", "shreds stability with {element} decay"),
    SpiritualAffinity.PERMAFROST: ("Permafrost Crash", "hammers foes with {element} slabs that freeze solid"),
    SpiritualAffinity.DUSTSTORM: ("Duststorm Maelstrom", "lashes {element} grit into slicing cyclones"),
    SpiritualAffinity.PLASMA: ("Solar Lance", "erupts in {element} arcs that vaporize defenses"),
    SpiritualAffinity.STEAM: ("Boil Burst", "surrounds targets in scalding {element} vapors"),
    SpiritualAffinity.INFERNO: ("Inferno Gale", "unleashes {element} firestorms riding hurricane winds"),
    SpiritualAffinity.FLASHFROST: ("Flashfrost Arc", "snaps {element} bolts that freeze on impact"),
    SpiritualAffinity.FROSTFLOW: ("Frostflow Surge", "drowns foes in {element} torrents that refreeze instantly"),
    SpiritualAffinity.BLIZZARD: ("Whiteout Roar", "howls {element} blizzards that blind the arena"),
    SpiritualAffinity.TEMPEST: ("Tempest Breaker", "drops {element} stormfronts that batter all sides"),
    SpiritualAffinity.MIST: ("Mistveil Drift", "coils {element} vapors that seep through defenses"),
}


_TOOL_ACTION_PROFILES: dict[WeaponType, tuple[str, str]] = {
    WeaponType.SWORD: ("Edge Cascade", "unleashes a fan of {element} steel arcs"),
    WeaponType.SPEAR: ("Piercing Drive", "lunges in a straight {element} line that impales defenders"),
    WeaponType.BOW: ("Storm Volley", "looses a wave of {element} bolts that detonate on impact"),
    WeaponType.INSTRUMENT: ("Resonant Aria", "releases a {element} chord that batters foes"),
    WeaponType.BRUSH: ("Glyph Stroke", "draws a {element} sigil that lashes forward"),
    WeaponType.WHIP: ("Snap Reaver", "cracks a {element} lash that coils around the enemy"),
    WeaponType.FAN: ("Cyclone Fan", "fans out {element} currents that slice and shove"),
    WeaponType.HAMMER: ("Breaker Impact", "brings down a {element} smash that sends shockwaves"),
    WeaponType.TRIDENT: ("Riptide Thrust", "drives {element} tines through the defence"),
    WeaponType.BARE_HAND: ("Palm Burst", "channels a {element} burst through open hands"),
}


def _beast_action_profile(affinity: SpiritualAffinity | None) -> tuple[str, str]:
    if affinity is None:
        return ("Wild Rush", "surges with elemental force and batters the foe")
    return _BEAST_ACTION_PROFILES.get(
        affinity,
        ("Wild Rush", "surges with {element} force and batters the foe"),
    )


def _tool_action_profile(weapon: WeaponType | None) -> tuple[str, str]:
    if weapon is None:
        return ("Focus Burst", "channels {element} power in a concentrated strike")
    return _TOOL_ACTION_PROFILES.get(
        weapon,
        ("Focus Burst", "channels {element} power in a concentrated strike"),
    )



def _manifest_label_for_category(
    category: MartialSoulType, favoured_weapons: Sequence[WeaponType | str]
) -> str:
    if category is MartialSoulType.TOOL:
        for weapon in favoured_weapons:
            if isinstance(weapon, WeaponType):
                label = _WEAPON_MANIFEST_LABELS.get(weapon)
                if label:
                    return label
            else:
                try:
                    weapon_type = WeaponType.from_value(weapon)
                except ValueError:
                    continue
                label = _WEAPON_MANIFEST_LABELS.get(weapon_type)
                if label:
                    return label
        return _FALLBACK_CATEGORY_MANIFEST.get(MartialSoulType.TOOL, "Manifestation")
    if category is MartialSoulType.BODY:
        return _FALLBACK_CATEGORY_MANIFEST.get(MartialSoulType.BODY, "Manifestation")
    return _FALLBACK_CATEGORY_MANIFEST.get(MartialSoulType.BEAST, "Manifestation")


def _infer_common_beast_manifest_label(
    affinities: Sequence[SpiritualAffinity],
) -> str:
    for affinity in affinities:
        if affinity in _COMMON_BEAST_MANIFEST_BY_AFFINITY:
            return _COMMON_BEAST_MANIFEST_BY_AFFINITY[affinity]
        for component in affinity.components:
            manifest = _COMMON_BEAST_MANIFEST_BY_AFFINITY.get(component)
            if manifest:
                return manifest
    return "Beast"


def _primary_affinity_label(affinities: Sequence[SpiritualAffinity]) -> str:
    labels = [affinity.display_name for affinity in affinities]
    if not labels:
        return "Elemental"
    if len(labels) == 1:
        return labels[0]
    return "/".join(labels)


def _simple_beast_name(affinities: Sequence[SpiritualAffinity]) -> str:
    manifest = _infer_common_beast_manifest_label(affinities)
    element = _primary_affinity_label(affinities)
    return f"{element} {manifest}".strip()


def _simple_tool_name(
    favoured_weapons: Sequence[WeaponType | str],
    affinities: Sequence[SpiritualAffinity],
) -> str:
    manifest = _manifest_label_for_category(MartialSoulType.TOOL, favoured_weapons)
    if not manifest:
        manifest = _FALLBACK_CATEGORY_MANIFEST.get(MartialSoulType.TOOL, "Tool")
    element = _primary_affinity_label(affinities)
    return f"{element} {manifest}".strip()


def _infer_beast_manifest_from_name(name: str | None) -> str | None:
    if not name:
        return None
    tokens = re.findall(r"[a-zA-Z]+", name)
    if not tokens:
        return None
    lowered = [token.lower() for token in tokens]
    for candidate in _BEAST_CREATURE_TITLES:
        candidate_tokens = re.findall(r"[a-zA-Z]+", candidate.lower())
        if not candidate_tokens or len(candidate_tokens) > len(lowered):
            continue
        if lowered[-len(candidate_tokens) :] == candidate_tokens:
            return candidate
    if lowered:
        return tokens[-1].title()
    return None


def _infer_manifest_label(
    category: MartialSoulType,
    favoured_weapons: Sequence[WeaponType | str],
    affinities: Sequence[SpiritualAffinity],
    *,
    name: str | None = None,
) -> str:
    if category is MartialSoulType.TOOL:
        return _manifest_label_for_category(category, favoured_weapons)
    manifest = _infer_beast_manifest_from_name(name)
    if manifest and manifest.strip().lower() in _STRIP_NAME_TOKENS:
        manifest = ""
    if manifest:
        return manifest
    for affinity in affinities:
        if affinity in _BEAST_MANIFEST_BY_AFFINITY:
            return _BEAST_MANIFEST_BY_AFFINITY[affinity]
        for component in affinity.components:
            manifest = _BEAST_MANIFEST_BY_AFFINITY.get(component)
            if manifest:
                return manifest
    return _FALLBACK_CATEGORY_MANIFEST.get(category, "Manifestation")


def _sanitize_martial_soul_name(
    name: str,
    *,
    category: MartialSoulType,
    favoured_weapons: Sequence[WeaponType | str],
    affinities: Sequence[SpiritualAffinity],
) -> str:
    cleaned = str(name or "").strip()
    manifest = _infer_manifest_label(
        category,
        favoured_weapons,
        affinities,
        name=cleaned,
    )
    if not cleaned:
        return manifest

    words = cleaned.split()
    if not words:
        return manifest
    if not any(word.lower() in _STRIP_NAME_TOKENS for word in words):
        return cleaned

    filtered: list[str] = []
    for word in words:
        lower = word.lower()
        if lower in _STRIP_NAME_TOKENS:
            continue
        filtered.append(word)

    if not filtered:
        return manifest

    existing = {word.lower() for word in filtered}
    if manifest:
        for token in manifest.split():
            lower_token = token.lower()
            if lower_token not in existing:
                filtered.append(token)
                existing.add(lower_token)

    sanitized = " ".join(filtered).strip()
    return sanitized or manifest


def _requires_humble_name(name: str | None) -> bool:
    if not name:
        return True
    tokens = re.findall(r"[a-zA-Z']+", str(name).lower())
    return any(token in _GRANDIOSE_NAME_TOKENS for token in tokens)


def _signature_skill_key_from_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")
    if not slug:
        slug = "martial_soul"
    return f"martial_soul.{slug}.signature"


def build_martial_soul_signature_skill(
    soul: MartialSoul, *, ability_key: str | None = None
) -> Skill:
    key = (ability_key or soul.signature_skill_key()).strip()
    if not key:
        key = _signature_skill_key_from_name(soul.name)

    affinities = list(soul.affinities)
    primary_affinity = affinities[0] if affinities else None
    grade = max(1, int(soul.grade))
    normalized_grade = min(9, grade)

    damage_ratio = _SIGNATURE_DAMAGE_BY_GRADE.get(
        normalized_grade, _SIGNATURE_DAMAGE_BY_GRADE[9]
    )
    proficiency_max = max(60, 120 + normalized_grade * 15)
    trigger_chance = _SIGNATURE_TRIGGER_BY_GRADE.get(
        normalized_grade, _SIGNATURE_TRIGGER_BY_GRADE[9]
    )

    favoured_weapon = soul.favoured_weapons[0] if soul.favoured_weapons else None
    weapon: WeaponType | None
    if isinstance(favoured_weapon, WeaponType):
        weapon = favoured_weapon
    elif isinstance(favoured_weapon, str):
        try:
            weapon = WeaponType.from_value(favoured_weapon)
        except ValueError:
            weapon = None
    else:
        weapon = None

    if soul.category in {MartialSoulType.BEAST, MartialSoulType.BODY}:
        damage_type = DamageType.PHYSICAL
        archetype = SkillArchetype.BEAST
        if weapon is None:
            weapon = WeaponType.BARE_HAND
        action_name, action_clause = _beast_action_profile(primary_affinity)
    else:
        damage_type = DamageType.QI
        archetype = SkillArchetype.WEAPON
        action_name, action_clause = _tool_action_profile(weapon)

    element_label = _primary_affinity_label(affinities).lower()
    damage_label = "physical" if damage_type is DamageType.PHYSICAL else "qi"
    damage_percent = int(round(damage_ratio * 100))
    trigger_percent = int(round(trigger_chance * 100))
    action_clause = action_clause.format(element=element_label)

    highlight_code = None
    for affinity in affinities:
        highlight_code = _AFFINITY_ANSI_CODES.get(affinity)
        if highlight_code is None and affinity.is_mixed:
            for component in affinity.components:
                highlight_code = _AFFINITY_ANSI_CODES.get(component)
                if highlight_code is not None:
                    break
        if highlight_code is not None:
            break

    if highlight_code is not None:
        name_segment = f"\u001b[1;{highlight_code}m{soul.name}{_ANSI_RESET}"
    else:
        name_segment = soul.name

    description = (
        f"{_ANSI_DIM_GRAY}When this technique triggers ({trigger_percent}% chance to trigger), "
        f"your {name_segment} {action_clause}, dealing {damage_percent}% {damage_label} damage.{_ANSI_RESET}"
    )

    grade_label = f"tier {grade}"
    skill_name = _SIGNATURE_SKILL_NAME_OVERRIDES.get(soul.name)
    if not skill_name:
        skill_name = action_name or "Signature Art"

    return Skill(
        key=key,
        name=skill_name,
        grade=grade_label,
        skill_type=damage_type,
        damage_ratio=damage_ratio,
        proficiency_max=proficiency_max,
        description=description,
        element=primary_affinity,
        elements=affinities,
        trigger_chance=trigger_chance,
        weapon=weapon,
        archetype=archetype,
        category=SkillCategory.ACTIVE,
    )


def _instantiate_default_martial_soul(preset: Mapping[str, Any]) -> MartialSoul:
    grade = int(preset.get("grade", 1))
    affinity_entries: list[SpiritualAffinity] = []
    for entry in preset.get("affinities", ()): 
        if isinstance(entry, SpiritualAffinity):
            affinity_entries.append(entry)
        else:
            try:
                affinity_entries.append(coerce_affinity(entry))
            except ValueError:
                continue
    affinities = tuple(affinity_entries)
    target_affinity_count = _target_affinity_count_for_grade(grade)
    normalized_affinities = _normalize_affinities_for_grade(affinities, grade)
    if normalized_affinities and len(normalized_affinities) == target_affinity_count:
        affinities = normalized_affinities
    elif len(affinities) > target_affinity_count:
        affinities = affinities[:target_affinity_count]
    category_value = preset.get("category", MartialSoulType.BEAST)
    if isinstance(category_value, MartialSoulType):
        category = category_value
    else:
        category = MartialSoulType.from_value(str(category_value))
    favoured_raw = preset.get("favoured_weapons", ())
    if isinstance(favoured_raw, Mapping):
        favoured_weapons: tuple[WeaponType | str, ...] = tuple(favoured_raw.values())
    elif isinstance(favoured_raw, Iterable) and not isinstance(favoured_raw, (str, bytes)):
        favoured_weapons = tuple(favoured_raw)
    elif favoured_raw:
        favoured_weapons = (favoured_raw,)
    else:
        favoured_weapons = ()
    name = _sanitize_martial_soul_name(
        preset.get("name", "Awakened Manifestation"),
        category=category,
        favoured_weapons=favoured_weapons,
        affinities=affinities,
    )
    return MartialSoul(
        name=name,
        category=category,
        grade=grade,
        affinities=affinities,
        description=preset.get("description"),
        favoured_weapons=favoured_weapons,
        innate_attributes=_base_innate_attributes_for_grade(grade),
        base_ability_slots=_base_ability_slots_for_grade(grade),
    )


def _normalize_martial_soul_category(
    value: MartialSoulType | str | None,
    *,
    default: MartialSoulType | None = MartialSoulType.BEAST,
) -> MartialSoulType | None:
    if isinstance(value, MartialSoulType):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"any", "all", "either", "random", "both"}:
        return None
    return MartialSoulType.from_value(normalized, default=default or MartialSoulType.BEAST)


def _filter_souls_by_category(
    souls: Sequence[MartialSoul], category: MartialSoulType | None
) -> list[MartialSoul]:
    if category is None:
        return list(souls)
    matches = [soul for soul in souls if soul.category is category]
    return matches or list(souls)


def _filter_souls_by_affinity(
    souls: Sequence[MartialSoul],
    affinity: SpiritualAffinity,
    *,
    category_hint: MartialSoulType | None = None,
) -> list[MartialSoul]:
    matches = [
        soul
        for soul in souls
        if affinity in soul.affinities
        and (category_hint is None or soul.category is category_hint)
    ]
    return matches


class SpiritRingColor(str, Enum):
    """Canonical colour progression for spirit rings."""

    WHITE = "white"
    YELLOW = "yellow"
    PURPLE = "purple"
    BLACK = "black"
    RED = "red"
    GOLD = "gold"

    @classmethod
    def from_value(cls, value: str | None) -> "SpiritRingColor":
        if not value:
            return cls.WHITE
        normalized = str(value).strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        raise ValueError(f"Unknown spirit ring colour: {value}")


SPIRIT_RING_COLOR_AGE_RANGES: dict[SpiritRingColor, tuple[int, int | None]] = {
    SpiritRingColor.WHITE: (10, 99),
    SpiritRingColor.YELLOW: (100, 999),
    SpiritRingColor.PURPLE: (1_000, 9_999),
    SpiritRingColor.BLACK: (10_000, 99_999),
    SpiritRingColor.RED: (100_000, 999_999),
    SpiritRingColor.GOLD: (1_000_000, None),
}

_DEFAULT_RING_AGE: dict[SpiritRingColor, int] = {
    colour: lower if upper is None else int((lower + upper) / 2)
    for colour, (lower, upper) in SPIRIT_RING_COLOR_AGE_RANGES.items()
}


def _colour_for_age(age: int) -> SpiritRingColor:
    if age <= 0:
        return SpiritRingColor.WHITE
    for colour, (minimum, maximum) in SPIRIT_RING_COLOR_AGE_RANGES.items():
        if age < minimum:
            continue
        if maximum is None or age <= maximum:
            return colour
    return SpiritRingColor.GOLD


def _colour_for_grade(grade: int) -> SpiritRingColor:
    if grade >= 9:
        return SpiritRingColor.GOLD
    if grade >= 8:
        return SpiritRingColor.RED
    if grade >= 7:
        return SpiritRingColor.BLACK
    if grade >= 5:
        return SpiritRingColor.PURPLE
    if grade >= 3:
        return SpiritRingColor.YELLOW
    return SpiritRingColor.WHITE


@dataclass(frozen=True, slots=True)
class SoulRingTier:
    """Represents a qualitative age tier for spirit rings."""

    key: str
    min_age: int
    max_age: int | None
    ability_unlocks: int
    stat_multiplier: float

    def contains(self, age: int) -> bool:
        if age < self.min_age:
            return False
        if self.max_age is None:
            return True
        return age <= self.max_age


SOUL_RING_AGE_TIERS: tuple[SoulRingTier, ...] = (
    SoulRingTier("juvenile", 10, 99, 1, 0.05),
    SoulRingTier("mature", 100, 999, 1, 0.1),
    SoulRingTier("veteran", 1_000, 9_999, 2, 0.16),
    SoulRingTier("ancient", 10_000, 99_999, 3, 0.28),
    SoulRingTier("legendary", 100_000, 999_999, 4, 0.45),
    SoulRingTier("primordial", 1_000_000, None, 5, 0.65),
)

_SOUL_RING_TIER_LOOKUP: dict[str, SoulRingTier] = {
    tier.key: tier for tier in SOUL_RING_AGE_TIERS
}


def _tier_for_age(age: int) -> SoulRingTier:
    sanitized = max(1, int(age))
    for tier in SOUL_RING_AGE_TIERS:
        if tier.contains(sanitized):
            return tier
    return SOUL_RING_AGE_TIERS[-1]


def _tier_from_key(key: str | None) -> SoulRingTier | None:
    if not key:
        return None
    normalized = str(key).strip().lower()
    return _SOUL_RING_TIER_LOOKUP.get(normalized)


@dataclass(slots=True)
class SpiritRing:
    """Represents a spirit ring bound to a martial soul."""

    slot_index: int
    color: SpiritRingColor
    age: int
    martial_soul: str | None = None
    ability_keys: tuple[str, ...] = field(default_factory=tuple)
    domain_effect: str | None = None
    source: str | None = None
    age_tier: str | None = None
    ability_slot_count: int = 0
    stat_multiplier: float = 0.0

    def __post_init__(self) -> None:
        self.slot_index = max(0, int(self.slot_index))
        self.age = max(1, int(self.age))
        if isinstance(self.color, SpiritRingColor):
            colour = self.color
        else:
            colour = SpiritRingColor.from_value(str(self.color))
        derived = _colour_for_age(self.age)
        if derived != colour:
            # Clamp to derived colour to maintain consistency between age and colour.
            colour = derived
        self.color = colour
        tier = _tier_from_key(self.age_tier) or _tier_for_age(self.age)
        self.age_tier = tier.key
        if self.ability_slot_count:
            try:
                slots = int(self.ability_slot_count)
            except (TypeError, ValueError):
                slots = tier.ability_unlocks
            self.ability_slot_count = max(0, min(10, slots))
        else:
            self.ability_slot_count = tier.ability_unlocks
        if self.stat_multiplier:
            try:
                multiplier = float(self.stat_multiplier)
            except (TypeError, ValueError):
                multiplier = tier.stat_multiplier
            self.stat_multiplier = max(0.0, multiplier)
        else:
            self.stat_multiplier = tier.stat_multiplier
        if self.martial_soul:
            self.martial_soul = str(self.martial_soul)
        self.ability_keys = tuple(str(entry) for entry in self.ability_keys if entry)
        if self.domain_effect:
            self.domain_effect = str(self.domain_effect)
        if self.source:
            self.source = str(self.source)

    @property
    def colour(self) -> SpiritRingColor:  # Alias for British spelling used in lore.
        return self.color

    @property
    def ability_slots(self) -> int:
        return self.ability_slot_count

    @property
    def unlocked_abilities(self) -> tuple[str, ...]:
        if not self.ability_keys:
            return ()
        limit = self.ability_slot_count if self.ability_slot_count > 0 else len(self.ability_keys)
        return tuple(self.ability_keys[:limit])

    def stat_bonus(self, base_attributes: Stats) -> Stats:
        if self.stat_multiplier <= 0:
            return Stats()
        return base_attributes.scaled(self.stat_multiplier)

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "slot_index": int(self.slot_index),
            "color": self.color.value,
            "age": int(self.age),
            "age_tier": self.age_tier,
            "ability_slots": int(self.ability_slot_count),
            "stat_multiplier": float(self.stat_multiplier),
        }
        if self.martial_soul:
            payload["martial_soul"] = self.martial_soul
        if self.ability_keys:
            payload["ability_keys"] = list(self.ability_keys)
        if self.domain_effect:
            payload["domain_effect"] = self.domain_effect
        if self.source:
            payload["source"] = self.source
        return payload

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "SpiritRing":
        slot_index = int(payload.get("slot_index", payload.get("slot", 0)))
        age = int(payload.get("age", 10))
        color_value = payload.get("color") or payload.get("colour")
        try:
            color = SpiritRingColor.from_value(color_value)
        except ValueError:
            color = _colour_for_age(age)
        ability_raw = payload.get("ability_keys", ())
        if isinstance(ability_raw, (list, tuple, set)):
            abilities = tuple(str(entry) for entry in ability_raw if entry)
        elif ability_raw:
            abilities = (str(ability_raw),)
        else:
            abilities = ()
        domain_effect = payload.get("domain_effect")
        source = payload.get("source")
        martial_soul = payload.get("martial_soul")
        age_tier = payload.get("age_tier")
        ability_slots = payload.get("ability_slots", payload.get("ability_slot_count", 0))
        stat_multiplier = payload.get("stat_multiplier", 0.0)
        return cls(
            slot_index=slot_index,
            color=color,
            age=age,
            martial_soul=str(martial_soul) if martial_soul else None,
            ability_keys=abilities,
            domain_effect=str(domain_effect) if domain_effect else None,
            source=str(source) if source else None,
            age_tier=str(age_tier) if age_tier else None,
            ability_slot_count=int(ability_slots) if ability_slots else 0,
            stat_multiplier=float(stat_multiplier) if stat_multiplier else 0.0,
        )

    @classmethod
    def estimate_from_mutation(
        cls,
        mutation: InnateSoulMutation,
        slot_index: int,
        *,
        martial_soul: str | None = None,
    ) -> "SpiritRing":
        base = mutation.variant
        colour = _colour_for_grade(base.grade)
        age = _DEFAULT_RING_AGE[colour]
        return cls(
            slot_index=slot_index,
            color=colour,
            age=age,
            martial_soul=martial_soul or base.name,
            ability_keys=(mutation.trigger,) if mutation.trigger else (),
            source=mutation.source,
        )


class SpiritBoneSlot(str, Enum):
    HEAD = "head"
    TORSO = "torso"
    LEFT_ARM = "left_arm"
    RIGHT_ARM = "right_arm"
    LEFT_LEG = "left_leg"
    RIGHT_LEG = "right_leg"
    EXTERNAL = "external"

    @classmethod
    def from_value(cls, value: str | None) -> "SpiritBoneSlot":
        if not value:
            return cls.EXTERNAL
        normalized = str(value).strip().lower().replace("-", "_")
        for member in cls:
            if member.value == normalized:
                return member
        raise ValueError(f"Unknown spirit bone slot: {value}")


@dataclass(slots=True)
class SpiritBone:
    slot: SpiritBoneSlot
    age: int
    abilities: tuple[str, ...] = field(default_factory=tuple)
    passive_bonuses: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.slot, SpiritBoneSlot):
            self.slot = SpiritBoneSlot.from_value(str(self.slot))
        self.age = max(1, int(self.age))
        self.abilities = tuple(str(entry) for entry in self.abilities if entry)
        self.passive_bonuses = tuple(str(entry) for entry in self.passive_bonuses if entry)

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "slot": self.slot.value,
            "age": int(self.age),
        }
        if self.abilities:
            payload["abilities"] = list(self.abilities)
        if self.passive_bonuses:
            payload["passive_bonuses"] = list(self.passive_bonuses)
        return payload

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "SpiritBone":
        slot = payload.get("slot")
        age = int(payload.get("age", 10))
        abilities_raw = payload.get("abilities", ())
        if isinstance(abilities_raw, (list, tuple, set)):
            abilities = tuple(str(entry) for entry in abilities_raw if entry)
        elif abilities_raw:
            abilities = (str(abilities_raw),)
        else:
            abilities = ()
        passive_raw = payload.get("passive_bonuses", ())
        if isinstance(passive_raw, (list, tuple, set)):
            passive = tuple(str(entry) for entry in passive_raw if entry)
        elif passive_raw:
            passive = (str(passive_raw),)
        else:
            passive = ()
        return cls(slot=slot, age=age, abilities=abilities, passive_bonuses=passive)


@dataclass(frozen=True, slots=True)
class SoulPowerRank:
    """Represents a bracket of soul power levels and the expected ring slots."""

    index: int
    title: str
    min_level: int
    max_level: int
    ring_slots: int

    def contains(self, level: int) -> bool:
        return self.min_level <= level <= self.max_level


SOUL_POWER_RANK_TABLE: tuple[SoulPowerRank, ...] = (
    SoulPowerRank(1, "Qi Condensation", 1, 5, 1),
    SoulPowerRank(2, "Foundation Establishment", 6, 10, 2),
    SoulPowerRank(3, "Core Formation", 11, 15, 3),
    SoulPowerRank(4, "Nascent Soul", 16, 20, 4),
    SoulPowerRank(5, "Soul Formation", 21, 25, 5),
    SoulPowerRank(6, "Soul Transformation", 26, 30, 6),
    SoulPowerRank(7, "Ascendant", 31, 35, 7),
    SoulPowerRank(8, "Illusory Yin", 36, 40, 8),
    SoulPowerRank(9, "Corporeal Yang", 41, 45, 9),
    SoulPowerRank(10, "Nirvana Scryer", 46, 50, 9),
    SoulPowerRank(11, "Nirvana Cleanser", 51, 55, 9),
    SoulPowerRank(12, "Nirvana Shatterer", 56, 60, 9),
    SoulPowerRank(13, "Heaven's Blight", 61, 65, 9),
    SoulPowerRank(14, "Nirvana Void", 66, 70, 10),
    SoulPowerRank(15, "Spirit Void", 71, 75, 10),
    SoulPowerRank(16, "Arcane Void", 76, 80, 10),
    SoulPowerRank(17, "Void Tribulant", 81, 85, 10),
    SoulPowerRank(18, "Half-Heaven Trampling", 86, 90, 10),
    SoulPowerRank(19, "Heaven Trampling", 91, 100, 10),
)


def rank_for_level(level: int) -> SoulPowerRank:
    sanitized = max(1, int(level))
    for entry in SOUL_POWER_RANK_TABLE:
        if entry.contains(sanitized):
            return entry
    return SOUL_POWER_RANK_TABLE[-1]


def max_ring_slots_for_level(level: int) -> int:
    return rank_for_level(level).ring_slots


def required_ring_count_for_level(level: int) -> int:
    """Return how many rings are expected before surpassing a level threshold."""

    sanitized = max(1, int(level))
    # Ranks unlock a new ring every ten levels until reaching the cap.
    if sanitized >= 100:
        return 10
    slots = math.ceil(sanitized / 10)
    return max(1, min(slots, 9))


_FALLBACK_CATEGORY_MANIFEST: dict[MartialSoulType, str] = {
    MartialSoulType.BEAST: "Dragon",
    MartialSoulType.TOOL: "Blade",
    MartialSoulType.BODY: "Avatar",
}

_WEAPON_MANIFEST_LABELS: dict[WeaponType, str] = {
    WeaponType.SWORD: "Sword",
    WeaponType.SPEAR: "Spear",
    WeaponType.BOW: "Bow",
    WeaponType.INSTRUMENT: "Instrument",
    WeaponType.BRUSH: "Brush",
    WeaponType.WHIP: "Whip",
    WeaponType.FAN: "Fan",
    WeaponType.HAMMER: "Hammer",
    WeaponType.TRIDENT: "Trident",
}

_BEAST_MANIFEST_BY_AFFINITY: dict[SpiritualAffinity, str] = {
    SpiritualAffinity.FIRE: "Phoenix",
    SpiritualAffinity.WATER: "Leviathan",
    SpiritualAffinity.WIND: "Sky Roc",
    SpiritualAffinity.EARTH: "Jade Qilin",
    SpiritualAffinity.METAL: "Aurum Lion",
    SpiritualAffinity.ICE: "Frost Phoenix",
    SpiritualAffinity.LIGHTNING: "Thunder Qilin",
    SpiritualAffinity.LIGHT: "Radiant Swan",
    SpiritualAffinity.DARKNESS: "Nightshade Fox",
    SpiritualAffinity.LIFE: "Verdant Dryad",
    SpiritualAffinity.DEATH: "Nether Wraith",
    SpiritualAffinity.SPACE: "Void Serpent",
    SpiritualAffinity.TIME: "Chrono Dragonfly",
    SpiritualAffinity.GRAVITY: "Starbound Tortoise",
    SpiritualAffinity.POISON: "Venom Serpent",
    SpiritualAffinity.MUD: "Bog Hydra",
    SpiritualAffinity.TEMPERATURE: "Solar Salamander",
    SpiritualAffinity.LAVA: "Magma Behemoth",
    SpiritualAffinity.TWILIGHT: "Duskwind Kitsune",
    SpiritualAffinity.ENTROPY: "Ruin Mantis",
    SpiritualAffinity.PERMAFROST: "Glacier Stag",
    SpiritualAffinity.DUSTSTORM: "Sandstorm Roc",
    SpiritualAffinity.PLASMA: "Solar Wyvern",
    SpiritualAffinity.STEAM: "Mistforged Dragon",
    SpiritualAffinity.INFERNO: "Inferno Garuda",
    SpiritualAffinity.FLASHFROST: "Thunderfrost Lynx",
    SpiritualAffinity.FROSTFLOW: "Aurora Leviathan",
    SpiritualAffinity.BLIZZARD: "Snowstorm Gryphon",
    SpiritualAffinity.TEMPEST: "Stormcrown Dragon",
    SpiritualAffinity.MIST: "Mistveil Serpent",
}

_COMMON_BEAST_MANIFEST_BY_AFFINITY: dict[SpiritualAffinity, str] = {
    SpiritualAffinity.FIRE: "Salamander",
    SpiritualAffinity.WATER: "Otter",
    SpiritualAffinity.WIND: "Kestrel",
    SpiritualAffinity.EARTH: "Badger",
    SpiritualAffinity.METAL: "Armadillo",
    SpiritualAffinity.ICE: "Stoat",
    SpiritualAffinity.LIGHTNING: "Jay",
    SpiritualAffinity.LIGHT: "Heron",
    SpiritualAffinity.DARKNESS: "Cat",
    SpiritualAffinity.LIFE: "Deer",
    SpiritualAffinity.DEATH: "Crow",
    SpiritualAffinity.SPACE: "Moth",
    SpiritualAffinity.TIME: "Cricket",
    SpiritualAffinity.GRAVITY: "Tortoise",
    SpiritualAffinity.POISON: "Toad",
    SpiritualAffinity.MUD: "Snail",
    SpiritualAffinity.TEMPERATURE: "Newt",
    SpiritualAffinity.LAVA: "Gecko",
    SpiritualAffinity.TWILIGHT: "Fox",
    SpiritualAffinity.ENTROPY: "Mite",
    SpiritualAffinity.PERMAFROST: "Seal",
    SpiritualAffinity.DUSTSTORM: "Vulture",
    SpiritualAffinity.PLASMA: "Firefly",
    SpiritualAffinity.STEAM: "Beaver",
    SpiritualAffinity.INFERNO: "Jackal",
    SpiritualAffinity.FLASHFROST: "Lynx",
    SpiritualAffinity.FROSTFLOW: "Narwhal",
    SpiritualAffinity.BLIZZARD: "Snowy Owl",
    SpiritualAffinity.TEMPEST: "Albatross",
    SpiritualAffinity.MIST: "Egret",
}

_STRIP_NAME_TOKENS = {
    "soul",
    "souls",
    "martial",
    "dormant",
    "sealed",
    "inactive",
    "suppressed",
    "awakened",
    "slumbering",
    "latent",
    "active",
}


_GRANDIOSE_NAME_TOKENS: frozenset[str] = frozenset(
    {
        "astral",
        "celestial",
        "demon",
        "divine",
        "dragon",
        "emperor",
        "empress",
        "eternal",
        "goddess",
        "god",
        "heavenly",
        "immortal",
        "king",
        "lord",
        "mythic",
        "overlord",
        "paragon",
        "phoenix",
        "primordial",
        "queen",
        "radiant",
        "sovereign",
        "supreme",
        "tyrant",
    }
)


_SIGNATURE_SKILL_NAME_OVERRIDES: dict[str, str] = {}


_MARTIAL_SOUL_CATALOG = _load_martial_soul_catalog()
_BEAST_CREATURE_TITLES = tuple(
    sorted(
        {
            soul.name
            for soul in _MARTIAL_SOUL_CATALOG
            if soul.category is MartialSoulType.BEAST
        },
        key=lambda title: (-len(re.findall(r"[a-zA-Z]+", title)), -len(title)),
    )
)
