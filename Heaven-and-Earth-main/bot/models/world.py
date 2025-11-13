"""World and content related domain models."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence

from .combat import (
    SpiritualAffinity,
    Stats,
    WeaponType,
    PLAYER_STAT_NAMES,
    coerce_affinity,
    default_stats,
    _coerce_number,
    _stats_from_attrs,
)
from .progression import (
    CultivationPath,
    CultivationStage,
    EquipmentSlot,
    STAGE_STAT_TARGETS,
    DEFAULT_STAGE_BASE_STAT,
)
from ._validation import (
    FieldSpec,
    MappingSpec,
    ModelValidator,
    SequenceSpec,
    is_non_empty_str,
)


@dataclass(slots=True)
class Item:
    key: str
    name: str
    description: str
    item_type: str
    grade: Optional[str] = None
    equipment_slot: Optional[EquipmentSlot] = None
    slots_required: int = 1
    weapon_type: Optional[WeaponType] = None
    strength: float = 0.0
    physique: float = 0.0
    agility: float = 0.0
    inventory_space_bonus: int = 0
    skill_unlocks: List[str] = field(default_factory=list)
    evolves_to: Optional[str] = None
    evolution_requirements: Dict[str, str] = field(default_factory=dict)
    grants_titles: List[str] = field(default_factory=list)
    race_transformation: Optional[str] = None

    @property
    def stat_modifiers(self) -> Stats:
        return _stats_from_attrs(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Item":
        payload = dict(data)
        modifiers = payload.pop("stat_modifiers", None)
        if modifiers is not None:
            stats = modifiers if isinstance(modifiers, Stats) else Stats.from_mapping(modifiers)
            for name, value in stats.items():
                payload[name] = value
        payload.setdefault("grants_titles", payload.pop("title_rewards", []))
        if "grade" not in payload:
            for alias in ("rank", "rarity"):
                if alias in payload:
                    payload["grade"] = payload.pop(alias)
                    break
        if "race_transformation" not in payload:
            for alias in ("race_change_to", "changes_race_to"):
                if alias in payload:
                    payload["race_transformation"] = payload.pop(alias)
                    break
        return cls(**payload)


    def __post_init__(self) -> None:
        if self.grade is not None:
            self.grade = str(self.grade).strip() or None
        if isinstance(self.inventory_space_bonus, str):
            try:
                value = int(float(self.inventory_space_bonus))
            except ValueError:
                value = 0
        else:
            value = int(self.inventory_space_bonus)
        self.inventory_space_bonus = max(0, value)
        if self.equipment_slot is not None and not isinstance(self.equipment_slot, EquipmentSlot):
            try:
                self.equipment_slot = EquipmentSlot.from_value(self.equipment_slot)
            except ValueError:
                self.equipment_slot = EquipmentSlot.ACCESSORY
        if isinstance(self.slots_required, str):
            try:
                slots = int(float(self.slots_required))
            except ValueError:
                slots = 1
        else:
            slots = int(self.slots_required)
        self.slots_required = max(1, slots)
        if self.weapon_type is not None and not isinstance(self.weapon_type, WeaponType):
            try:
                self.weapon_type = WeaponType.from_value(self.weapon_type)
            except ValueError:
                self.weapon_type = None
        if self.race_transformation is not None:
            self.race_transformation = str(self.race_transformation).strip() or None


class ItemValidator(ModelValidator):
    model = Item
    fields = {
        "key": FieldSpec(is_non_empty_str, "a non-empty string key"),
        "name": FieldSpec(is_non_empty_str, "a non-empty string name"),
        "description": FieldSpec(str, "a textual description"),
        "item_type": FieldSpec(is_non_empty_str, "an item type identifier"),
        "grade": FieldSpec(str, "an item grade", required=False, allow_none=True),
        "equipment_slot": FieldSpec(
            (EquipmentSlot, str), "an equipment slot", required=False, allow_none=True
        ),
        "slots_required": FieldSpec(int, "an integer slot count", required=False),
        "weapon_type": FieldSpec(
            (WeaponType, str), "a weapon type", required=False, allow_none=True
        ),
        "strength": FieldSpec(float, "a strength bonus", required=False),
        "physique": FieldSpec(float, "a physique bonus", required=False),
        "agility": FieldSpec(float, "an agility bonus", required=False),
        "inventory_space_bonus": FieldSpec(
            int, "an inventory space bonus", required=False
        ),
        "skill_unlocks": FieldSpec(
            SequenceSpec(str), "a list of skill keys", required=False
        ),
        "evolves_to": FieldSpec(str, "an evolution key", required=False, allow_none=True),
        "evolution_requirements": FieldSpec(
            MappingSpec(str, str),
            "a mapping of evolution requirements",
            required=False,
        ),
        "grants_titles": FieldSpec(
            SequenceSpec(str), "a list of granted title keys", required=False
        ),
        "race_transformation": FieldSpec(
            str, "a race key", required=False, allow_none=True
        ),
    }


Item.validator = ItemValidator


@dataclass(slots=True)
class LootDrop:
    """Represents a potential loot outcome with chance and quantity."""

    chance: float
    amount: int = 1
    kind: str = "item"

    def __post_init__(self) -> None:
        try:
            chance_value = _coerce_number(self.chance)
        except (TypeError, ValueError):
            chance_value = 0.0
        if chance_value > 1:
            chance_value /= 100.0
        self.chance = max(0.0, min(1.0, chance_value))
        try:
            amount_value = int(self.amount)
        except (TypeError, ValueError):
            amount_value = 1
        self.amount = max(1, amount_value)
        self.kind = str(self.kind or "item").strip().lower() or "item"

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "LootDrop":
        payload = dict(data)
        raw_chance = payload.pop("chance", payload.pop("probability", 0.0))
        try:
            chance = _coerce_number(raw_chance)
        except (TypeError, ValueError):
            chance = 0.0
        payload.setdefault("chance", chance)
        if "amount" not in payload and "quantity" in payload:
            payload["amount"] = payload.pop("quantity")
        payload.setdefault("kind", payload.pop("type", "item"))
        return cls(**payload)


class LootDropValidator(ModelValidator):
    model = LootDrop
    fields = {
        "chance": FieldSpec(float, "a numeric drop chance"),
        "amount": FieldSpec(int, "an integer amount", required=False),
        "kind": FieldSpec(str, "a loot kind identifier", required=False),
    }


LootDrop.validator = LootDropValidator


@dataclass(slots=True)
class QuestObjective:
    target_type: str
    target_key: str
    kill_count: int

    def __post_init__(self) -> None:
        normalized_type = str(self.target_type).lower()
        if normalized_type not in {"enemy", "boss"}:
            raise ValueError("Quest objectives must target an enemy or boss")
        self.target_type = normalized_type
        self.target_key = str(self.target_key)
        try:
            count = int(self.kill_count)
        except (TypeError, ValueError) as exc:
            raise ValueError("Quest objective kill count must be an integer") from exc
        if count <= 0:
            raise ValueError("Quest objective kill count must be positive")
        self.kill_count = count

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "QuestObjective":
        if isinstance(data, cls):
            return data
        payload = dict(data)
        target_type = payload.pop("target_type", payload.pop("type", "enemy"))
        target_key = payload.pop("target_key", payload.pop("target", ""))
        kill_count = payload.pop("kill_count", payload.pop("count", 1))
        return cls(target_type=target_type, target_key=target_key, kill_count=kill_count)


class QuestObjectiveValidator(ModelValidator):
    model = QuestObjective
    fields = {
        "target_type": FieldSpec(is_non_empty_str, "a target type"),
        "target_key": FieldSpec(is_non_empty_str, "a target key"),
        "kill_count": FieldSpec(int, "an integer kill count"),
    }


QuestObjective.validator = QuestObjectiveValidator


@dataclass(slots=True)
class Quest:
    key: str
    name: str
    description: str
    objective: QuestObjective
    rewards: Dict[str, int]

    def __post_init__(self) -> None:
        if not isinstance(self.objective, QuestObjective):
            if isinstance(self.objective, Mapping):
                self.objective = QuestObjective.from_dict(self.objective)
            else:
                raise TypeError("Quest objective must be a QuestObjective or mapping")

        rewards: Dict[str, int] = {}
        for item_key, amount in self.rewards.items():
            key = str(item_key)
            try:
                count = int(amount)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid reward amount for {item_key!r}") from exc
            if count <= 0:
                raise ValueError("Quest reward amounts must be positive")
            rewards[key] = count
        self.rewards = rewards

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Quest":
        payload = dict(data)
        return cls(**payload)


class QuestValidator(ModelValidator):
    model = Quest
    fields = {
        "key": FieldSpec(is_non_empty_str, "a non-empty string key"),
        "name": FieldSpec(is_non_empty_str, "a non-empty string name"),
        "description": FieldSpec(str, "a textual description"),
        "objective": FieldSpec(
            (QuestObjective, Mapping),
            "a quest objective mapping",
        ),
        "rewards": FieldSpec(
            MappingSpec(str, int), "a mapping of reward keys to amounts"
        ),
    }


Quest.validator = QuestValidator


class LocationNPCType(str, Enum):
    """Enumerates the different interaction styles for a location NPC."""

    ENEMY = "enemy"
    SHOP = "shop"
    DIALOG = "dialog"

    @classmethod
    def from_value(cls, value: "LocationNPCType | str") -> "LocationNPCType":
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise ValueError(f"Unknown NPC type: {value!r}") from exc


@dataclass(slots=True)
class LocationNPC:
    """Represents an interactable character present within a location."""

    name: str
    npc_type: LocationNPCType = LocationNPCType.DIALOG
    description: str = ""
    reference: str | None = None
    dialogue: str = ""
    shop_items: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        try:
            self.npc_type = LocationNPCType.from_value(self.npc_type)
        except ValueError as exc:
            raise ValueError(f"Invalid NPC type for {self.name!r}: {self.npc_type!r}") from exc

        self.name = str(self.name).strip() or "Unknown"
        if self.description:
            self.description = str(self.description).strip()
        if self.dialogue:
            self.dialogue = str(self.dialogue).strip()
        if self.reference:
            self.reference = str(self.reference).strip()

        if not isinstance(self.shop_items, list):
            self.shop_items = self._normalize_shop_items(self.shop_items)
        else:
            self.shop_items = [str(item).strip() for item in self.shop_items if str(item).strip()]

        if self.npc_type is LocationNPCType.SHOP and not self.shop_items and self.reference:
            self.shop_items = self._normalize_shop_items(self.reference)

    @staticmethod
    def _normalize_shop_items(source: Any) -> List[str]:
        if source is None:
            return []
        if isinstance(source, str):
            tokens = re.split(r"[,\s]+", source.strip())
            return [token for token in tokens if token]
        if isinstance(source, Iterable):
            return [str(item).strip() for item in source if str(item).strip()]
        return []

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "LocationNPC":
        payload = dict(data)
        return cls(**payload)


class LocationNPCValidator(ModelValidator):
    model = LocationNPC
    fields = {
        "name": FieldSpec(is_non_empty_str, "a non-empty string name"),
        "npc_type": FieldSpec(
            (LocationNPCType, str), "an NPC type", required=False
        ),
        "description": FieldSpec(str, "a textual description", required=False, allow_none=True),
        "reference": FieldSpec(str, "a reference key", required=False, allow_none=True),
        "dialogue": FieldSpec(str, "dialogue text", required=False, allow_none=True),
        "shop_items": FieldSpec(
            SequenceSpec(str), "a list of shop item keys", required=False
        ),
    }


LocationNPC.validator = LocationNPCValidator


@dataclass(slots=True)
class Location:
    name: str
    description: str = ""
    enemies: List[str] = field(default_factory=list)
    bosses: List[str] = field(default_factory=list)
    quests: List[str] = field(default_factory=list)
    encounter_rate: float = 0.0
    is_safe: bool = False
    wander_loot: Dict[str, LootDrop] = field(default_factory=dict)
    npcs: List[LocationNPC] = field(default_factory=list)
    channel_id: int | None = None
    location_id: str | None = None
    map_coordinate: str | None = None

    def __post_init__(self) -> None:
        if self.channel_id is not None:
            try:
                self.channel_id = int(self.channel_id)
            except (TypeError, ValueError):
                self.channel_id = None

        try:
            rate = float(self.encounter_rate)
        except (TypeError, ValueError):
            rate = 0.0
        if rate > 1:
            rate /= 100.0

        raw_is_safe = self.is_safe
        if isinstance(raw_is_safe, str):
            normalized = raw_is_safe.strip().lower()
            if normalized in {"", "0", "false", "no", "off"}:
                is_safe = False
            elif normalized in {"1", "true", "yes", "on"}:
                is_safe = True
            else:
                is_safe = bool(normalized)
        else:
            is_safe = bool(raw_is_safe)

        self.is_safe = is_safe
        normalized_rate = max(0.0, min(1.0, rate))
        if self.is_safe:
            normalized_rate = 0.0
        self.encounter_rate = normalized_rate
        if not isinstance(self.wander_loot, dict):
            self.wander_loot = {}

        self.name = str(self.name).strip() or "Unnamed Location"
        self.description = str(self.description or "").strip()

        if self.channel_id is not None:
            try:
                channel_key = int(self.channel_id)
            except (TypeError, ValueError):
                channel_key = None
            else:
                self.channel_id = channel_key
        identifier = str(self.location_id or "").strip()
        if self.channel_id is not None:
            identifier = str(self.channel_id)
        elif not identifier:
            identifier = self._slugify_name(self.name)
        self.location_id = identifier

        unique_enemies: list[str] = []
        for key in list(self.enemies):
            if key not in unique_enemies:
                unique_enemies.append(key)
        unique_bosses: list[str] = []
        for key in list(self.bosses):
            if key not in unique_bosses:
                unique_bosses.append(key)
        self.enemies = unique_enemies
        self.bosses = unique_bosses

        parsed_npcs: list[LocationNPC] = []
        for entry in self.npcs:
            if isinstance(entry, LocationNPC):
                parsed_npcs.append(entry)
            elif isinstance(entry, Mapping):
                parsed_npcs.append(LocationNPC.from_mapping(entry))
            else:
                parsed_npcs.append(LocationNPC(name=str(entry)))
        unique_npcs: dict[tuple[str, LocationNPCType], LocationNPC] = {}
        for npc in parsed_npcs:
            key = (npc.name.lower(), npc.npc_type)
            if key not in unique_npcs:
                unique_npcs[key] = npc
        self.npcs = list(unique_npcs.values())

        if self.map_coordinate:
            normalized = self._normalize_coordinate(self.map_coordinate)
            self.map_coordinate = normalized


    @staticmethod
    def _slugify_name(name: str) -> str:
        normalized = unicodedata.normalize("NFKD", name)
        ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
        cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", ascii_text).strip("-_")
        simplified = cleaned.lower() or "location"
        return simplified

    @staticmethod
    def _normalize_coordinate(value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                return None
            parts = cleaned.split(":")
            if len(parts) == 3:
                try:
                    x, y, z = (int(part) for part in parts)
                except ValueError:
                    return None
                return f"{x}:{y}:{z}"
            return None
        if isinstance(value, Mapping):
            try:
                x = int(value.get("x"))
                y = int(value.get("y"))
                z = int(value.get("z", 0))
            except (TypeError, ValueError):
                return None
            return f"{x}:{y}:{z}"
        if isinstance(value, Sequence):
            parts = list(value)
            if len(parts) not in {2, 3}:
                return None
            try:
                x = int(parts[0])
                y = int(parts[1])
                z = int(parts[2]) if len(parts) == 3 else 0
            except (TypeError, ValueError):
                return None
            return f"{x}:{y}:{z}"
        return None

    def apply_storage_key(self, key: str) -> None:
        storage_key = str(key).strip()
        if not storage_key:
            return
        if self.channel_id is None:
            try:
                self.channel_id = int(storage_key)
            except (TypeError, ValueError):
                self.channel_id = None
        if self.channel_id is not None:
            self.location_id = str(self.channel_id)
        elif not self.location_id or self.location_id == "0":
            self.location_id = storage_key

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Location":
        payload = dict(data)
        channel_raw = payload.pop("channel_id", None)
        location_identifier = payload.pop("location_id", payload.pop("key", None))
        coordinate_raw = payload.pop("map_coordinate", None)
        if channel_raw is not None:
            try:
                payload["channel_id"] = int(channel_raw)
            except (TypeError, ValueError):
                payload["channel_id"] = None
        else:
            payload["channel_id"] = None

        if location_identifier is not None:
            payload["location_id"] = str(location_identifier).strip() or None
        else:
            payload["location_id"] = None

        if coordinate_raw is not None:
            payload["map_coordinate"] = cls._normalize_coordinate(coordinate_raw)
        else:
            payload["map_coordinate"] = None

        loot_payload = payload.pop("wander_loot", payload.pop("wander_loot_table", {}))
        if isinstance(loot_payload, Mapping):
            parsed_loot: Dict[str, LootDrop] = {}
            for key, value in loot_payload.items():
                if isinstance(value, LootDrop):
                    parsed_loot[key] = value
                elif isinstance(value, Mapping):
                    parsed_loot[key] = LootDrop.from_mapping(value)
            payload["wander_loot"] = parsed_loot
        else:
            payload["wander_loot"] = {}

        npc_payload = payload.pop("npcs", [])
        parsed_npcs: list[LocationNPC] = []
        if isinstance(npc_payload, Mapping):
            npc_payload = list(npc_payload.values())
        if isinstance(npc_payload, list):
            for entry in npc_payload:
                if isinstance(entry, LocationNPC):
                    parsed_npcs.append(entry)
                elif isinstance(entry, Mapping):
                    parsed_npcs.append(LocationNPC.from_mapping(entry))
                else:
                    parsed_npcs.append(LocationNPC(name=str(entry)))
        payload["npcs"] = parsed_npcs

        return cls(**payload)


class LocationValidator(ModelValidator):
    model = Location
    fields = {
        "name": FieldSpec(is_non_empty_str, "a non-empty string name"),
        "description": FieldSpec(str, "a textual description", required=False, allow_none=True),
        "enemies": FieldSpec(
            SequenceSpec(str), "a list of enemy keys", required=False
        ),
        "bosses": FieldSpec(
            SequenceSpec(str), "a list of boss keys", required=False
        ),
        "quests": FieldSpec(
            SequenceSpec(str), "a list of quest keys", required=False
        ),
        "encounter_rate": FieldSpec(float, "a numeric encounter rate", required=False),
        "wander_loot": FieldSpec(
            MappingSpec(str, (LootDrop, Mapping)),
            "a mapping of loot keys to drop definitions",
            required=False,
        ),
        "npcs": FieldSpec(
            SequenceSpec((LocationNPC, Mapping)),
            "a list of NPC definitions",
            required=False,
        ),
        "channel_id": FieldSpec(int, "a channel identifier", required=False, allow_none=True),
        "location_id": FieldSpec(str, "a location identifier", required=False, allow_none=True),
        "is_safe": FieldSpec(bool, "whether the location is a sanctuary", required=False),
        "map_coordinate": FieldSpec(str, "tile coordinate in x:y:z format", required=False, allow_none=True),
    }


Location.validator = LocationValidator
@dataclass(slots=True)
class Enemy:
    key: str
    name: str
    active_path: str = CultivationPath.QI.value
    cultivation_stage: Optional[str] = None
    body_cultivation_stage: Optional[str] = None
    soul_cultivation_stage: Optional[str] = None
    race_key: Optional[str] = None
    innate_stats: Stats = field(default_factory=default_stats)
    strength: float = 10.0
    physique: float = 10.0
    agility: float = 10.0
    affinity: SpiritualAffinity | None = None
    skills: List[str] = field(default_factory=list)
    loot_table: Dict[str, LootDrop] = field(default_factory=dict)
    elemental_resistances: List[SpiritualAffinity] = field(default_factory=list)
    title_rewards: List[str] = field(default_factory=list)
    escape_chance: float = 0.25
    decision_prompt_chance: float = 0.0

    @property
    def stats(self) -> Stats:
        return _stats_from_attrs(self)

    def base_stats_for_stages(
        self,
        qi_stage: Optional[CultivationStage],
        body_stage: Optional[CultivationStage],
        soul_stage: Optional[CultivationStage],
    ) -> Stats:
        values = {
            name: DEFAULT_STAGE_BASE_STAT * getattr(self.innate_stats, name) / 10.0
            for name in PLAYER_STAT_NAMES
        }

        def _apply(stage: Optional[CultivationStage]) -> None:
            if stage is None:
                return
            path = CultivationPath.from_value(stage.path)
            targets = STAGE_STAT_TARGETS.get(path, ())
            ratio = max(0.0, float(stage.base_stat))
            if DEFAULT_STAGE_BASE_STAT:
                ratio /= float(DEFAULT_STAGE_BASE_STAT)
            for stat_name in targets:
                values[stat_name] = ratio * getattr(self.innate_stats, stat_name)

        active_path = CultivationPath.from_value(self.active_path or CultivationPath.QI)
        stage_map = {
            CultivationPath.QI: qi_stage,
            CultivationPath.BODY: body_stage,
            CultivationPath.SOUL: soul_stage,
        }
        _apply(stage_map.get(active_path))
        return Stats(**values)

    @staticmethod
    def _normalize_payload(data: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(data)
        payload.pop("innate_min", None)
        payload.pop("innate_max", None)
        return payload

    def __post_init__(self) -> None:
        if self.race_key:
            self.race_key = str(self.race_key).strip() or None
        try:
            self.active_path = CultivationPath.from_value(self.active_path).value
        except ValueError:
            self.active_path = CultivationPath.QI.value

        if isinstance(self.innate_stats, Mapping):
            self.innate_stats = Stats.from_mapping(self.innate_stats)
        elif not isinstance(self.innate_stats, Stats):
            self.innate_stats = default_stats()

        for name in PLAYER_STAT_NAMES:
            value = getattr(self.innate_stats, name)
            if value < 0:
                setattr(self.innate_stats, name, 0.0)

        try:
            escape = float(self.escape_chance)
        except (TypeError, ValueError):
            escape = 0.0
        if escape > 1:
            escape /= 100.0
        self.escape_chance = max(0.0, min(1.0, escape))

        try:
            prompt_chance = float(self.decision_prompt_chance)
        except (TypeError, ValueError):
            prompt_chance = 0.0
        if prompt_chance > 1:
            prompt_chance /= 100.0
        self.decision_prompt_chance = max(0.0, min(1.0, prompt_chance))

        normalized_skills: List[str] = []
        for entry in self.skills:
            key = getattr(entry, "key", entry)
            if not isinstance(key, str):
                key = str(key)
            if key not in normalized_skills:
                normalized_skills.append(key)
        self.skills = normalized_skills

        loot: Dict[str, LootDrop] = {}
        for item_key, raw in self.loot_table.items():
            if isinstance(raw, LootDrop):
                drop = raw
            elif isinstance(raw, Mapping):
                drop = LootDrop.from_mapping(raw)
            else:
                drop = LootDrop(chance=_coerce_number(raw), amount=1)
            loot[item_key] = drop
        self.loot_table = loot

        resistances: List[SpiritualAffinity] = []
        for entry in self.elemental_resistances:
            if isinstance(entry, SpiritualAffinity):
                resistances.append(entry)
                continue
            value = entry
            if isinstance(value, Enum):
                value = value.value
            try:
                resistances.append(SpiritualAffinity(str(value)))
            except ValueError:
                continue
        if self.affinity and self.affinity not in resistances:
            resistances.append(self.affinity)
        self.elemental_resistances = resistances

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Enemy":
        payload = dict(data)
        payload.pop("level", None)
        stats_payload = payload.pop("stats", None)
        if stats_payload is not None:
            stats = stats_payload if isinstance(stats_payload, Stats) else Stats.from_mapping(stats_payload)
            for name, value in stats.items():
                payload[name] = value
        damage_resistances = payload.pop("damage_resistances", None)
        if damage_resistances and "elemental_resistances" not in payload:
            inferred: List[SpiritualAffinity] = []
            if isinstance(damage_resistances, Mapping):
                for key, amount in damage_resistances.items():
                    if not amount:
                        continue
                    candidate = key.value if isinstance(key, Enum) else key
                    try:
                        inferred.append(SpiritualAffinity(str(candidate)))
                    except ValueError:
                        continue
            payload["elemental_resistances"] = inferred
        payload.setdefault("title_rewards", payload.pop("grants_titles", []))
        payload = cls._normalize_payload(payload)
        return cls(**payload)


class EnemyValidator(ModelValidator):
    model = Enemy
    fields = {
        "key": FieldSpec(is_non_empty_str, "a non-empty string key"),
        "name": FieldSpec(is_non_empty_str, "a non-empty string name"),
        "active_path": FieldSpec(
            (CultivationPath, str), "an active cultivation path", required=False
        ),
        "cultivation_stage": FieldSpec(
            str, "a cultivation stage key", required=False, allow_none=True
        ),
        "body_cultivation_stage": FieldSpec(
            str, "a body cultivation stage key", required=False, allow_none=True
        ),
        "soul_cultivation_stage": FieldSpec(
            str, "a soul cultivation stage key", required=False, allow_none=True
        ),
        "race_key": FieldSpec(str, "a race key", required=False, allow_none=True),
        "innate_stats": FieldSpec(
            (Stats, Mapping), "innate stats", required=False
        ),
        "strength": FieldSpec(float, "a strength value", required=False),
        "physique": FieldSpec(float, "a physique value", required=False),
        "agility": FieldSpec(float, "an agility value", required=False),
        "affinity": FieldSpec(
            (SpiritualAffinity, str), "an affinity identifier", required=False, allow_none=True
        ),
        "skills": FieldSpec(
            SequenceSpec(str), "a list of skill keys", required=False
        ),
        "loot_table": FieldSpec(
            MappingSpec(str, (LootDrop, Mapping, int, float)),
            "a mapping of loot definitions",
            required=False,
        ),
        "elemental_resistances": FieldSpec(
            SequenceSpec((SpiritualAffinity, str)),
            "a list of elemental resistances",
            required=False,
        ),
        "title_rewards": FieldSpec(
            SequenceSpec(str), "a list of title keys", required=False
        ),
        "escape_chance": FieldSpec(float, "a numeric escape chance", required=False),
        "decision_prompt_chance": FieldSpec(
            float, "a numeric prompt chance", required=False
        ),
    }


Enemy.validator = EnemyValidator


@dataclass(slots=True)
class Boss(Enemy):
    special_mechanics: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.special_mechanics is None:
            self.special_mechanics = ""
        else:
            self.special_mechanics = str(self.special_mechanics).strip()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Boss":
        payload = dict(data)
        payload.pop("level", None)
        stats_payload = payload.pop("stats", None)
        if stats_payload is not None:
            stats = stats_payload if isinstance(stats_payload, Stats) else Stats.from_mapping(stats_payload)
            for name, value in stats.items():
                payload[name] = value
        payload.setdefault("title_rewards", payload.pop("grants_titles", []))
        return cls(**payload)


class BossValidator(EnemyValidator):
    model = Boss
    fields = dict(EnemyValidator.fields)
    fields.update(
        {
            "special_mechanics": FieldSpec(
                str, "special mechanics description", required=False, allow_none=True
            )
        }
    )


Boss.validator = BossValidator


@dataclass(slots=True)
class Currency:
    key: str
    name: str
    description: str


class CurrencyValidator(ModelValidator):
    model = Currency
    fields = {
        "key": FieldSpec(is_non_empty_str, "a non-empty string key"),
        "name": FieldSpec(is_non_empty_str, "a non-empty string name"),
        "description": FieldSpec(str, "a textual description"),
    }


Currency.validator = CurrencyValidator


@dataclass(slots=True)
class ShopItem:
    item_key: str
    currency_key: str
    price: int


class ShopItemValidator(ModelValidator):
    model = ShopItem
    fields = {
        "item_key": FieldSpec(is_non_empty_str, "an item key"),
        "currency_key": FieldSpec(is_non_empty_str, "a currency key"),
        "price": FieldSpec(int, "an integer price"),
    }


ShopItem.validator = ShopItemValidator


__all__ = [
    "Item",
    "LootDrop",
    "QuestObjective",
    "Quest",
    "LocationNPCType",
    "LocationNPC",
    "Location",
    "Enemy",
    "Boss",
    "Currency",
    "ShopItem",
]
