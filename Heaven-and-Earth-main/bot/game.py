"""Core gameplay mechanics shared across commands and interactions."""

from __future__ import annotations

import math
import random
import time
from collections import defaultdict
from dataclasses import asdict
from typing import Collection, Dict, Iterable, Iterator, List, Mapping, NamedTuple, Optional, Sequence, Tuple, TYPE_CHECKING

from .models.combat import (
    CombatEncounter,
    CombatTurn,
    DamageType,
    Skill,
    SpiritualAffinity,
    Stats,
    WeaponType,
    PLAYER_STAT_NAMES,
    resistance_reduction_fraction,
)
from .models.players import BondProfile, Party, PlayerProgress
from .models.progression import (
    CultivationPath,
    CultivationPhase,
    CultivationStage,
    CultivationTechnique,
    EquipmentSlot,
    DEFAULT_STAGE_BASE_STAT,
    MORTAL_REALM_KEY,
    MORTAL_REALM_NAME,
    Race,
    SpecialTrait,
    Title,
)
from .models.world import (
    Boss,
    Currency,
    Enemy,
    Item,
    Location,
    LocationNPC,
    LootDrop,
    Quest,
    QuestObjective,
    ShopItem,
)
from .models.soul_land import MartialSoul, build_martial_soul_signature_skill

if TYPE_CHECKING:  # pragma: no cover
    from .travel import TravelEngine
from .models.map import (
    EnvironmentalModifiers,
    FogOfWarState,
    LeyLineLink,
    LeyLineNode,
    Region,
    RegionEdge,
    RegionEscalationTrack,
    SeasonProfile,
    TerrainType,
    Tile,
    TileCategory,
    TileCoordinate,
    TileHeatTracker,
    TravelEvent,
    TravelEventScheduler,
    manhattan_distance,
    Waypoint,
    WeatherState,
    WorldMap,
    Zone,
)
from .energy import energy_cap_for_realm


DEFAULT_REALM_LIFESPANS: dict[str, str] = {
    MORTAL_REALM_KEY: "60–80 years",
    "qi-condensation": "90–140 years",
    "foundation-establishment": "140–220 years",
    "core-formation": "220–320 years",
    "nascent-soul": "320–450 years",
    "soul-formation": "450–650 years",
    "soul-transformation": "650–850 years",
    "ascendant": "850–1,100 years",
    "illusory-yin": "1,500–3,000 years",
    "corporeal-yang": "3,000–6,000 years",
    "nirvana-scryer": "10,000–12,000 years per tribulation cycle",
    "nirvana-cleanser": "12,000–15,000 years per cycle",
    "nirvana-shatterer": "15,000–18,000 years per cycle",
    "heavens-blight": "18,000–22,000 years per cycle",
    "nirvana-void": "80,000–120,000 years (practical immortality)",
    "spirit-void": "120,000–200,000 years",
    "arcane-void": "200,000–350,000 years",
    "void-tribulant": "350,000–500,000 years",
    "half-heaven-trampling": "500,000–800,000 years",
    "heaven-trampling": "Lifespan undefined; existence outside linear time",
}


# Cultivation multipliers scale slowly between stages but ramp up between realms.
# Stage deltas stay tiny so cultivators within the same realm remain competitive,
# while realm jumps provide the decisive power spikes.
_STAGE_PHASE_MULTIPLIER_STEP_START = 0.10
_STAGE_PHASE_MULTIPLIER_STEP_GROWTH = 1.12
_STAGE_PHASE_MULTIPLIER_STEP_BONUS = 0.01
_REALM_BASE_MULTIPLIER_START = 1.0
_REALM_BREAKTHROUGH_INCREMENT_START = 1.30
_REALM_BREAKTHROUGH_INCREMENT_GROWTH = 1.18
_REALM_BREAKTHROUGH_INCREMENT_BONUS = 0.40


def _progressive_realm_base(order: int) -> float:
    """Return the base innate multiplier for a realm index."""

    if order <= 0:
        return round(_REALM_BASE_MULTIPLIER_START, 2)

    base = _REALM_BASE_MULTIPLIER_START
    increment = _REALM_BREAKTHROUGH_INCREMENT_START
    for _ in range(order):
        base += increment
        increment = (
            increment * _REALM_BREAKTHROUGH_INCREMENT_GROWTH
            + _REALM_BREAKTHROUGH_INCREMENT_BONUS
        )
    return round(base, 2)


def _progressive_stage_step(order: int) -> float:
    """Return the phase-to-phase multiplier delta for a realm index."""

    step = _STAGE_PHASE_MULTIPLIER_STEP_START
    if order <= 0:
        return round(step, 4)

    for _ in range(order):
        step = step * _STAGE_PHASE_MULTIPLIER_STEP_GROWTH + _STAGE_PHASE_MULTIPLIER_STEP_BONUS
    return round(step, 4)


class GameState:
    """In-memory cache for frequently accessed entities."""

    def __init__(self) -> None:
        self.qi_cultivation_stages: Dict[str, CultivationStage] = {}
        self.body_cultivation_stages: Dict[str, CultivationStage] = {}
        self.soul_cultivation_stages: Dict[str, CultivationStage] = {}
        self._stage_storage_keys: Dict[tuple[str, str], str] = {}
        self._storage_key_index: Dict[str, tuple[CultivationPath, str]] = {}
        self.races: Dict[str, Race] = {}
        self.traits: Dict[str, SpecialTrait] = {}
        self.skills: Dict[str, Skill] = {}
        self.cultivation_techniques: Dict[str, CultivationTechnique] = {}
        self.items: Dict[str, Item] = {}
        self.quests: Dict[str, Quest] = {}
        self.enemies: Dict[str, Enemy] = {}
        self.bosses: Dict[str, Boss] = {}
        self.locations: Dict[str, Location] = {}
        self.location_channels: Dict[int, str] = {}
        self.npcs: Dict[str, LocationNPC] = {}
        self.currencies: Dict[str, Currency] = {}
        self.shop_items: Dict[str, ShopItem] = {}
        self.titles: Dict[str, Title] = {}
        self.bonds: Dict[str, BondProfile] = {}
        self.parties: Dict[str, Party] = {}
        self.combats: Dict[str, CombatEncounter] = {}
        self.combat_channels: Dict[str, int] = {}
        self.signature_skills_seeded: bool = False
        self.innate_soul_exp_multiplier_ranges: Dict[int, Tuple[float, float]] = {}
        self.innate_soul_exp_ranges_loaded: bool = False
        self.reset_innate_soul_exp_range()
        self.innate_soul_exp_ranges_loaded = False
        self.world_map: WorldMap | None = None
        self.region_tracks: Dict[str, RegionEscalationTrack] = {}
        self.travel_event_scheduler: TravelEventScheduler = TravelEventScheduler()
        self.tile_heat_tracker: TileHeatTracker = TileHeatTracker()
        self.players: Dict[int, PlayerProgress] = {}
        self.player_fog: Dict[int, FogOfWarState] = {}
        self.tile_locations: Dict[str, str] = {}
        self.player_positions: Dict[str, set[int]] = defaultdict(set)
        self._travel_engine: "TravelEngine" | None = None
        self.last_time_phase: Optional[str] = None
        self.region_weather_cache: Dict[str, WeatherState] = {}

    def register_location(
        self, location: Location, *, storage_key: Optional[str] = None
    ) -> None:
        """Insert or update a location while keeping channel lookups in sync."""

        if storage_key:
            location.apply_storage_key(storage_key)

        channel_id: Optional[int] = None
        if location.channel_id is not None:
            try:
                channel_id = int(location.channel_id)
            except (TypeError, ValueError):
                channel_id = None
            else:
                location.channel_id = channel_id
                location.location_id = str(channel_id)

        if not location.location_id:
            location.location_id = location._slugify_name(location.name)

        key = location.location_id
        if not key:
            return

        previous = self.locations.get(key)
        if previous and previous.channel_id is not None:
            self.location_channels.pop(int(previous.channel_id), None)
        if previous and previous.map_coordinate:
            self.tile_locations.pop(previous.map_coordinate, None)

        if channel_id is not None:
            existing_key = self.location_channels.get(channel_id)
            if existing_key and existing_key != key:
                displaced = self.locations.pop(existing_key, None)
                if displaced and displaced.map_coordinate:
                    self.tile_locations.pop(displaced.map_coordinate, None)

        # Clear any stale channel mappings pointing to this key.
        for channel, mapped in list(self.location_channels.items()):
            if mapped == key and channel != channel_id:
                self.location_channels.pop(channel, None)

        self.locations[key] = location
        if channel_id is not None:
            self.location_channels[channel_id] = key

        if location.map_coordinate:
            normalized = location.map_coordinate
            self.tile_locations[normalized] = key
            self.ensure_world_map()
            self.ensure_tile_for_location(location)
            for player in self.players.values():
                if player.world_position == normalized:
                    player.location = key

    def register_player(self, player: PlayerProgress) -> None:
        if not player.world_position:
            candidate: Optional[str] = None
            if player.location and ":" in str(player.location):
                candidate = str(player.location)
            elif player.location and player.location in self.locations:
                location = self.locations[player.location]
                candidate = location.map_coordinate
            if candidate is None:
                candidate = "0:0:0"
            player.world_position = candidate
        try:
            coordinate = TileCoordinate.from_key(str(player.world_position))
        except ValueError:
            coordinate = TileCoordinate(0, 0, 0)
            player.world_position = coordinate.to_key()
        player.mark_tile_explored(coordinate, surveyed=True)
        mapped_location = self.tile_locations.get(player.world_position)
        if mapped_location:
            player.location = mapped_location
        self.ensure_player_energy(player)
        self.players[player.user_id] = player
        self.player_fog.setdefault(player.user_id, player.fog_of_war)
        self.update_player_position(player.user_id, None, player.world_position)
        self.ensure_martial_soul_signature_skills()
        for soul in player.martial_souls:
            self.register_martial_soul_skills(soul)

    def forget_player(self, user_id: int) -> None:
        """Remove a player from the active game state caches."""

        player = self.players.pop(user_id, None)
        previous_position: str | None = None
        if player is not None:
            previous_position = player.world_position
            player.visible_players.clear()

        if previous_position:
            self.update_player_position(user_id, previous_position, None)
        else:
            for coordinate, occupants in list(self.player_positions.items()):
                if user_id in occupants:
                    occupants.discard(user_id)
                    if not occupants:
                        self.player_positions.pop(coordinate, None)

        self.player_fog.pop(user_id, None)

        for party_id, party in list(self.parties.items()):
            if user_id not in party.member_ids and party.leader_id != user_id:
                continue
            party.member_ids = [pid for pid in party.member_ids if pid != user_id]
            if party.leader_id == user_id:
                if party.member_ids:
                    party.leader_id = party.member_ids[0]
                else:
                    self.parties.pop(party_id, None)
                    continue
            self.parties[party_id] = party

        for other in self.players.values():
            other.visible_players.pop(user_id, None)

        scheduler = self.travel_event_scheduler
        if scheduler.pending:
            scheduler.pending = [
                task
                for task in scheduler.pending
                if not _event_mentions_player(task.event, user_id)
            ]

    def update_player_position(
        self, user_id: int, previous: str | None, current: str | None
    ) -> None:
        """Keep spatial indices synchronised when a player moves."""

        if previous:
            occupants = self.player_positions.get(previous)
            if occupants and user_id in occupants:
                occupants.discard(user_id)
                if not occupants:
                    self.player_positions.pop(previous, None)
        if current:
            self.player_positions.setdefault(current, set()).add(user_id)

    def players_within(
        self,
        coordinate: TileCoordinate,
        *,
        radius: int,
        exclude: Optional[int] = None,
    ) -> List[PlayerProgress]:
        """Return all players within ``radius`` (Manhattan) of ``coordinate``."""

        results: List[PlayerProgress] = []
        for user_id, other in self.players.items():
            if exclude is not None and user_id == exclude:
                continue
            other_coord = other.tile_coordinate()
            if other_coord is None:
                continue
            if manhattan_distance(other_coord, coordinate) <= radius:
                results.append(other)
        return results

    def energy_capacity_for_player(self, player: PlayerProgress) -> float:
        """Determine the current maximum energy for ``player``."""

        if not self.qi_cultivation_stages:
            self.ensure_default_stages()

        try:
            active_path = CultivationPath.from_value(player.active_path)
        except ValueError:
            active_path = CultivationPath.QI

        stage_key = player.stage_key_for_path(active_path)
        stage = self.get_stage(stage_key, active_path)
        if stage is None:
            stage = self.get_stage(player.cultivation_stage, CultivationPath.QI)

        realm = getattr(stage, "realm", None) if stage else None
        realm_order = getattr(stage, "realm_order", None) if stage else None
        step = getattr(stage, "step", None) if stage else None
        return energy_cap_for_realm(realm, realm_order=realm_order, step=step)

    def ensure_player_energy(
        self,
        player: PlayerProgress,
        *,
        now: float | None = None,
        force_refill: bool = False,
    ) -> float:
        """Synchronise ``player``'s stored energy with their realm cap."""

        capacity = self.energy_capacity_for_player(player)
        player.sync_energy(capacity, now=now, force_refill=force_refill)
        return capacity

    def fog_of_war_for(self, user_id: int) -> FogOfWarState:
        return self.player_fog.setdefault(user_id, FogOfWarState())

    def ensure_world_map(self) -> None:
        if self.world_map is not None:
            return
        world_map = WorldMap()
        world_map.seasons = [
            SeasonProfile(
                key="spring",
                name="Blooming Spring",
                duration_days=20,
                weather_weights={"clear": 3.0, "rain": 2.0, "fog": 1.0},
                base_temperature=18.0,
                base_wind_speed=3.0,
            ),
            SeasonProfile(
                key="summer",
                name="Radiant Summer",
                duration_days=20,
                weather_weights={"clear": 4.0, "storm": 1.0, "humid": 1.5},
                base_temperature=26.0,
                base_wind_speed=2.0,
            ),
            SeasonProfile(
                key="autumn",
                name="Harvest Autumn",
                duration_days=20,
                weather_weights={"clear": 2.0, "wind": 2.0, "fog": 1.5},
                base_temperature=16.0,
                base_wind_speed=4.0,
            ),
            SeasonProfile(
                key="winter",
                name="Crystal Winter",
                duration_days=20,
                weather_weights={"snow": 2.5, "clear": 1.0, "blizzard": 0.5},
                base_temperature=2.0,
                base_wind_speed=6.0,
            ),
        ]
        self.last_time_phase = world_map.clock.time_of_day()

        region_specs: Dict[str, Dict[str, object]] = {
            "verdant-frontier": {
                "name": "Verdant Frontier",
                "description": "Rolling heartlands that stitch Verdant Moon Village to the surrounding wilds.",
                "zones": {
                    "verdant-heart": {
                        "name": "Verdant Heartlands",
                        "description": "Terraced fields and moonlit wards protecting the village outskirts.",
                        "threat": 0.6,
                        "recommended": 1,
                        "tiles": [
                            {
                                "coordinate": TileCoordinate(0, 0, 0),
                                "terrain": TerrainType.URBAN,
                                "elevation": 12.0,
                                "difficulty": 0.8,
                                "qi": 1.1,
                                "hazard": 0.0,
                            },
                            {
                                "coordinate": TileCoordinate(0, -1, 0),
                                "terrain": TerrainType.PLAINS,
                                "elevation": 10.0,
                                "difficulty": 0.9,
                                "qi": 1.0,
                                "hazard": 0.1,
                            },
                            {
                                "coordinate": TileCoordinate(-1, -1, 0),
                                "terrain": TerrainType.PLAINS,
                                "elevation": 9.0,
                                "difficulty": 1.0,
                                "qi": 1.0,
                                "hazard": 0.12,
                            },
                        ],
                    },
                    "whispering-woods": {
                        "name": "Whispering Woods",
                        "description": "Ancient pines murmuring with roaming bandits and spirit beasts.",
                        "threat": 1.2,
                        "recommended": 2,
                        "tiles": [
                            {
                                "coordinate": TileCoordinate(0, 1, 0),
                                "terrain": TerrainType.FOREST,
                                "elevation": 24.0,
                                "difficulty": 1.1,
                                "qi": 1.2,
                                "hazard": 0.35,
                            }
                        ],
                    },
                    "obsidian-grotto": {
                        "name": "Obsidian Serpent Grotto",
                        "description": "Slick caverns dripping with corrosive qi and hidden caches.",
                        "threat": 1.4,
                        "recommended": 3,
                        "tiles": [
                            {
                                "coordinate": TileCoordinate(-1, 1, 0),
                                "terrain": TerrainType.SUBTERRANEAN,
                                "elevation": -6.0,
                                "difficulty": 1.2,
                                "qi": 1.25,
                                "hazard": 0.42,
                            }
                        ],
                    },
                    "starfall-hollows": {
                        "name": "Starfall Hollows",
                        "description": "A crystalline basin collecting the motes of nightly meteor showers.",
                        "threat": 1.3,
                        "recommended": 3,
                        "tiles": [
                            {
                                "coordinate": TileCoordinate(-1, 0, 0),
                                "terrain": TerrainType.COAST,
                                "elevation": 2.0,
                                "difficulty": 1.0,
                                "qi": 1.3,
                                "hazard": 0.33,
                            }
                        ],
                    },
                    "sky-terraces": {
                        "name": "Azure Sky Terraces",
                        "description": "Floating pavilions that channel high-altitude winds into insight.",
                        "threat": 0.4,
                        "recommended": 2,
                        "tiles": [
                            {
                                "coordinate": TileCoordinate(0, 2, 0),
                                "terrain": TerrainType.SKY,
                                "elevation": 60.0,
                                "difficulty": 1.0,
                                "qi": 1.35,
                                "hazard": 0.18,
                            }
                        ],
                    },
                },
            },
            "ember-highlands": {
                "name": "Ember Highlands",
                "description": "Molten ridges and thunder-wreathed plateaus contested by rival sects.",
                "zones": {
                    "ember-ridge": {
                        "name": "Emberflame Ridge",
                        "description": "Jagged ledges crowned with burning lotus blooms.",
                        "threat": 1.6,
                        "recommended": 4,
                        "tiles": [
                            {
                                "coordinate": TileCoordinate(1, 0, 0),
                                "terrain": TerrainType.PLAINS,
                                "elevation": 48.0,
                                "difficulty": 1.25,
                                "qi": 1.4,
                                "hazard": 0.58,
                            },
                            {
                                "coordinate": TileCoordinate(1, -1, 0),
                                "terrain": TerrainType.PLAINS,
                                "elevation": 42.0,
                                "difficulty": 1.2,
                                "qi": 1.2,
                                "hazard": 0.46,
                            },
                        ],
                    },
                    "crimson-bastion": {
                        "name": "Crimson Sun Bastion",
                        "description": "Fortified terraces where commanders coordinate frontier defences.",
                        "threat": 0.9,
                        "recommended": 3,
                        "tiles": [
                            {
                                "coordinate": TileCoordinate(1, 1, 0),
                                "terrain": TerrainType.URBAN,
                                "elevation": 40.0,
                                "difficulty": 1.1,
                                "qi": 1.15,
                                "hazard": 0.2,
                            }
                        ],
                    },
                    "thunder-plateau": {
                        "name": "Thunderpeak Plateau",
                        "description": "An elevated mesa where storms constantly reforge the land.",
                        "threat": 1.7,
                        "recommended": 5,
                        "tiles": [
                            {
                                "coordinate": TileCoordinate(2, 1, 0),
                                "terrain": TerrainType.PLAINS,
                                "elevation": 65.0,
                                "difficulty": 1.35,
                                "qi": 1.45,
                                "hazard": 0.6,
                            },
                            {
                                "coordinate": TileCoordinate(2, 0, 0),
                                "terrain": TerrainType.PLAINS,
                                "elevation": 58.0,
                                "difficulty": 1.3,
                                "qi": 1.3,
                                "hazard": 0.5,
                            },
                        ],
                    },
                },
            },
        }

        for region_id, region_info in region_specs.items():
            region = Region(
                region_id=region_id,
                name=str(region_info["name"]),
                description=str(region_info["description"]),
            )
            for zone_id, zone_info in region_info.get("zones", {}).items():
                zone = Zone(
                    zone_id=zone_id,
                    name=str(zone_info["name"]),
                    description=str(zone_info["description"]),
                    threat_rating=float(zone_info.get("threat", 1.0)),
                    recommended_level=int(zone_info.get("recommended", 1)),
                )
                for tile_spec in zone_info.get("tiles", []):
                    coordinate = tile_spec["coordinate"]
                    tile = Tile(
                        coordinate=coordinate,
                        terrain=tile_spec["terrain"],
                        elevation=float(tile_spec.get("elevation", 0.0)),
                        traversal_difficulty=float(tile_spec.get("difficulty", 1.0)),
                        environmental=EnvironmentalModifiers(
                            weather=str(tile_spec.get("weather", "clear")),
                            qi_density=float(tile_spec.get("qi", 1.0)),
                            hazard_level=float(tile_spec.get("hazard", 0.0)),
                        ),
                    )
                    zone.add_tile(tile)
                region.add_zone(zone)
            world_map.add_region(region)

        region_connections: Dict[str, List[tuple[str, float, float]]] = {
            "verdant-frontier": [("ember-highlands", 4.0, 0.5)],
            "ember-highlands": [("verdant-frontier", 4.0, 0.5)],
        }
        for source_region, connections in region_connections.items():
            region = world_map.get_region(source_region)
            if not region:
                continue
            for target_key, distance, hazard in connections:
                edge = RegionEdge(
                    region_key=target_key,
                    distance=distance,
                    hazard=hazard,
                    travel_modes={"move"},
                )
                region.connect(edge)

        waypoint_specs = [
            (
                "verdant-moon-village",
                TileCoordinate(0, 0, 0),
                "Verdant Moon Village",
                "A tranquil settlement whose moonlit wards soothe weary cultivators.",
                {"safe", "trade"},
            ),
            (
                "azure-sky-pavilion",
                TileCoordinate(0, 2, 0),
                "Azure Sky Pavilion",
                "Cloud-kissed terraces sharing insights with disciples aligned to the wind.",
                {"safe", "insight"},
            ),
            (
                "crimson-sun-outpost",
                TileCoordinate(1, 1, 0),
                "Crimson Sun Outpost",
                "A martial bastion trading provisions and defensive talismans.",
                {"safe", "trade"},
            ),
            (
                "starfall-basin",
                TileCoordinate(-1, 0, 0),
                "Starfall Basin",
                "Meteor-lit pools rich with condensed qi motes.",
                {"exploration", "luminance"},
            ),
            (
                "thunderpeak-plateau",
                TileCoordinate(2, 1, 0),
                "Thunderpeak Plateau",
                "Storm-wreathed plateaus tempering those who brave their lightning.",
                {"hazard", "challenge"},
            ),
        ]
        for key, coordinate, name, description, tags in waypoint_specs:
            world_map.register_waypoint(
                Waypoint(
                    key=key,
                    coordinate=coordinate,
                    name=name,
                    description=description,
                    tags=set(tags),
                )
            )

        ley_line_specs = [
            ("verdant-heart", TileCoordinate(0, 0, 0), 1.0),
            ("azure-zenith", TileCoordinate(0, 2, 0), 1.1),
            ("crimson-gate", TileCoordinate(1, 1, 0), 1.2),
            ("thunder-ward", TileCoordinate(2, 1, 0), 1.35),
            ("serpent-depths", TileCoordinate(-1, 1, 0), 1.25),
        ]
        for key, coordinate, attunement in ley_line_specs:
            world_map.register_ley_line(
                LeyLineNode(key=key, coordinate=coordinate, attunement_cost=attunement)
            )
        world_map.connect_ley_lines("verdant-heart", LeyLineLink(target_key="azure-zenith", difficulty=0.85))
        world_map.connect_ley_lines("verdant-heart", LeyLineLink(target_key="crimson-gate", difficulty=0.95))
        world_map.connect_ley_lines("crimson-gate", LeyLineLink(target_key="thunder-ward", difficulty=1.15))
        world_map.connect_ley_lines("verdant-heart", LeyLineLink(target_key="serpent-depths", difficulty=1.05))
        world_map.connect_ley_lines("serpent-depths", LeyLineLink(target_key="starfall-basin", difficulty=1.1))

        for region in world_map.regions.values():
            state = world_map.ensure_region_weather(region.region_id)
            self.region_weather_cache[region.region_id] = WeatherState(
                pattern=state.pattern,
                intensity=state.intensity,
                temperature=state.temperature,
                wind_speed=state.wind_speed,
                visibility_penalty=state.visibility_penalty,
                duration_seconds=state.duration_seconds,
                elapsed_seconds=state.elapsed_seconds,
            )

        self.world_map = world_map
        for location in self.locations.values():
            if location.map_coordinate:
                self.tile_locations[location.map_coordinate] = location.location_id
                self.ensure_tile_for_location(location)

    def region_for_coordinate(self, coordinate: TileCoordinate) -> Optional[Region]:
        if self.world_map is None:
            return None
        key = coordinate.to_key()
        for region in self.world_map.regions.values():
            for zone in region.zones.values():
                if key in zone.tiles:
                    return region
        return None

    def advance_world_time(
        self, seconds: float
    ) -> tuple[Optional[tuple[str, str]], Dict[str, WeatherState], Optional[SeasonProfile]]:
        if self.world_map is None:
            return None, {}, None
        phase_change = self.world_map.advance_time(seconds)
        if phase_change:
            self.last_time_phase = phase_change[1]
        weather_changes = self.world_map.advance_weather(seconds)
        for region_id, state in weather_changes.items():
            self.region_weather_cache[region_id] = WeatherState(
                pattern=state.pattern,
                intensity=state.intensity,
                temperature=state.temperature,
                wind_speed=state.wind_speed,
                visibility_penalty=state.visibility_penalty,
                duration_seconds=state.duration_seconds,
                elapsed_seconds=state.elapsed_seconds,
            )
        rotated = self.world_map.rotate_season_if_needed()
        if rotated:
            for region_id in self.world_map.regions:
                state = self.world_map.ensure_region_weather(region_id)
                weather_changes[region_id] = state
                self.region_weather_cache[region_id] = WeatherState(
                    pattern=state.pattern,
                    intensity=state.intensity,
                    temperature=state.temperature,
                    wind_speed=state.wind_speed,
                    visibility_penalty=state.visibility_penalty,
                    duration_seconds=state.duration_seconds,
                    elapsed_seconds=state.elapsed_seconds,
                )
        return phase_change, weather_changes, rotated

    def weather_for_tile(self, tile: Tile) -> WeatherState:
        if self.world_map is None:
            return WeatherState()
        region = self.region_for_coordinate(tile.coordinate)
        if region is None:
            return WeatherState()
        return self.world_map.ensure_region_weather(region.region_id)

    def world_time_summary(self) -> str:
        if self.world_map is None:
            return "Time unknown"
        return self.world_map.clock.formatted_time()

    def ensure_tile_for_location(self, location: Location) -> None:
        if self.world_map is None or not location.map_coordinate:
            return
        try:
            coordinate = TileCoordinate.from_key(location.map_coordinate)
        except ValueError:
            return
        tile = self.world_map.tile_for_key(location.map_coordinate)
        if tile is None:
            region = self.world_map.get_region("uncharted-frontier")
            if region is None:
                region = Region(
                    region_id="uncharted-frontier",
                    name="Uncharted Frontier",
                    description="Wilderlands slowly charted by daring cultivators.",
                )
                self.world_map.add_region(region)
            zone = region.zones.get("frontier-expanse")
            if zone is None:
                zone = Zone(
                    zone_id="frontier-expanse",
                    name="Frontier Expanse",
                    description="Rolling terrain awaiting detailed surveys.",
                    threat_rating=1.0,
                )
                region.add_zone(zone)
            tile = Tile(
                coordinate=coordinate,
                terrain=TerrainType.PLAINS,
                elevation=0.0,
                traversal_difficulty=1.0,
                environmental=EnvironmentalModifiers(),
            )
            zone.add_tile(tile)
        if location.name not in tile.points_of_interest:
            tile.points_of_interest.append(location.name)
        if tile.points_of_interest:
            tile.category = TileCategory.POINT_OF_INTEREST
        if location.is_safe:
            tile.is_safe = True

    def location_key_for_channel(self, channel_id: Optional[int]) -> Optional[str]:
        if channel_id is None:
            return None
        lookup = self.location_channels.get(int(channel_id))
        if lookup is not None:
            return lookup
        fallback = str(int(channel_id))
        if fallback in self.locations:
            self.location_channels[int(channel_id)] = fallback
            return fallback
        return None

    def location_for_tile(self, tile_key: str) -> Optional[Location]:
        location_id = self.tile_locations.get(tile_key)
        if location_id is None:
            return None
        return self.locations.get(location_id)

    @property
    def travel_engine(self) -> "TravelEngine":
        if self._travel_engine is None:
            from .travel import TravelEngine

            self._travel_engine = TravelEngine(self)
        return self._travel_engine

    def get_location_for_channel(
        self, channel_id: Optional[int]
    ) -> Optional[Location]:
        key = self.location_key_for_channel(channel_id)
        if key is None:
            return None
        return self.locations.get(key)

    def channel_anchor(self, channel_id: int) -> TileCoordinate:
        """Derive a deterministic anchor coordinate for a channel."""

        seed = f"channel:{int(channel_id)}"
        rng = random.Random(seed)
        x = rng.randint(-96, 96)
        y = rng.randint(-96, 96)
        return TileCoordinate(x, y, 0)

    def ensure_channel_location(
        self,
        channel_id: int,
        *,
        channel_name: str | None = None,
        create_if_missing: bool = False,
    ) -> Location:
        """Ensure cached metadata for a location tied to ``channel_id``.

        When ``create_if_missing`` is ``False`` (the default), a ``LookupError``
        is raised if no location has been configured for the channel.  Setting
        the flag to ``True`` will create a minimal :class:`Location` entry that
        anchors the channel on the world map.
        """

        existing = self.get_location_for_channel(channel_id)
        normalized_name = (channel_name or "").strip()
        anchor = self.channel_anchor(channel_id)
        anchor_key = anchor.to_key()

        if existing:
            if not existing.map_coordinate:
                existing.map_coordinate = anchor_key
            if normalized_name and existing.name in {
                "Unnamed Location",
                str(int(channel_id)),
                f"Channel {channel_id}",
            }:
                existing.name = normalized_name
            self.register_location(existing, storage_key=str(channel_id))
            return existing

        if not create_if_missing:
            raise LookupError(f"No location configured for channel {channel_id}")

        location = Location(
            name=normalized_name or f"Channel {channel_id}",
            description="",
            encounter_rate=0.0,
            is_safe=False,
            channel_id=channel_id,
            map_coordinate=anchor_key,
        )
        self.register_location(location, storage_key=str(channel_id))
        return location

    def ensure_default_stages(self) -> None:
        """Populate baseline cultivation stages when none are configured."""

        mortal_present = any(
            stage.is_mortal for stage in self.qi_cultivation_stages.values()
        )
        needs_qi_seed = not self.qi_cultivation_stages

        if not mortal_present:
            self._register_mortal_realm()

        if needs_qi_seed:
            default_qi_realms: list[tuple[str, str, int]] = [
                ("qi-condensation", "Qi Condensation", 1),
                ("foundation-establishment", "Foundation Establishment", 1),
                ("core-formation", "Core Formation", 1),
                ("nascent-soul", "Nascent Soul", 1),
                ("soul-formation", "Soul Formation", 1),
                ("soul-transformation", "Soul Transformation", 1),
                ("ascendant", "Ascendant", 1),
                ("illusory-yin", "Illusory Yin", 1),
                ("corporeal-yang", "Corporeal Yang", 1),
                ("nirvana-scryer", "Nirvana Scryer", 2),
                ("nirvana-cleanser", "Nirvana Cleanser", 2),
                ("nirvana-shatterer", "Nirvana Shatterer", 2),
                ("heavens-blight", "Heaven's Blight", 2),
                ("nirvana-void", "Nirvana Void", 3),
                ("spirit-void", "Spirit Void", 3),
                ("arcane-void", "Arcane Void", 3),
                ("void-tribulant", "Void Tribulant", 3),
                ("half-heaven-trampling", "Half-Heaven Trampling", 3),
                ("heaven-trampling", "Heaven Trampling", 3),
            ]
            for order, (realm_key, realm_name, step) in enumerate(default_qi_realms):
                base_ratio = _progressive_realm_base(order)
                stage_step = round(
                    DEFAULT_STAGE_BASE_STAT * _progressive_stage_step(order), 2
                )
                self._register_default_realm(
                    path=CultivationPath.QI,
                    realm_key=realm_key,
                    realm_name=realm_name,
                    base_stat=round(
                        DEFAULT_STAGE_BASE_STAT * base_ratio, 2
                    ),
                    base_stat_step=stage_step,
                    realm_order=order,
                    step=step,
                    lifespan=DEFAULT_REALM_LIFESPANS.get(realm_key, ""),
                )
        if not self.body_cultivation_stages:
            self._register_default_realm(
                path=CultivationPath.BODY,
                realm_key="body-tempering",
                realm_name="Body Tempering",
                base_stat=round(
                    DEFAULT_STAGE_BASE_STAT * _progressive_realm_base(0), 2
                ),
                base_stat_step=round(
                    DEFAULT_STAGE_BASE_STAT * _progressive_stage_step(0), 2
                ),
                realm_order=0,
            )
        if not self.soul_cultivation_stages:
            self._register_default_realm(
                path=CultivationPath.SOUL,
                realm_key="soul-awakening",
                realm_name="Soul Awakening",
                base_stat=round(
                    DEFAULT_STAGE_BASE_STAT * _progressive_realm_base(0), 2
                ),
                base_stat_step=round(
                    DEFAULT_STAGE_BASE_STAT * _progressive_stage_step(0), 2
                ),
                realm_order=0,
            )

    def _register_mortal_realm(self) -> None:
        self.register_stage(
            CultivationStage(
                key=MORTAL_REALM_KEY,
                name=MORTAL_REALM_NAME,
                success_rate=1.0,
                path=CultivationPath.QI.value,
                base_stat=1.0,
                realm=MORTAL_REALM_NAME,
                phase=CultivationPhase.INITIAL.value,
                realm_order=-1,
                step=0,
                exp_required=100,
                lifespan=DEFAULT_REALM_LIFESPANS.get(MORTAL_REALM_KEY, ""),
            )
        )

    def _register_default_realm(
        self,
        *,
        path: CultivationPath,
        realm_key: str,
        realm_name: str,
        base_stat: float,
        base_stat_step: float,
        realm_order: int = 0,
        step: int = 1,
        lifespan: str | None = None,
    ) -> None:
        """Register a complete set of phases for a baseline cultivation realm."""

        for phase in CultivationPhase:
            phase_index = phase.order_index
            stage_key = (
                realm_key
                if phase is CultivationPhase.INITIAL
                else f"{realm_key}-{phase.value}"
            )
            stage_name = (
                realm_name
                if phase is CultivationPhase.INITIAL
                else f"{realm_name} ({phase.display_name})"
            )
            multiplier = round(base_stat + base_stat_step * phase_index, 2)
            self.register_stage(
                CultivationStage(
                    key=stage_key,
                    name=stage_name,
                    success_rate=1.0,
                    path=path.value,
                    base_stat=multiplier,
                    realm=realm_name,
                    phase=phase.value,
                    realm_order=realm_order,
                    step=step,
                    lifespan=lifespan or "",
                )
            )

    def ensure_default_race(self) -> None:
        """Insert a generic lineage when no races exist."""

        if self.races:
            return
        default_race = Race(
            key="human",
            name="Human",
            description="A versatile mortal lineage with balanced potential.",
        )
        self.races[default_race.key] = default_race

    def ensure_default_currency(self) -> None:
        """Guarantee baseline spiritual currencies exist."""

        defaults = {
            "spirit-stone": Currency(
                key="spirit-stone",
                name="Spirit Stone",
                description=(
                    "A lustrous gem condensed from the qi of heaven and earth,"
                    " prized by cultivators as the standard medium of exchange."
                ),
            ),
            "tael": Currency(
                key="tael",
                name="Tael",
                description=(
                    "A refined ingot of spirit-tempered silver, trusted for sect"
                    " stipends and mundane trade throughout cultivation cities."
                ),
            ),
        }

        for key, currency in defaults.items():
            self.currencies.setdefault(key, currency)

    @staticmethod
    def _default_equipment_items() -> list[Item]:
        """Return the baseline xianxia-flavoured equipment set."""

        return [
            Item(
                key="jade-spirit-sword",
                name="Jade Spirit Sword",
                description="A spirit-forged blade that sings with verdant qi.",
                item_type="equipment",
                grade="tier 1",
                equipment_slot=EquipmentSlot.WEAPON,
                weapon_type=WeaponType.SWORD,
                strength=12.0,
                agility=3.0,
            ),
            Item(
                key="cloudsilk-vestments",
                name="Cloudsilk Vestments",
                description="Robes woven from celestial silkworm threads that steady the body.",
                item_type="equipment",
                grade="tier 1",
                equipment_slot=EquipmentSlot.ARMOR,
                physique=11.0,
            ),
            Item(
                key="windstep-boots",
                name="Windstep Boots",
                description="Boots etched with gale runes, lightening every stride.",
                item_type="equipment",
                grade="tier 1",
                equipment_slot=EquipmentSlot.BOOTS,
                agility=10.0,
            ),
            Item(
                key="crimson-lotus-spear",
                name="Crimson Lotus Spear",
                description="A spear wreathed in lotus flames that pierce protective wards.",
                item_type="equipment",
                grade="tier 2",
                equipment_slot=EquipmentSlot.WEAPON,
                weapon_type=WeaponType.SPEAR,
                strength=20.0,
                agility=6.0,
                slots_required=2,
            ),
            Item(
                key="moonshadow-fan",
                name="Moonshadow Fan",
                description="A moon-silk fan that bends twilight winds into slicing arcs.",
                item_type="equipment",
                grade="tier 2",
                equipment_slot=EquipmentSlot.WEAPON,
                weapon_type=WeaponType.FAN,
                strength=8.0,
                agility=18.0,
            ),
            Item(
                key="serpentscale-belt",
                name="Serpentscale Belt",
                description="A belt interwoven with serpentscale talismans that steady the core.",
                item_type="equipment",
                grade="tier 2",
                equipment_slot=EquipmentSlot.BELT,
                physique=15.0,
                inventory_space_bonus=8,
            ),
            Item(
                key="starlit-pendant",
                name="Starlit Pendant",
                description="A pendant that catches astral motes, sharpening perception and poise.",
                item_type="equipment",
                grade="tier 3",
                equipment_slot=EquipmentSlot.NECKLACE,
                strength=5.0,
                agility=9.0,
            ),
            Item(
                key="thunderstep-greaves",
                name="Thunderstep Greaves",
                description="Greaves that crackle with thunder qi, pushing each stride into a charge.",
                item_type="equipment",
                grade="tier 3",
                equipment_slot=EquipmentSlot.BOOTS,
                strength=4.0,
                agility=18.0,
            ),
            Item(
                key="auric-sunblade",
                name="Auric Sunblade",
                description="A radiant longsword tempered within sunfire kilns.",
                item_type="equipment",
                grade="tier 3",
                equipment_slot=EquipmentSlot.WEAPON,
                weapon_type=WeaponType.SWORD,
                strength=24.0,
                agility=10.0,
                evolves_to="celestial-dragon-sabre",
                evolution_requirements={"spirit-stone": "900"},
            ),
            Item(
                key="celestial-dragon-sabre",
                name="Celestial Dragon Sabre",
                description="An ascendant sabre that roars with draconic qi when drawn.",
                item_type="equipment",
                grade="tier 4",
                equipment_slot=EquipmentSlot.WEAPON,
                weapon_type=WeaponType.SWORD,
                strength=32.0,
                agility=12.0,
                slots_required=2,
            ),
            Item(
                key="aurora-ward-robes",
                name="Aurora Ward Robes",
                description="Robes layered with prismatic wards that disperse lethal strikes.",
                item_type="equipment",
                grade="tier 3",
                equipment_slot=EquipmentSlot.ARMOR,
                physique=18.0,
                agility=5.0,
            ),
            Item(
                key="dragonbone-bracelet",
                name="Dragonbone Bracelet",
                description="A bracelet carved from slumbering dragonbone, pulsing with latent might.",
                item_type="equipment",
                grade="tier 3",
                equipment_slot=EquipmentSlot.ACCESSORY,
                strength=8.0,
                agility=8.0,
            ),
        ]

    @staticmethod
    def _build_loot_drops(
        equipment_keys: Sequence[str], chances: Sequence[float]
    ) -> Dict[str, LootDrop]:
        loot: Dict[str, LootDrop] = {}
        for key, chance in zip(equipment_keys, chances):
            loot[key] = LootDrop(chance=chance, amount=1)
        return loot

    def ensure_default_equipment(self) -> None:
        """Guarantee that themed starter equipment is available."""

        defaults = self._default_equipment_items()
        has_equipment = any(
            isinstance(item, Item)
            and isinstance(getattr(item, "item_type", ""), str)
            and item.item_type.lower() == "equipment"
            for item in self.items.values()
        )
        if not has_equipment:
            for item in defaults:
                self.items[item.key] = item
            return
        for item in defaults:
            self.items.setdefault(item.key, item)

    def ensure_default_shop_items(self) -> None:
        """Create a baseline market stocked with starter equipment."""

        self.ensure_default_equipment()
        self.ensure_default_currency()

        catalogue = [
            ("village-market-jade-spirit-sword", "jade-spirit-sword", 180),
            ("village-market-cloudsilk-vestments", "cloudsilk-vestments", 150),
            ("village-market-windstep-boots", "windstep-boots", 140),
            ("azure-pavilion-moonshadow-fan", "moonshadow-fan", 420),
            ("azure-pavilion-starlit-pendant", "starlit-pendant", 520),
            ("azure-pavilion-cloudsilk-vestments", "cloudsilk-vestments", 150),
            ("crimson-outpost-crimson-lotus-spear", "crimson-lotus-spear", 480),
            ("crimson-outpost-auric-sunblade", "auric-sunblade", 720),
            ("crimson-outpost-serpentscale-belt", "serpentscale-belt", 360),
            ("serpent-bazaar-aurora-ward-robes", "aurora-ward-robes", 540),
            ("serpent-bazaar-dragonbone-bracelet", "dragonbone-bracelet", 460),
            ("starfall-emporium-thunderstep-greaves", "thunderstep-greaves", 560),
            (
                "starfall-emporium-celestial-dragon-sabre",
                "celestial-dragon-sabre",
                980,
            ),
            ("thunderpeak-forge-auric-sunblade", "auric-sunblade", 740),
            ("thunderpeak-forge-thunderstep-greaves", "thunderstep-greaves", 540),
        ]
        for key, item_key, price in catalogue:
            self.shop_items.setdefault(
                key,
                ShopItem(
                    item_key=item_key,
                    currency_key="spirit-stone",
                    price=price,
                ),
            )

    def ensure_default_enemies(self) -> None:
        """Populate a suite of baseline enemies with themed loot tables."""

        self.ensure_default_equipment()
        self.ensure_default_currency()

        default_equipment_keys = [item.key for item in self._default_equipment_items()]
        equipment_loot = self._build_loot_drops(default_equipment_keys, (0.2, 0.15, 0.1))

        templates: dict[str, Enemy] = {
            "mist-veiled-disciple": Enemy(
                key="mist-veiled-disciple",
                name="Mist-Veiled Disciple",
                active_path=CultivationPath.QI.value,
                cultivation_stage="qi-condensation",
                strength=16.0,
                physique=14.0,
                agility=15.0,
                affinity=SpiritualAffinity.WIND,
                loot_table=dict(equipment_loot),
            ),
            "jade-forest-bandit": Enemy(
                key="jade-forest-bandit",
                name="Jade Forest Bandit",
                active_path=CultivationPath.QI.value,
                cultivation_stage="qi-condensation",
                strength=18.0,
                physique=15.0,
                agility=14.0,
                affinity=SpiritualAffinity.EARTH,
                loot_table={
                    "spirit-stone": LootDrop(chance=0.7, amount=25, kind="currency"),
                    "cloudsilk-vestments": LootDrop(chance=0.06, amount=1),
                },
            ),
            "ashen-spirit-wolf": Enemy(
                key="ashen-spirit-wolf",
                name="Ashen Spirit Wolf",
                active_path=CultivationPath.BODY.value,
                cultivation_stage="body-tempering",
                strength=14.0,
                physique=12.0,
                agility=18.0,
                affinity=SpiritualAffinity.WIND,
                loot_table={
                    "spirit-stone": LootDrop(chance=0.55, amount=18, kind="currency"),
                    "windstep-boots": LootDrop(chance=0.08, amount=1),
                },
            ),
            "bloodshade-cultivator": Enemy(
                key="bloodshade-cultivator",
                name="Bloodshade Cultivator",
                active_path=CultivationPath.QI.value,
                cultivation_stage="qi-condensation",
                strength=17.0,
                physique=16.0,
                agility=16.0,
                affinity=SpiritualAffinity.FIRE,
                loot_table={
                    "spirit-stone": LootDrop(chance=0.6, amount=30, kind="currency"),
                    "jade-spirit-sword": LootDrop(chance=0.05, amount=1),
                },
            ),
            "crimson-lotus-disciple": Enemy(
                key="crimson-lotus-disciple",
                name="Crimson Lotus Disciple",
                active_path=CultivationPath.QI.value,
                cultivation_stage="foundation-establishment",
                strength=24.0,
                physique=19.0,
                agility=18.0,
                affinity=SpiritualAffinity.FIRE,
                loot_table={
                    "spirit-stone": LootDrop(chance=0.75, amount=45, kind="currency"),
                    "crimson-lotus-spear": LootDrop(chance=0.08, amount=1),
                    "auric-sunblade": LootDrop(chance=0.04, amount=1),
                },
            ),
            "blazewing-phoenix": Enemy(
                key="blazewing-phoenix",
                name="Blazewing Phoenix",
                active_path=CultivationPath.SOUL.value,
                cultivation_stage="core-formation",
                strength=26.0,
                physique=21.0,
                agility=24.0,
                affinity=SpiritualAffinity.LAVA,
                loot_table={
                    "spirit-stone": LootDrop(chance=0.62, amount=60, kind="currency"),
                    "aurora-ward-robes": LootDrop(chance=0.06, amount=1),
                    "auric-sunblade": LootDrop(chance=0.03, amount=1),
                },
                elemental_resistances=[SpiritualAffinity.FIRE],
            ),
            "serpentscale-adept": Enemy(
                key="serpentscale-adept",
                name="Serpentscale Adept",
                active_path=CultivationPath.BODY.value,
                cultivation_stage="foundation-establishment",
                strength=20.0,
                physique=22.0,
                agility=17.0,
                affinity=SpiritualAffinity.POISON,
                loot_table={
                    "spirit-stone": LootDrop(chance=0.7, amount=40, kind="currency"),
                    "serpentscale-belt": LootDrop(chance=0.09, amount=1),
                },
                elemental_resistances=[SpiritualAffinity.POISON],
            ),
            "obsidian-naga": Enemy(
                key="obsidian-naga",
                name="Obsidian Naga",
                active_path=CultivationPath.SOUL.value,
                cultivation_stage="nascent-soul",
                strength=28.0,
                physique=24.0,
                agility=20.0,
                affinity=SpiritualAffinity.DARKNESS,
                loot_table={
                    "spirit-stone": LootDrop(chance=0.65, amount=65, kind="currency"),
                    "dragonbone-bracelet": LootDrop(chance=0.07, amount=1),
                },
                elemental_resistances=[SpiritualAffinity.DARKNESS, SpiritualAffinity.WATER],
            ),
            "moonlit-assassin": Enemy(
                key="moonlit-assassin",
                name="Moonlit Assassin",
                active_path=CultivationPath.QI.value,
                cultivation_stage="foundation-establishment",
                strength=21.0,
                physique=17.0,
                agility=24.0,
                affinity=SpiritualAffinity.TWILIGHT,
                loot_table={
                    "spirit-stone": LootDrop(chance=0.68, amount=42, kind="currency"),
                    "moonshadow-fan": LootDrop(chance=0.09, amount=1),
                },
            ),
            "starlit-spirit": Enemy(
                key="starlit-spirit",
                name="Starlit Spirit",
                active_path=CultivationPath.SOUL.value,
                cultivation_stage="nascent-soul",
                strength=23.0,
                physique=20.0,
                agility=27.0,
                affinity=SpiritualAffinity.LIGHT,
                loot_table={
                    "spirit-stone": LootDrop(chance=0.66, amount=70, kind="currency"),
                    "starlit-pendant": LootDrop(chance=0.1, amount=1),
                    "celestial-dragon-sabre": LootDrop(chance=0.03, amount=1),
                },
            ),
            "thunderpeak-guardian": Enemy(
                key="thunderpeak-guardian",
                name="Thunderpeak Guardian",
                active_path=CultivationPath.BODY.value,
                cultivation_stage="core-formation",
                strength=27.0,
                physique=26.0,
                agility=23.0,
                affinity=SpiritualAffinity.LIGHTNING,
                loot_table={
                    "spirit-stone": LootDrop(chance=0.7, amount=66, kind="currency"),
                    "thunderstep-greaves": LootDrop(chance=0.1, amount=1),
                    "aurora-ward-robes": LootDrop(chance=0.05, amount=1),
                },
                elemental_resistances=[SpiritualAffinity.LIGHTNING],
            ),
        }

        for key, template in templates.items():
            existing = self.enemies.get(key)
            if existing is None:
                self.enemies[key] = template
                continue
            if not existing.loot_table and template.loot_table:
                existing.loot_table = dict(template.loot_table)
            if not existing.cultivation_stage:
                existing.cultivation_stage = template.cultivation_stage
            if existing.affinity is None and template.affinity is not None:
                existing.affinity = template.affinity
            if not existing.active_path:
                existing.active_path = template.active_path
            self.enemies[key] = existing

    def ensure_default_quests(self) -> None:
        """Ensure the world offers introductory quests tied to default foes."""

        self.ensure_default_enemies()
        self.ensure_default_shop_items()

        quest_templates = [
            Quest(
                key="bandit-menace",
                name="Bandit Menace",
                description="Drive off the brigands preying on Verdant Moon Village.",
                objective=QuestObjective(
                    target_type="enemy",
                    target_key="jade-forest-bandit",
                    kill_count=5,
                ),
                rewards={
                    "spirit-stone": 120,
                    "cloudsilk-vestments": 1,
                },
            ),
            Quest(
                key="wolf-pelt-contract",
                name="Whispering Howls",
                description="Collect pelts from the Ashen Spirit Wolves prowling the forest.",
                objective=QuestObjective(
                    target_type="enemy",
                    target_key="ashen-spirit-wolf",
                    kill_count=4,
                ),
                rewards={
                    "spirit-stone": 90,
                    "windstep-boots": 1,
                },
            ),
            Quest(
                key="emberflame-suppression",
                name="Emberflame Suppression",
                description="Break the Ember Sect's forward scouts stationed along Emberflame Ridge.",
                objective=QuestObjective(
                    target_type="enemy",
                    target_key="crimson-lotus-disciple",
                    kill_count=3,
                ),
                rewards={
                    "spirit-stone": 160,
                    "auric-sunblade": 1,
                },
            ),
            Quest(
                key="serpent-sanctum",
                name="Sanctify the Serpent Grotto",
                description="Cleanse the Obsidian Serpent Grotto of poisonous adepts.",
                objective=QuestObjective(
                    target_type="enemy",
                    target_key="serpentscale-adept",
                    kill_count=4,
                ),
                rewards={
                    "spirit-stone": 170,
                    "serpentscale-belt": 1,
                },
            ),
            Quest(
                key="shadow-veil-hunt",
                name="Shadow Veil Hunt",
                description="Unmask and defeat the assassins lurking beneath the moonlit canopies.",
                objective=QuestObjective(
                    target_type="enemy",
                    target_key="moonlit-assassin",
                    kill_count=3,
                ),
                rewards={
                    "spirit-stone": 150,
                    "moonshadow-fan": 1,
                },
            ),
            Quest(
                key="starfall-radiance",
                name="Starfall Radiance",
                description="Harvest luminous motes from the spirits drifting above Starfall Basin.",
                objective=QuestObjective(
                    target_type="enemy",
                    target_key="starlit-spirit",
                    kill_count=2,
                ),
                rewards={
                    "spirit-stone": 240,
                    "starlit-pendant": 1,
                },
            ),
            Quest(
                key="thunderpeak-accord",
                name="Thunderpeak Accord",
                description="Strike down the guardians barring passage through Thunderpeak Plateau.",
                objective=QuestObjective(
                    target_type="enemy",
                    target_key="thunderpeak-guardian",
                    kill_count=2,
                ),
                rewards={
                    "spirit-stone": 260,
                    "thunderstep-greaves": 1,
                },
            ),
        ]

        for quest in quest_templates:
            self.quests.setdefault(quest.key, quest)

    def ensure_default_stages(self) -> None:
        """Populate baseline cultivation stages when none are configured."""

        mortal_present = any(
            stage.is_mortal for stage in self.qi_cultivation_stages.values()
        )
        needs_qi_seed = not self.qi_cultivation_stages

        if not mortal_present:
            self._register_mortal_realm()

        if needs_qi_seed:
            default_qi_realms: list[tuple[str, str, int]] = [
                ("qi-condensation", "Qi Condensation", 1),
                ("foundation-establishment", "Foundation Establishment", 1),
                ("core-formation", "Core Formation", 1),
                ("nascent-soul", "Nascent Soul", 1),
                ("soul-formation", "Soul Formation", 1),
                ("soul-transformation", "Soul Transformation", 1),
                ("ascendant", "Ascendant", 1),
                ("illusory-yin", "Illusory Yin", 1),
                ("corporeal-yang", "Corporeal Yang", 1),
                ("nirvana-scryer", "Nirvana Scryer", 2),
                ("nirvana-cleanser", "Nirvana Cleanser", 2),
                ("nirvana-shatterer", "Nirvana Shatterer", 2),
                ("heavens-blight", "Heaven's Blight", 2),
                ("nirvana-void", "Nirvana Void", 3),
                ("spirit-void", "Spirit Void", 3),
                ("arcane-void", "Arcane Void", 3),
                ("void-tribulant", "Void Tribulant", 3),
                ("half-heaven-trampling", "Half-Heaven Trampling", 3),
                ("heaven-trampling", "Heaven Trampling", 3),
            ]
            for order, (realm_key, realm_name, step) in enumerate(default_qi_realms):
                base_ratio = _progressive_realm_base(order)
                stage_step = round(
                    DEFAULT_STAGE_BASE_STAT * _progressive_stage_step(order), 2
                )
                self._register_default_realm(
                    path=CultivationPath.QI,
                    realm_key=realm_key,
                    realm_name=realm_name,
                    base_stat=round(
                        DEFAULT_STAGE_BASE_STAT * base_ratio, 2
                    ),
                    base_stat_step=stage_step,
                    realm_order=order,
                    step=step,
                    lifespan=DEFAULT_REALM_LIFESPANS.get(realm_key, ""),
                )
        if not self.body_cultivation_stages:
            self._register_default_realm(
                path=CultivationPath.BODY,
                realm_key="body-tempering",
                realm_name="Body Tempering",
                base_stat=round(
                    DEFAULT_STAGE_BASE_STAT * _progressive_realm_base(0), 2
                ),
                base_stat_step=round(
                    DEFAULT_STAGE_BASE_STAT * _progressive_stage_step(0), 2
                ),
            )
        if not self.soul_cultivation_stages:
            self._register_default_realm(
                path=CultivationPath.SOUL,
                realm_key="soul-awakening",
                realm_name="Soul Awakening",
                base_stat=round(
                    DEFAULT_STAGE_BASE_STAT * _progressive_realm_base(0), 2
                ),
                base_stat_step=round(
                    DEFAULT_STAGE_BASE_STAT * _progressive_stage_step(0), 2
                ),
            )

    def _register_default_realm(
        self,
        *,
        path: CultivationPath,
        realm_key: str,
        realm_name: str,
        base_stat: float,
        base_stat_step: float,
        realm_order: int = 0,
        step: int = 1,
        lifespan: str | None = None,
    ) -> None:
        """Register a complete set of phases for a baseline cultivation realm."""

        for phase in CultivationPhase:
            phase_index = phase.order_index
            stage_key = (
                realm_key
                if phase is CultivationPhase.INITIAL
                else f"{realm_key}-{phase.value}"
            )
            stage_name = (
                realm_name
                if phase is CultivationPhase.INITIAL
                else f"{realm_name} ({phase.display_name})"
            )
            multiplier = round(base_stat + base_stat_step * phase_index, 2)
            self.register_stage(
                CultivationStage(
                    key=stage_key,
                    name=stage_name,
                    success_rate=1.0,
                    path=path.value,
                    base_stat=multiplier,
                    realm=realm_name,
                    phase=phase.value,
                    realm_order=realm_order,
                    step=step,
                    lifespan=lifespan or "",
                )
            )

    def ensure_default_race(self) -> None:
        """Insert a generic lineage when no races exist."""

        if self.races:
            return
        default_race = Race(
            key="human",
            name="Human",
            description="A versatile mortal lineage with balanced potential.",
        )
        self.races[default_race.key] = default_race

    def ensure_default_currency(self) -> None:
        """Guarantee baseline spiritual currencies exist."""

        defaults = {
            "spirit-stone": Currency(
                key="spirit-stone",
                name="Spirit Stone",
                description=(
                    "A lustrous gem condensed from the qi of heaven and earth,"
                    " prized by cultivators as the standard medium of exchange."
                ),
            ),
            "tael": Currency(
                key="tael",
                name="Tael",
                description=(
                    "A refined ingot of spirit-tempered silver, trusted for sect"
                    " stipends and mundane trade throughout cultivation cities."
                ),
            ),
        }

        for key, currency in defaults.items():
            self.currencies.setdefault(key, currency)

    @staticmethod
    def _default_equipment_items() -> list[Item]:
        """Return the baseline xianxia-flavoured equipment set."""

        return [
            Item(
                key="jade-spirit-sword",
                name="Jade Spirit Sword",
                description="A spirit-forged blade that sings with verdant qi.",
                item_type="equipment",
                grade="tier 1",
                equipment_slot=EquipmentSlot.WEAPON,
                weapon_type=WeaponType.SWORD,
                strength=12.0,
                agility=3.0,
            ),
            Item(
                key="cloudsilk-vestments",
                name="Cloudsilk Vestments",
                description="Robes woven from celestial silkworm threads that steady the body.",
                item_type="equipment",
                grade="tier 1",
                equipment_slot=EquipmentSlot.ARMOR,
                physique=11.0,
            ),
            Item(
                key="windstep-boots",
                name="Windstep Boots",
                description="Boots etched with gale runes, lightening every stride.",
                item_type="equipment",
                grade="tier 1",
                equipment_slot=EquipmentSlot.BOOTS,
                agility=10.0,
            ),
            Item(
                key="crimson-lotus-spear",
                name="Crimson Lotus Spear",
                description="A spear wreathed in lotus flames that pierce protective wards.",
                item_type="equipment",
                grade="tier 2",
                equipment_slot=EquipmentSlot.WEAPON,
                weapon_type=WeaponType.SPEAR,
                strength=20.0,
                agility=6.0,
                slots_required=2,
            ),
            Item(
                key="moonshadow-fan",
                name="Moonshadow Fan",
                description="A moon-silk fan that bends twilight winds into slicing arcs.",
                item_type="equipment",
                grade="tier 2",
                equipment_slot=EquipmentSlot.WEAPON,
                weapon_type=WeaponType.FAN,
                strength=8.0,
                agility=18.0,
            ),
            Item(
                key="serpentscale-belt",
                name="Serpentscale Belt",
                description="A belt interwoven with serpentscale talismans that steady the core.",
                item_type="equipment",
                grade="tier 2",
                equipment_slot=EquipmentSlot.BELT,
                physique=15.0,
                inventory_space_bonus=8,
            ),
            Item(
                key="starlit-pendant",
                name="Starlit Pendant",
                description="A pendant that catches astral motes, sharpening perception and poise.",
                item_type="equipment",
                grade="tier 3",
                equipment_slot=EquipmentSlot.NECKLACE,
                strength=5.0,
                agility=9.0,
            ),
            Item(
                key="thunderstep-greaves",
                name="Thunderstep Greaves",
                description="Greaves that crackle with thunder qi, pushing each stride into a charge.",
                item_type="equipment",
                grade="tier 3",
                equipment_slot=EquipmentSlot.BOOTS,
                strength=4.0,
                agility=18.0,
            ),
            Item(
                key="auric-sunblade",
                name="Auric Sunblade",
                description="A radiant longsword tempered within sunfire kilns.",
                item_type="equipment",
                grade="tier 3",
                equipment_slot=EquipmentSlot.WEAPON,
                weapon_type=WeaponType.SWORD,
                strength=24.0,
                agility=10.0,
                evolves_to="celestial-dragon-sabre",
                evolution_requirements={"spirit-stone": "900"},
            ),
            Item(
                key="celestial-dragon-sabre",
                name="Celestial Dragon Sabre",
                description="An ascendant sabre that roars with draconic qi when drawn.",
                item_type="equipment",
                grade="tier 4",
                equipment_slot=EquipmentSlot.WEAPON,
                weapon_type=WeaponType.SWORD,
                strength=32.0,
                agility=12.0,
                slots_required=2,
            ),
            Item(
                key="aurora-ward-robes",
                name="Aurora Ward Robes",
                description="Robes layered with prismatic wards that disperse lethal strikes.",
                item_type="equipment",
                grade="tier 3",
                equipment_slot=EquipmentSlot.ARMOR,
                physique=18.0,
                agility=5.0,
            ),
            Item(
                key="dragonbone-bracelet",
                name="Dragonbone Bracelet",
                description="A bracelet carved from slumbering dragonbone, pulsing with latent might.",
                item_type="equipment",
                grade="tier 3",
                equipment_slot=EquipmentSlot.ACCESSORY,
                strength=8.0,
                agility=8.0,
            ),
        ]

    @staticmethod
    def _build_loot_drops(
        equipment_keys: Sequence[str], chances: Sequence[float]
    ) -> Dict[str, LootDrop]:
        loot: Dict[str, LootDrop] = {}
        for key, chance in zip(equipment_keys, chances):
            loot[key] = LootDrop(chance=chance, amount=1)
        return loot

    def ensure_default_equipment(self) -> None:
        """Guarantee that themed starter equipment is available."""

        defaults = self._default_equipment_items()
        has_equipment = any(
            isinstance(item, Item)
            and isinstance(getattr(item, "item_type", ""), str)
            and item.item_type.lower() == "equipment"
            for item in self.items.values()
        )
        if not has_equipment:
            for item in defaults:
                self.items[item.key] = item
            return
        for item in defaults:
            self.items.setdefault(item.key, item)

    def ensure_default_shop_items(self) -> None:
        """Create a baseline market stocked with starter equipment."""

        self.ensure_default_equipment()
        self.ensure_default_currency()

        catalogue = [
            ("village-market-jade-spirit-sword", "jade-spirit-sword", 180),
            ("village-market-cloudsilk-vestments", "cloudsilk-vestments", 150),
            ("village-market-windstep-boots", "windstep-boots", 140),
            ("azure-pavilion-moonshadow-fan", "moonshadow-fan", 420),
            ("azure-pavilion-starlit-pendant", "starlit-pendant", 520),
            ("azure-pavilion-cloudsilk-vestments", "cloudsilk-vestments", 150),
            ("crimson-outpost-crimson-lotus-spear", "crimson-lotus-spear", 480),
            ("crimson-outpost-auric-sunblade", "auric-sunblade", 720),
            ("crimson-outpost-serpentscale-belt", "serpentscale-belt", 360),
            ("serpent-bazaar-aurora-ward-robes", "aurora-ward-robes", 540),
            ("serpent-bazaar-dragonbone-bracelet", "dragonbone-bracelet", 460),
            ("starfall-emporium-thunderstep-greaves", "thunderstep-greaves", 560),
            (
                "starfall-emporium-celestial-dragon-sabre",
                "celestial-dragon-sabre",
                980,
            ),
            ("thunderpeak-forge-auric-sunblade", "auric-sunblade", 740),
            ("thunderpeak-forge-thunderstep-greaves", "thunderstep-greaves", 540),
        ]
        for key, item_key, price in catalogue:
            self.shop_items.setdefault(
                key,
                ShopItem(
                    item_key=item_key,
                    currency_key="spirit-stone",
                    price=price,
                ),
            )

    def ensure_default_enemies(self) -> None:
        """Populate a suite of baseline enemies with themed loot tables."""

        self.ensure_default_equipment()
        self.ensure_default_currency()

        default_equipment_keys = [item.key for item in self._default_equipment_items()]
        equipment_loot = self._build_loot_drops(default_equipment_keys, (0.2, 0.15, 0.1))

        templates: dict[str, Enemy] = {
            "mist-veiled-disciple": Enemy(
                key="mist-veiled-disciple",
                name="Mist-Veiled Disciple",
                active_path=CultivationPath.QI.value,
                cultivation_stage="qi-condensation",
                strength=16.0,
                physique=14.0,
                agility=15.0,
                affinity=SpiritualAffinity.WIND,
                loot_table=dict(equipment_loot),
            ),
            "jade-forest-bandit": Enemy(
                key="jade-forest-bandit",
                name="Jade Forest Bandit",
                active_path=CultivationPath.QI.value,
                cultivation_stage="qi-condensation",
                strength=18.0,
                physique=15.0,
                agility=14.0,
                affinity=SpiritualAffinity.EARTH,
                loot_table={
                    "spirit-stone": LootDrop(chance=0.7, amount=25, kind="currency"),
                    "cloudsilk-vestments": LootDrop(chance=0.06, amount=1),
                },
            ),
            "ashen-spirit-wolf": Enemy(
                key="ashen-spirit-wolf",
                name="Ashen Spirit Wolf",
                active_path=CultivationPath.BODY.value,
                cultivation_stage="body-tempering",
                strength=14.0,
                physique=12.0,
                agility=18.0,
                affinity=SpiritualAffinity.WIND,
                loot_table={
                    "spirit-stone": LootDrop(chance=0.55, amount=18, kind="currency"),
                    "windstep-boots": LootDrop(chance=0.08, amount=1),
                },
            ),
            "bloodshade-cultivator": Enemy(
                key="bloodshade-cultivator",
                name="Bloodshade Cultivator",
                active_path=CultivationPath.QI.value,
                cultivation_stage="qi-condensation",
                strength=17.0,
                physique=16.0,
                agility=16.0,
                affinity=SpiritualAffinity.FIRE,
                loot_table={
                    "spirit-stone": LootDrop(chance=0.6, amount=30, kind="currency"),
                    "jade-spirit-sword": LootDrop(chance=0.05, amount=1),
                },
            ),
            "crimson-lotus-disciple": Enemy(
                key="crimson-lotus-disciple",
                name="Crimson Lotus Disciple",
                active_path=CultivationPath.QI.value,
                cultivation_stage="foundation-establishment",
                strength=24.0,
                physique=19.0,
                agility=18.0,
                affinity=SpiritualAffinity.FIRE,
                loot_table={
                    "spirit-stone": LootDrop(chance=0.75, amount=45, kind="currency"),
                    "crimson-lotus-spear": LootDrop(chance=0.08, amount=1),
                    "auric-sunblade": LootDrop(chance=0.04, amount=1),
                },
            ),
            "blazewing-phoenix": Enemy(
                key="blazewing-phoenix",
                name="Blazewing Phoenix",
                active_path=CultivationPath.SOUL.value,
                cultivation_stage="core-formation",
                strength=26.0,
                physique=21.0,
                agility=24.0,
                affinity=SpiritualAffinity.LAVA,
                loot_table={
                    "spirit-stone": LootDrop(chance=0.62, amount=60, kind="currency"),
                    "aurora-ward-robes": LootDrop(chance=0.06, amount=1),
                    "auric-sunblade": LootDrop(chance=0.03, amount=1),
                },
                elemental_resistances=[SpiritualAffinity.FIRE],
            ),
            "serpentscale-adept": Enemy(
                key="serpentscale-adept",
                name="Serpentscale Adept",
                active_path=CultivationPath.BODY.value,
                cultivation_stage="foundation-establishment",
                strength=20.0,
                physique=22.0,
                agility=17.0,
                affinity=SpiritualAffinity.POISON,
                loot_table={
                    "spirit-stone": LootDrop(chance=0.7, amount=40, kind="currency"),
                    "serpentscale-belt": LootDrop(chance=0.09, amount=1),
                },
                elemental_resistances=[SpiritualAffinity.POISON],
            ),
            "obsidian-naga": Enemy(
                key="obsidian-naga",
                name="Obsidian Naga",
                active_path=CultivationPath.SOUL.value,
                cultivation_stage="nascent-soul",
                strength=28.0,
                physique=24.0,
                agility=20.0,
                affinity=SpiritualAffinity.DARKNESS,
                loot_table={
                    "spirit-stone": LootDrop(chance=0.65, amount=65, kind="currency"),
                    "dragonbone-bracelet": LootDrop(chance=0.07, amount=1),
                },
                elemental_resistances=[SpiritualAffinity.DARKNESS, SpiritualAffinity.WATER],
            ),
            "moonlit-assassin": Enemy(
                key="moonlit-assassin",
                name="Moonlit Assassin",
                active_path=CultivationPath.QI.value,
                cultivation_stage="foundation-establishment",
                strength=21.0,
                physique=17.0,
                agility=24.0,
                affinity=SpiritualAffinity.TWILIGHT,
                loot_table={
                    "spirit-stone": LootDrop(chance=0.68, amount=42, kind="currency"),
                    "moonshadow-fan": LootDrop(chance=0.09, amount=1),
                },
            ),
            "starlit-spirit": Enemy(
                key="starlit-spirit",
                name="Starlit Spirit",
                active_path=CultivationPath.SOUL.value,
                cultivation_stage="nascent-soul",
                strength=23.0,
                physique=20.0,
                agility=27.0,
                affinity=SpiritualAffinity.LIGHT,
                loot_table={
                    "spirit-stone": LootDrop(chance=0.66, amount=70, kind="currency"),
                    "starlit-pendant": LootDrop(chance=0.1, amount=1),
                    "celestial-dragon-sabre": LootDrop(chance=0.03, amount=1),
                },
            ),
            "thunderpeak-guardian": Enemy(
                key="thunderpeak-guardian",
                name="Thunderpeak Guardian",
                active_path=CultivationPath.BODY.value,
                cultivation_stage="core-formation",
                strength=27.0,
                physique=26.0,
                agility=23.0,
                affinity=SpiritualAffinity.LIGHTNING,
                loot_table={
                    "spirit-stone": LootDrop(chance=0.7, amount=66, kind="currency"),
                    "thunderstep-greaves": LootDrop(chance=0.1, amount=1),
                    "aurora-ward-robes": LootDrop(chance=0.05, amount=1),
                },
                elemental_resistances=[SpiritualAffinity.LIGHTNING],
            ),
        }

        for key, template in templates.items():
            existing = self.enemies.get(key)
            if existing is None:
                self.enemies[key] = template
                continue
            if not existing.loot_table and template.loot_table:
                existing.loot_table = dict(template.loot_table)
            if not existing.cultivation_stage:
                existing.cultivation_stage = template.cultivation_stage
            if existing.affinity is None and template.affinity is not None:
                existing.affinity = template.affinity
            if not existing.active_path:
                existing.active_path = template.active_path
            self.enemies[key] = existing

    def ensure_default_quests(self) -> None:
        """Ensure the world offers introductory quests tied to default foes."""

        self.ensure_default_enemies()
        self.ensure_default_shop_items()

        quest_templates = [
            Quest(
                key="bandit-menace",
                name="Bandit Menace",
                description="Drive off the brigands preying on Verdant Moon Village.",
                objective=QuestObjective(
                    target_type="enemy",
                    target_key="jade-forest-bandit",
                    kill_count=5,
                ),
                rewards={
                    "spirit-stone": 120,
                    "cloudsilk-vestments": 1,
                },
            ),
            Quest(
                key="wolf-pelt-contract",
                name="Whispering Howls",
                description="Collect pelts from the Ashen Spirit Wolves prowling the forest.",
                objective=QuestObjective(
                    target_type="enemy",
                    target_key="ashen-spirit-wolf",
                    kill_count=4,
                ),
                rewards={
                    "spirit-stone": 90,
                    "windstep-boots": 1,
                },
            ),
            Quest(
                key="emberflame-suppression",
                name="Emberflame Suppression",
                description="Break the Ember Sect's forward scouts stationed along Emberflame Ridge.",
                objective=QuestObjective(
                    target_type="enemy",
                    target_key="crimson-lotus-disciple",
                    kill_count=3,
                ),
                rewards={
                    "spirit-stone": 160,
                    "auric-sunblade": 1,
                },
            ),
            Quest(
                key="serpent-sanctum",
                name="Sanctify the Serpent Grotto",
                description="Cleanse the Obsidian Serpent Grotto of poisonous adepts.",
                objective=QuestObjective(
                    target_type="enemy",
                    target_key="serpentscale-adept",
                    kill_count=4,
                ),
                rewards={
                    "spirit-stone": 170,
                    "serpentscale-belt": 1,
                },
            ),
            Quest(
                key="shadow-veil-hunt",
                name="Shadow Veil Hunt",
                description="Unmask and defeat the assassins lurking beneath the moonlit canopies.",
                objective=QuestObjective(
                    target_type="enemy",
                    target_key="moonlit-assassin",
                    kill_count=3,
                ),
                rewards={
                    "spirit-stone": 150,
                    "moonshadow-fan": 1,
                },
            ),
            Quest(
                key="starfall-radiance",
                name="Starfall Radiance",
                description="Harvest luminous motes from the spirits drifting above Starfall Basin.",
                objective=QuestObjective(
                    target_type="enemy",
                    target_key="starlit-spirit",
                    kill_count=2,
                ),
                rewards={
                    "spirit-stone": 240,
                    "starlit-pendant": 1,
                },
            ),
            Quest(
                key="thunderpeak-accord",
                name="Thunderpeak Accord",
                description="Strike down the guardians barring passage through Thunderpeak Plateau.",
                objective=QuestObjective(
                    target_type="enemy",
                    target_key="thunderpeak-guardian",
                    kill_count=2,
                ),
                rewards={
                    "spirit-stone": 260,
                    "thunderstep-greaves": 1,
                },
            ),
        ]

        for quest in quest_templates:
            self.quests.setdefault(quest.key, quest)

    def sync_cultivation_stage_partitions(self) -> None:
        """Normalize stored stage mappings after loading data."""

        for storage_key, stage in list(self.iter_stage_records()):
            self.register_stage(stage, storage_key=storage_key)

    def iter_stage_records(self) -> Iterator[tuple[str, CultivationStage]]:
        """Yield stored stage entries using their persistence keys."""

        for key, stage in self.qi_cultivation_stages.items():
            storage_key = self._stage_storage_keys.get((CultivationPath.QI.value, key))
            if storage_key is None:
                storage_key = f"{CultivationPath.QI.value}:{key}"
            yield storage_key, stage
        for key, stage in self.body_cultivation_stages.items():
            storage_key = self._stage_storage_keys.get((CultivationPath.BODY.value, key))
            if storage_key is None:
                storage_key = f"{CultivationPath.BODY.value}:{key}"
            yield storage_key, stage
        for key, stage in self.soul_cultivation_stages.items():
            storage_key = self._stage_storage_keys.get((CultivationPath.SOUL.value, key))
            if storage_key is None:
                storage_key = f"{CultivationPath.SOUL.value}:{key}"
            yield storage_key, stage

    def register_stage(
        self, stage: CultivationStage, *, storage_key: Optional[str] = None
    ) -> None:
        """Add or update a cultivation stage in the appropriate cache."""

        path = CultivationPath.from_value(getattr(stage, "path", CultivationPath.QI.value))
        key = str(stage.key or "").strip()
        if not key:
            raise ValueError("Cultivation stages must define a key")

        canonical_storage = f"{path.value}:{key}"
        resolved_storage = storage_key or self._stage_storage_keys.get((path.value, key))
        if resolved_storage and ":" not in resolved_storage:
            resolved_storage = canonical_storage
        if not resolved_storage:
            resolved_storage = canonical_storage

        # Guard against collisions where a persistence key was reused by another path.
        existing = self._storage_key_index.get(resolved_storage)
        if existing is not None and existing != (path, key):
            collision_storage = resolved_storage
            if existing[0] is path:
                previous_path, previous_key = existing
                if previous_path is CultivationPath.QI:
                    self.qi_cultivation_stages.pop(previous_key, None)
                elif previous_path is CultivationPath.BODY:
                    self.body_cultivation_stages.pop(previous_key, None)
                else:
                    self.soul_cultivation_stages.pop(previous_key, None)
                self._stage_storage_keys.pop((previous_path.value, previous_key), None)
            self._storage_key_index.pop(collision_storage, None)
            resolved_storage = canonical_storage

        previous_storage = self._stage_storage_keys.get((path.value, key))
        if previous_storage and previous_storage != resolved_storage:
            self._storage_key_index.pop(previous_storage, None)

        # Store the stage in the appropriate partition without touching other paths.
        if path is CultivationPath.BODY:
            self.body_cultivation_stages[key] = stage
        elif path is CultivationPath.SOUL:
            self.soul_cultivation_stages[key] = stage
        else:
            self.qi_cultivation_stages[key] = stage

        self._stage_storage_keys[(path.value, key)] = resolved_storage
        self._storage_key_index[resolved_storage] = (path, key)

    def resolve_stage_storage_key(self, path: CultivationPath | str, key: str) -> str:
        """Return the persistence key used for a stage."""

        path_value = CultivationPath.from_value(path).value
        return self._stage_storage_keys.get((path_value, key), f"{path_value}:{key}")

    def get_stage(
        self, key: Optional[str], path: CultivationPath | str | None = None
    ) -> Optional[CultivationStage]:
        """Fetch a cultivation stage by key, optionally constrained by path."""

        if not key:
            return None
        if path is None:
            stage = self.qi_cultivation_stages.get(key)
            if stage is not None:
                return stage
            return self.body_cultivation_stages.get(key)
        path_value = CultivationPath.from_value(path)
        if path_value is CultivationPath.BODY:
            return self.body_cultivation_stages.get(key)
        if path_value is CultivationPath.SOUL:
            return self.soul_cultivation_stages.get(key)
        return self.qi_cultivation_stages.get(key)

    def stage_rank(self, path: CultivationPath | str, stage_key: str | None) -> Optional[int]:
        if not stage_key:
            return None
        path_value = CultivationPath.from_value(path)
        if path_value is CultivationPath.BODY:
            stages = self.body_cultivation_stages
        elif path_value is CultivationPath.SOUL:
            stages = self.soul_cultivation_stages
        else:
            stages = self.qi_cultivation_stages
        stage = stages.get(stage_key)
        if stage is None:
            return None
        ordered = sorted(stages.values(), key=lambda candidate: candidate.ordering_tuple)
        for index, candidate in enumerate(ordered):
            if candidate.key == stage.key:
                return index
        return None

    def stage_meets_requirement(
        self,
        path: CultivationPath | str,
        current_key: str | None,
        required_key: str | None,
    ) -> bool:
        if not required_key:
            return True
        current_rank = self.stage_rank(path, current_key)
        required_rank = self.stage_rank(path, required_key)
        if current_rank is None or required_rank is None:
            return False
        return current_rank >= required_rank

    def bond_conditions_met(
        self,
        player: PlayerProgress,
        partner: PlayerProgress,
        bond: BondProfile,
    ) -> bool:
        if not bond.key or player.bond_key != bond.key or partner.bond_key != bond.key:
            return False
        if player.bond_partner_id != partner.user_id or partner.bond_partner_id != player.user_id:
            return False
        try:
            player_path = CultivationPath.from_value(player.active_path)
            partner_path = CultivationPath.from_value(partner.active_path)
        except ValueError:
            return False
        required_path = bond.required_path
        if player_path is not required_path or partner_path is not required_path:
            return False
        if bond.min_stage:
            if not self.stage_meets_requirement(required_path, player.stage_key_for_path(required_path), bond.min_stage):
                return False
            if not self.stage_meets_requirement(
                required_path,
                partner.stage_key_for_path(required_path),
                bond.min_stage,
            ):
                return False
        return True

    def bond_bonuses(
        self,
        bond: BondProfile,
        player: PlayerProgress,
        partner: PlayerProgress,
        player_gain: int,
        partner_gain: int,
    ) -> tuple[int, int]:
        if not self.bond_conditions_met(player, partner, bond):
            return (0, 0)
        return (
            bond.bonus_amount(player_gain),
            bond.bonus_amount(partner_gain),
        )

    def iter_all_stages(self) -> Iterator[CultivationStage]:
        """Iterate over every known cultivation stage."""

        for stage in self.qi_cultivation_stages.values():
            yield stage
        for stage in self.body_cultivation_stages.values():
            yield stage
        for stage in self.soul_cultivation_stages.values():
            yield stage

    def ensure_martial_soul_signature_skills(self) -> None:
        """Seed the state with signature skills for default martial souls."""

        if self.signature_skills_seeded:
            return
        for soul in MartialSoul.default_pool():
            self.register_martial_soul_skills(soul)
        self.signature_skills_seeded = True

    def register_martial_soul_skills(self, soul: MartialSoul) -> None:
        for ability_key in soul.signature_abilities or (soul.signature_skill_key(),):
            normalized = str(ability_key).strip()
            if not normalized:
                continue
            self.skills[normalized] = build_martial_soul_signature_skill(
                soul, ability_key=normalized
            )

    def snapshot(self) -> Dict[str, List[Dict[str, object]]]:
        return {
            "qi_cultivation_stages": [
                asdict(stage) for stage in self.qi_cultivation_stages.values()
            ],
            "body_cultivation_stages": [
                asdict(stage) for stage in self.body_cultivation_stages.values()
            ],
            "races": [asdict(x) for x in self.races.values()],
        }

    @staticmethod
    def _default_innate_soul_exp_multiplier(grade: int) -> float:
        grade = max(1, min(9, int(grade)))
        return 0.5 + (grade - 1) * (1.0 / 8)

    def set_innate_soul_exp_range(self, grade: int, minimum: float, maximum: float) -> None:
        grade = max(1, min(9, int(grade)))
        try:
            lower = float(minimum)
            upper = float(maximum)
        except (TypeError, ValueError):
            lower = self._default_innate_soul_exp_multiplier(grade)
            upper = lower
        if lower > upper:
            lower, upper = upper, lower
        if not math.isfinite(lower):
            lower = self._default_innate_soul_exp_multiplier(grade)
        if not math.isfinite(upper):
            upper = lower
        lower = max(0.0, lower)
        upper = max(lower, upper)
        self.innate_soul_exp_multiplier_ranges[grade] = (lower, upper)
        self.innate_soul_exp_ranges_loaded = True

    def reset_innate_soul_exp_range(self, grade: int | None = None) -> None:
        if grade is None:
            for entry in range(1, 10):
                default = self._default_innate_soul_exp_multiplier(entry)
                self.innate_soul_exp_multiplier_ranges[entry] = (default, default)
        else:
            entry = max(1, min(9, int(grade)))
            default = self._default_innate_soul_exp_multiplier(entry)
            self.innate_soul_exp_multiplier_ranges[entry] = (default, default)
        self.innate_soul_exp_ranges_loaded = True

    def update_innate_soul_exp_ranges(self, overrides: Mapping[str, object]) -> None:
        for key, values in overrides.items():
            try:
                grade = int(key)
            except (TypeError, ValueError):
                continue
            if not 1 <= grade <= 9:
                continue
            if isinstance(values, Mapping):
                minimum = values.get("min")
                if minimum is None:
                    minimum = values.get("minimum", values.get("low"))
                maximum = values.get("max")
                if maximum is None:
                    maximum = values.get("maximum", values.get("high"))
                if minimum is None and maximum is None:
                    continue
            elif isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
                if len(values) < 2:
                    continue
                minimum, maximum = values[0], values[1]
            else:
                continue
            try:
                min_val = float(minimum) if minimum is not None else None
                max_val = float(maximum) if maximum is not None else None
            except (TypeError, ValueError):
                continue
            if min_val is None:
                min_val = self._default_innate_soul_exp_multiplier(grade)
            if max_val is None:
                max_val = min_val
            self.set_innate_soul_exp_range(grade, min_val, max_val)
        self.innate_soul_exp_ranges_loaded = True

    def innate_soul_exp_range_for_grade(self, grade: int) -> Tuple[float, float]:
        grade = max(1, min(9, int(grade)))
        return self.innate_soul_exp_multiplier_ranges.get(
            grade,
            (
                self._default_innate_soul_exp_multiplier(grade),
                self._default_innate_soul_exp_multiplier(grade),
            ),
        )


def roll_talent_stats(
    min_value: int = 1,
    max_value: int = 20,
    *,
    dice_count: int = 5,
    dice_faces: int = 5,
    drop_lowest: int = 1,
) -> Stats:
    """Generate talent stat rolls within the provided range.

    Each stat is calculated by rolling ``dice_count`` dice with ``dice_faces`` sides,
    dropping the lowest ``drop_lowest`` results, and clamping the outcome between the
    provided ``min_value`` and ``max_value`` bounds.
    """

    try:
        lower = int(min_value)
    except (TypeError, ValueError):
        lower = 0
    try:
        upper = int(max_value)
    except (TypeError, ValueError):
        upper = lower

    lower = max(0, lower)
    if lower > upper:
        lower, upper = upper, lower

    try:
        count = int(dice_count)
    except (TypeError, ValueError):
        count = 5
    count = max(1, count)

    try:
        faces = int(dice_faces)
    except (TypeError, ValueError):
        faces = 5
    faces = max(1, faces)

    try:
        drop = int(drop_lowest)
    except (TypeError, ValueError):
        drop = 1
    drop = max(0, min(drop, count - 1))

    def _roll_stat() -> float:
        rolls = [random.randint(1, faces) for _ in range(count)]
        rolls.sort(reverse=True)
        kept = rolls[:-drop] if drop else rolls
        total = sum(kept)
        if total < lower:
            return float(lower)
        if total > upper:
            return float(upper)
        return float(total)

    return Stats(**{name: _roll_stat() for name in PLAYER_STAT_NAMES})
def _exp_ratio(current: int, required: int) -> float:
    if required <= 0:
        return 1.0
    if current <= 0:
        return 0.0
    return current / float(required)


def cultivation_ready(player: PlayerProgress, path: str | None = None) -> bool:
    target_path = (
        CultivationPath.from_value(path)
        if path is not None
        else CultivationPath.from_value(player.active_path)
    )
    if target_path.value != player.active_path:
        return False
    if target_path is CultivationPath.BODY:
        return player.combat_exp_required > 0 and (
            player.combat_exp >= player.combat_exp_required
        )
    if target_path is CultivationPath.SOUL:
        return player.soul_exp_required > 0 and (
            player.soul_exp >= player.soul_exp_required
        )
    return player.cultivation_exp_required > 0 and (
        player.cultivation_exp >= player.cultivation_exp_required
    )


def attempt_breakthrough(
    player: PlayerProgress, stage: CultivationStage, path: str | None
) -> bool:
    """Apply breakthrough logic and return success flag."""

    normalized = str(path).lower() if path is not None else ""
    if normalized:
        path_value = CultivationPath.from_value(normalized)
    else:
        path_value = CultivationPath.from_value(player.active_path)

    if path_value.value != player.active_path:
        return False

    if path_value is CultivationPath.BODY:
        ratio = _exp_ratio(player.combat_exp, player.combat_exp_required)
    elif path_value is CultivationPath.QI:
        ratio = _exp_ratio(player.cultivation_exp, player.cultivation_exp_required)
    else:
        ratio = _exp_ratio(player.soul_exp, player.soul_exp_required)
    ratio = max(1.0, ratio)
    multiplier = min(ratio, 2.0)
    effective_success = min(1.0, stage.success_rate * multiplier)

    roll = random.random()
    if roll <= effective_success:
        return True

    # failure, reduce accumulated experience
    loss = max(0.0, min(1.0, stage.breakthrough_failure_loss))
    if path_value is CultivationPath.BODY:
        player.combat_exp = int(player.combat_exp * (1 - loss))
    elif path_value is CultivationPath.QI:
        player.cultivation_exp = int(player.cultivation_exp * (1 - loss))
    else:
        player.soul_exp = int(player.soul_exp * (1 - loss))
    return False


class DamageResolution(NamedTuple):
    rolled: float
    applied: float
    pool: str  # "hp" or "soul"
    minimum: int
    maximum: int


class BaseExpGain(NamedTuple):
    """Breakdown entry for cultivation experience gained from a innate soul."""

    name: str
    grade: int
    exp: int


def resolve_damage(
    attacker: PlayerProgress,
    defender_hp: float,
    defender_soul_hp: float,
    skill: Skill,
    qi_stage: CultivationStage,
    body_stage: Optional[CultivationStage],
    soul_stage: Optional[CultivationStage],
    race: Optional[Race],
    traits: List[SpecialTrait],
    items: Sequence[Item] | None,
    resistances: Sequence[SpiritualAffinity],
    weapon_types: Collection[WeaponType] | None = None,
) -> DamageResolution:
    stats = attacker.effective_stats(
        qi_stage, body_stage, soul_stage, race, traits, list(items or [])
    )
    base_stat = stats.attacks
    proficiency = attacker.skill_proficiency.get(skill.key, 0)
    damage = skill.damage_for(base_stat, proficiency)
    base = attacker.combined_innate_soul(traits)
    attack_elements = skill.elements or (() if skill.element is None else (skill.element,))
    if base:
        damage *= base.damage_multiplier(attack_elements)
    damage *= attacker.martial_soul_damage_multiplier(skill, weapon_types)
    if attack_elements:
        reduction_fraction = resistance_reduction_fraction(attack_elements, resistances)
        damage *= max(0.0, 1 - 0.25 * reduction_fraction)
    minimum = math.floor(max(0.0, damage * 0.8) + 0.5)
    maximum = math.floor(max(0.0, damage * 1.2) + 0.5)
    variance = random.uniform(0.8, 1.2)
    final_damage = max(0.0, damage * variance)
    final_damage = math.floor(final_damage + 0.5)
    target_pool = "hp"
    remaining = defender_hp
    applied = min(remaining, final_damage)
    return DamageResolution(
        rolled=final_damage,
        applied=applied,
        pool=target_pool,
        minimum=minimum,
        maximum=maximum,
    )


def gain_skill_proficiency(
    player: PlayerProgress, skill: Skill, amount: int = 1
) -> None:
    if amount <= 0:
        return

    skill_key = skill.key
    current = max(0, int(player.skill_proficiency.get(skill_key, 0)))
    proficiency_cap = max(0, int(skill.proficiency_max))

    if proficiency_cap > 0:
        new_value = min(proficiency_cap, current + amount)
    else:
        new_value = current + amount

    player.skill_proficiency[skill_key] = new_value


def can_evolve_skill(skill: Skill, player: PlayerProgress) -> bool:
    if not skill.evolves_to:
        return False
    proficiency = player.skill_proficiency.get(skill.key, 0)
    required_prof = int(skill.proficiency_max)
    if proficiency < required_prof:
        return False
    for requirement, value in skill.evolution_requirements.items():
        if requirement == "stage" and player.cultivation_stage != value:
            return False
        if requirement == "item" and player.inventory.get(value, 0) <= 0:
            return False
    return True


def perform_cultivation(
    player: PlayerProgress,
    gain: int,
    *,
    multiplier_ranges: Mapping[int, Tuple[float, float]] | None = None,
    now: float | None = None,
) -> List[BaseExpGain]:
    timestamp = time.time() if now is None else float(now)
    player.last_cultivate_ts = timestamp
    player.last_train_ts = timestamp
    player.last_temper_ts = timestamp
    if player.active_path != CultivationPath.QI.value:
        return []
    gains: list[int] = []
    ranges = multiplier_ranges or {}

    def _roll_gain(grade_value: int) -> int:
        innate_soul_grade = max(1, min(9, int(grade_value)))
        if innate_soul_grade in ranges:
            lower, upper = ranges[innate_soul_grade]
        else:
            default = GameState._default_innate_soul_exp_multiplier(innate_soul_grade)
            lower = upper = default
        if lower > upper:
            lower, upper = upper, lower
        lower = max(0.0, float(lower))
        upper = max(lower, float(upper))
        minimum = max(1, int(round(gain * lower)))
        maximum = max(minimum, int(round(gain * upper)))
        if minimum == maximum:
            return minimum
        return random.randint(minimum, maximum)

    active_souls = player.get_active_martial_souls()
    if not active_souls:
        active_souls = player.martial_souls
    for soul in active_souls:
        grade = max(1, min(9, soul.grade))
        gains.append(_roll_gain(grade))
    if not gains:
        default_grade = active_souls[0].grade if active_souls else 1
        grade = max(1, min(9, default_grade))
        gains.append(_roll_gain(grade))
    adjusted_gain = sum(gains)
    player.cultivation_exp += adjusted_gain

    breakdown: List[BaseExpGain] = []
    for soul, exp_gain in zip(active_souls, gains):
        display = soul.name.strip() or f"Grade {soul.grade} Soul"
        breakdown.append(BaseExpGain(name=display, grade=soul.grade, exp=exp_gain))
    if not breakdown and gains:
        placeholder_name = (
            active_souls[0].name.strip() if active_souls else "Martial Soul"
        )
        grade = active_souls[0].grade if active_souls else 1
        breakdown.append(BaseExpGain(name=placeholder_name, grade=grade, exp=gains[0]))
    return breakdown


def gain_combat_exp(
    player: PlayerProgress, gain: int, *, now: float | None = None
) -> None:
    timestamp = time.time() if now is None else float(now)
    player.last_train_ts = timestamp
    player.last_cultivate_ts = timestamp
    player.last_temper_ts = timestamp
    if player.active_path == CultivationPath.BODY.value:
        player.combat_exp += gain


def gain_soul_exp(
    player: PlayerProgress, gain: int, *, now: float | None = None
) -> None:
    timestamp = time.time() if now is None else float(now)
    player.last_temper_ts = timestamp
    player.last_cultivate_ts = timestamp
    player.last_train_ts = timestamp
    if player.active_path == CultivationPath.SOUL.value:
        player.soul_exp += gain


def inventory_load(player: PlayerProgress) -> int:
    return sum(max(0, int(amount)) for amount in player.inventory.values())


def inventory_capacity(player: PlayerProgress, items: Mapping[str, Item]) -> int:
    capacity = max(0, int(player.inventory_capacity))
    for key in player.iter_equipped_item_keys():
        item = items.get(key)
        if not item:
            continue
        capacity += max(0, int(getattr(item, "inventory_space_bonus", 0)))
    return capacity


def add_item_to_inventory(
    player: PlayerProgress,
    item_key: str,
    amount: int,
    items: Mapping[str, Item],
) -> int:
    if amount <= 0:
        return 0
    capacity = inventory_capacity(player, items)
    load = inventory_load(player)
    available = max(0, capacity - load)
    if available <= 0:
        return 0
    to_add = min(available, amount)
    if to_add <= 0:
        return 0
    player.inventory[item_key] = player.inventory.get(item_key, 0) + to_add
    return to_add


def remove_item_from_inventory(
    player: PlayerProgress,
    item_key: str,
    amount: int,
) -> int:
    """Remove ``amount`` of ``item_key`` from ``player``'s inventory."""

    if amount <= 0:
        return 0

    current = int(player.inventory.get(item_key, 0))
    if current <= 0:
        return 0

    to_remove = min(current, amount)
    remaining = current - to_remove
    if remaining > 0:
        player.inventory[item_key] = remaining
    else:
        player.inventory.pop(item_key, None)
    return to_remove


def award_loot(
    player: PlayerProgress,
    drops: Mapping[str, LootDrop],
    items: Mapping[str, Item],
    currencies: Mapping[str, Currency],
) -> Tuple[List[tuple[str, int]], List[tuple[str, int]]]:
    obtained: List[tuple[str, int]] = []
    skipped: List[tuple[str, int]] = []
    for item_key, drop in drops.items():
        if random.random() > drop.chance:
            continue
        amount = max(1, int(drop.amount))
        if drop.kind == "currency" or (drop.kind == "item" and item_key not in items and item_key in currencies):
            current = player.currencies.get(item_key, 0)
            player.currencies[item_key] = current + amount
            obtained.append((item_key, amount))
            continue

        added = add_item_to_inventory(player, item_key, amount, items)
        if added:
            obtained.append((item_key, added))
        if added < amount:
            skipped.append((item_key, amount - added))
    return obtained, skipped


def create_party(party_id: str, leader_id: int) -> Party:
    return Party(party_id=party_id, leader_id=leader_id, member_ids=[leader_id], location=None)


def join_party(party: Party, user_id: int) -> None:
    if user_id not in party.member_ids:
        party.member_ids.append(user_id)


def leave_party(party: Party, user_id: int) -> None:
    if user_id in party.member_ids:
        party.member_ids.remove(user_id)


def _event_mentions_player(event: TravelEvent, user_id: int) -> bool:
    data = getattr(event, "data", None)
    if not isinstance(data, Mapping):
        return False

    def _mentions(value: object) -> bool:
        if isinstance(value, int):
            return value == user_id
        if isinstance(value, Mapping):
            return any(_mentions(item) for item in value.values())
        if isinstance(value, (list, tuple, set)):
            return any(_mentions(item) for item in value)
        return False

    return _mentions(data)


def build_turn_order(players: Sequence[int], enemies: Sequence[str]) -> List[str | int]:
    combined: List[str | int] = list(players) + list(enemies)
    random.shuffle(combined)
    return combined


def start_encounter(encounter: CombatEncounter) -> None:
    encounter.current_turn = 0


def next_turn(encounter: CombatEncounter) -> int:
    encounter.current_turn = (encounter.current_turn + 1) % len(encounter.turn_order)
    return encounter.turn_order[encounter.current_turn]


def timestamp() -> float:
    return time.time()


class BondMissionResult(NamedTuple):
    mission_key: str
    player_new_techniques: List[str]
    partner_new_techniques: List[str]
    player_new_skills: List[str]
    partner_new_skills: List[str]
    player_updated: bool
    partner_updated: bool
    already_completed: bool


def complete_bond_mission(
    state: "GameState",
    player: PlayerProgress,
    partner: PlayerProgress,
    bond: BondProfile,
    *,
    now: float | None = None,
) -> BondMissionResult:
    """Resolve the co-operative mission rewards for a bond."""

    mission_key = str(bond.bond_mission_key or "").strip().lower()
    if not mission_key:
        raise ValueError("Bond mission key is not configured")

    timestamp = time.time() if now is None else float(now)
    if timestamp <= 0:
        timestamp = time.time()

    player_completed = mission_key in player.bond_missions
    partner_completed = mission_key in partner.bond_missions
    already_completed = player_completed and partner_completed

    player_updated = False
    partner_updated = False
    player_new_techniques: List[str] = []
    partner_new_techniques: List[str] = []
    player_new_skills: List[str] = []
    partner_new_skills: List[str] = []

    def grant_rewards(
        progress: PlayerProgress,
        technique_key: str,
        technique_targets: List[str],
        skill_targets: List[str],
    ) -> bool:
        updated = False
        normalized = str(technique_key).strip()
        if not normalized:
            return False
        technique = state.cultivation_techniques.get(normalized)
        if normalized not in progress.cultivation_technique_keys:
            progress.cultivation_technique_keys.append(normalized)
            technique_targets.append(normalized)
            updated = True
        if technique:
            for raw_skill in technique.skills:
                skill_key = str(raw_skill).strip()
                if not skill_key:
                    continue
                if skill_key not in progress.skill_proficiency:
                    progress.skill_proficiency[skill_key] = 0
                    skill_targets.append(skill_key)
                    updated = True
        return updated

    for technique_key in bond.bond_soul_techniques:
        if grant_rewards(player, technique_key, player_new_techniques, player_new_skills):
            player_updated = True
        if grant_rewards(partner, technique_key, partner_new_techniques, partner_new_skills):
            partner_updated = True

    if not player_completed:
        player.bond_missions[mission_key] = timestamp
        player_updated = True
    if not partner_completed:
        partner.bond_missions[mission_key] = timestamp
        partner_updated = True

    return BondMissionResult(
        mission_key,
        player_new_techniques,
        partner_new_techniques,
        player_new_skills,
        partner_new_skills,
        player_updated,
        partner_updated,
        already_completed,
    )


__all__ = [
    "GameState",
    "attempt_breakthrough",
    "add_item_to_inventory",
    "remove_item_from_inventory",
    "award_loot",
    "DamageResolution",
    "BaseExpGain",
    "build_turn_order",
    "can_evolve_skill",
    "create_party",
    "cultivation_ready",
    "complete_bond_mission",
    "inventory_capacity",
    "inventory_load",
    "gain_combat_exp",
    "gain_soul_exp",
    "gain_skill_proficiency",
    "BondMissionResult",
    "join_party",
    "leave_party",
    "next_turn",
    "perform_cultivation",
    "resolve_damage",
    "start_encounter",
    "timestamp",
]
