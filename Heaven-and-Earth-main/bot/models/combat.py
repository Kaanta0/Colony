"""Combat-related domain models and helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import (
    Any,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from ._validation import FieldSpec, MappingSpec, ModelValidator, is_non_empty_str


class DamageType(str, Enum):
    """Enumerates the supported damage types for skills and attacks."""

    PHYSICAL = "physical"
    QI = "qi"
    SOUL = "soul"
    TRUE = "true"

    @classmethod
    def _missing_(cls, value):  # type: ignore[override]
        """Allow dynamic string-based damage types."""

        if value is None:
            raise ValueError("Damage type cannot be None")
        normalized = str(value).lower()
        if normalized.startswith("damagetype."):
            normalized = normalized.split(".", 1)[1]
        if normalized == "spiritual":
            return cls.QI
        existing = cls._value2member_map_.get(normalized)
        if existing is not None:
            return existing
        pseudo_member = str.__new__(cls, normalized)
        pseudo_member._name_ = normalized.upper()
        pseudo_member._value_ = normalized
        cls._value2member_map_[normalized] = pseudo_member
        cls._member_map_[pseudo_member._name_] = pseudo_member
        return pseudo_member

    @classmethod
    def from_value(cls, value: "DamageType | str") -> "DamageType":
        return value if isinstance(value, cls) else cls(value)


class SkillCategory(str, Enum):
    """Classifies a skill as an active technique or passive skill."""

    ACTIVE = "active"
    PASSIVE = "passive"

    @classmethod
    def from_value(cls, value: "SkillCategory | str" | None) -> "SkillCategory":
        if isinstance(value, cls):
            return value
        if value is None:
            return cls.ACTIVE
        normalized = str(value).strip().lower()
        try:
            return cls(normalized)
        except ValueError:
            return cls.ACTIVE


class SkillArchetype(str, Enum):
    """Represents the combat style a skill belongs to."""

    GENERAL = "general"
    BEAST = "beast"
    WEAPON = "weapon"

    @classmethod
    def from_value(
        cls, value: "SkillArchetype | str" | None, *, default: "SkillArchetype" = None
    ) -> "SkillArchetype":
        if isinstance(value, cls):
            return value
        if value is None:
            return default or cls.GENERAL
        normalized = str(value).strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        return default or cls.GENERAL


class WeaponType(str, Enum):
    """Enumerates supported weapon categories."""

    BARE_HAND = "bare-handed"
    SWORD = "sword"
    SPEAR = "spear"
    BOW = "bow"
    INSTRUMENT = "instrument"
    BRUSH = "brush"
    WHIP = "whip"
    FAN = "fan"
    HAMMER = "hammer"
    TRIDENT = "trident"

    @classmethod
    def from_value(
        cls, value: "WeaponType | str | None", *, default: "WeaponType | None" = None
    ) -> "WeaponType":
        if isinstance(value, cls):
            return value
        if value is None:
            if default is not None:
                return default
            raise ValueError("Weapon type cannot be None")
        normalized = str(value).strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        if default is not None:
            return default
        raise ValueError(f"Unknown weapon type: {value}")


PLAYER_STAT_NAMES: tuple[str, ...] = (
    "strength",
    "physique",
    "agility",
)


def _coerce_number(value: float | int) -> float:
    return float(value)


def _stats_from_attrs(source: object) -> "Stats":
    values: dict[str, float] = {}
    for name in PLAYER_STAT_NAMES:
        total = 0.0
        if hasattr(source, name):
            total += _coerce_number(getattr(source, name))
        values[name] = total
    return Stats(**values)


def _multipliers_from_attrs(source: object) -> "StatMultipliers":
    values: dict[str, float] = {}
    for name in PLAYER_STAT_NAMES:
        total = 1.0
        if hasattr(source, name):
            total *= _coerce_number(getattr(source, name))
        values[name] = total
    return StatMultipliers(**values)


@dataclass(slots=True)
class Stats:
    """Represents the primary stats for a cultivator."""

    strength: float = 0.0
    physique: float = 0.0
    agility: float = 0.0

    @property
    def attacks(self) -> float:
        return max(0.0, self.strength * 4.0)

    @property
    def health_points(self) -> float:
        return max(0.0, self.physique * 3.0)

    @property
    def defense(self) -> float:
        return max(0.0, self.physique * 2.0)

    @property
    def dodges(self) -> float:
        return max(0.0, self.agility * 4.0)

    @property
    def hit_points(self) -> float:
        return max(0.0, self.agility * 8.0)

    def copy(self) -> "Stats":
        return Stats(**self.to_mapping())

    def to_mapping(self) -> Dict[str, float]:
        return {name: getattr(self, name) for name in PLAYER_STAT_NAMES}

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, float | int]) -> "Stats":
        data: dict[str, float] = {}
        for name in PLAYER_STAT_NAMES:
            total = 0.0
            if name in mapping:
                total += _coerce_number(mapping[name])
            data[name] = total
        return cls(**data)

    @classmethod
    def from_iterable(cls, pairs: Iterable[tuple[str, float | int]]) -> "Stats":
        return cls.from_mapping(dict(pairs))

    def items(self):
        for name in PLAYER_STAT_NAMES:
            yield name, getattr(self, name)

    def get(self, key: str, default: float = 0.0) -> float:
        if key in PLAYER_STAT_NAMES:
            return getattr(self, key)
        return default

    def add_in_place(self, other: "Stats") -> None:
        for name in PLAYER_STAT_NAMES:
            setattr(self, name, getattr(self, name) + getattr(other, name))

    def added(self, other: "Stats") -> "Stats":
        result = self.copy()
        result.add_in_place(other)
        return result

    def scale_in_place(self, factor: float) -> None:
        for name in PLAYER_STAT_NAMES:
            setattr(self, name, getattr(self, name) * factor)

    def scaled(self, factor: float) -> "Stats":
        result = self.copy()
        result.scale_in_place(factor)
        return result

    def add_scaled_in_place(self, other: "Stats", factor: float) -> None:
        for name in PLAYER_STAT_NAMES:
            setattr(
                self,
                name,
                getattr(self, name) + getattr(other, name) * factor,
            )

    def add_scaled(self, other: "Stats", factor: float) -> "Stats":
        result = self.copy()
        result.add_scaled_in_place(other, factor)
        return result

    def apply_multipliers_in_place(self, multipliers: "StatMultipliers") -> None:
        for name in PLAYER_STAT_NAMES:
            setattr(self, name, getattr(self, name) * getattr(multipliers, name))

    def apply_multipliers(self, multipliers: "StatMultipliers") -> "Stats":
        result = self.copy()
        result.apply_multipliers_in_place(multipliers)
        return result


def default_stats() -> "Stats":
    return Stats.from_mapping({stat: 10 for stat in PLAYER_STAT_NAMES})


@dataclass(slots=True)
class StatMultipliers:
    """Represents per-stat multiplicative modifiers."""

    strength: float = 1.0
    physique: float = 1.0
    agility: float = 1.0

    def to_mapping(self) -> Dict[str, float]:
        return {name: getattr(self, name) for name in PLAYER_STAT_NAMES}

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, float | int]) -> "StatMultipliers":
        data: dict[str, float] = {}
        for name in PLAYER_STAT_NAMES:
            total = 1.0
            if name in mapping:
                total *= _coerce_number(mapping[name])
            data[name] = total
        return cls(**data)


class SpiritualAffinity(str, Enum):
    """Enumerates the elemental affinities available for innate souls and skills."""

    FIRE = "fire"
    WATER = "water"
    WIND = "wind"
    EARTH = "earth"
    METAL = "metal"
    ICE = "ice"
    LIGHTNING = "lightning"
    LIGHT = "light"
    DARKNESS = "darkness"
    LIFE = "life"
    DEATH = "death"
    SAMSARA = "samsara"
    SPACE = "space"
    TIME = "time"
    GRAVITY = "gravity"
    POISON = "poison"
    MUD = "mud"
    TEMPERATURE = "temperature"
    LAVA = "lava"
    TWILIGHT = "twilight"
    ENTROPY = "entropy"
    PERMAFROST = "permafrost"
    DUSTSTORM = "duststorm"
    PLASMA = "plasma"
    STEAM = "steam"
    INFERNO = "inferno"
    FLASHFROST = "flashfrost"
    FROSTFLOW = "frostflow"
    BLIZZARD = "blizzard"
    TEMPEST = "tempest"
    MIST = "mist"
    FORGEFIRE = "forgefire"
    SOLARFLARE = "solarflare"
    SHADOWFLAME = "shadowflame"
    PHOENIXFIRE = "phoenixfire"
    NECROPYRE = "necropyre"
    NOVAFIRE = "novafire"
    CHRONOPYRE = "chronopyre"
    RUINFIRE = "ruinfire"
    QUICKSILVER = "quicksilver"
    STORMTIDE = "stormtide"
    LUMINWAVE = "luminwave"
    SPRINGTIDE = "springtide"
    GRAVEFLOOD = "graveflood"
    VOIDTIDE = "voidtide"
    CHRONOTIDE = "chronotide"
    CORROSION = "corrosion"
    SKYSTEEL = "skysteel"
    AURORAGALE = "auroragale"
    UMBRALZEPHYR = "umbralzephyr"
    VERDANTRUSH = "verdantrush"
    REAPERGALE = "reapergale"
    VOIDDRAFT = "voiddraft"
    CHRONOZEPHYR = "chronozephyr"
    RUINWHIRL = "ruinwhirl"
    STONEFORGE = "stoneforge"
    QUAKEBOLT = "quakebolt"
    DAWNSTONE = "dawnstone"
    UMBRASTONE = "umbrastone"
    GAIAWARD = "gaiaward"
    GRAVELOAM = "graveloam"
    ASTRALSTONE = "astralstone"
    EPOCHSTONE = "epochstone"
    ROTCLAY = "rotclay"
    FROSTSTEEL = "froststeel"
    VOLTSTEEL = "voltsteel"
    AURICHALCUM = "aurichalcum"
    UMBRALSTEEL = "umbralsteel"
    LIVINGSTEEL = "livingsteel"
    NECROSTEEL = "necrosteel"
    STARSTEEL = "starsteel"
    CHRONOSTEEL = "chronosteel"
    RUSTBLIGHT = "rustblight"
    AURORAFROST = "aurorafrost"
    NIGHTFROST = "nightfrost"
    FROSTBLOOM = "frostbloom"
    GRAVEFROST = "gravefrost"
    VOIDFROST = "voidfrost"
    CHRONOFROST = "chronofrost"
    BLIGHTFROST = "blightfrost"
    PHOTONSTORM = "photonstorm"
    SHADOWBOLT = "shadowbolt"
    VITALSPARK = "vitalspark"
    DOOMSPARK = "doomspark"
    STARBOLT = "starbolt"
    CHRONOBOLT = "chronobolt"
    RUINBOLT = "ruinbolt"
    SERAPHIC = "seraphic"
    MOURNLIGHT = "mournlight"
    COSMOLIGHT = "cosmolight"
    EPOCHGLOW = "epochglow"
    FADEGLOW = "fadeglow"
    SHADOWBLOOM = "shadowbloom"
    VOIDMOURNE = "voidmourne"
    VOIDSHADE = "voidshade"
    GLOAMHOUR = "gloamhour"
    ABYSS = "abyss"
    STARBLOOM = "starbloom"
    EVERBLOOM = "everbloom"
    WITHERBLOOM = "witherbloom"
    VOIDGRAVE = "voidgrave"
    DOOMHOUR = "doomhour"
    THANATOS = "thanatos"
    VOIDDECAY = "voiddecay"
    HEATDEATH = "heatdeath"

    @property
    def display_name(self) -> str:
        return self.value.replace("_", " ").title()

    @property
    def components(self) -> tuple["SpiritualAffinity", ...]:
        relationship = AFFINITY_RELATIONSHIPS.get(self)
        if relationship is None:
            return (self,)
        return relationship.components or (self,)

    @property
    def is_mixed(self) -> bool:
        components = self.components
        return len(components) > 1 or components[0] is not self

    def name_segments(self) -> tuple[tuple[str, "SpiritualAffinity"], ...]:
        """Split the affinity display name for per-component colouring.

        Mixed affinities are rendered with a segment for each component so the
        calling context can colourise the label with multiple ANSI colours. The
        split points are tuned for readability but fall back to an even split if
        an explicit configuration is unavailable.
        """

        components = self.components
        name = self.display_name
        if not components:
            return ((name, self),)
        if len(components) <= 1:
            return ((name, components[0]),)

        split_index = _MIXED_AFFINITY_SPLIT_POINTS.get(self)
        segments: list[tuple[str, SpiritualAffinity]] = []

        if len(components) == 2:
            total_length = len(name)
            if split_index is None or not (0 < split_index < total_length):
                split_index = max(1, min(total_length - 1, total_length // 2))
            first, second = name[:split_index], name[split_index:]
            if first:
                segments.append((first, components[0]))
            if second:
                segments.append((second, components[1]))
        else:
            total_length = len(name)
            component_count = len(components)
            if component_count <= 0:
                return ((name, self),)
            base_width = max(1, total_length // component_count)
            start = 0
            for index, component in enumerate(components):
                if index == component_count - 1:
                    end = total_length
                else:
                    remaining = component_count - index - 1
                    max_start = total_length - remaining
                    end = min(max_start, start + base_width)
                segment = name[start:end]
                if segment:
                    segments.append((segment, component))
                start = end

        if not segments:
            return ((name, components[0]),)
        return tuple(segments)


_MIXED_AFFINITY_SPLIT_POINTS: dict[SpiritualAffinity, int] = {
    SpiritualAffinity.SAMSARA: 3,
    SpiritualAffinity.GRAVITY: 4,
    SpiritualAffinity.POISON: 3,
    SpiritualAffinity.MUD: 2,
    SpiritualAffinity.TEMPERATURE: 5,
    SpiritualAffinity.LAVA: 2,
    SpiritualAffinity.TWILIGHT: 3,
    SpiritualAffinity.PERMAFROST: 5,
    SpiritualAffinity.DUSTSTORM: 4,
    SpiritualAffinity.PLASMA: 4,
    SpiritualAffinity.STEAM: 2,
    SpiritualAffinity.INFERNO: 5,
    SpiritualAffinity.FLASHFROST: 5,
    SpiritualAffinity.FROSTFLOW: 5,
    SpiritualAffinity.BLIZZARD: 5,
    SpiritualAffinity.TEMPEST: 4,
    SpiritualAffinity.MIST: 2,
}


@dataclass(frozen=True)
class AffinityRelationship:
    """Defines the interaction profile for a given affinity."""

    components: tuple[SpiritualAffinity, ...]
    resistances: tuple[SpiritualAffinity, ...] = ()
    strengths: tuple[SpiritualAffinity, ...] = ()
    weaknesses: tuple[SpiritualAffinity, ...] = ()
    resistance_modifier: float = 1.0
    strength_modifier: float = 0.5
    weakness_modifier: float = -1.0

    def modifier_for(self, target: SpiritualAffinity) -> float:
        if target in self.resistances:
            return self.resistance_modifier
        if target in self.strengths:
            return self.strength_modifier
        if target in self.weaknesses:
            return self.weakness_modifier
        target_relationship = AFFINITY_RELATIONSHIPS.get(target)
        target_components = (
            target_relationship.components
            if target_relationship is not None
            else (target,)
        )
        overlap = set(self.components) & set(target_components)
        if not overlap:
            return 0.0
        denominator = max(len(self.components), len(target_components))
        return len(overlap) / float(denominator)

AFFINITY_RELATIONSHIPS: dict[SpiritualAffinity, AffinityRelationship] = {
    SpiritualAffinity.FIRE: AffinityRelationship(
        components=(SpiritualAffinity.FIRE,),
        resistances=(
            SpiritualAffinity.FIRE,
            SpiritualAffinity.LAVA,
            SpiritualAffinity.TEMPERATURE,
            SpiritualAffinity.LIGHTNING,
        ),
        strengths=(
            SpiritualAffinity.WIND,
            SpiritualAffinity.LIGHT,
            SpiritualAffinity.METAL,
        ),
        weaknesses=(
            SpiritualAffinity.WATER,
            SpiritualAffinity.MUD,
            SpiritualAffinity.ICE,
        ),
    ),
    SpiritualAffinity.WATER: AffinityRelationship(
        components=(SpiritualAffinity.WATER,),
        resistances=(
            SpiritualAffinity.WATER,
            SpiritualAffinity.ICE,
            SpiritualAffinity.MUD,
            SpiritualAffinity.POISON,
        ),
        strengths=(
            SpiritualAffinity.TWILIGHT,
            SpiritualAffinity.LIFE,
            SpiritualAffinity.DARKNESS,
        ),
        weaknesses=(
            SpiritualAffinity.FIRE,
            SpiritualAffinity.LAVA,
            SpiritualAffinity.TEMPERATURE,
        ),
    ),
    SpiritualAffinity.WIND: AffinityRelationship(
        components=(SpiritualAffinity.WIND,),
        resistances=(SpiritualAffinity.WIND, SpiritualAffinity.LIGHTNING),
        strengths=(SpiritualAffinity.LIGHT, SpiritualAffinity.ICE),
        weaknesses=(
            SpiritualAffinity.EARTH,
            SpiritualAffinity.METAL,
            SpiritualAffinity.GRAVITY,
        ),
    ),
    SpiritualAffinity.EARTH: AffinityRelationship(
        components=(SpiritualAffinity.EARTH,),
        resistances=(
            SpiritualAffinity.EARTH,
            SpiritualAffinity.LAVA,
            SpiritualAffinity.METAL,
            SpiritualAffinity.MUD,
        ),
        strengths=(SpiritualAffinity.POISON, SpiritualAffinity.GRAVITY),
        weaknesses=(
            SpiritualAffinity.WIND,
            SpiritualAffinity.LIGHTNING,
            SpiritualAffinity.WATER,
        ),
    ),
    SpiritualAffinity.METAL: AffinityRelationship(
        components=(SpiritualAffinity.METAL,),
        resistances=(
            SpiritualAffinity.METAL,
            SpiritualAffinity.LIGHTNING,
            SpiritualAffinity.EARTH,
        ),
        strengths=(SpiritualAffinity.WIND, SpiritualAffinity.ICE),
        weaknesses=(
            SpiritualAffinity.POISON,
            SpiritualAffinity.LIFE,
            SpiritualAffinity.ENTROPY,
        ),
    ),
    SpiritualAffinity.ICE: AffinityRelationship(
        components=(SpiritualAffinity.ICE,),
        resistances=(SpiritualAffinity.ICE, SpiritualAffinity.WATER),
        strengths=(SpiritualAffinity.WIND, SpiritualAffinity.LIGHT),
        weaknesses=(
            SpiritualAffinity.FIRE,
            SpiritualAffinity.TEMPERATURE,
            SpiritualAffinity.LAVA,
        ),
    ),
    SpiritualAffinity.LIGHTNING: AffinityRelationship(
        components=(SpiritualAffinity.LIGHTNING,),
        resistances=(SpiritualAffinity.LIGHTNING, SpiritualAffinity.WIND),
        strengths=(SpiritualAffinity.FIRE, SpiritualAffinity.METAL),
        weaknesses=(
            SpiritualAffinity.EARTH,
            SpiritualAffinity.WATER,
            SpiritualAffinity.GRAVITY,
        ),
    ),
    SpiritualAffinity.LIGHT: AffinityRelationship(
        components=(SpiritualAffinity.LIGHT,),
        resistances=(
            SpiritualAffinity.LIGHT,
            SpiritualAffinity.LIFE,
            SpiritualAffinity.TIME,
        ),
        strengths=(
            SpiritualAffinity.WIND,
            SpiritualAffinity.FIRE,
            SpiritualAffinity.SPACE,
        ),
        weaknesses=(
            SpiritualAffinity.DARKNESS,
            SpiritualAffinity.TWILIGHT,
            SpiritualAffinity.ENTROPY,
        ),
    ),
    SpiritualAffinity.DARKNESS: AffinityRelationship(
        components=(SpiritualAffinity.DARKNESS,),
        resistances=(
            SpiritualAffinity.DARKNESS,
            SpiritualAffinity.TWILIGHT,
            SpiritualAffinity.ENTROPY,
        ),
        strengths=(
            SpiritualAffinity.DEATH,
            SpiritualAffinity.POISON,
            SpiritualAffinity.SPACE,
        ),
        weaknesses=(
            SpiritualAffinity.LIGHT,
            SpiritualAffinity.LIFE,
            SpiritualAffinity.TIME,
        ),
    ),
    SpiritualAffinity.LIFE: AffinityRelationship(
        components=(SpiritualAffinity.LIFE,),
        resistances=(
            SpiritualAffinity.LIFE,
            SpiritualAffinity.LIGHT,
            SpiritualAffinity.WATER,
        ),
        strengths=(SpiritualAffinity.EARTH, SpiritualAffinity.WIND),
        weaknesses=(
            SpiritualAffinity.DEATH,
            SpiritualAffinity.ENTROPY,
            SpiritualAffinity.POISON,
        ),
    ),
    SpiritualAffinity.DEATH: AffinityRelationship(
        components=(SpiritualAffinity.DEATH,),
        resistances=(
            SpiritualAffinity.DEATH,
            SpiritualAffinity.DARKNESS,
            SpiritualAffinity.ENTROPY,
        ),
        strengths=(SpiritualAffinity.POISON, SpiritualAffinity.TWILIGHT),
        weaknesses=(
            SpiritualAffinity.LIFE,
            SpiritualAffinity.LIGHT,
            SpiritualAffinity.SPACE,
        ),
    ),
    SpiritualAffinity.SAMSARA: AffinityRelationship(
        components=(SpiritualAffinity.LIFE, SpiritualAffinity.DEATH),
        resistances=(
            SpiritualAffinity.SAMSARA,
            SpiritualAffinity.LIFE,
            SpiritualAffinity.DEATH,
        ),
        strengths=(SpiritualAffinity.ENTROPY, SpiritualAffinity.POISON),
        weaknesses=(
            SpiritualAffinity.LIGHT,
            SpiritualAffinity.DARKNESS,
            SpiritualAffinity.SPACE,
        ),
    ),
    SpiritualAffinity.SPACE: AffinityRelationship(
        components=(SpiritualAffinity.SPACE,),
        resistances=(SpiritualAffinity.SPACE, SpiritualAffinity.TIME),
        strengths=(SpiritualAffinity.LIGHT, SpiritualAffinity.WIND),
        weaknesses=(
            SpiritualAffinity.GRAVITY,
            SpiritualAffinity.EARTH,
            SpiritualAffinity.ENTROPY,
        ),
    ),
    SpiritualAffinity.TIME: AffinityRelationship(
        components=(SpiritualAffinity.TIME,),
        resistances=(SpiritualAffinity.TIME, SpiritualAffinity.SPACE),
        strengths=(SpiritualAffinity.LIGHT, SpiritualAffinity.LIFE),
        weaknesses=(
            SpiritualAffinity.ENTROPY,
            SpiritualAffinity.DARKNESS,
            SpiritualAffinity.GRAVITY,
        ),
    ),
    SpiritualAffinity.GRAVITY: AffinityRelationship(
        components=(SpiritualAffinity.SPACE, SpiritualAffinity.TIME),
        resistances=(
            SpiritualAffinity.GRAVITY,
            SpiritualAffinity.SPACE,
            SpiritualAffinity.TIME,
            SpiritualAffinity.EARTH,
        ),
        strengths=(SpiritualAffinity.METAL, SpiritualAffinity.WATER),
        weaknesses=(
            SpiritualAffinity.WIND,
            SpiritualAffinity.LIGHTNING,
            SpiritualAffinity.ENTROPY,
        ),
    ),
    SpiritualAffinity.POISON: AffinityRelationship(
        components=(SpiritualAffinity.WATER, SpiritualAffinity.DARKNESS),
        resistances=(
            SpiritualAffinity.POISON,
            SpiritualAffinity.WATER,
            SpiritualAffinity.DARKNESS,
        ),
        strengths=(SpiritualAffinity.DEATH, SpiritualAffinity.EARTH),
        weaknesses=(
            SpiritualAffinity.LIFE,
            SpiritualAffinity.LIGHT,
            SpiritualAffinity.METAL,
        ),
    ),
    SpiritualAffinity.MUD: AffinityRelationship(
        components=(SpiritualAffinity.EARTH, SpiritualAffinity.WATER),
        resistances=(
            SpiritualAffinity.MUD,
            SpiritualAffinity.EARTH,
            SpiritualAffinity.WATER,
        ),
        strengths=(SpiritualAffinity.LAVA, SpiritualAffinity.POISON),
        weaknesses=(
            SpiritualAffinity.WIND,
            SpiritualAffinity.LIGHTNING,
            SpiritualAffinity.FIRE,
        ),
    ),
    SpiritualAffinity.TEMPERATURE: AffinityRelationship(
        components=(SpiritualAffinity.FIRE, SpiritualAffinity.ICE),
        resistances=(
            SpiritualAffinity.TEMPERATURE,
            SpiritualAffinity.FIRE,
            SpiritualAffinity.ICE,
        ),
        strengths=(SpiritualAffinity.WIND, SpiritualAffinity.WATER),
        weaknesses=(
            SpiritualAffinity.EARTH,
            SpiritualAffinity.TIME,
            SpiritualAffinity.LIFE,
        ),
    ),
    SpiritualAffinity.LAVA: AffinityRelationship(
        components=(SpiritualAffinity.EARTH, SpiritualAffinity.FIRE),
        resistances=(
            SpiritualAffinity.LAVA,
            SpiritualAffinity.FIRE,
            SpiritualAffinity.EARTH,
        ),
        strengths=(SpiritualAffinity.METAL, SpiritualAffinity.TEMPERATURE),
        weaknesses=(
            SpiritualAffinity.WATER,
            SpiritualAffinity.ICE,
            SpiritualAffinity.WIND,
        ),
    ),
    SpiritualAffinity.TWILIGHT: AffinityRelationship(
        components=(SpiritualAffinity.LIGHT, SpiritualAffinity.DARKNESS),
        resistances=(
            SpiritualAffinity.TWILIGHT,
            SpiritualAffinity.LIGHT,
            SpiritualAffinity.DARKNESS,
        ),
        strengths=(SpiritualAffinity.SPACE, SpiritualAffinity.TIME),
        weaknesses=(
            SpiritualAffinity.ENTROPY,
            SpiritualAffinity.LIGHTNING,
            SpiritualAffinity.LIFE,
        ),
    ),
    SpiritualAffinity.PERMAFROST: AffinityRelationship(
        components=(SpiritualAffinity.EARTH, SpiritualAffinity.ICE),
        resistances=(
            SpiritualAffinity.PERMAFROST,
            SpiritualAffinity.EARTH,
            SpiritualAffinity.ICE,
        ),
        strengths=(SpiritualAffinity.LAVA, SpiritualAffinity.TEMPERATURE),
        weaknesses=(
            SpiritualAffinity.FIRE,
            SpiritualAffinity.LIGHTNING,
            SpiritualAffinity.WIND,
        ),
    ),
    SpiritualAffinity.DUSTSTORM: AffinityRelationship(
        components=(SpiritualAffinity.EARTH, SpiritualAffinity.WIND),
        resistances=(
            SpiritualAffinity.DUSTSTORM,
            SpiritualAffinity.EARTH,
            SpiritualAffinity.WIND,
        ),
        strengths=(SpiritualAffinity.LIGHTNING, SpiritualAffinity.LIGHT),
        weaknesses=(
            SpiritualAffinity.WATER,
            SpiritualAffinity.METAL,
            SpiritualAffinity.LIFE,
        ),
    ),
    SpiritualAffinity.PLASMA: AffinityRelationship(
        components=(SpiritualAffinity.FIRE, SpiritualAffinity.LIGHTNING),
        resistances=(
            SpiritualAffinity.PLASMA,
            SpiritualAffinity.FIRE,
            SpiritualAffinity.LIGHTNING,
        ),
        strengths=(SpiritualAffinity.METAL, SpiritualAffinity.WIND),
        weaknesses=(
            SpiritualAffinity.WATER,
            SpiritualAffinity.EARTH,
            SpiritualAffinity.ICE,
        ),
    ),
    SpiritualAffinity.STEAM: AffinityRelationship(
        components=(SpiritualAffinity.FIRE, SpiritualAffinity.WATER),
        resistances=(
            SpiritualAffinity.STEAM,
            SpiritualAffinity.FIRE,
            SpiritualAffinity.WATER,
        ),
        strengths=(SpiritualAffinity.ICE, SpiritualAffinity.MUD),
        weaknesses=(
            SpiritualAffinity.WIND,
            SpiritualAffinity.LIGHTNING,
            SpiritualAffinity.TEMPERATURE,
        ),
    ),
    SpiritualAffinity.INFERNO: AffinityRelationship(
        components=(SpiritualAffinity.FIRE, SpiritualAffinity.WIND),
        resistances=(
            SpiritualAffinity.INFERNO,
            SpiritualAffinity.FIRE,
            SpiritualAffinity.WIND,
        ),
        strengths=(SpiritualAffinity.ICE, SpiritualAffinity.METAL),
        weaknesses=(
            SpiritualAffinity.WATER,
            SpiritualAffinity.MUD,
            SpiritualAffinity.GRAVITY,
        ),
    ),
    SpiritualAffinity.FLASHFROST: AffinityRelationship(
        components=(SpiritualAffinity.ICE, SpiritualAffinity.LIGHTNING),
        resistances=(
            SpiritualAffinity.FLASHFROST,
            SpiritualAffinity.ICE,
            SpiritualAffinity.LIGHTNING,
        ),
        strengths=(SpiritualAffinity.FIRE, SpiritualAffinity.WIND),
        weaknesses=(
            SpiritualAffinity.EARTH,
            SpiritualAffinity.TEMPERATURE,
            SpiritualAffinity.LAVA,
        ),
    ),
    SpiritualAffinity.FROSTFLOW: AffinityRelationship(
        components=(SpiritualAffinity.ICE, SpiritualAffinity.WATER),
        resistances=(
            SpiritualAffinity.FROSTFLOW,
            SpiritualAffinity.ICE,
            SpiritualAffinity.WATER,
        ),
        strengths=(SpiritualAffinity.FIRE, SpiritualAffinity.LAVA),
        weaknesses=(
            SpiritualAffinity.LIGHTNING,
            SpiritualAffinity.EARTH,
            SpiritualAffinity.TEMPERATURE,
        ),
    ),
    SpiritualAffinity.BLIZZARD: AffinityRelationship(
        components=(SpiritualAffinity.ICE, SpiritualAffinity.WIND),
        resistances=(
            SpiritualAffinity.BLIZZARD,
            SpiritualAffinity.ICE,
            SpiritualAffinity.WIND,
        ),
        strengths=(SpiritualAffinity.FIRE, SpiritualAffinity.LIGHT),
        weaknesses=(
            SpiritualAffinity.EARTH,
            SpiritualAffinity.METAL,
            SpiritualAffinity.LIGHTNING,
        ),
    ),
    SpiritualAffinity.TEMPEST: AffinityRelationship(
        components=(SpiritualAffinity.LIGHTNING, SpiritualAffinity.WIND),
        resistances=(
            SpiritualAffinity.TEMPEST,
            SpiritualAffinity.LIGHTNING,
            SpiritualAffinity.WIND,
        ),
        strengths=(SpiritualAffinity.FIRE, SpiritualAffinity.LIGHT),
        weaknesses=(
            SpiritualAffinity.EARTH,
            SpiritualAffinity.WATER,
            SpiritualAffinity.GRAVITY,
        ),
    ),
    SpiritualAffinity.MIST: AffinityRelationship(
        components=(SpiritualAffinity.WATER, SpiritualAffinity.WIND),
        resistances=(
            SpiritualAffinity.MIST,
            SpiritualAffinity.WATER,
            SpiritualAffinity.WIND,
        ),
        strengths=(SpiritualAffinity.FIRE, SpiritualAffinity.LIGHT),
        weaknesses=(
            SpiritualAffinity.LIGHTNING,
            SpiritualAffinity.METAL,
            SpiritualAffinity.TEMPERATURE,
        ),
    ),
    SpiritualAffinity.FORGEFIRE: AffinityRelationship(
        components=(SpiritualAffinity.FIRE, SpiritualAffinity.METAL),
        resistances=(
            SpiritualAffinity.FORGEFIRE,
            SpiritualAffinity.FIRE,
            SpiritualAffinity.METAL,
        ),
    ),
    SpiritualAffinity.SOLARFLARE: AffinityRelationship(
        components=(SpiritualAffinity.FIRE, SpiritualAffinity.LIGHT),
        resistances=(
            SpiritualAffinity.SOLARFLARE,
            SpiritualAffinity.FIRE,
            SpiritualAffinity.LIGHT,
        ),
    ),
    SpiritualAffinity.SHADOWFLAME: AffinityRelationship(
        components=(SpiritualAffinity.FIRE, SpiritualAffinity.DARKNESS),
        resistances=(
            SpiritualAffinity.SHADOWFLAME,
            SpiritualAffinity.FIRE,
            SpiritualAffinity.DARKNESS,
        ),
    ),
    SpiritualAffinity.PHOENIXFIRE: AffinityRelationship(
        components=(SpiritualAffinity.FIRE, SpiritualAffinity.LIFE),
        resistances=(
            SpiritualAffinity.PHOENIXFIRE,
            SpiritualAffinity.FIRE,
            SpiritualAffinity.LIFE,
        ),
    ),
    SpiritualAffinity.NECROPYRE: AffinityRelationship(
        components=(SpiritualAffinity.FIRE, SpiritualAffinity.DEATH),
        resistances=(
            SpiritualAffinity.NECROPYRE,
            SpiritualAffinity.FIRE,
            SpiritualAffinity.DEATH,
        ),
    ),
    SpiritualAffinity.NOVAFIRE: AffinityRelationship(
        components=(SpiritualAffinity.FIRE, SpiritualAffinity.SPACE),
        resistances=(
            SpiritualAffinity.NOVAFIRE,
            SpiritualAffinity.FIRE,
            SpiritualAffinity.SPACE,
        ),
    ),
    SpiritualAffinity.CHRONOPYRE: AffinityRelationship(
        components=(SpiritualAffinity.FIRE, SpiritualAffinity.TIME),
        resistances=(
            SpiritualAffinity.CHRONOPYRE,
            SpiritualAffinity.FIRE,
            SpiritualAffinity.TIME,
        ),
    ),
    SpiritualAffinity.RUINFIRE: AffinityRelationship(
        components=(SpiritualAffinity.FIRE, SpiritualAffinity.ENTROPY),
        resistances=(
            SpiritualAffinity.RUINFIRE,
            SpiritualAffinity.FIRE,
            SpiritualAffinity.ENTROPY,
        ),
    ),
    SpiritualAffinity.QUICKSILVER: AffinityRelationship(
        components=(SpiritualAffinity.WATER, SpiritualAffinity.METAL),
        resistances=(
            SpiritualAffinity.QUICKSILVER,
            SpiritualAffinity.WATER,
            SpiritualAffinity.METAL,
        ),
    ),
    SpiritualAffinity.STORMTIDE: AffinityRelationship(
        components=(SpiritualAffinity.WATER, SpiritualAffinity.LIGHTNING),
        resistances=(
            SpiritualAffinity.STORMTIDE,
            SpiritualAffinity.WATER,
            SpiritualAffinity.LIGHTNING,
        ),
    ),
    SpiritualAffinity.LUMINWAVE: AffinityRelationship(
        components=(SpiritualAffinity.WATER, SpiritualAffinity.LIGHT),
        resistances=(
            SpiritualAffinity.LUMINWAVE,
            SpiritualAffinity.WATER,
            SpiritualAffinity.LIGHT,
        ),
    ),
    SpiritualAffinity.SPRINGTIDE: AffinityRelationship(
        components=(SpiritualAffinity.WATER, SpiritualAffinity.LIFE),
        resistances=(
            SpiritualAffinity.SPRINGTIDE,
            SpiritualAffinity.WATER,
            SpiritualAffinity.LIFE,
        ),
    ),
    SpiritualAffinity.GRAVEFLOOD: AffinityRelationship(
        components=(SpiritualAffinity.WATER, SpiritualAffinity.DEATH),
        resistances=(
            SpiritualAffinity.GRAVEFLOOD,
            SpiritualAffinity.WATER,
            SpiritualAffinity.DEATH,
        ),
    ),
    SpiritualAffinity.VOIDTIDE: AffinityRelationship(
        components=(SpiritualAffinity.WATER, SpiritualAffinity.SPACE),
        resistances=(
            SpiritualAffinity.VOIDTIDE,
            SpiritualAffinity.WATER,
            SpiritualAffinity.SPACE,
        ),
    ),
    SpiritualAffinity.CHRONOTIDE: AffinityRelationship(
        components=(SpiritualAffinity.WATER, SpiritualAffinity.TIME),
        resistances=(
            SpiritualAffinity.CHRONOTIDE,
            SpiritualAffinity.WATER,
            SpiritualAffinity.TIME,
        ),
    ),
    SpiritualAffinity.CORROSION: AffinityRelationship(
        components=(SpiritualAffinity.WATER, SpiritualAffinity.ENTROPY),
        resistances=(
            SpiritualAffinity.CORROSION,
            SpiritualAffinity.WATER,
            SpiritualAffinity.ENTROPY,
        ),
    ),
    SpiritualAffinity.SKYSTEEL: AffinityRelationship(
        components=(SpiritualAffinity.WIND, SpiritualAffinity.METAL),
        resistances=(
            SpiritualAffinity.SKYSTEEL,
            SpiritualAffinity.WIND,
            SpiritualAffinity.METAL,
        ),
    ),
    SpiritualAffinity.AURORAGALE: AffinityRelationship(
        components=(SpiritualAffinity.WIND, SpiritualAffinity.LIGHT),
        resistances=(
            SpiritualAffinity.AURORAGALE,
            SpiritualAffinity.WIND,
            SpiritualAffinity.LIGHT,
        ),
    ),
    SpiritualAffinity.UMBRALZEPHYR: AffinityRelationship(
        components=(SpiritualAffinity.WIND, SpiritualAffinity.DARKNESS),
        resistances=(
            SpiritualAffinity.UMBRALZEPHYR,
            SpiritualAffinity.WIND,
            SpiritualAffinity.DARKNESS,
        ),
    ),
    SpiritualAffinity.VERDANTRUSH: AffinityRelationship(
        components=(SpiritualAffinity.WIND, SpiritualAffinity.LIFE),
        resistances=(
            SpiritualAffinity.VERDANTRUSH,
            SpiritualAffinity.WIND,
            SpiritualAffinity.LIFE,
        ),
    ),
    SpiritualAffinity.REAPERGALE: AffinityRelationship(
        components=(SpiritualAffinity.WIND, SpiritualAffinity.DEATH),
        resistances=(
            SpiritualAffinity.REAPERGALE,
            SpiritualAffinity.WIND,
            SpiritualAffinity.DEATH,
        ),
    ),
    SpiritualAffinity.VOIDDRAFT: AffinityRelationship(
        components=(SpiritualAffinity.WIND, SpiritualAffinity.SPACE),
        resistances=(
            SpiritualAffinity.VOIDDRAFT,
            SpiritualAffinity.WIND,
            SpiritualAffinity.SPACE,
        ),
    ),
    SpiritualAffinity.CHRONOZEPHYR: AffinityRelationship(
        components=(SpiritualAffinity.WIND, SpiritualAffinity.TIME),
        resistances=(
            SpiritualAffinity.CHRONOZEPHYR,
            SpiritualAffinity.WIND,
            SpiritualAffinity.TIME,
        ),
    ),
    SpiritualAffinity.RUINWHIRL: AffinityRelationship(
        components=(SpiritualAffinity.WIND, SpiritualAffinity.ENTROPY),
        resistances=(
            SpiritualAffinity.RUINWHIRL,
            SpiritualAffinity.WIND,
            SpiritualAffinity.ENTROPY,
        ),
    ),
    SpiritualAffinity.STONEFORGE: AffinityRelationship(
        components=(SpiritualAffinity.EARTH, SpiritualAffinity.METAL),
        resistances=(
            SpiritualAffinity.STONEFORGE,
            SpiritualAffinity.EARTH,
            SpiritualAffinity.METAL,
        ),
    ),
    SpiritualAffinity.QUAKEBOLT: AffinityRelationship(
        components=(SpiritualAffinity.EARTH, SpiritualAffinity.LIGHTNING),
        resistances=(
            SpiritualAffinity.QUAKEBOLT,
            SpiritualAffinity.EARTH,
            SpiritualAffinity.LIGHTNING,
        ),
    ),
    SpiritualAffinity.DAWNSTONE: AffinityRelationship(
        components=(SpiritualAffinity.EARTH, SpiritualAffinity.LIGHT),
        resistances=(
            SpiritualAffinity.DAWNSTONE,
            SpiritualAffinity.EARTH,
            SpiritualAffinity.LIGHT,
        ),
    ),
    SpiritualAffinity.UMBRASTONE: AffinityRelationship(
        components=(SpiritualAffinity.EARTH, SpiritualAffinity.DARKNESS),
        resistances=(
            SpiritualAffinity.UMBRASTONE,
            SpiritualAffinity.EARTH,
            SpiritualAffinity.DARKNESS,
        ),
    ),
    SpiritualAffinity.GAIAWARD: AffinityRelationship(
        components=(SpiritualAffinity.EARTH, SpiritualAffinity.LIFE),
        resistances=(
            SpiritualAffinity.GAIAWARD,
            SpiritualAffinity.EARTH,
            SpiritualAffinity.LIFE,
        ),
    ),
    SpiritualAffinity.GRAVELOAM: AffinityRelationship(
        components=(SpiritualAffinity.EARTH, SpiritualAffinity.DEATH),
        resistances=(
            SpiritualAffinity.GRAVELOAM,
            SpiritualAffinity.EARTH,
            SpiritualAffinity.DEATH,
        ),
    ),
    SpiritualAffinity.ASTRALSTONE: AffinityRelationship(
        components=(SpiritualAffinity.EARTH, SpiritualAffinity.SPACE),
        resistances=(
            SpiritualAffinity.ASTRALSTONE,
            SpiritualAffinity.EARTH,
            SpiritualAffinity.SPACE,
        ),
    ),
    SpiritualAffinity.EPOCHSTONE: AffinityRelationship(
        components=(SpiritualAffinity.EARTH, SpiritualAffinity.TIME),
        resistances=(
            SpiritualAffinity.EPOCHSTONE,
            SpiritualAffinity.EARTH,
            SpiritualAffinity.TIME,
        ),
    ),
    SpiritualAffinity.ROTCLAY: AffinityRelationship(
        components=(SpiritualAffinity.EARTH, SpiritualAffinity.ENTROPY),
        resistances=(
            SpiritualAffinity.ROTCLAY,
            SpiritualAffinity.EARTH,
            SpiritualAffinity.ENTROPY,
        ),
    ),
    SpiritualAffinity.FROSTSTEEL: AffinityRelationship(
        components=(SpiritualAffinity.METAL, SpiritualAffinity.ICE),
        resistances=(
            SpiritualAffinity.FROSTSTEEL,
            SpiritualAffinity.METAL,
            SpiritualAffinity.ICE,
        ),
    ),
    SpiritualAffinity.VOLTSTEEL: AffinityRelationship(
        components=(SpiritualAffinity.METAL, SpiritualAffinity.LIGHTNING),
        resistances=(
            SpiritualAffinity.VOLTSTEEL,
            SpiritualAffinity.METAL,
            SpiritualAffinity.LIGHTNING,
        ),
    ),
    SpiritualAffinity.AURICHALCUM: AffinityRelationship(
        components=(SpiritualAffinity.METAL, SpiritualAffinity.LIGHT),
        resistances=(
            SpiritualAffinity.AURICHALCUM,
            SpiritualAffinity.METAL,
            SpiritualAffinity.LIGHT,
        ),
    ),
    SpiritualAffinity.UMBRALSTEEL: AffinityRelationship(
        components=(SpiritualAffinity.METAL, SpiritualAffinity.DARKNESS),
        resistances=(
            SpiritualAffinity.UMBRALSTEEL,
            SpiritualAffinity.METAL,
            SpiritualAffinity.DARKNESS,
        ),
    ),
    SpiritualAffinity.LIVINGSTEEL: AffinityRelationship(
        components=(SpiritualAffinity.METAL, SpiritualAffinity.LIFE),
        resistances=(
            SpiritualAffinity.LIVINGSTEEL,
            SpiritualAffinity.METAL,
            SpiritualAffinity.LIFE,
        ),
    ),
    SpiritualAffinity.NECROSTEEL: AffinityRelationship(
        components=(SpiritualAffinity.METAL, SpiritualAffinity.DEATH),
        resistances=(
            SpiritualAffinity.NECROSTEEL,
            SpiritualAffinity.METAL,
            SpiritualAffinity.DEATH,
        ),
    ),
    SpiritualAffinity.STARSTEEL: AffinityRelationship(
        components=(SpiritualAffinity.METAL, SpiritualAffinity.SPACE),
        resistances=(
            SpiritualAffinity.STARSTEEL,
            SpiritualAffinity.METAL,
            SpiritualAffinity.SPACE,
        ),
    ),
    SpiritualAffinity.CHRONOSTEEL: AffinityRelationship(
        components=(SpiritualAffinity.METAL, SpiritualAffinity.TIME),
        resistances=(
            SpiritualAffinity.CHRONOSTEEL,
            SpiritualAffinity.METAL,
            SpiritualAffinity.TIME,
        ),
    ),
    SpiritualAffinity.RUSTBLIGHT: AffinityRelationship(
        components=(SpiritualAffinity.METAL, SpiritualAffinity.ENTROPY),
        resistances=(
            SpiritualAffinity.RUSTBLIGHT,
            SpiritualAffinity.METAL,
            SpiritualAffinity.ENTROPY,
        ),
    ),
    SpiritualAffinity.AURORAFROST: AffinityRelationship(
        components=(SpiritualAffinity.ICE, SpiritualAffinity.LIGHT),
        resistances=(
            SpiritualAffinity.AURORAFROST,
            SpiritualAffinity.ICE,
            SpiritualAffinity.LIGHT,
        ),
    ),
    SpiritualAffinity.NIGHTFROST: AffinityRelationship(
        components=(SpiritualAffinity.ICE, SpiritualAffinity.DARKNESS),
        resistances=(
            SpiritualAffinity.NIGHTFROST,
            SpiritualAffinity.ICE,
            SpiritualAffinity.DARKNESS,
        ),
    ),
    SpiritualAffinity.FROSTBLOOM: AffinityRelationship(
        components=(SpiritualAffinity.ICE, SpiritualAffinity.LIFE),
        resistances=(
            SpiritualAffinity.FROSTBLOOM,
            SpiritualAffinity.ICE,
            SpiritualAffinity.LIFE,
        ),
    ),
    SpiritualAffinity.GRAVEFROST: AffinityRelationship(
        components=(SpiritualAffinity.ICE, SpiritualAffinity.DEATH),
        resistances=(
            SpiritualAffinity.GRAVEFROST,
            SpiritualAffinity.ICE,
            SpiritualAffinity.DEATH,
        ),
    ),
    SpiritualAffinity.VOIDFROST: AffinityRelationship(
        components=(SpiritualAffinity.ICE, SpiritualAffinity.SPACE),
        resistances=(
            SpiritualAffinity.VOIDFROST,
            SpiritualAffinity.ICE,
            SpiritualAffinity.SPACE,
        ),
    ),
    SpiritualAffinity.CHRONOFROST: AffinityRelationship(
        components=(SpiritualAffinity.ICE, SpiritualAffinity.TIME),
        resistances=(
            SpiritualAffinity.CHRONOFROST,
            SpiritualAffinity.ICE,
            SpiritualAffinity.TIME,
        ),
    ),
    SpiritualAffinity.BLIGHTFROST: AffinityRelationship(
        components=(SpiritualAffinity.ICE, SpiritualAffinity.ENTROPY),
        resistances=(
            SpiritualAffinity.BLIGHTFROST,
            SpiritualAffinity.ICE,
            SpiritualAffinity.ENTROPY,
        ),
    ),
    SpiritualAffinity.PHOTONSTORM: AffinityRelationship(
        components=(SpiritualAffinity.LIGHTNING, SpiritualAffinity.LIGHT),
        resistances=(
            SpiritualAffinity.PHOTONSTORM,
            SpiritualAffinity.LIGHTNING,
            SpiritualAffinity.LIGHT,
        ),
    ),
    SpiritualAffinity.SHADOWBOLT: AffinityRelationship(
        components=(SpiritualAffinity.LIGHTNING, SpiritualAffinity.DARKNESS),
        resistances=(
            SpiritualAffinity.SHADOWBOLT,
            SpiritualAffinity.LIGHTNING,
            SpiritualAffinity.DARKNESS,
        ),
    ),
    SpiritualAffinity.VITALSPARK: AffinityRelationship(
        components=(SpiritualAffinity.LIGHTNING, SpiritualAffinity.LIFE),
        resistances=(
            SpiritualAffinity.VITALSPARK,
            SpiritualAffinity.LIGHTNING,
            SpiritualAffinity.LIFE,
        ),
    ),
    SpiritualAffinity.DOOMSPARK: AffinityRelationship(
        components=(SpiritualAffinity.LIGHTNING, SpiritualAffinity.DEATH),
        resistances=(
            SpiritualAffinity.DOOMSPARK,
            SpiritualAffinity.LIGHTNING,
            SpiritualAffinity.DEATH,
        ),
    ),
    SpiritualAffinity.STARBOLT: AffinityRelationship(
        components=(SpiritualAffinity.LIGHTNING, SpiritualAffinity.SPACE),
        resistances=(
            SpiritualAffinity.STARBOLT,
            SpiritualAffinity.LIGHTNING,
            SpiritualAffinity.SPACE,
        ),
    ),
    SpiritualAffinity.CHRONOBOLT: AffinityRelationship(
        components=(SpiritualAffinity.LIGHTNING, SpiritualAffinity.TIME),
        resistances=(
            SpiritualAffinity.CHRONOBOLT,
            SpiritualAffinity.LIGHTNING,
            SpiritualAffinity.TIME,
        ),
    ),
    SpiritualAffinity.RUINBOLT: AffinityRelationship(
        components=(SpiritualAffinity.LIGHTNING, SpiritualAffinity.ENTROPY),
        resistances=(
            SpiritualAffinity.RUINBOLT,
            SpiritualAffinity.LIGHTNING,
            SpiritualAffinity.ENTROPY,
        ),
    ),
    SpiritualAffinity.SERAPHIC: AffinityRelationship(
        components=(SpiritualAffinity.LIGHT, SpiritualAffinity.LIFE),
        resistances=(
            SpiritualAffinity.SERAPHIC,
            SpiritualAffinity.LIGHT,
            SpiritualAffinity.LIFE,
        ),
    ),
    SpiritualAffinity.MOURNLIGHT: AffinityRelationship(
        components=(SpiritualAffinity.LIGHT, SpiritualAffinity.DEATH),
        resistances=(
            SpiritualAffinity.MOURNLIGHT,
            SpiritualAffinity.LIGHT,
            SpiritualAffinity.DEATH,
        ),
    ),
    SpiritualAffinity.COSMOLIGHT: AffinityRelationship(
        components=(SpiritualAffinity.LIGHT, SpiritualAffinity.SPACE),
        resistances=(
            SpiritualAffinity.COSMOLIGHT,
            SpiritualAffinity.LIGHT,
            SpiritualAffinity.SPACE,
        ),
    ),
    SpiritualAffinity.EPOCHGLOW: AffinityRelationship(
        components=(SpiritualAffinity.LIGHT, SpiritualAffinity.TIME),
        resistances=(
            SpiritualAffinity.EPOCHGLOW,
            SpiritualAffinity.LIGHT,
            SpiritualAffinity.TIME,
        ),
    ),
    SpiritualAffinity.FADEGLOW: AffinityRelationship(
        components=(SpiritualAffinity.LIGHT, SpiritualAffinity.ENTROPY),
        resistances=(
            SpiritualAffinity.FADEGLOW,
            SpiritualAffinity.LIGHT,
            SpiritualAffinity.ENTROPY,
        ),
    ),
    SpiritualAffinity.SHADOWBLOOM: AffinityRelationship(
        components=(SpiritualAffinity.DARKNESS, SpiritualAffinity.LIFE),
        resistances=(
            SpiritualAffinity.SHADOWBLOOM,
            SpiritualAffinity.DARKNESS,
            SpiritualAffinity.LIFE,
        ),
    ),
    SpiritualAffinity.VOIDMOURNE: AffinityRelationship(
        components=(SpiritualAffinity.DARKNESS, SpiritualAffinity.DEATH),
        resistances=(
            SpiritualAffinity.VOIDMOURNE,
            SpiritualAffinity.DARKNESS,
            SpiritualAffinity.DEATH,
        ),
    ),
    SpiritualAffinity.VOIDSHADE: AffinityRelationship(
        components=(SpiritualAffinity.DARKNESS, SpiritualAffinity.SPACE),
        resistances=(
            SpiritualAffinity.VOIDSHADE,
            SpiritualAffinity.DARKNESS,
            SpiritualAffinity.SPACE,
        ),
    ),
    SpiritualAffinity.GLOAMHOUR: AffinityRelationship(
        components=(SpiritualAffinity.DARKNESS, SpiritualAffinity.TIME),
        resistances=(
            SpiritualAffinity.GLOAMHOUR,
            SpiritualAffinity.DARKNESS,
            SpiritualAffinity.TIME,
        ),
    ),
    SpiritualAffinity.ABYSS: AffinityRelationship(
        components=(SpiritualAffinity.DARKNESS, SpiritualAffinity.ENTROPY),
        resistances=(
            SpiritualAffinity.ABYSS,
            SpiritualAffinity.DARKNESS,
            SpiritualAffinity.ENTROPY,
        ),
    ),
    SpiritualAffinity.STARBLOOM: AffinityRelationship(
        components=(SpiritualAffinity.LIFE, SpiritualAffinity.SPACE),
        resistances=(
            SpiritualAffinity.STARBLOOM,
            SpiritualAffinity.LIFE,
            SpiritualAffinity.SPACE,
        ),
    ),
    SpiritualAffinity.EVERBLOOM: AffinityRelationship(
        components=(SpiritualAffinity.LIFE, SpiritualAffinity.TIME),
        resistances=(
            SpiritualAffinity.EVERBLOOM,
            SpiritualAffinity.LIFE,
            SpiritualAffinity.TIME,
        ),
    ),
    SpiritualAffinity.WITHERBLOOM: AffinityRelationship(
        components=(SpiritualAffinity.LIFE, SpiritualAffinity.ENTROPY),
        resistances=(
            SpiritualAffinity.WITHERBLOOM,
            SpiritualAffinity.LIFE,
            SpiritualAffinity.ENTROPY,
        ),
    ),
    SpiritualAffinity.VOIDGRAVE: AffinityRelationship(
        components=(SpiritualAffinity.DEATH, SpiritualAffinity.SPACE),
        resistances=(
            SpiritualAffinity.VOIDGRAVE,
            SpiritualAffinity.DEATH,
            SpiritualAffinity.SPACE,
        ),
    ),
    SpiritualAffinity.DOOMHOUR: AffinityRelationship(
        components=(SpiritualAffinity.DEATH, SpiritualAffinity.TIME),
        resistances=(
            SpiritualAffinity.DOOMHOUR,
            SpiritualAffinity.DEATH,
            SpiritualAffinity.TIME,
        ),
    ),
    SpiritualAffinity.THANATOS: AffinityRelationship(
        components=(SpiritualAffinity.DEATH, SpiritualAffinity.ENTROPY),
        resistances=(
            SpiritualAffinity.THANATOS,
            SpiritualAffinity.DEATH,
            SpiritualAffinity.ENTROPY,
        ),
    ),
    SpiritualAffinity.VOIDDECAY: AffinityRelationship(
        components=(SpiritualAffinity.SPACE, SpiritualAffinity.ENTROPY),
        resistances=(
            SpiritualAffinity.VOIDDECAY,
            SpiritualAffinity.SPACE,
            SpiritualAffinity.ENTROPY,
        ),
    ),
    SpiritualAffinity.HEATDEATH: AffinityRelationship(
        components=(SpiritualAffinity.TIME, SpiritualAffinity.ENTROPY),
        resistances=(
            SpiritualAffinity.HEATDEATH,
            SpiritualAffinity.TIME,
            SpiritualAffinity.ENTROPY,
        ),
    ),
    SpiritualAffinity.ENTROPY: AffinityRelationship(
        components=(SpiritualAffinity.ENTROPY,),
        resistances=(
            SpiritualAffinity.ENTROPY,
            SpiritualAffinity.DEATH,
            SpiritualAffinity.DARKNESS,
        ),
        strengths=(SpiritualAffinity.POISON, SpiritualAffinity.GRAVITY),
        weaknesses=(
            SpiritualAffinity.LIFE,
            SpiritualAffinity.LIGHT,
            SpiritualAffinity.TIME,
        ),
        weakness_modifier=-1.25,
    ),
}


for affinity, relationship in list(AFFINITY_RELATIONSHIPS.items()):
    derived_strengths: dict[SpiritualAffinity, None] = {
        entry: None for entry in relationship.strengths
    }
    for target, target_relationship in AFFINITY_RELATIONSHIPS.items():
        if affinity in target_relationship.weaknesses:
            derived_strengths.setdefault(target, None)
    if derived_strengths:
        AFFINITY_RELATIONSHIPS[affinity] = replace(
            relationship,
            strengths=tuple(derived_strengths.keys()),
        )


def affinity_relationship_modifier(
    source: SpiritualAffinity, target: SpiritualAffinity | None
) -> float:
    if target is None:
        return 0.0
    relationship = AFFINITY_RELATIONSHIPS.get(source)
    if relationship is None:
        return 1.0 if source == target else 0.0
    return relationship.modifier_for(target)


def affinity_overlap_fraction(
    source: SpiritualAffinity, target: SpiritualAffinity | Sequence[SpiritualAffinity] | None
) -> float:
    """Backward compatible wrapper returning the strongest relationship modifier."""

    targets = normalize_affinities(target)
    if not targets:
        return 0.0
    return max(affinity_relationship_modifier(source, candidate) for candidate in targets)


def resistance_reduction_fraction(
    attack_affinity: SpiritualAffinity | Sequence[SpiritualAffinity] | None,
    resistances: Sequence[SpiritualAffinity],
) -> float:
    affinities = normalize_affinities(attack_affinity)
    if not affinities:
        return 0.0

    def _single_reduction(affinity: SpiritualAffinity) -> float:
        total_overlap = 0.0
        for resistance in resistances:
            total_overlap += affinity_relationship_modifier(resistance, affinity)
        return max(-1.0, min(total_overlap, 1.0))

    return min((_single_reduction(entry) for entry in affinities), default=0.0)


def coerce_affinity(value: SpiritualAffinity | str) -> SpiritualAffinity:
    if isinstance(value, SpiritualAffinity):
        return value
    return SpiritualAffinity(str(value).replace(" ", "_").lower())


def normalize_affinities(
    value: SpiritualAffinity
    | str
    | Sequence[SpiritualAffinity | str | None]
    | None,
) -> tuple[SpiritualAffinity, ...]:
    if value is None:
        return ()
    if isinstance(value, SpiritualAffinity):
        return (value,)
    if isinstance(value, str):
        try:
            return (coerce_affinity(value),)
        except ValueError:
            return ()
    if isinstance(value, Sequence):
        entries: list[SpiritualAffinity] = []
        for entry in value:
            if entry is None:
                continue
            if isinstance(entry, SpiritualAffinity):
                candidate = entry
            else:
                try:
                    candidate = coerce_affinity(entry)
                except ValueError:
                    continue
            if candidate not in entries:
                entries.append(candidate)
        return tuple(entries)
    try:
        coerced = coerce_affinity(value)
    except ValueError:
        return ()
    return (coerced,)


@dataclass(slots=True)
class PassiveHealEffect:
    """Represents periodic healing granted by a passive skill."""

    skill_key: str
    amount: float
    interval: int
    pool: str = "hp"

    def applies_on_round(self, round_number: int) -> bool:
        return self.interval > 0 and round_number % self.interval == 0

    @property
    def is_soul_pool(self) -> bool:
        return self.pool == "soul"


@dataclass(slots=True, init=False)
class Skill:
    key: str
    name: str
    grade: str
    skill_type: DamageType
    damage_ratio: float
    proficiency_max: int
    description: str = ""
    elements: tuple[SpiritualAffinity, ...] = field(default_factory=tuple)
    element: Optional[SpiritualAffinity] = None
    evolves_to: Optional[str] = None
    evolution_requirements: Dict[str, str] = field(default_factory=dict)
    trigger_chance: float = 0.2
    weapon: Optional[WeaponType] = None
    archetype: SkillArchetype = SkillArchetype.GENERAL
    category: SkillCategory = SkillCategory.ACTIVE
    stat_bonuses: Stats = field(default_factory=Stats)
    passive_heal_amount: float = 0.0
    passive_heal_interval: int = 0
    passive_heal_pool: str = "hp"

    def __init__(
        self,
        key: str,
        name: str,
        grade: str,
        skill_type: DamageType,
        damage_ratio: float,
        proficiency_max: int,
        description: str = "",
        *,
        element: Optional[SpiritualAffinity] = None,
        elements: Optional[Sequence[SpiritualAffinity | str | None]] = None,
        evolves_to: Optional[str] = None,
        evolution_requirements: Optional[Mapping[str, str]] = None,
        trigger_chance: float = 0.2,
        weapon: Optional[WeaponType] = None,
        archetype: SkillArchetype | str | None = SkillArchetype.GENERAL,
        category: SkillCategory | str | None = SkillCategory.ACTIVE,
        stat_bonuses: Stats | Mapping[str, float | int] | None = None,
        passive_heal_amount: float | int | None = None,
        passive_heal_interval: int | None = None,
        passive_heal_pool: str | None = None,
    ) -> None:
        self.key = key
        self.name = name
        self.grade = grade
        self.skill_type = skill_type
        self.damage_ratio = damage_ratio
        self.proficiency_max = proficiency_max
        self.description = description
        self.elements = tuple(elements or ())
        self.element = element
        self.evolves_to = evolves_to
        self.evolution_requirements = (
            dict(evolution_requirements) if evolution_requirements is not None else {}
        )
        self.trigger_chance = trigger_chance
        self.weapon = weapon
        self.archetype = SkillArchetype.from_value(archetype)
        self.category = SkillCategory.from_value(category)
        if isinstance(stat_bonuses, Stats):
            self.stat_bonuses = stat_bonuses
        elif isinstance(stat_bonuses, Mapping):
            self.stat_bonuses = Stats.from_mapping(stat_bonuses)
        else:
            self.stat_bonuses = Stats()
        self.passive_heal_amount = (
            float(passive_heal_amount)
            if passive_heal_amount is not None
            else 0.0
        )
        self.passive_heal_interval = (
            int(passive_heal_interval)
            if passive_heal_interval is not None
            else 0
        )
        self.passive_heal_pool = passive_heal_pool or "hp"
        self.__post_init__()

    def damage_for(self, base_stat: float, proficiency: int) -> float:
        bonus_steps = min(proficiency // (self.proficiency_max // 2 or 1), 2)
        return base_stat * (self.damage_ratio + 0.1 * bonus_steps)

    def __post_init__(self) -> None:
        if not isinstance(self.description, str):
            self.description = str(self.description or "")
        if not isinstance(self.skill_type, DamageType):
            self.skill_type = DamageType.from_value(self.skill_type)  # type: ignore[assignment]
        if isinstance(self.element, str) and self.element:
            self.element = SpiritualAffinity(self.element)
        normalized_elements = normalize_affinities(self.elements)
        if not normalized_elements and self.element is not None:
            normalized_elements = normalize_affinities(self.element)
        self.elements = normalized_elements
        if self.element is None and self.elements:
            self.element = self.elements[0]
        elif self.element is not None and self.element not in self.elements:
            self.elements = (self.element,) + tuple(
                affinity for affinity in self.elements if affinity is not self.element
            )
        if self.weapon is not None and not isinstance(self.weapon, WeaponType):
            try:
                self.weapon = WeaponType.from_value(self.weapon, default=WeaponType.BARE_HAND)
            except ValueError:
                self.weapon = WeaponType.BARE_HAND
        if not isinstance(self.archetype, SkillArchetype):
            self.archetype = SkillArchetype.from_value(self.archetype)
        if (
            self.archetype is SkillArchetype.GENERAL
            and self.weapon
            and self.weapon is not WeaponType.BARE_HAND
        ):
            self.archetype = SkillArchetype.WEAPON
        try:
            chance = float(self.trigger_chance)
        except (TypeError, ValueError):
            chance = 0.0
        if chance > 1:
            chance /= 100.0
        self.trigger_chance = max(0.0, min(1.0, chance))
        try:
            self.passive_heal_amount = max(0.0, float(self.passive_heal_amount))
        except (TypeError, ValueError):
            self.passive_heal_amount = 0.0
        try:
            interval = int(self.passive_heal_interval)
        except (TypeError, ValueError):
            interval = 0
        self.passive_heal_interval = max(0, interval)
        pool = str(self.passive_heal_pool or "hp").strip().lower()
        if pool not in {"hp", "soul"}:
            pool = "hp"
        self.passive_heal_pool = pool

    def passive_heal_effect(self) -> Optional[PassiveHealEffect]:
        if self.passive_heal_interval <= 0 or self.passive_heal_amount <= 0:
            return None
        return PassiveHealEffect(
            skill_key=self.key,
            amount=self.passive_heal_amount,
            interval=self.passive_heal_interval,
            pool=self.passive_heal_pool,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Skill":
        payload = dict(data)
        stat_payload = payload.pop("stat_bonuses", payload.pop("stat_bonus", None))
        if stat_payload is not None:
            if isinstance(stat_payload, Stats):
                payload["stat_bonuses"] = stat_payload
            elif isinstance(stat_payload, Mapping):
                payload["stat_bonuses"] = Stats.from_mapping(stat_payload)
        payload.pop("consumption", None)
        payload.pop("resource", None)
        if "category" in payload:
            payload["category"] = SkillCategory.from_value(payload["category"])
        elif "skill_category" in payload:
            payload["category"] = SkillCategory.from_value(payload.pop("skill_category"))
        if "archetype" in payload:
            payload["archetype"] = SkillArchetype.from_value(payload["archetype"])
        return cls(**payload)


class SkillValidator(ModelValidator):
    model = Skill
    fields = {
        "key": FieldSpec(is_non_empty_str, "a non-empty string key"),
        "name": FieldSpec(is_non_empty_str, "a non-empty string name"),
        "grade": FieldSpec(is_non_empty_str, "a non-empty string grade"),
        "skill_type": FieldSpec(
            (DamageType, str), "a damage type identifier"
        ),
        "damage_ratio": FieldSpec(float, "a numeric damage ratio"),
        "proficiency_max": FieldSpec(int, "an integer proficiency maximum"),
        "description": FieldSpec(str, "a string description", required=False, allow_none=True),
        "element": FieldSpec(
            (SpiritualAffinity, str),
            "an affinity identifier",
            required=False,
            allow_none=True,
        ),
        "evolves_to": FieldSpec(str, "an evolution key", required=False, allow_none=True),
        "evolution_requirements": FieldSpec(
            MappingSpec(str, str),
            "a mapping of requirement keys to values",
            required=False,
        ),
        "trigger_chance": FieldSpec(float, "a numeric trigger chance", required=False),
        "weapon": FieldSpec(
            (WeaponType, str), "a weapon type identifier", required=False, allow_none=True
        ),
        "archetype": FieldSpec(
            (SkillArchetype, str),
            "a combat archetype identifier",
            required=False,
            allow_none=True,
        ),
        "category": FieldSpec(
            (SkillCategory, str), "a skill category", required=False
        ),
        "stat_bonuses": FieldSpec(
            (Stats, MappingSpec(str, (int, float))),
            "stat bonuses mapping",
            required=False,
        ),
        "passive_heal_amount": FieldSpec(
            float, "a numeric passive heal amount", required=False
        ),
        "passive_heal_interval": FieldSpec(
            int, "an integer passive heal interval", required=False
        ),
        "passive_heal_pool": FieldSpec(
            str,
            "a healing pool identifier",
            required=False,
        ),
    }


Skill.validator = SkillValidator


def skill_grade_number(grade: str | None) -> Optional[int]:
    if not grade:
        return None
    grade_text = str(grade).strip()
    if not grade_text:
        return None
    digit_match = re.search(r"(\d+)", grade_text)
    if digit_match:
        value = int(digit_match.group(1))
    else:
        roman_match = re.search(r"\b([ivxlcdm]+)\b", grade_text, re.IGNORECASE)
        if roman_match:
            roman_value = roman_match.group(1).upper()
            roman_map = {
                "I": 1,
                "II": 2,
                "III": 3,
                "IV": 4,
                "V": 5,
                "VI": 6,
                "VII": 7,
                "VIII": 8,
                "IX": 9,
                "X": 10,
            }
            value = roman_map.get(roman_value, 1)
        else:
            word_match = re.search(
                r"\b(one|two|three|four|five|six|seven|eight|nine|ten)\b",
                grade_text,
                re.IGNORECASE,
            )
            if word_match:
                word_map = {
                    "one": 1,
                    "two": 2,
                    "three": 3,
                    "four": 4,
                    "five": 5,
                    "six": 6,
                    "seven": 7,
                    "eight": 8,
                    "nine": 9,
                    "ten": 10,
                }
                value = word_map.get(word_match.group(1).lower(), 1)
            else:
                return None
    return max(1, min(10, value))


@dataclass(slots=True)
class CombatTurn:
    attacker_id: int
    skill_key: Optional[str]
    damage: float
    critical: bool
    defeated: List[int] = field(default_factory=list)


@dataclass(slots=True)
class CombatEncounter:
    encounter_id: str
    party_id: str
    enemy_keys: List[str]
    boss_key: Optional[str]
    turn_order: List[str | int]
    current_turn: int
    player_hp: Dict[int, float]
    player_max_hp: Dict[int, float]
    player_stamina: Dict[int, float]
    player_max_stamina: Dict[int, float]
    player_qi: Dict[int, float]
    player_max_qi: Dict[int, float]
    player_soul_hp: Dict[int, float]
    player_max_soul_hp: Dict[int, float]
    enemy_hp: Dict[str, float]
    enemy_max_hp: Dict[str, float]
    enemy_soul_hp: Dict[str, float]
    enemy_max_soul_hp: Dict[str, float]
    log: List[CombatTurn] = field(default_factory=list)
    history: List[str] = field(default_factory=list)
    message_id: Optional[int] = None
    turns_taken: int = 0
    round_number: int = 1


__all__ = [
    "DamageType",
    "SkillCategory",
    "SkillArchetype",
    "WeaponType",
    "PLAYER_STAT_NAMES",
    "Stats",
    "StatMultipliers",
    "default_stats",
    "SpiritualAffinity",
    "AffinityRelationship",
    "AFFINITY_RELATIONSHIPS",
    "affinity_relationship_modifier",
    "affinity_overlap_fraction",
    "resistance_reduction_fraction",
    "coerce_affinity",
    "normalize_affinities",
    "PassiveHealEffect",
    "Skill",
    "skill_grade_number",
    "CombatTurn",
    "CombatEncounter",
]
