"""Progression-related domain models."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from ._validation import (
    FieldSpec,
    MappingSpec,
    ModelValidator,
    SequenceSpec,
    is_non_empty_str,
)

from .combat import (
    PLAYER_STAT_NAMES,
    Stats,
    StatMultipliers,
    SpiritualAffinity,
    affinity_overlap_fraction,
    coerce_affinity,
    _multipliers_from_attrs,
)
from .innate_souls import InnateSoul, InnateSoulSet


class CultivationPath(str, Enum):
    """Enumerates the supported cultivation paths."""

    BODY = "body"
    QI = "qi"
    SOUL = "soul"

    @classmethod
    def from_value(cls, value: str | "CultivationPath") -> "CultivationPath":
        if isinstance(value, cls):
            return value
        normalized = str(value).lower()
        try:
            return cls(normalized)
        except ValueError:
            return cls.QI


class EquipmentSlot(str, Enum):
    """Equipment positions available to a cultivator."""

    NECKLACE = "necklace"
    ARMOR = "armor"
    BELT = "belt"
    BOOTS = "boots"
    ACCESSORY = "accessory"
    WEAPON = "weapon"

    @classmethod
    def from_value(
        cls, value: "EquipmentSlot | str | None", *, default: "EquipmentSlot | None" = None
    ) -> "EquipmentSlot":
        if isinstance(value, cls):
            return value
        if value is None:
            if default is not None:
                return default
            raise ValueError("Equipment slot cannot be None")
        normalized = str(value).strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        if default is not None:
            return default
        raise ValueError(f"Unknown equipment slot: {value}")


EQUIPMENT_SLOT_CAPACITY: Dict[EquipmentSlot, int] = {
    EquipmentSlot.NECKLACE: 1,
    EquipmentSlot.ARMOR: 1,
    EquipmentSlot.BELT: 1,
    EquipmentSlot.BOOTS: 1,
    EquipmentSlot.ACCESSORY: 4,
    EquipmentSlot.WEAPON: 1,
}

EQUIPMENT_SLOT_ORDER: Tuple[EquipmentSlot, ...] = (
    EquipmentSlot.NECKLACE,
    EquipmentSlot.ARMOR,
    EquipmentSlot.BELT,
    EquipmentSlot.BOOTS,
    EquipmentSlot.ACCESSORY,
    EquipmentSlot.WEAPON,
)


def equipment_slot_capacity(slot: EquipmentSlot | str) -> int:
    try:
        slot_key = EquipmentSlot.from_value(slot)
    except ValueError:
        return 0
    return EQUIPMENT_SLOT_CAPACITY.get(slot_key, 0)


DEFAULT_STAGE_BASE_STAT = 10.0

MORTAL_REALM_KEY = "mortal"
MORTAL_REALM_NAME = "Mortal"


STAGE_STAT_TARGETS: dict[CultivationPath, tuple[str, ...]] = {
    CultivationPath.QI: PLAYER_STAT_NAMES,
    CultivationPath.BODY: PLAYER_STAT_NAMES,
    CultivationPath.SOUL: PLAYER_STAT_NAMES,
}


class CultivationPhase(str, Enum):
    """Enumerates the major phases for cultivation realms."""

    INITIAL = "initial"
    EARLY = "early"
    MID = "mid"
    LATE = "late"
    PEAK = "peak"

    @property
    def order_index(self) -> int:
        order = {
            CultivationPhase.INITIAL: 0,
            CultivationPhase.EARLY: 1,
            CultivationPhase.MID: 2,
            CultivationPhase.LATE: 3,
            CultivationPhase.PEAK: 4,
        }
        return order[self]

    @property
    def display_name(self) -> str:
        return self.value.title()

    @classmethod
    def from_value(cls, value: "CultivationPhase | str") -> "CultivationPhase":
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower()
        try:
            return cls(normalized)
        except ValueError:
            return cls.INITIAL


@dataclass(slots=True)
class CultivationStage:
    """Represents a cultivation stage that players can progress through."""

    key: str
    name: str
    success_rate: float
    path: str = CultivationPath.QI.value
    base_stat: float = 10.0
    strength: float = 0.0
    physique: float = 0.0
    agility: float = 0.0
    breakthrough_failure_loss: float = 0.75
    exp_required: int = 100
    role_id: Optional[int] = None
    realm: str = ""
    phase: CultivationPhase = CultivationPhase.INITIAL
    realm_order: int = 0
    step: int = 1
    lifespan: str = ""

    def __post_init__(self) -> None:
        try:
            path_value = CultivationPath.from_value(self.path)
        except ValueError:
            path_value = CultivationPath.QI
        self.path = path_value.value

        try:
            required = int(self.exp_required)
        except (TypeError, ValueError):
            required = 100
        self.exp_required = max(1, required)

        if self.realm:
            self.realm = str(self.realm).strip() or self.name
        else:
            self.realm = self.name

        self.phase = CultivationPhase.from_value(self.phase)

        try:
            order = int(self.realm_order)
        except (TypeError, ValueError):
            order = 0
        self.realm_order = max(-1_000_000, min(1_000_000, order))

        try:
            step_value = int(self.step)
        except (TypeError, ValueError):
            step_value = 1
        self.step = max(0, min(1_000_000, step_value))

        self.lifespan = str(self.lifespan or "").strip()

    @property
    def stat_bonuses(self) -> Stats:
        allowed = STAGE_STAT_TARGETS.get(CultivationPath.from_value(self.path), ())
        values = {name: 0.0 for name in PLAYER_STAT_NAMES}
        for name in allowed:
            values[name] = getattr(self, name, 0.0)
        return Stats(**values)

    @property
    def phase_display(self) -> str:
        if self.is_mortal:
            return ""
        return self.phase.display_name

    @property
    def combined_name(self) -> str:
        realm = self.realm or self.name
        phase_name = self.phase_display
        if not phase_name:
            return realm
        return f"{realm} — {phase_name}"

    @property
    def is_mortal(self) -> bool:
        key = str(self.key or "").strip().lower()
        realm = str(self.realm or "").strip().lower()
        return key == MORTAL_REALM_KEY or realm == MORTAL_REALM_KEY

    @property
    def ordering_tuple(self) -> tuple[int, int, float, float, str]:
        return (
            self.realm_order,
            self.phase.order_index,
            float(self.base_stat),
            float(self.success_rate),
            self.key,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CultivationStage":
        payload = dict(data)
        if "path" in payload:
            payload["path"] = str(payload["path"]).lower()
        elif "stage_type" in payload:
            payload["path"] = str(payload.pop("stage_type")).lower()
        elif "cultivation_path" in payload:
            payload["path"] = str(payload.pop("cultivation_path")).lower()
        if "phase" in payload:
            payload["phase"] = CultivationPhase.from_value(payload["phase"]).value
        elif "tier" in payload:
            payload["phase"] = CultivationPhase.from_value(payload.pop("tier")).value
        payload.setdefault("realm", payload.get("realm_name", payload.get("realm_key", "")))
        if "realm_order" not in payload and "realm_index" in payload:
            payload["realm_order"] = payload.pop("realm_index")
        bonuses = payload.pop("stat_bonuses", None)
        payload.pop("min_level", None)
        if bonuses is not None:
            stats = bonuses if isinstance(bonuses, Stats) else Stats.from_mapping(bonuses)
            for name, value in stats.items():
                payload[name] = value
        if "exp_required" in payload:
            try:
                payload["exp_required"] = int(payload["exp_required"])
            except (TypeError, ValueError):
                payload.pop("exp_required")
        allowed_keys = {field.name for field in fields(cls)}
        for key in list(payload):
            if key not in allowed_keys:
                payload.pop(key, None)
        return cls(**payload)


class CultivationStageValidator(ModelValidator):
    model = CultivationStage
    fields = {
        "key": FieldSpec(is_non_empty_str, "a non-empty string key"),
        "name": FieldSpec(is_non_empty_str, "a non-empty string name"),
        "success_rate": FieldSpec(float, "a numeric success rate"),
        "path": FieldSpec(
            (CultivationPath, str), "a cultivation path identifier", required=False
        ),
        "base_stat": FieldSpec(float, "a numeric base stat", required=False),
        "strength": FieldSpec(float, "a numeric strength bonus", required=False),
        "physique": FieldSpec(float, "a numeric physique bonus", required=False),
        "agility": FieldSpec(float, "a numeric agility bonus", required=False),
        "breakthrough_failure_loss": FieldSpec(
            float, "a numeric failure loss", required=False
        ),
        "exp_required": FieldSpec(int, "an integer experience requirement", required=False),
        "role_id": FieldSpec(int, "a Discord role identifier", required=False, allow_none=True),
        "realm": FieldSpec(str, "a realm name", required=False, allow_none=True),
        "phase": FieldSpec((CultivationPhase, str), "a cultivation phase", required=False),
        "realm_order": FieldSpec(int, "an integer realm order", required=False),
        "step": FieldSpec(int, "an integer step", required=False),
        "lifespan": FieldSpec(
            str, "a textual lifespan description", required=False, allow_none=True
        ),
    }


CultivationStage.validator = CultivationStageValidator


@dataclass(slots=True)
class Race:
    """Defines talent dice and stat multipliers for a race."""

    key: str
    name: str
    description: str = ""
    innate_dice_count: int = 5
    innate_dice_faces: int = 5
    innate_drop_lowest: int = 1
    strength: float = 1.0
    physique: float = 1.0
    agility: float = 1.0
    role_id: int | None = None

    def __post_init__(self) -> None:
        try:
            dice_count = int(self.innate_dice_count)
        except (TypeError, ValueError):
            dice_count = 5
        try:
            dice_faces = int(self.innate_dice_faces)
        except (TypeError, ValueError):
            dice_faces = 5
        try:
            drop = int(self.innate_drop_lowest)
        except (TypeError, ValueError):
            drop = 1
        self.innate_dice_count = max(1, dice_count)
        self.innate_dice_faces = max(1, dice_faces)
        self.innate_drop_lowest = max(0, min(drop, self.innate_dice_count - 1))

        if self.description is None:
            self.description = ""

    @property
    def stat_multipliers(self) -> StatMultipliers:
        return _multipliers_from_attrs(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Race":
        payload = dict(data)
        multipliers = payload.pop("stat_multipliers", None)
        if multipliers is not None:
            stats = (
                multipliers
                if isinstance(multipliers, StatMultipliers)
                else StatMultipliers.from_mapping(multipliers)
            )
            for name, value in stats.to_mapping().items():
                payload[name] = value
        return cls(**payload)


class RaceValidator(ModelValidator):
    model = Race
    fields = {
        "key": FieldSpec(is_non_empty_str, "a non-empty string key"),
        "name": FieldSpec(is_non_empty_str, "a non-empty string name"),
        "description": FieldSpec(str, "a textual description", required=False, allow_none=True),
        "innate_dice_count": FieldSpec(int, "an integer dice count", required=False),
        "innate_dice_faces": FieldSpec(int, "an integer dice face count", required=False),
        "innate_drop_lowest": FieldSpec(int, "an integer drop count", required=False),
        "strength": FieldSpec(float, "a strength multiplier", required=False),
        "physique": FieldSpec(float, "a physique multiplier", required=False),
        "agility": FieldSpec(float, "an agility multiplier", required=False),
        "role_id": FieldSpec(int, "a Discord role identifier", required=False, allow_none=True),
    }


Race.validator = RaceValidator


@dataclass(slots=True)
class SpecialTrait:
    key: str
    name: str
    description: str
    strength: float = 1.0
    physique: float = 1.0
    agility: float = 1.0
    grants_titles: List[str] = field(default_factory=list)
    grants_affinities: List[SpiritualAffinity] = field(default_factory=list)
    role_id: int | None = None

    @property
    def stat_multipliers(self) -> StatMultipliers:
        return _multipliers_from_attrs(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SpecialTrait":
        payload = dict(data)
        modifiers = payload.pop("stat_multipliers", None)
        if modifiers is not None:
            stats = (
                modifiers
                if isinstance(modifiers, StatMultipliers)
                else StatMultipliers.from_mapping(modifiers)
            )
            for name, value in stats.to_mapping().items():
                payload[name] = value
        payload.setdefault("grants_titles", payload.pop("title_rewards", []))
        raw_affinities = payload.pop("grants_affinities", [])
        affinities: list[SpiritualAffinity] = []
        if isinstance(raw_affinities, (list, tuple, set)):
            source = raw_affinities
        elif raw_affinities:
            source = [raw_affinities]
        else:
            source = []
        for entry in source:
            try:
                affinities.append(coerce_affinity(entry))
            except ValueError:
                continue
        payload["grants_affinities"] = affinities
        if "role_id" in payload:
            role_value = payload.get("role_id")
            role_id: int | None
            try:
                role_id = int(role_value) if role_value is not None else None
            except (TypeError, ValueError):
                role_id = None
            else:
                if role_id is None or role_id <= 0:
                    role_id = None
            payload["role_id"] = role_id
        return cls(**payload)


class SpecialTraitValidator(ModelValidator):
    model = SpecialTrait
    fields = {
        "key": FieldSpec(is_non_empty_str, "a non-empty string key"),
        "name": FieldSpec(is_non_empty_str, "a non-empty string name"),
        "description": FieldSpec(str, "a textual description"),
        "strength": FieldSpec(float, "a strength multiplier", required=False),
        "physique": FieldSpec(float, "a physique multiplier", required=False),
        "agility": FieldSpec(float, "an agility multiplier", required=False),
        "grants_titles": FieldSpec(
            SequenceSpec(str),
            "a list of granted title keys",
            required=False,
        ),
        "grants_affinities": FieldSpec(
            SequenceSpec((SpiritualAffinity, str)),
            "a list of granted affinities",
            required=False,
        ),
        "role_id": FieldSpec(int, "a Discord role identifier", required=False, allow_none=True),
    }


SpecialTrait.validator = SpecialTraitValidator


class TechniqueStatMode(str, Enum):
    """Determines how cultivation technique stat values are applied."""

    ADDITION = "addition"
    MULTIPLIER = "multiplier"

    @classmethod
    def from_value(
        cls, value: "TechniqueStatMode | str | None"
    ) -> "TechniqueStatMode":
        if isinstance(value, cls):
            return value
        if value is None:
            return cls.ADDITION
        normalized = str(value).strip().lower()
        try:
            return cls(normalized)
        except ValueError:
            return cls.ADDITION


@dataclass(slots=True)
class CultivationTechnique:
    """Non-combat techniques that boost training efficiency and stats."""

    key: str
    name: str
    grade: str
    path: str
    description: str = ""
    experience_addition: float = 0.0
    experience_multiplier: float = 0.0
    affinity: SpiritualAffinity | None = None
    skills: list[str] = field(default_factory=list)
    stat_mode: TechniqueStatMode = TechniqueStatMode.ADDITION
    stats: Stats = field(default_factory=Stats)

    def __post_init__(self) -> None:
        self.grade = str(self.grade or "").strip() or "mortal"
        try:
            path_value = CultivationPath.from_value(self.path)
        except ValueError:
            path_value = CultivationPath.QI
        self.path = path_value.value

        try:
            addition = float(self.experience_addition)
        except (TypeError, ValueError):
            addition = 0.0
        self.experience_addition = addition

        try:
            multiplier = float(self.experience_multiplier)
        except (TypeError, ValueError):
            multiplier = 0.0
        self.experience_multiplier = multiplier

        if self.affinity is not None and not isinstance(self.affinity, SpiritualAffinity):
            try:
                self.affinity = SpiritualAffinity(str(self.affinity))
            except ValueError:
                self.affinity = None

        if not isinstance(self.skills, list):
            if self.skills:
                self.skills = [str(self.skills)]
            else:
                self.skills = []
        else:
            self.skills = [str(entry) for entry in self.skills if entry]

        self.stat_mode = TechniqueStatMode.from_value(self.stat_mode)

        if isinstance(self.stats, Stats):
            stats = self.stats
        elif isinstance(self.stats, Mapping):
            stats = Stats.from_mapping(self.stats)
        else:
            values = {name: 0.0 for name in PLAYER_STAT_NAMES}
            for name in PLAYER_STAT_NAMES:
                raw = getattr(self, name, None)
                if raw is None:
                    continue
                try:
                    values[name] = float(raw)
                except (TypeError, ValueError):
                    continue
            stats = Stats.from_mapping(values)
        self.stats = stats

    def experience_adjustments(
        self, path: CultivationPath | str
    ) -> tuple[float, float]:
        try:
            requested_path = CultivationPath.from_value(path)
        except ValueError:
            return 0.0, 0.0
        technique_path = CultivationPath.from_value(self.path)
        if requested_path is not technique_path:
            return 0.0, 0.0
        return self.experience_addition, self.experience_multiplier

    def is_affinity_compatible(
        self, base: InnateSoulSet | None
    ) -> bool:
        if self.affinity is None:
            return True
        if base is None:
            return False
        return base.matches(self.affinity)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CultivationTechnique":
        payload = dict(data)
        payload.setdefault("description", payload.pop("details", ""))
        payload.setdefault("grade", payload.get("rank", "mortal"))

        if "path" not in payload:
            for alias in ("cultivation_path", "stage_type", "training_path"):
                if alias in payload:
                    payload["path"] = payload.pop(alias)
                    break

        for alias in ("experience_addition", "exp_addition", "addition_bonus"):
            if alias in payload:
                try:
                    payload["experience_addition"] = float(payload.pop(alias))
                except (TypeError, ValueError):
                    payload["experience_addition"] = 0.0
                break
        else:
            payload.setdefault("experience_addition", 0.0)

        for alias in ("experience_multiplier", "exp_multiplier", "multiplier_bonus"):
            if alias in payload:
                try:
                    payload["experience_multiplier"] = float(payload.pop(alias))
                except (TypeError, ValueError):
                    payload["experience_multiplier"] = 0.0
                break
        else:
            payload.setdefault("experience_multiplier", 0.0)

        if "stat_mode" not in payload and "stats_mode" in payload:
            payload["stat_mode"] = payload.pop("stats_mode")

        stats_payload = payload.pop("stats", None)
        if stats_payload is not None:
            payload["stats"] = Stats.from_mapping(stats_payload)
        else:
            stat_values = {
                name: payload.pop(name, 0.0)
                for name in PLAYER_STAT_NAMES
                if name in payload
            }
            if stat_values:
                payload["stats"] = Stats.from_mapping(stat_values)

        skills_payload = payload.get("skills")
        if skills_payload is None and "grants_skills" in payload:
            skills_payload = payload.pop("grants_skills")
        if skills_payload is not None:
            if isinstance(skills_payload, (list, tuple, set)):
                payload["skills"] = [str(entry) for entry in skills_payload if entry]
            elif skills_payload:
                payload["skills"] = [str(skills_payload)]
            else:
                payload["skills"] = []

        affinity_value = payload.get("affinity")
        if affinity_value and not isinstance(affinity_value, SpiritualAffinity):
            try:
                payload["affinity"] = SpiritualAffinity(str(affinity_value))
            except ValueError:
                payload["affinity"] = None

        return cls(**payload)


class CultivationTechniqueValidator(ModelValidator):
    model = CultivationTechnique
    fields = {
        "key": FieldSpec(is_non_empty_str, "a non-empty string key"),
        "name": FieldSpec(is_non_empty_str, "a non-empty string name"),
        "grade": FieldSpec(is_non_empty_str, "a non-empty string grade"),
        "path": FieldSpec((CultivationPath, str), "a cultivation path"),
        "description": FieldSpec(str, "a textual description", required=False, allow_none=True),
        "experience_addition": FieldSpec(
            float, "a numeric experience addition", required=False
        ),
        "experience_multiplier": FieldSpec(
            float, "a numeric experience multiplier", required=False
        ),
        "affinity": FieldSpec(
            (SpiritualAffinity, str),
            "an affinity identifier",
            required=False,
            allow_none=True,
        ),
        "skills": FieldSpec(
            SequenceSpec(str), "a list of skill keys", required=False
        ),
        "stat_mode": FieldSpec(
            (TechniqueStatMode, str), "a stat mode", required=False
        ),
        "stats": FieldSpec(
            (Stats, MappingSpec(str, (int, float))),
            "stat bonuses",
            required=False,
        ),
    }


CultivationTechnique.validator = CultivationTechniqueValidator


class TitlePosition(str, Enum):
    """Placement options for equippable titles."""

    PREFIX = "prefix"
    SUFFIX = "suffix"

    @classmethod
    def from_value(cls, value: "TitlePosition | str") -> "TitlePosition":
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise ValueError(f"Unknown title position: {value!r}") from exc


@dataclass(slots=True)
class Title:
    """Represents an honorific that can be earned by players."""

    key: str
    name: str
    description: str
    position: TitlePosition = TitlePosition.PREFIX

    def __post_init__(self) -> None:
        self.position = TitlePosition.from_value(self.position)


class TitleValidator(ModelValidator):
    model = Title
    fields = {
        "key": FieldSpec(is_non_empty_str, "a non-empty string key"),
        "name": FieldSpec(is_non_empty_str, "a non-empty string name"),
        "description": FieldSpec(str, "a textual description"),
        "position": FieldSpec(
            (TitlePosition, str), "a title position", required=False
        ),
    }


Title.validator = TitleValidator


__all__ = [
    "CultivationPath",
    "EquipmentSlot",
    "EQUIPMENT_SLOT_CAPACITY",
    "EQUIPMENT_SLOT_ORDER",
    "equipment_slot_capacity",
    "DEFAULT_STAGE_BASE_STAT",
    "STAGE_STAT_TARGETS",
    "InnateSoul",
    "InnateSoulSet",
    "CultivationPhase",
    "CultivationStage",
    "Race",
    "SpecialTrait",
    "TechniqueStatMode",
    "CultivationTechnique",
    "Title",
    "TitlePosition",
]
