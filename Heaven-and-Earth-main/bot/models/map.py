"""Advanced world map and travel domain models.

This module introduces a fully fledged exploratory layer for the bot.  It
models a hierarchical world map, travel actions, dynamic encounters, resource
nodes, faction control, fog of war tracking, and supporting systems such as
travel mastery progression and escalation timelines.  The goal is to provide
enough structure so every design point from the exploration overhaul proposal
is captured in code and ready for future integration with command handlers.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum, auto
import random
from typing import (
    Any,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)


# ---------------------------------------------------------------------------
# Core map primitives
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TileCoordinate:
    """Precise coordinate of a tile on the layered map."""

    x: int
    y: int
    z: int = 0

    def to_key(self) -> str:
        return f"{self.x}:{self.y}:{self.z}"

    @classmethod
    def from_key(cls, key: str) -> "TileCoordinate":
        try:
            x_str, y_str, z_str = key.split(":", 2)
        except ValueError:
            raise ValueError(f"Invalid coordinate key: {key}") from None
        return cls(int(x_str), int(y_str), int(z_str))


class TerrainType(str, Enum):
    """Common terrain tags used to theme tiles."""

    FOREST = "forest"
    MARSH = "marsh"
    MOUNTAIN = "mountain"
    PLAINS = "plains"
    DESERT = "desert"
    COAST = "coast"
    URBAN = "urban"
    SUBTERRANEAN = "subterranean"
    SKY = "sky"


class TileCategory(str, Enum):
    """High-level tile groupings exposed to the player."""

    NORMAL = "normal"
    POINT_OF_INTEREST = "point_of_interest"


@dataclass(slots=True)
class EnvironmentalModifiers:
    """Dynamic environmental effects influencing encounters and combat."""

    weather: str = "clear"
    qi_density: float = 1.0
    corruption_level: float = 0.0
    hazard_level: float = 0.0
    ambient_temperature: float = 20.0
    notes: List[str] = field(default_factory=list)


@dataclass(slots=True)
class DynamicOwnership:
    """Represents faction control and fortification on a tile."""

    faction: Optional[str] = None
    influence: float = 0.0
    fortification_level: int = 0
    last_contested_ts: Optional[float] = None


@dataclass(slots=True)
class ProceduralHookTemplate:
    """Template for procedural content spawners tied to tiles."""

    key: str
    weight: float = 1.0
    parameters: Dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class ResourceNode:
    """Gatherable resource node with depletion and respawn timers."""

    key: str
    resource_type: str
    tier: int
    remaining_yield: int
    respawn_seconds: float
    last_harvest_ts: Optional[float] = None


@dataclass(slots=True)
class TileDynamicState:
    """Mutable per-tile state used for encounter pacing and escalation."""

    noise: float = 0.0
    heat: float = 0.0
    recent_events: deque[str] = field(default_factory=lambda: deque(maxlen=10))
    active_nodes: Dict[str, ResourceNode] = field(default_factory=dict)


@dataclass(slots=True)
class Tile:
    """A single traversable tile within a zone."""

    coordinate: TileCoordinate
    terrain: TerrainType
    elevation: float = 0.0
    traversal_difficulty: float = 1.0
    is_safe: bool = False
    category: TileCategory = TileCategory.NORMAL
    environmental: EnvironmentalModifiers = field(default_factory=EnvironmentalModifiers)
    ownership: DynamicOwnership = field(default_factory=DynamicOwnership)
    points_of_interest: List[str] = field(default_factory=list)
    procedural_hooks: List[ProceduralHookTemplate] = field(default_factory=list)
    zone_id: Optional[str] = None
    dynamic: TileDynamicState = field(default_factory=TileDynamicState)


@dataclass(slots=True)
class Zone:
    """Collection of tiles with a shared identity inside a region."""

    zone_id: str
    name: str
    description: str
    tiles: Dict[str, Tile] = field(default_factory=dict)
    zone_type: str = "overworld"
    threat_rating: float = 1.0
    recommended_level: int = 1

    def add_tile(self, tile: Tile) -> None:
        tile.zone_id = self.zone_id
        self.tiles[tile.coordinate.to_key()] = tile

    def iter_tiles(self) -> Iterator[Tile]:
        return iter(self.tiles.values())


@dataclass(slots=True)
class RegionEdge:
    """Connection between two regions with travel metadata."""

    region_key: str
    distance: float
    hazard: float = 0.0
    travel_modes: Set[str] = field(default_factory=set)


@dataclass(slots=True)
class Region:
    """High-level map grouping containing zones and adjacency information."""

    region_id: str
    name: str
    description: str
    zones: Dict[str, Zone] = field(default_factory=dict)
    adjacency: Dict[str, RegionEdge] = field(default_factory=dict)
    conflict_timeline: List["EscalationStage"] = field(default_factory=list)
    current_escalation_index: int = 0

    def add_zone(self, zone: Zone) -> None:
        self.zones[zone.zone_id] = zone

    def connect(self, edge: RegionEdge) -> None:
        self.adjacency[edge.region_key] = edge

    def active_escalation_stage(self) -> Optional["EscalationStage"]:
        if not self.conflict_timeline:
            return None
        index = max(0, min(self.current_escalation_index, len(self.conflict_timeline) - 1))
        return self.conflict_timeline[index]


@dataclass(slots=True)
class EscalationStage:
    """Represents a milestone in a region-wide conflict timeline."""

    key: str
    threshold: float
    description: str
    spawn_table_modifiers: Dict[str, float] = field(default_factory=dict)
    poi_changes: List[str] = field(default_factory=list)


@dataclass(slots=True)
class WorldClock:
    """Tracks global in-world time progression for travel narration."""

    day: int = 1
    seconds_into_day: float = 8 * 3600.0  # start at morning by default

    _PHASES: Tuple[Tuple[int, str], ...] = (
        (5, "dawn"),
        (11, "day"),
        (17, "dusk"),
        (21, "night"),
    )

    def time_of_day(self) -> str:
        hour = int(self.seconds_into_day // 3600) % 24
        current = "night"
        for threshold, phase in self._PHASES:
            if hour < threshold:
                return phase
            current = phase
        return current

    def formatted_time(self) -> str:
        total_seconds = int(self.seconds_into_day) % (24 * 3600)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        return f"Day {self.day} — {hours:02d}:{minutes:02d} ({self.time_of_day().title()})"

    def advance(self, seconds: float) -> Optional[Tuple[str, str]]:
        previous_phase = self.time_of_day()
        self.seconds_into_day += max(0.0, seconds)
        while self.seconds_into_day >= 24 * 3600:
            self.seconds_into_day -= 24 * 3600
            self.day += 1
        current_phase = self.time_of_day()
        if current_phase != previous_phase:
            return previous_phase, current_phase
        return None


@dataclass(slots=True)
class WeatherState:
    """Dynamic weather information used to influence encounters."""

    pattern: str = "clear"
    intensity: float = 0.0
    temperature: float = 20.0
    wind_speed: float = 5.0
    visibility_penalty: float = 0.0
    duration_seconds: float = 600.0
    elapsed_seconds: float = 0.0

    def advance(self, seconds: float) -> bool:
        self.elapsed_seconds += max(0.0, seconds)
        if self.elapsed_seconds >= self.duration_seconds:
            self.elapsed_seconds = 0.0
            return True
        return False

    def apply_profile(self, profile: "SeasonProfile", *, weight_bias: Optional[Dict[str, float]] = None) -> None:
        weights = dict(profile.weather_weights)
        if weight_bias:
            for key, delta in weight_bias.items():
                weights[key] = weights.get(key, 0.0) + delta
        total = sum(max(value, 0.0) for value in weights.values())
        if total <= 0:
            choice = "clear"
        else:
            threshold = random.random() * total
            cumulative = 0.0
            choice = "clear"
            for name, weight in weights.items():
                cumulative += max(weight, 0.0)
                if threshold <= cumulative:
                    choice = name
                    break
        self.pattern = choice
        self.intensity = 0.2 + random.random() * 0.8
        self.temperature = profile.base_temperature + random.uniform(-5.0, 5.0)
        self.wind_speed = max(0.0, profile.base_wind_speed + random.uniform(-2.0, 4.0))
        self.visibility_penalty = 0.0 if choice == "clear" else min(0.8, 0.2 + self.intensity * 0.6)
        self.duration_seconds = max(300.0, random.uniform(300.0, 1200.0))


@dataclass(slots=True)
class SeasonProfile:
    """Defines season-dependent weather behaviour for regions."""

    key: str
    name: str
    duration_days: int
    weather_weights: Dict[str, float]
    base_temperature: float = 18.0
    base_wind_speed: float = 4.0


@dataclass(slots=True)
class Waypoint:
    """Notable waypoint used for fast travel summaries."""

    key: str
    coordinate: TileCoordinate
    name: str
    description: str
    tags: Set[str] = field(default_factory=set)


@dataclass(slots=True)
class LeyLineNode:
    """A ley line anchor used for phase-shift travel."""

    key: str
    coordinate: TileCoordinate
    attunement_cost: float = 1.0
    stability: float = 1.0


@dataclass(slots=True)
class LeyLineLink:
    """Connection between two ley line anchors."""

    target_key: str
    difficulty: float = 1.0


@dataclass(slots=True)
class WorldMap:
    """Top level container managing regions, zones, tiles, and world state."""

    regions: Dict[str, Region] = field(default_factory=dict)
    waypoints: Dict[str, Waypoint] = field(default_factory=dict)
    ley_lines: Dict[str, LeyLineNode] = field(default_factory=dict)
    ley_line_links: Dict[str, List[LeyLineLink]] = field(default_factory=lambda: defaultdict(list))
    clock: WorldClock = field(default_factory=WorldClock)
    seasons: List[SeasonProfile] = field(default_factory=list)
    active_season_index: int = 0
    region_weather: Dict[str, WeatherState] = field(default_factory=dict)
    generated_tiles: Dict[str, Tile] = field(default_factory=dict)

    def add_region(self, region: Region) -> None:
        self.regions[region.region_id] = region

    def get_region(self, region_id: str) -> Optional[Region]:
        return self.regions.get(region_id)

    def iter_tiles(self) -> Iterator[Tile]:
        for region in self.regions.values():
            for zone in region.zones.values():
                yield from zone.iter_tiles()
        yield from self.generated_tiles.values()

    def tile_for_key(self, key: str) -> Optional[Tile]:
        for region in self.regions.values():
            for zone in region.zones.values():
                tile = zone.tiles.get(key)
                if tile is not None:
                    return tile
        tile = self.generated_tiles.get(key)
        if tile is not None:
            return tile
        try:
            coordinate = TileCoordinate.from_key(key)
        except ValueError:
            return None
        generated = self._generate_tile(coordinate)
        self.generated_tiles[key] = generated
        return generated

    def _generate_tile(self, coordinate: TileCoordinate) -> Tile:
        """Procedurally fabricate a traversable tile when data is missing."""

        seed = f"{coordinate.x}:{coordinate.y}:{coordinate.z}"
        rng = random.Random(seed)
        sanctuary_chance = 0.05
        poi_chance = 0.18
        is_safe = rng.random() < sanctuary_chance
        is_point_of_interest = is_safe or rng.random() < poi_chance
        category = (
            TileCategory.POINT_OF_INTEREST
            if is_point_of_interest
            else TileCategory.NORMAL
        )
        elevation = rng.uniform(-15.0, 120.0)
        hazard = min(1.0, max(0.0, rng.random() * 0.6))
        qi_density = 0.9 + rng.random() * 0.6
        points: List[str] = []
        if category is TileCategory.POINT_OF_INTEREST:
            poi_names = [
                "Hidden Shrine",
                "Spirit Den",
                "Traveler's Cache",
                "Abandoned Camp",
                "Wayfarer Bazaar",
            ]
            name = rng.choice(poi_names)
            points.append(f"{name} {coordinate.x:+d}:{coordinate.y:+d}")
            if is_safe and name != "Hidden Shrine":
                points.append("Protected Refuge")
        tile = Tile(
            coordinate=coordinate,
            terrain=TerrainType.PLAINS,
            elevation=elevation,
            traversal_difficulty=1.0,
            is_safe=is_safe,
            category=category,
            environmental=EnvironmentalModifiers(
                weather="clear",
                qi_density=qi_density,
                hazard_level=hazard,
            ),
            points_of_interest=points,
        )
        return tile

    def register_waypoint(self, waypoint: Waypoint) -> None:
        self.waypoints[waypoint.key] = waypoint

    def waypoints_near(self, coordinate: TileCoordinate, radius: int = 0) -> List[Waypoint]:
        results: List[Waypoint] = []
        for waypoint in self.waypoints.values():
            if manhattan_distance(waypoint.coordinate, coordinate) <= radius:
                results.append(waypoint)
        return results

    def register_ley_line(self, node: LeyLineNode) -> None:
        self.ley_lines[node.key] = node

    def connect_ley_lines(self, source_key: str, link: LeyLineLink) -> None:
        self.ley_line_links[source_key].append(link)

    def active_season(self) -> Optional[SeasonProfile]:
        if not self.seasons:
            return None
        index = max(0, min(self.active_season_index, len(self.seasons) - 1))
        return self.seasons[index]

    def advance_time(self, seconds: float) -> Optional[Tuple[str, str]]:
        return self.clock.advance(seconds)

    def rotate_season_if_needed(self) -> Optional[SeasonProfile]:
        season = self.active_season()
        if not season:
            return None
        total_day_fraction = self.clock.day % max(1, season.duration_days)
        if total_day_fraction == 0:
            self.active_season_index = (self.active_season_index + 1) % len(self.seasons)
            new_season = self.active_season()
            if new_season:
                for state in self.region_weather.values():
                    state.apply_profile(new_season)
            return new_season
        return None

    def ensure_region_weather(self, region_id: str) -> WeatherState:
        state = self.region_weather.get(region_id)
        if state is None:
            state = WeatherState()
            profile = self.active_season()
            if profile:
                state.apply_profile(profile)
            self.region_weather[region_id] = state
        return state

    def advance_weather(self, seconds: float) -> Dict[str, WeatherState]:
        changed: Dict[str, WeatherState] = {}
        season = self.active_season()
        for region_id, state in list(self.region_weather.items()):
            if state.advance(seconds) and season:
                state.apply_profile(season)
                changed[region_id] = state
        return changed


# ---------------------------------------------------------------------------
# Fog of war tracking
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FogOfWarState:
    """Per-player exploration caches used to render fog of war."""

    discovered_tiles: Set[str] = field(default_factory=set)
    surveyed_tiles: Set[str] = field(default_factory=set)
    rumored_points: Set[str] = field(default_factory=set)

    def mark_discovered(self, coordinate: TileCoordinate) -> None:
        key = coordinate.to_key()
        self.discovered_tiles.add(key)

    def mark_surveyed(self, coordinate: TileCoordinate, radius: int = 0) -> None:
        self.mark_discovered(coordinate)
        self.surveyed_tiles.add(coordinate.to_key())

    def add_rumor(self, description: str) -> None:
        if description:
            self.rumored_points.add(description.strip())


# ---------------------------------------------------------------------------
# Travel actions and movement modelling
# ---------------------------------------------------------------------------


class TravelMode(Enum):
    """Single traversal approach used for all movement."""

    MOVE = auto()


@dataclass(slots=True)
class MovementCostBreakdown:
    """Detailed cost report for a travel step."""

    time_seconds: float
    stamina_cost: float
    resource_cost: Dict[str, int] = field(default_factory=dict)
    noise_generated: float = 0.0
    corruption_exposure: float = 0.0


@dataclass(slots=True)
class TravelSegment:
    """Chunk of movement processed together to keep pacing manageable."""

    coordinates: List[TileCoordinate]
    mode: TravelMode
    cost: MovementCostBreakdown


@dataclass(slots=True)
class TravelPath:
    """Full travel plan returned by pathfinding."""

    segments: List[TravelSegment]
    total_cost: MovementCostBreakdown


@dataclass(slots=True)
class TravelAction:
    """Base class for movement related commands."""

    actor_id: int
    start: TileCoordinate


@dataclass(slots=True)
class TraverseAction(TravelAction):
    distance: int
    mode: TravelMode = TravelMode.MOVE


@dataclass(slots=True)
class ScoutAction(TravelAction):
    radius: int = 1
    stealth_bonus: float = 0.0


@dataclass(slots=True)
class CampAction(TravelAction):
    duration_seconds: float = 600.0
    warding_strength: float = 0.0


@dataclass(slots=True)
class ForageAction(TravelAction):
    """Action to harvest nearby resource nodes while travelling."""

    yield_expectation: float = 1.0
    stealth_modifier: float = 0.0


@dataclass(slots=True)
class SetTrapAction(TravelAction):
    """Action to establish a trap that influences subsequent encounters."""

    trap_key: str = "snare"
    potency: float = 0.5
    duration_seconds: float = 1800.0


# ---------------------------------------------------------------------------
# Dynamic encounter systems
# ---------------------------------------------------------------------------


class EncounterBucket(Enum):
    """Buckets of encounter themes resolved during travel."""

    AMBIENT_FAUNA = auto()
    HOSTILE_PATROL = auto()
    ROGUE_CULTIVATOR = auto()
    FACTION_AGENT = auto()
    WORLD_BOSS_SCOUT = auto()
    ENVIRONMENTAL_HAZARD = auto()


@dataclass(slots=True)
class EncounterWeightProfile:
    """Weights for each bucket influenced by terrain, weather, and history."""

    weights: Dict[EncounterBucket, float] = field(
        default_factory=lambda: defaultdict(float)
    )

    def normalized(self) -> Dict[EncounterBucket, float]:
        total = sum(self.weights.values())
        if total <= 0:
            return {bucket: 0.0 for bucket in EncounterBucket}
        return {bucket: value / total for bucket, value in self.weights.items()}


class EncounterKind(str, Enum):
    """High level flavour of an encounter roll."""

    COMBAT = "combat"
    LOOT = "loot"
    DISCOVERY = "discovery"
    NPC = "npc"
    HAZARD = "hazard"


@dataclass(slots=True)
class SpawnContext:
    """Inputs used when rolling for encounters while travelling."""

    tile: Tile
    time_of_day: str
    weather: str
    player_notoriety: float
    faction_alignment: Dict[str, float]
    active_quests: Set[str]
    noise_level: float


@dataclass(slots=True)
class EncounterRoll:
    """Outcome of an encounter roll including optional prefight options."""

    bucket: EncounterBucket
    description: str
    danger_rating: float
    preemptive_options: List[str] = field(default_factory=list)
    kind: EncounterKind = EncounterKind.COMBAT
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NoiseMeter:
    """Tracks accumulated noise to trigger patrols or bosses."""

    value: float = 0.0
    decay_per_second: float = 0.1

    def tick(self, seconds: float) -> None:
        self.value = max(0.0, self.value - self.decay_per_second * seconds)

    def add(self, amount: float) -> None:
        self.value += max(0.0, amount)

    def clone(self) -> "NoiseMeter":
        return NoiseMeter(value=self.value, decay_per_second=self.decay_per_second)


@dataclass(slots=True)
class TileHeatTracker:
    """Simple tracker preventing repetitive spawns on the same tile."""

    heat: MutableMapping[str, float] = field(default_factory=lambda: defaultdict(float))

    def bump(self, key: str, amount: float = 1.0) -> None:
        self.heat[key] += amount

    def decay(self, amount: float = 0.1) -> None:
        for key in list(self.heat.keys()):
            self.heat[key] = max(0.0, self.heat[key] - amount)
            if self.heat[key] <= 0:
                self.heat.pop(key, None)


# ---------------------------------------------------------------------------
# Escalation and world state management
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RegionEscalationTrack:
    """Mutable progress meter driving conflict timelines."""

    region_id: str
    progress: float = 0.0
    thresholds: List[float] = field(default_factory=list)

    def advance(self, amount: float) -> int:
        self.progress += max(0.0, amount)
        reached = 0
        while self.thresholds and self.progress >= self.thresholds[0]:
            reached += 1
            self.thresholds.pop(0)
        return reached


@dataclass(slots=True)
class TravelReward:
    """Rewards granted upon finishing travel segments or milestones."""

    exploration_xp: int = 0
    loot_keys: List[str] = field(default_factory=list)
    milestone_name: Optional[str] = None
    reputation_changes: Dict[str, float] = field(default_factory=dict)


class ExpeditionStepType(Enum):
    """Enumeration of steps that make up a travel expedition."""

    TRAVEL = auto()
    SCOUT = auto()
    CAMP = auto()
    FORAGE = auto()
    SET_TRAP = auto()
    OBSERVE = auto()


@dataclass(slots=True)
class ExpeditionStep:
    """Single actionable step within an expedition plan."""

    step_type: ExpeditionStepType
    coordinate: TileCoordinate
    description: str
    mode: Optional[TravelMode] = None
    data: Dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class ExpeditionPlan:
    """Full expedition outline consisting of multiple steps."""

    destination: TileCoordinate
    steps: List[ExpeditionStep]
    estimated_cost: MovementCostBreakdown
    risk_rating: float = 0.0
    objectives: List[str] = field(default_factory=list)


@dataclass(slots=True)
class ExpeditionOutcome:
    """Outcome of executing an expedition plan."""

    plan: ExpeditionPlan
    events: List["TravelEvent"] = field(default_factory=list)
    rewards: List[TravelReward] = field(default_factory=list)
    milestones: List[str] = field(default_factory=list)
    travel_path: Optional[TravelPath] = None


@dataclass(slots=True)
class TravelJournalEntry:
    """Record of noteworthy travel results stored on the player."""

    timestamp: float
    coordinate: TileCoordinate
    summary: str
    events: List[str] = field(default_factory=list)
    time_of_day: str = "day"
    weather_pattern: str = "clear"


@dataclass(slots=True)
class TravelMasteryNode:
    """Single node within a travel mastery tree."""

    key: str
    name: str
    description: str
    prerequisites: List[str] = field(default_factory=list)
    bonuses: Dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class TravelMasteryProgress:
    """Tracks player progression through travel mastery trees."""

    unlocked_nodes: Set[str] = field(default_factory=set)
    experience: int = 0
    level: int = 1
    pending_rewards: List[TravelReward] = field(default_factory=list)

    def add_experience(self, amount: int) -> None:
        self.experience += max(0, amount)
        while self.experience >= self.level * 100:
            self.experience -= self.level * 100
            self.level += 1


@dataclass(slots=True)
class AdaptiveDifficultyProfile:
    """Represents how encounters scale for a player or party."""

    baseline_power: float
    max_safe_level: float
    current_tile_threat: float

    def effective_threat(self) -> float:
        return min(self.max_safe_level, self.current_tile_threat * self.baseline_power)


# ---------------------------------------------------------------------------
# Event queueing and scheduling
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TravelEvent:
    """Event generated during travel such as encounters or vignettes."""

    key: str
    description: str
    bucket: Optional[EncounterBucket] = None
    data: Dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class TravelEventQueue:
    """FIFO queue storing pending travel events for processing."""

    events: deque[TravelEvent] = field(default_factory=deque)

    def push(self, event: TravelEvent) -> None:
        self.events.append(event)

    def pop(self) -> Optional[TravelEvent]:
        if self.events:
            return self.events.popleft()
        return None

    def __len__(self) -> int:  # pragma: no cover - simple pass-through
        return len(self.events)


@dataclass(slots=True)
class ScheduledTravelTask:
    """Task scheduled to occur after a delay in the travel loop."""

    execute_at: float
    event: TravelEvent


@dataclass(slots=True)
class TravelEventScheduler:
    """Event scheduler used to process world ticks and travel events."""

    pending: List[ScheduledTravelTask] = field(default_factory=list)

    def schedule(self, task: ScheduledTravelTask) -> None:
        self.pending.append(task)
        self.pending.sort(key=lambda item: item.execute_at)

    def pop_ready(self, now: float) -> List[TravelEvent]:
        ready: List[TravelEvent] = []
        while self.pending and self.pending[0].execute_at <= now:
            ready.append(self.pending.pop(0).event)
        return ready


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def neighbours_for(tile: TileCoordinate) -> Iterator[TileCoordinate]:
    """Yield all 4-directional neighbours on the same layer."""

    deltas = ((1, 0), (-1, 0), (0, 1), (0, -1))
    for dx, dy in deltas:
        yield TileCoordinate(tile.x + dx, tile.y + dy, tile.z)


def manhattan_distance(a: TileCoordinate, b: TileCoordinate) -> int:
    return abs(a.x - b.x) + abs(a.y - b.y) + abs(a.z - b.z)


def heuristic_cost(a: TileCoordinate, b: TileCoordinate) -> float:
    return float(manhattan_distance(a, b))


def encode_segment_key(coordinates: Sequence[TileCoordinate], mode: TravelMode) -> str:
    coord_key = "->".join(coord.to_key() for coord in coordinates)
    return f"{mode.name}:{coord_key}"

