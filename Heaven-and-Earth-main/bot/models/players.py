"""Player-centric domain models."""

from __future__ import annotations

import time
from dataclasses import InitVar, dataclass, field
from typing import (
    Any,
    Collection,
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
from urllib.parse import urlparse

from .combat import (
    Skill,
    Stats,
    WeaponType,
    PLAYER_STAT_NAMES,
    SpiritualAffinity,
    default_stats,
)
from .progression import (
    CultivationPath,
    CultivationStage,
    EquipmentSlot,
    EQUIPMENT_SLOT_ORDER,
    SpecialTrait,
    Title,
    TitlePosition,
    STAGE_STAT_TARGETS,
    DEFAULT_STAGE_BASE_STAT,
    Race,
)
from .innate_souls import InnateSoulMutation, InnateSoul, InnateSoulSet
from .soul_land import (
    MartialSoul,
    SpiritBone,
    SoulPowerRank,
    SpiritRing,
    max_ring_slots_for_level,
    rank_for_level,
    required_ring_count_for_level,
)
from .world import Item
from .map import (
    ExpeditionOutcome,
    ExpeditionPlan,
    FogOfWarState,
    NoiseMeter,
    TravelJournalEntry,
    TravelMasteryProgress,
    TravelMode,
    TravelReward,
    TileCoordinate,
)
from ..energy import ENERGY_DAILY_REFILL_SECONDS


def _coerce_sequence_to_tuple(value: Any) -> Any:
    """Recursively convert nested lists into tuples for RNG state restoration."""

    if isinstance(value, list):
        return tuple(_coerce_sequence_to_tuple(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_coerce_sequence_to_tuple(item) for item in value)
    return value


def _normalize_martial_souls(value: Any) -> list[MartialSoul]:
    if isinstance(value, MartialSoul):
        souls = [value]
    elif isinstance(value, Mapping):
        souls = [MartialSoul.from_mapping(value)]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        souls = []
        for entry in value:
            if isinstance(entry, MartialSoul):
                souls.append(entry)
            elif isinstance(entry, Mapping):
                souls.append(MartialSoul.from_mapping(entry))
    else:
        souls = []
    return souls


def _normalize_soul_rings(
    value: Any,
    *,
    default_martial_soul: str | None = None,
    alias_lookup: Mapping[str, str] | None = None,
) -> list[SpiritRing]:
    if isinstance(value, SpiritRing):
        rings = [value]
    elif isinstance(value, Mapping):
        rings = [SpiritRing.from_mapping(value)]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        rings = []
        for entry in value:
            if isinstance(entry, SpiritRing):
                rings.append(entry)
            elif isinstance(entry, Mapping):
                rings.append(SpiritRing.from_mapping(entry))
    else:
        rings = []
    rings.sort(key=lambda ring: ring.slot_index)
    for index, ring in enumerate(rings):
        ring.slot_index = index
        if alias_lookup and ring.martial_soul:
            key = str(ring.martial_soul).strip().lower()
            mapped = alias_lookup.get(key)
            if mapped:
                ring.martial_soul = mapped
    return rings


def _normalize_spirit_bones(value: Any) -> list[SpiritBone]:
    if isinstance(value, SpiritBone):
        bones = [value]
    elif isinstance(value, Mapping):
        bones = [SpiritBone.from_mapping(value)]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        bones = []
        for entry in value:
            if isinstance(entry, SpiritBone):
                bones.append(entry)
            elif isinstance(entry, Mapping):
                bones.append(SpiritBone.from_mapping(entry))
    else:
        bones = []
    return bones


@dataclass(frozen=True, slots=True)
class PronounSet:
    """Represents grammatical pronouns for a character."""

    subject: str
    obj: str
    possessive: str
    possessive_pronoun: str
    reflexive: str

    @classmethod
    def from_gender(cls, gender: str | None) -> "PronounSet":
        normalized = (gender or "").strip().lower()
        if normalized in {"male", "man", "m", "he", "masculine"}:
            return cls("he", "him", "his", "his", "himself")
        if normalized in {"female", "woman", "f", "she", "feminine"}:
            return cls("she", "her", "her", "hers", "herself")
        return cls.neutral()

    @classmethod
    def neutral(cls) -> "PronounSet":
        return cls("they", "them", "their", "theirs", "themselves")


PLAYER_STATS = list(PLAYER_STAT_NAMES)

MAX_ACTIVE_MARTIAL_SOULS = 2
TWIN_MARTIAL_SOUL_PENALTY = 0.85


@dataclass(slots=True)
class PlayerProgress:
    user_id: int
    name: str
    cultivation_stage: str
    active_path: str = CultivationPath.QI.value
    cultivation_exp: int = 0
    cultivation_exp_required: int = 100
    combat_exp: int = 0
    combat_exp_required: int = 100
    soul_exp: int = 0
    soul_exp_required: int = 100
    unlocked_paths: List[str] = field(
        default_factory=lambda: [CultivationPath.QI.value]
    )
    stats: Stats = field(default_factory=Stats)
    body_cultivation_stage: Optional[str] = None
    soul_cultivation_stage: Optional[str] = None
    innate_stats: Stats = field(default_factory=default_stats)
    primary_martial_soul: str | None = None
    martial_souls: List[MartialSoul] = field(default_factory=list)
    active_martial_soul_names: List[str] = field(default_factory=list)
    soul_rings: List[SpiritRing] = field(default_factory=list)
    soul_bones: List[SpiritBone] = field(default_factory=list)
    soul_power_level: int = 1
    gender: str = "unspecified"
    path_role_overrides: Dict[str, str] = field(default_factory=dict)
    current_hp: float | None = None
    current_soul_hp: float | None = None
    last_safe_zone: Optional[str] = None
    in_combat: bool = False
    race_key: Optional[str] = None
    trait_keys: List[str] = field(default_factory=list)
    cultivation_technique_keys: List[str] = field(default_factory=list)
    skill_proficiency: Dict[str, int] = field(default_factory=dict)
    inventory: Dict[str, int] = field(default_factory=dict)
    currencies: Dict[str, int] = field(default_factory=dict)
    inventory_capacity: int = 20
    equipped_items: List[str] = field(default_factory=list)
    equipment: Dict[str, List[str]] = field(default_factory=dict)
    location: Optional[str] = None
    world_position: Optional[str] = None
    party_id: Optional[str] = None
    bond_key: Optional[str] = None
    bond_partner_id: Optional[int] = None
    bond_missions: Dict[str, float] = field(default_factory=dict)
    profile_image_url: Optional[str] = None
    last_cultivate_ts: Optional[float] = None
    last_train_ts: Optional[float] = None
    last_temper_ts: Optional[float] = None
    titles: List[str] = field(default_factory=list)
    active_title_prefix: Optional[str] = None
    active_title_suffix: Optional[str] = None
    class_key: InitVar[Optional[str]] = None
    fog_of_war: FogOfWarState = field(default_factory=FogOfWarState)
    travel_mastery: TravelMasteryProgress = field(default_factory=TravelMasteryProgress)
    exploration_milestones: Set[str] = field(default_factory=set)
    travel_noise: NoiseMeter = field(default_factory=NoiseMeter)
    travel_rng_seed: int | None = None
    travel_rng_state: Any = None
    active_travel_mode: TravelMode = TravelMode.MOVE
    travel_log: List[str] = field(default_factory=list)
    known_waypoints: Set[str] = field(default_factory=set)
    travel_journal: List[TravelJournalEntry] = field(default_factory=list)
    last_expedition_plan: ExpeditionPlan | None = None
    expedition_history: List[ExpeditionOutcome] = field(default_factory=list)
    energy: float | None = None
    last_energy_refresh: float | None = None
    visible_players: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    legacy_techniques: List[str] = field(default_factory=list)
    legacy_traits: List[str] = field(default_factory=list)
    legacy_heirs: List[int] = field(default_factory=list)
    retired_at: float | None = None

    def _martial_soul_lookup(self) -> Dict[str, MartialSoul]:
        return {soul.name.strip().lower(): soul for soul in self.martial_souls}

    def get_active_martial_souls(self) -> List[MartialSoul]:
        lookup = self._martial_soul_lookup()
        active: list[MartialSoul] = []
        primary_key = (self.primary_martial_soul or "").strip().lower()
        if primary_key:
            soul = lookup.get(primary_key)
            if soul:
                active.append(soul)
        for name in self.active_martial_soul_names:
            key = str(name).strip().lower()
            soul = lookup.get(key)
            if soul and soul not in active:
                active.append(soul)
            if len(active) >= MAX_ACTIVE_MARTIAL_SOULS:
                break
        if not active and self.martial_souls:
            active.append(self.martial_souls[0])
        if not primary_key and active:
            self.primary_martial_soul = active[0].name
        return active[:MAX_ACTIVE_MARTIAL_SOULS]

    def soul_rings_by_soul(self) -> Dict[str, List[SpiritRing]]:
        mapping: Dict[str, List[SpiritRing]] = {
            soul.name: [] for soul in self.martial_souls
        }
        fallback_rings: list[SpiritRing] = []
        for ring in self.soul_rings:
            if ring.martial_soul:
                key = str(ring.martial_soul).strip().lower()
                for soul in self.martial_souls:
                    if soul.name.strip().lower() == key:
                        mapping[soul.name].append(ring)
                        break
                else:
                    fallback_rings.append(ring)
            else:
                fallback_rings.append(ring)
        active_souls = self.get_active_martial_souls()
        if active_souls and fallback_rings:
            mapping.setdefault(active_souls[0].name, []).extend(fallback_rings)
        return mapping

    def total_martial_soul_stats(self) -> Stats:
        """Return zero stat bonuses to avoid martial souls inflating player stats."""

        return Stats()

    def martial_soul_damage_multiplier(
        self, skill: Skill, weapon_types: Collection[WeaponType] | None = None
    ) -> float:
        active_souls = self.get_active_martial_souls()
        if not active_souls:
            return 1.0
        ring_map = self.soul_rings_by_soul()
        equipped: set[WeaponType] = set()
        if weapon_types:
            for weapon in weapon_types:
                if isinstance(weapon, WeaponType):
                    equipped.add(weapon)
                else:
                    try:
                        equipped.add(WeaponType.from_value(weapon))
                    except (TypeError, ValueError):
                        continue
        multiplier = 1.0
        for soul in active_souls:
            rings = ring_map.get(soul.name, [])
            multiplier *= soul.damage_multiplier(
                skill_weapon=skill.weapon,
                equipped_weapons=equipped,
                skill_affinity=skill.elements,
                skill_archetype=skill.archetype,
                ring_count=len(rings),
            )
        return multiplier

    def total_spirit_ability_slots(self) -> int:
        total = 0
        ring_map = self.soul_rings_by_soul()
        for soul in self.get_active_martial_souls():
            rings = ring_map.get(soul.name, [])
            ring_count = len(rings)
            base_slots = soul.ability_slots(self.soul_power_level, ring_count)
            ring_slots = sum(ring.ability_slots for ring in rings)
            total += base_slots + ring_slots
        return max(0, total)

    def unlocked_spirit_abilities(self) -> List[str]:
        unlocked: list[str] = []
        ring_map = self.soul_rings_by_soul()
        for soul in self.get_active_martial_souls():
            rings = ring_map.get(soul.name, [])
            base_slots = soul.ability_slots(self.soul_power_level, len(rings))
            ring_slots = sum(ring.ability_slots for ring in rings)
            slots = base_slots + ring_slots
            ability_order: list[str] = []
            seen: set[str] = set()
            for ability in soul.signature_abilities:
                normalized = ability.strip()
                if normalized and normalized not in seen:
                    ability_order.append(normalized)
                    seen.add(normalized)
            for ring in rings:
                for ability in ring.unlocked_abilities:
                    normalized = ability.strip()
                    if normalized and normalized not in seen:
                        ability_order.append(normalized)
                        seen.add(normalized)
                    if len(ability_order) >= slots:
                        break
                if len(ability_order) >= slots:
                    break
            unlocked.extend(ability_order[:slots])
        return unlocked

    def effective_stats(
        self,
        qi_stage: CultivationStage,
        body_stage: Optional[CultivationStage],
        soul_stage: Optional[CultivationStage],
        race: Optional[Race],
        traits: List[SpecialTrait],
        items: Optional[List[Item]] = None,
    ) -> Stats:
        """Return stats after applying all multipliers and modifiers."""

        result = self.stats.copy()

        active_path = CultivationPath.from_value(self.active_path or CultivationPath.QI)
        stage_bonus_map = {
            CultivationPath.QI: qi_stage,
            CultivationPath.BODY: body_stage,
            CultivationPath.SOUL: soul_stage,
        }
        active_stage = stage_bonus_map.get(active_path)
        if active_stage is not None:
            result.add_in_place(active_stage.stat_bonuses)

        body_ratio = 0.0
        if active_path is CultivationPath.BODY and self.combat_exp_required:
            body_ratio = max(
                0.0, min(1.0, self.combat_exp / float(self.combat_exp_required))
            )
        qi_ratio = 0.0
        if active_path is CultivationPath.QI and self.cultivation_exp_required:
            qi_ratio = max(
                0.0,
                min(1.0, self.cultivation_exp / float(self.cultivation_exp_required)),
            )
        soul_ratio = 0.0
        if active_path is CultivationPath.SOUL and self.soul_exp_required:
            soul_ratio = max(
                0.0,
                min(1.0, self.soul_exp / float(self.soul_exp_required)),
            )

        if body_ratio > 0:
            body_multiplier = 1.0 + body_ratio
            for key in STAGE_STAT_TARGETS.get(CultivationPath.BODY, ()): 
                setattr(result, key, getattr(result, key) * body_multiplier)

        if qi_ratio > 0:
            qi_multiplier = 1.0 + qi_ratio
            for key in STAGE_STAT_TARGETS.get(CultivationPath.QI, ()): 
                setattr(result, key, getattr(result, key) * qi_multiplier)

        if soul_ratio > 0:
            soul_multiplier = 1.0 + soul_ratio
            for key in STAGE_STAT_TARGETS.get(CultivationPath.SOUL, ()): 
                setattr(result, key, getattr(result, key) * soul_multiplier)

        if race:
            result.apply_multipliers_in_place(race.stat_multipliers)

        for trait in traits:
            result.apply_multipliers_in_place(trait.stat_multipliers)

        if items:
            for item in items:
                result.add_in_place(item.stat_modifiers)

        return result

    def recalculate_stage_stats(
        self,
        qi_stage: CultivationStage | None,
        body_stage: Optional[CultivationStage],
        soul_stage: Optional[CultivationStage],
    ) -> None:
        """Recompute base stats from innate rolls and long-term bonuses."""

        previous_max_hp, previous_max_soul = self.max_health_caps()

        active_path = CultivationPath.from_value(self.active_path or CultivationPath.QI)
        innate_source = self.innate_stats
        if (
            active_path is CultivationPath.QI
            and qi_stage is not None
            and qi_stage.is_mortal
        ):
            innate_source = Stats(strength=1.0, physique=1.0, agility=1.0)

        values = {
            name: DEFAULT_STAGE_BASE_STAT * getattr(innate_source, name) / 10.0
            for name in PLAYER_STAT_NAMES
        }

        def _apply_stage(stage: CultivationStage | None) -> None:
            if stage is None:
                return
            path = CultivationPath.from_value(stage.path)
            targets = STAGE_STAT_TARGETS.get(path, ())
            ratio = max(0.0, float(stage.base_stat))
            if DEFAULT_STAGE_BASE_STAT:
                ratio /= float(DEFAULT_STAGE_BASE_STAT)
            for stat_name in targets:
                values[stat_name] = ratio * getattr(innate_source, stat_name)

        stage_map = {
            CultivationPath.QI: qi_stage,
            CultivationPath.BODY: body_stage,
            CultivationPath.SOUL: soul_stage,
        }
        for path, stage in stage_map.items():
            if path is not active_path:
                continue
            _apply_stage(stage)

        self.stats = Stats(**values)
        self.sync_health(
            previous_max_hp=previous_max_hp,
            previous_max_soul_hp=previous_max_soul,
        )

    def max_health_caps(self, stats: Stats | None = None) -> tuple[float, float]:
        basis = stats or self.stats
        max_hp = max(1.0, basis.health_points)
        return max_hp, max_hp

    def sync_health(
        self,
        *,
        previous_max_hp: float | None = None,
        previous_max_soul_hp: float | None = None,
        full: bool = False,
    ) -> None:
        """Clamp or refill stored health pools to current stat caps."""

        max_hp, max_soul = self.max_health_caps()
        if full:
            self.current_hp = max_hp
            self.current_soul_hp = max_soul
            return

        if self.current_hp is None:
            self.current_hp = max_hp
        else:
            try:
                value = max(0.0, float(self.current_hp))
            except (TypeError, ValueError):
                value = max_hp
            if previous_max_hp and previous_max_hp > 0 and value > 0:
                ratio = min(1.0, max(0.0, value / previous_max_hp))
                value = ratio * max_hp
            self.current_hp = min(max_hp, value)

        if self.current_soul_hp is None:
            self.current_soul_hp = max_soul
        else:
            try:
                value = max(0.0, float(self.current_soul_hp))
            except (TypeError, ValueError):
                value = max_soul
            if previous_max_soul_hp and previous_max_soul_hp > 0 and value > 0:
                ratio = min(1.0, max(0.0, value / previous_max_soul_hp))
                value = ratio * max_soul
            self.current_soul_hp = min(max_soul, value)

    def restore_full_health(self) -> None:
        """Fully restore HP and soul HP to their current maxima."""

        self.sync_health(full=True)

    def sync_energy(
        self,
        max_energy: float,
        *,
        now: float | None = None,
        refill_interval: float = ENERGY_DAILY_REFILL_SECONDS,
        force_refill: bool = False,
    ) -> None:
        """Clamp, initialise, or refill the stored travel energy pool."""

        try:
            capacity = max(0.0, float(max_energy))
        except (TypeError, ValueError):
            capacity = 0.0

        timestamp = float(now) if now is not None else time.time()

        refresh_ts: float | None
        if self.last_energy_refresh is None:
            refresh_ts = None
        else:
            try:
                refresh_ts = float(self.last_energy_refresh)
            except (TypeError, ValueError):
                refresh_ts = None

        current_energy: float | None
        if self.energy is None:
            current_energy = None
        else:
            try:
                current_energy = max(0.0, float(self.energy))
            except (TypeError, ValueError):
                current_energy = None

        needs_refill = force_refill or current_energy is None
        if not needs_refill and refresh_ts is not None and refill_interval > 0:
            if timestamp - refresh_ts >= refill_interval:
                needs_refill = True

        if needs_refill:
            current_energy = capacity
            refresh_ts = timestamp
        else:
            if current_energy is None:
                current_energy = capacity
            current_energy = min(capacity, current_energy)
            refresh_ts = refresh_ts or timestamp

        self.energy = current_energy
        self.last_energy_refresh = refresh_ts

    def consume_energy(self, amount: float) -> bool:
        """Attempt to spend ``amount`` of travel energy."""

        try:
            cost = max(0.0, float(amount))
        except (TypeError, ValueError):
            cost = 0.0
        if cost <= 0.0:
            return True

        try:
            available = max(0.0, float(self.energy or 0.0))
        except (TypeError, ValueError):
            available = 0.0

        if cost > available + 1e-6:
            return False

        self.energy = max(0.0, available - cost)
        return True

    def set_travel_mode(self, mode: TravelMode | str) -> None:
        """Update the player's preferred travel mode."""

        self.active_travel_mode = TravelMode.MOVE

    def append_travel_log(self, entry: str) -> None:
        if not entry:
            return
        text = str(entry).strip()
        if not text:
            return
        if text[-1] not in ".!?":
            text = f"{text}."
        if text and text[0].islower():
            text = text[0].upper() + text[1:]
        self.travel_log.append(text)
        if len(self.travel_log) > 50:
            self.travel_log = self.travel_log[-50:]

    def grant_travel_reward(self, reward: TravelReward) -> None:
        self.travel_mastery.pending_rewards.append(reward)
        if reward.milestone_name:
            self.exploration_milestones.add(reward.milestone_name)
        if reward.exploration_xp:
            self.travel_mastery.add_experience(reward.exploration_xp)
        if reward.reputation_changes:
            for faction, change in reward.reputation_changes.items():
                key = f"faction:{faction}"
                self.exploration_milestones.add(f"{key}:{change:+.2f}")

    def unlock_waypoint(self, waypoint_key: str) -> None:
        if waypoint_key:
            self.known_waypoints.add(waypoint_key)

    def record_journal_entry(self, entry: TravelJournalEntry) -> None:
        self.travel_journal.append(entry)
        if len(self.travel_journal) > 50:
            self.travel_journal = self.travel_journal[-50:]

    def remember_expedition(self, outcome: ExpeditionOutcome) -> None:
        self.last_expedition_plan = outcome.plan
        self.expedition_history.append(outcome)
        if len(self.expedition_history) > 10:
            self.expedition_history = self.expedition_history[-10:]

    def mark_tile_explored(
        self, coordinate: TileCoordinate, *, surveyed: bool = False, rumor: str | None = None
    ) -> None:
        self.fog_of_war.mark_discovered(coordinate)
        if surveyed:
            self.fog_of_war.mark_surveyed(coordinate)
        if rumor:
            self.fog_of_war.add_rumor(rumor)
        self.world_position = coordinate.to_key()

    def tile_coordinate(self) -> TileCoordinate | None:
        if not self.world_position:
            return None
        try:
            return TileCoordinate.from_key(self.world_position)
        except ValueError:
            return None

    def __post_init__(self, class_key: Optional[str]) -> None:
        try:
            user_id = int(self.user_id)
        except (TypeError, ValueError):
            user_id = 0
        self.user_id = user_id

        if isinstance(self.world_position, TileCoordinate):
            self.world_position = self.world_position.to_key()
        elif isinstance(self.world_position, Mapping):
            try:
                coordinate = TileCoordinate(
                    int(self.world_position.get("x")),
                    int(self.world_position.get("y")),
                    int(self.world_position.get("z", 0)),
                )
            except (TypeError, ValueError):
                coordinate = None
            self.world_position = coordinate.to_key() if coordinate else None
        elif isinstance(self.world_position, Sequence) and not isinstance(
            self.world_position, (str, bytes)
        ):
            parts = list(self.world_position)
            coordinate = None
            if len(parts) in {2, 3}:
                try:
                    coordinate = TileCoordinate(
                        int(parts[0]),
                        int(parts[1]),
                        int(parts[2]) if len(parts) == 3 else 0,
                    )
                except (TypeError, ValueError):
                    coordinate = None
            self.world_position = coordinate.to_key() if coordinate else None
        elif isinstance(self.world_position, str):
            try:
                coordinate = TileCoordinate.from_key(self.world_position)
            except ValueError:
                self.world_position = None
            else:
                self.world_position = coordinate.to_key()

        if not self.race_key and class_key:
            normalized = str(class_key).strip()
            self.race_key = normalized or None
        if self.race_key:
            self.race_key = str(self.race_key).strip() or None
        profile_url = self.profile_image_url
        if profile_url:
            profile_url = str(profile_url).strip()
            if profile_url:
                parsed = urlparse(profile_url)
                if parsed.scheme in {"http", "https"} and parsed.netloc:
                    self.profile_image_url = profile_url
                else:
                    self.profile_image_url = None
            else:
                self.profile_image_url = None
        else:
            self.profile_image_url = None

        if self.travel_rng_seed is not None:
            try:
                self.travel_rng_seed = int(self.travel_rng_seed)
            except (TypeError, ValueError):
                self.travel_rng_seed = None
        if self.travel_rng_state is not None:
            self.travel_rng_state = _coerce_sequence_to_tuple(self.travel_rng_state)

        if self.bond_partner_id is not None:
            try:
                partner_id = int(self.bond_partner_id)
            except (TypeError, ValueError):
                partner_id = 0
            self.bond_partner_id = partner_id if partner_id > 0 else None
        if self.bond_key:
            normalized_bond = str(self.bond_key).strip().lower()
            self.bond_key = normalized_bond or None

        if isinstance(self.bond_missions, Mapping):
            missions: dict[str, float] = {}
            for raw_key, raw_value in self.bond_missions.items():
                key = str(raw_key).strip().lower()
                if not key:
                    continue
                try:
                    timestamp = float(raw_value)
                except (TypeError, ValueError):
                    timestamp = 0.0
                missions[key] = timestamp if timestamp > 0 else 0.0
            self.bond_missions = missions
        else:
            self.bond_missions = {}

        if isinstance(self.visible_players, Mapping):
            normalized_visible: dict[int, dict[str, Any]] = {}
            for raw_id, payload in self.visible_players.items():
                try:
                    user_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                if isinstance(payload, Mapping):
                    normalized_visible[user_id] = {
                        str(key): value for key, value in payload.items()
                    }
                else:
                    normalized_visible[user_id] = {}
            self.visible_players = normalized_visible
        else:
            self.visible_players = {}
        unlocked: list[str]
        if isinstance(self.unlocked_paths, Mapping):
            unlocked = [
                str(path).strip()
                for path, enabled in self.unlocked_paths.items()
                if enabled
            ]
        elif isinstance(self.unlocked_paths, (list, tuple, set)):
            unlocked = [str(path).strip() for path in self.unlocked_paths]
        else:
            unlocked = []

        normalized_paths: list[str] = []
        for entry in [*unlocked, CultivationPath.QI.value]:
            if not entry:
                continue
            try:
                path_value = CultivationPath.from_value(entry).value
            except ValueError:
                continue
            if path_value not in normalized_paths:
                normalized_paths.append(path_value)
        if not normalized_paths:
            normalized_paths = [CultivationPath.QI.value]
        self.unlocked_paths = normalized_paths

        try:
            active_path_value = CultivationPath.from_value(self.active_path).value
        except ValueError:
            active_path_value = CultivationPath.QI.value
        if active_path_value not in self.unlocked_paths:
            active_path_value = self.unlocked_paths[0]
        self.active_path = active_path_value
        if isinstance(self.stats, dict):
            self.stats = Stats.from_mapping(self.stats)
        if isinstance(self.innate_stats, dict):
            self.innate_stats = Stats.from_mapping(self.innate_stats)
        if isinstance(self.path_role_overrides, Mapping):
            overrides: dict[str, str] = {}
            for raw_path, raw_stage in self.path_role_overrides.items():
                try:
                    path_value = CultivationPath.from_value(raw_path).value
                except ValueError:
                    path_value = str(raw_path).strip().lower()
                    try:
                        path_value = CultivationPath.from_value(path_value).value
                    except ValueError:
                        continue
                stage_key = str(raw_stage).strip()
                if not path_value or not stage_key:
                    continue
                overrides[path_value] = stage_key
            self.path_role_overrides = overrides
        else:
            self.path_role_overrides = {}
        if isinstance(self.cultivation_technique_keys, Mapping):
            technique_candidates = [
                str(key).strip()
                for key, enabled in self.cultivation_technique_keys.items()
                if enabled
            ]
        elif isinstance(self.cultivation_technique_keys, (list, tuple, set)):
            technique_candidates = [str(entry).strip() for entry in self.cultivation_technique_keys]
        else:
            technique_candidates = []
        normalized_techniques: list[str] = []
        seen_techniques: set[str] = set()
        for key in technique_candidates:
            if not key or key in seen_techniques:
                continue
            normalized_techniques.append(key)
            seen_techniques.add(key)
        self.cultivation_technique_keys = normalized_techniques
        self.martial_souls = _normalize_martial_souls(self.martial_souls)
        if not self.martial_souls:
            self.martial_souls = [MartialSoul.default(category="any")]
        active_names_raw = self.active_martial_soul_names
        if isinstance(active_names_raw, Mapping):
            candidates = [
                str(name).strip()
                for name, enabled in active_names_raw.items()
                if enabled
            ]
        elif isinstance(active_names_raw, (list, tuple, set)):
            candidates = [str(name).strip() for name in active_names_raw]
        elif isinstance(active_names_raw, str):
            candidates = [active_names_raw.strip()]
        elif active_names_raw:
            candidates = [str(active_names_raw).strip()]
        else:
            candidates = []
        lookup: Dict[str, str] = {}
        for soul in self.martial_souls:
            canonical = soul.name.strip()
            if not canonical:
                continue
            key = canonical.lower()
            lookup[key] = canonical
            for alias in soul.legacy_aliases():
                alias_key = alias.strip().lower()
                if alias_key and alias_key not in lookup:
                    lookup[alias_key] = canonical
        normalized_active: list[str] = []
        for name in candidates:
            key = name.strip().lower()
            actual = lookup.get(key)
            if actual and actual not in normalized_active:
                normalized_active.append(actual)
            if len(normalized_active) >= MAX_ACTIVE_MARTIAL_SOULS:
                break
        if not normalized_active and self.martial_souls:
            normalized_active.append(self.martial_souls[0].name)
        self.active_martial_soul_names = normalized_active[:MAX_ACTIVE_MARTIAL_SOULS]
        if self.primary_martial_soul:
            primary_key = str(self.primary_martial_soul).strip().lower()
            self.primary_martial_soul = lookup.get(primary_key)
        if not self.primary_martial_soul and self.martial_souls:
            self.primary_martial_soul = self.martial_souls[0].name
        default_martial_soul = normalized_active[0] if normalized_active else None
        self.soul_rings = _normalize_soul_rings(
            self.soul_rings,
            default_martial_soul=default_martial_soul,
            alias_lookup=lookup,
        )
        self.soul_bones = _normalize_spirit_bones(self.soul_bones)
        try:
            self.soul_power_level = max(1, int(self.soul_power_level))
        except (TypeError, ValueError):
            self.soul_power_level = 1

        if not self.gender:
            self.gender = "unspecified"
        else:
            normalized_gender = str(self.gender).strip().lower()
            if normalized_gender in {"male", "man", "m", "he", "masculine"}:
                self.gender = "male"
            elif normalized_gender in {"female", "woman", "f", "she", "feminine"}:
                self.gender = "female"
            else:
                self.gender = "unspecified"

        if isinstance(self.skill_proficiency, Mapping):
            proficiencies: Dict[str, int] = {}
            for key, value in self.skill_proficiency.items():
                try:
                    proficiencies[str(key)] = int(value)
                except (TypeError, ValueError):
                    proficiencies[str(key)] = 0
            self.skill_proficiency = proficiencies
        elif isinstance(self.skill_proficiency, (list, tuple, set)):
            self.skill_proficiency = {str(key): 0 for key in self.skill_proficiency}
        elif not isinstance(self.skill_proficiency, dict):
            self.skill_proficiency = {}

        # Remove legacy neutral and affinity-based defaults in favour of martial soul techniques.
        self.skill_proficiency.pop("basic_attack", None)
        for affinity in SpiritualAffinity:
            self.skill_proficiency.pop(f"basic_{affinity.value}_physical", None)
            self.skill_proficiency.pop(f"basic_{affinity.value}_spiritual", None)
            self.skill_proficiency.pop(f"basic_{affinity.value}_soul", None)

        innate_soul_set = InnateSoulSet(self.innate_souls)
        affinities = innate_soul_set.affinities if innate_soul_set else ()

        signature_keys: set[str] = set()
        for soul in self.martial_souls:
            ability_keys = soul.signature_abilities or (soul.signature_skill_key(),)
            for ability_key in ability_keys:
                normalized = str(ability_key).strip()
                if not normalized or normalized in signature_keys:
                    continue
                self.skill_proficiency.setdefault(normalized, 0)
                signature_keys.add(normalized)

        if isinstance(self.inventory_capacity, str):
            try:
                self.inventory_capacity = int(float(self.inventory_capacity))
            except ValueError:
                self.inventory_capacity = 20
        self.inventory_capacity = max(0, int(self.inventory_capacity))
        if isinstance(self.equipped_items, (tuple, set)):
            self.equipped_items = [str(value) for value in self.equipped_items]
        elif isinstance(self.equipped_items, str):
            self.equipped_items = [self.equipped_items]
        else:
            self.equipped_items = [str(value) for value in self.equipped_items]
        if isinstance(self.equipment, Mapping):
            normalized: Dict[str, List[str]] = {}
            for raw_slot, values in self.equipment.items():
                slot_key = str(raw_slot).strip().lower()
                if isinstance(values, Mapping):
                    extracted = [
                        str(entry)
                        for entry in values.values()
                        if isinstance(entry, (str, int)) or entry is not None
                    ]
                elif isinstance(values, (list, tuple, set)):
                    extracted = [str(entry) for entry in values if entry is not None]
                elif values is None:
                    extracted = []
                else:
                    extracted = [str(values)]
                if not extracted:
                    continue
                slot_value = EquipmentSlot.from_value(slot_key, default=None)
                if slot_value is not None:
                    slot_key = slot_value.value
                normalized.setdefault(slot_key, []).extend(extracted)
            self.equipment = normalized
        else:
            self.equipment = {}
        if not self.equipment and self.equipped_items:
            self.equipment = {EquipmentSlot.ACCESSORY.value: list(self.equipped_items)}
        self._sync_equipped_items()
        if isinstance(self.titles, (tuple, set)):
            self.titles = list(self.titles)
        elif isinstance(self.titles, str):
            self.titles = [self.titles]

        if isinstance(self.active_title_prefix, (list, set, tuple)):
            self.active_title_prefix = next(iter(self.active_title_prefix), None)
        if isinstance(self.active_title_suffix, (list, set, tuple)):
            self.active_title_suffix = next(iter(self.active_title_suffix), None)

        if self.last_safe_zone:
            self.last_safe_zone = str(self.last_safe_zone).strip() or None

        if self.current_hp is not None:
            try:
                self.current_hp = max(0.0, float(self.current_hp))
            except (TypeError, ValueError):
                self.current_hp = None
        if self.current_soul_hp is not None:
            try:
                self.current_soul_hp = max(0.0, float(self.current_soul_hp))
            except (TypeError, ValueError):
                self.current_soul_hp = None

        if self.energy is not None:
            try:
                self.energy = max(0.0, float(self.energy))
            except (TypeError, ValueError):
                self.energy = None
        if self.last_energy_refresh is not None:
            try:
                self.last_energy_refresh = float(self.last_energy_refresh)
            except (TypeError, ValueError):
                self.last_energy_refresh = None

        self.sync_health()
        self.in_combat = bool(self.in_combat)

        if not isinstance(self.fog_of_war, FogOfWarState):
            if isinstance(self.fog_of_war, Mapping):
                discovered = set(self.fog_of_war.get("discovered_tiles", ()))
                surveyed = set(self.fog_of_war.get("surveyed_tiles", ()))
                rumored = set(self.fog_of_war.get("rumored_points", ()))
                self.fog_of_war = FogOfWarState(discovered, surveyed, rumored)
            else:
                self.fog_of_war = FogOfWarState()

        if not isinstance(self.travel_mastery, TravelMasteryProgress):
            if isinstance(self.travel_mastery, Mapping):
                unlocked = set(self.travel_mastery.get("unlocked_nodes", ()))
                exp = int(self.travel_mastery.get("experience", 0))
                level = int(self.travel_mastery.get("level", 1))
                rewards = [
                    reward
                    if isinstance(reward, TravelReward)
                    else TravelReward(
                        exploration_xp=int(reward.get("exploration_xp", 0)),
                        loot_keys=list(reward.get("loot_keys", ())),
                        milestone_name=reward.get("milestone_name"),
                    )
                    for reward in self.travel_mastery.get("pending_rewards", [])
                    if isinstance(reward, (Mapping, TravelReward))
                ]
                self.travel_mastery = TravelMasteryProgress(unlocked, exp, level, rewards)
            else:
                self.travel_mastery = TravelMasteryProgress()

        if not isinstance(self.exploration_milestones, set):
            if isinstance(self.exploration_milestones, Mapping):
                self.exploration_milestones = {
                    key
                    for key, enabled in self.exploration_milestones.items()
                    if enabled
                }
            else:
                self.exploration_milestones = set(self.exploration_milestones or [])

        if not isinstance(self.travel_noise, NoiseMeter):
            if isinstance(self.travel_noise, Mapping):
                value = float(self.travel_noise.get("value", 0.0))
                decay = float(self.travel_noise.get("decay_per_second", 0.1))
                self.travel_noise = NoiseMeter(value=value, decay_per_second=decay)
            else:
                self.travel_noise = NoiseMeter()

        try:
            if isinstance(self.active_travel_mode, TravelMode):
                mode = self.active_travel_mode
            else:
                mode = TravelMode[str(self.active_travel_mode).upper()]
        except (KeyError, AttributeError, ValueError):
            mode = TravelMode.MOVE
        self.active_travel_mode = mode

        if not isinstance(self.travel_log, list):
            if isinstance(self.travel_log, (tuple, set)):
                self.travel_log = list(self.travel_log)
            elif self.travel_log is None:
                self.travel_log = []
            else:
                self.travel_log = [str(self.travel_log)]
        self.travel_log = [str(entry) for entry in self.travel_log if entry]

        if isinstance(self.known_waypoints, Mapping):
            normalized_waypoints = {
                str(key)
                for key, enabled in self.known_waypoints.items()
                if enabled and key
            }
        elif isinstance(self.known_waypoints, (list, tuple, set)):
            normalized_waypoints = {str(key) for key in self.known_waypoints if key}
        elif self.known_waypoints:
            normalized_waypoints = {str(self.known_waypoints)}
        else:
            normalized_waypoints = set()
        self.known_waypoints = normalized_waypoints

        if isinstance(self.legacy_techniques, Mapping):
            technique_candidates = [
                str(key).strip()
                for key, enabled in self.legacy_techniques.items()
                if enabled
            ]
        elif isinstance(self.legacy_techniques, (list, tuple, set)):
            technique_candidates = [
                str(entry).strip() for entry in self.legacy_techniques
            ]
        elif self.legacy_techniques:
            technique_candidates = [str(self.legacy_techniques).strip()]
        else:
            technique_candidates = []
        legacy_techniques: list[str] = []
        seen_legacy_techniques: set[str] = set()
        for key in technique_candidates:
            if not key or key in seen_legacy_techniques:
                continue
            legacy_techniques.append(key)
            seen_legacy_techniques.add(key)
        self.legacy_techniques = legacy_techniques

        if isinstance(self.legacy_traits, Mapping):
            trait_candidates = [
                str(key).strip()
                for key, enabled in self.legacy_traits.items()
                if enabled
            ]
        elif isinstance(self.legacy_traits, (list, tuple, set)):
            trait_candidates = [str(entry).strip() for entry in self.legacy_traits]
        elif self.legacy_traits:
            trait_candidates = [str(self.legacy_traits).strip()]
        else:
            trait_candidates = []
        legacy_traits: list[str] = []
        seen_legacy_traits: set[str] = set()
        for key in trait_candidates:
            if not key or key in seen_legacy_traits:
                continue
            legacy_traits.append(key)
            seen_legacy_traits.add(key)
        self.legacy_traits = legacy_traits

        if isinstance(self.legacy_heirs, Mapping):
            heir_candidates: list[Any] = [
                candidate
                for candidate, enabled in self.legacy_heirs.items()
                if enabled
            ]
        elif isinstance(self.legacy_heirs, (list, tuple, set)):
            heir_candidates = list(self.legacy_heirs)
        elif self.legacy_heirs:
            heir_candidates = [self.legacy_heirs]
        else:
            heir_candidates = []
        normalized_heirs: list[int] = []
        seen_heirs: set[int] = set()
        for value in heir_candidates:
            try:
                heir_id = int(value)
            except (TypeError, ValueError):
                continue
            if heir_id <= 0 or heir_id in seen_heirs:
                continue
            normalized_heirs.append(heir_id)
            seen_heirs.add(heir_id)
        self.legacy_heirs = normalized_heirs

        if self.retired_at is not None:
            try:
                self.retired_at = float(self.retired_at)
            except (TypeError, ValueError):
                self.retired_at = None

    def pronouns(self) -> PronounSet:
        """Return the pronoun set associated with this cultivator."""

        return PronounSet.from_gender(self.gender)

    def is_path_unlocked(self, path: CultivationPath | str) -> bool:
        value = CultivationPath.from_value(path).value
        return value in self.unlocked_paths

    def stage_key_for_path(self, path: CultivationPath | str) -> Optional[str]:
        path_value = CultivationPath.from_value(path)
        if path_value is CultivationPath.BODY:
            return self.body_cultivation_stage
        if path_value is CultivationPath.SOUL:
            return self.soul_cultivation_stage
        return self.cultivation_stage

    def set_stage_for_path(
        self,
        path: CultivationPath | str,
        stage_key: Optional[str],
    ) -> None:
        path_value = CultivationPath.from_value(path)
        if path_value is CultivationPath.BODY:
            self.body_cultivation_stage = stage_key
        elif path_value is CultivationPath.SOUL:
            self.soul_cultivation_stage = stage_key
        else:
            self.cultivation_stage = stage_key or self.cultivation_stage

    def reset_progress_for_path(
        self,
        path: CultivationPath | str,
        *,
        stage_key: Optional[str],
        exp_required: int,
    ) -> None:
        path_value = CultivationPath.from_value(path)
        normalized_required = max(1, int(exp_required)) if exp_required else 1
        if path_value is CultivationPath.BODY:
            self.body_cultivation_stage = stage_key
            self.combat_exp = 0
            self.combat_exp_required = normalized_required
        elif path_value is CultivationPath.SOUL:
            self.soul_cultivation_stage = stage_key
            self.soul_exp = 0
            self.soul_exp_required = normalized_required
        else:
            self.cultivation_stage = stage_key or self.cultivation_stage
            self.cultivation_exp = 0
            self.cultivation_exp_required = normalized_required

    def unlock_path(self, path: CultivationPath | str) -> None:
        path_value = CultivationPath.from_value(path).value
        if path_value not in self.unlocked_paths:
            self.unlocked_paths.append(path_value)

    def switch_active_path(
        self,
        path: CultivationPath | str,
        *,
        stage_key: Optional[str],
        exp_required: int,
    ) -> None:
        path_value = CultivationPath.from_value(path)
        self.unlock_path(path_value)
        self.reset_progress_for_path(
            path_value,
            stage_key=stage_key,
            exp_required=exp_required,
        )
        self.active_path = path_value.value

    def _sync_equipped_items(self) -> None:
        if self.equipment:
            ordered: List[str] = []
            ordered_keys: Set[str] = set()
            for slot in EQUIPMENT_SLOT_ORDER:
                slot_values = self.equipment.get(slot.value)
                if not slot_values:
                    continue
                ordered_keys.add(slot.value)
                ordered.extend(str(value) for value in slot_values if value)
            for slot_key, values in self.equipment.items():
                if slot_key in ordered_keys:
                    continue
                ordered.extend(str(value) for value in values if value)
            self.equipped_items = ordered
        else:
            self.equipped_items = [str(value) for value in self.equipped_items if value]

    def iter_equipped_item_keys(self) -> Iterator[str]:
        if self.equipment:
            seen: Set[str] = set()
            for slot in EQUIPMENT_SLOT_ORDER:
                slot_values = self.equipment.get(slot.value)
                if not slot_values:
                    continue
                seen.add(slot.value)
                for value in slot_values:
                    yield str(value)
            for slot_key, values in self.equipment.items():
                if slot_key in seen:
                    continue
                for value in values:
                    yield str(value)
        else:
            for value in self.equipped_items:
                yield str(value)

    def equipped_item_keys(self) -> List[str]:
        return list(self.iter_equipped_item_keys())

    def rebuild_equipped_items(self) -> None:
        self._sync_equipped_items()

    @property
    def innate_souls(self) -> list[InnateSoul]:
        return [
            InnateSoul(
                name=soul.name,
                grade=soul.grade,
                affinities=soul.affinities,
            )
            for soul in self.martial_souls
        ]

    @property
    def innate_soul_set(self) -> InnateSoulSet:
        return InnateSoulSet(self.innate_souls)

    @property
    def innate_soul(self) -> InnateSoulSet | None:
        base = self.innate_soul_set
        return base if base else None

    @property
    def soul_power_rank(self) -> SoulPowerRank:
        return rank_for_level(self.soul_power_level)

    @property
    def max_spirit_ring_slots(self) -> int:
        return max_ring_slots_for_level(self.soul_power_level)

    @property
    def required_spirit_rings(self) -> int:
        return required_ring_count_for_level(self.soul_power_level)

    def has_required_spirit_rings(self) -> bool:
        return len(self.soul_rings) >= self.required_spirit_rings

    @property
    def active_innate_soul_mutations(self) -> list[InnateSoulMutation]:
        """Legacy compatibility shim returning no active mutations."""

        return []

    @property
    def innate_soul_mutation_history(self) -> list[InnateSoulMutation]:
        """Legacy compatibility shim returning no mutation history."""

        return []

    @property
    def pending_innate_soul_mutations(self) -> list[InnateSoulMutation]:
        """Legacy compatibility shim returning no pending opportunities."""

        return []

    @property
    def innate_soul_hybridized(self) -> bool:
        """Legacy compatibility flag preserved for older interfaces."""

        return False

    def combined_innate_soul(
        self, traits: Sequence[SpecialTrait] | None = None
    ) -> InnateSoulSet | None:
        base = self.innate_soul_set
        extras: list[SpiritualAffinity] = []
        for trait in traits or ():
            extras.extend(trait.grants_affinities)
        combined = base.with_bonus_affinities(extras)
        return combined if combined else None

    def equip_title(self, position: TitlePosition, title_key: Optional[str]) -> None:
        """Equip or unequip a title for the given position."""

        if title_key is not None and title_key not in self.titles:
            return
        if position == TitlePosition.PREFIX:
            self.active_title_prefix = title_key
        else:
            self.active_title_suffix = title_key

    def auto_equip_title(self, title: "Title") -> None:
        """Equip a newly earned title if the corresponding slot is empty."""

        if title.position == TitlePosition.PREFIX and not self.active_title_prefix:
            self.active_title_prefix = title.key
        elif title.position == TitlePosition.SUFFIX and not self.active_title_suffix:
            self.active_title_suffix = title.key

    def grant_title(
        self,
        title_key: str,
        *,
        position: TitlePosition | str | None = None,
    ) -> bool:
        if title_key in self.titles:
            return False
        self.titles.append(title_key)
        if position is not None:
            try:
                resolved = TitlePosition.from_value(position)
            except ValueError:
                resolved = None
            else:
                if resolved == TitlePosition.PREFIX and not self.active_title_prefix:
                    self.active_title_prefix = title_key
                elif resolved == TitlePosition.SUFFIX and not self.active_title_suffix:
                    self.active_title_suffix = title_key
        return True

    def grant_titles(
        self,
        title_keys: Sequence[str],
        *,
        positions: Mapping[str, TitlePosition] | None = None,
    ) -> List[str]:
        newly_added: List[str] = []
        for key in title_keys:
            position = positions.get(key) if positions else None
            if self.grant_title(key, position=position):
                newly_added.append(key)
        return newly_added

    def revoke_title(self, title_key: str) -> bool:
        if title_key not in self.titles:
            return False
        self.titles.remove(title_key)
        if self.active_title_prefix == title_key:
            self.active_title_prefix = None
        if self.active_title_suffix == title_key:
            self.active_title_suffix = None
        return True

    def add_legacy_technique(self, technique_key: str) -> bool:
        normalized = str(technique_key).strip()
        if not normalized or normalized in self.legacy_techniques:
            return False
        self.legacy_techniques.append(normalized)
        return True

    def remove_legacy_technique(self, technique_key: str) -> bool:
        normalized = str(technique_key).strip()
        if normalized not in self.legacy_techniques:
            return False
        self.legacy_techniques.remove(normalized)
        return True

    def add_legacy_trait(self, trait_key: str) -> bool:
        normalized = str(trait_key).strip()
        if not normalized or normalized in self.legacy_traits:
            return False
        self.legacy_traits.append(normalized)
        return True

    def remove_legacy_trait(self, trait_key: str) -> bool:
        normalized = str(trait_key).strip()
        if normalized not in self.legacy_traits:
            return False
        self.legacy_traits.remove(normalized)
        return True

    def designate_legacy_heir(self, user_id: int) -> bool:
        try:
            normalized = int(user_id)
        except (TypeError, ValueError):
            return False
        if normalized <= 0 or normalized in self.legacy_heirs:
            return False
        self.legacy_heirs.append(normalized)
        return True

    def revoke_legacy_heir(self, user_id: int) -> bool:
        try:
            normalized = int(user_id)
        except (TypeError, ValueError):
            return False
        if normalized not in self.legacy_heirs:
            return False
        self.legacy_heirs.remove(normalized)
        return True


def iter_equipped_item_keys(player: PlayerProgress) -> Iterator[str]:
    yield from player.iter_equipped_item_keys()


def equipped_items_for_player(
    player: PlayerProgress, items: Mapping[str, Item]
) -> List[Item]:
    equipped: List[Item] = []
    for key in player.iter_equipped_item_keys():
        item = items.get(key)
        if item:
            equipped.append(item)
    return equipped


def equipment_slot_usage(
    player: PlayerProgress, slot: EquipmentSlot, items: Mapping[str, Item]
) -> int:
    keys = player.equipment.get(slot.value, [])
    usage = 0
    for key in keys:
        item = items.get(key)
        if item is None:
            usage += 1
            continue
        cost = getattr(item, "slots_required", 1) or 1
        usage += max(1, int(cost))
    return usage


def add_equipped_item(player: PlayerProgress, slot: EquipmentSlot, item_key: str) -> None:
    bucket = player.equipment.setdefault(slot.value, [])
    bucket.append(str(item_key))
    player.rebuild_equipped_items()


def remove_equipped_item(player: PlayerProgress, item_key: str) -> bool:
    target = str(item_key)
    for slot_key, values in list(player.equipment.items()):
        if target in values:
            values.remove(target)
            if not values:
                player.equipment.pop(slot_key, None)
            player.rebuild_equipped_items()
            return True
    return False


def grant_legacy_to_heir(
    ancestor: PlayerProgress, heir: PlayerProgress
) -> tuple[list[str], list[str]]:
    """Transfer legacy techniques and traits from ``ancestor`` to ``heir``.

    Returns a tuple containing two lists: newly granted technique keys and newly
    granted trait keys.
    """

    granted_techniques: list[str] = []
    seen_techniques: set[str] = set()
    for key in ancestor.legacy_techniques:
        normalized = str(key).strip()
        if not normalized or normalized in seen_techniques:
            continue
        seen_techniques.add(normalized)
        if normalized in heir.cultivation_technique_keys:
            continue
        heir.cultivation_technique_keys.append(normalized)
        granted_techniques.append(normalized)

    granted_traits: list[str] = []
    seen_traits: set[str] = set()
    for key in ancestor.legacy_traits:
        normalized = str(key).strip()
        if not normalized or normalized in seen_traits:
            continue
        seen_traits.add(normalized)
        if normalized in heir.trait_keys:
            continue
        heir.trait_keys.append(normalized)
        granted_traits.append(normalized)

    return granted_techniques, granted_traits


def active_weapon_types(player: PlayerProgress, items: Mapping[str, Item]) -> Set[WeaponType]:
    types: Set[WeaponType] = set()
    for key in player.equipment.get(EquipmentSlot.WEAPON.value, []):
        item = items.get(key)
        if item and item.weapon_type:
            types.add(item.weapon_type)
    if types:
        return types
    return {WeaponType.BARE_HAND}


@dataclass(slots=True)
class BondProfile:
    """Configuration for co-operative cultivation bonds."""

    key: str
    name: str
    description: str = ""
    path: str = CultivationPath.QI.value
    min_stage: Optional[str] = None
    exp_multiplier: float = 0.0
    flat_bonus: int = 0
    bond_mission_key: Optional[str] = None
    bond_soul_techniques: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        key = str(self.key).strip().lower()
        if not key:
            raise ValueError("Bond profiles must define a key")
        self.key = key
        self.name = str(self.name).strip() or self.key.replace("-", " ").title()
        self.description = str(self.description or "").strip()
        try:
            path_value = CultivationPath.from_value(self.path)
        except ValueError:
            path_value = CultivationPath.QI
        self.path = path_value.value
        self.min_stage = str(self.min_stage).strip() or None if self.min_stage else None
        try:
            multiplier = float(self.exp_multiplier)
        except (TypeError, ValueError):
            multiplier = 0.0
        self.exp_multiplier = max(-0.99, min(10.0, multiplier))
        try:
            flat = int(self.flat_bonus)
        except (TypeError, ValueError):
            flat = 0
        self.flat_bonus = max(0, flat)

        mission_key = str(self.bond_mission_key).strip().lower() if self.bond_mission_key else None
        self.bond_mission_key = mission_key or None

        techniques: list[str] = []
        seen: set[str] = set()
        raw_techniques = self.bond_soul_techniques
        if isinstance(raw_techniques, Mapping):
            candidates = [
                str(key).strip()
                for key, enabled in raw_techniques.items()
                if enabled
            ]
        elif isinstance(raw_techniques, Sequence) and not isinstance(
            raw_techniques, (str, bytes)
        ):
            candidates = [str(entry).strip() for entry in raw_techniques]
        elif raw_techniques:
            candidates = [str(raw_techniques).strip()]
        else:
            candidates = []
        for technique_key in candidates:
            if not technique_key or technique_key in seen:
                continue
            techniques.append(technique_key)
            seen.add(technique_key)
        self.bond_soul_techniques = techniques

    @property
    def required_path(self) -> CultivationPath:
        return CultivationPath.from_value(self.path)

    def bonus_amount(self, gain: int) -> int:
        if gain <= 0:
            return 0
        bonus = 0
        if self.exp_multiplier:
            bonus += int(round(gain * self.exp_multiplier))
        if self.flat_bonus:
            bonus += self.flat_bonus
        return max(0, bonus)


@dataclass(slots=True)
class Party:
    party_id: str
    leader_id: int
    member_ids: List[int]
    location: Optional[str]




__all__ = [
    "PronounSet",
    "PlayerProgress",
    "BondProfile",
    "Party",
    "iter_equipped_item_keys",
    "equipped_items_for_player",
    "equipment_slot_usage",
    "add_equipped_item",
    "remove_equipped_item",
    "grant_legacy_to_heir",
    "active_weapon_types",
]
