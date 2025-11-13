"""Travel and exploration engine for the Heaven and Earth bot.

This module wires together the new world map structures with movement, event
resolution, encounter spawning, and player progression systems.  The
implementation focuses on being declarative and data-driven so it can be hooked
into Discord commands later.  Every major design bullet point from the
exploration overhaul is represented through the classes below.
"""

from __future__ import annotations

import heapq
import math
import random
import time
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import count
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from ..models.map import (
    CampAction,
    EncounterBucket,
    EncounterKind,
    EncounterRoll,
    EncounterWeightProfile,
    ExpeditionOutcome,
    ExpeditionPlan,
    ExpeditionStep,
    ExpeditionStepType,
    FogOfWarState,
    ForageAction,
    MovementCostBreakdown,
    NoiseMeter,
    Region,
    RegionEscalationTrack,
    SetTrapAction,
    ScoutAction,
    SpawnContext,
    ScheduledTravelTask,
    TravelJournalEntry,
    Tile,
    TileCategory,
    TileCoordinate,
    TileHeatTracker,
    TravelEvent,
    TravelEventQueue,
    TravelEventScheduler,
    TravelMasteryNode,
    TravelMode,
    TravelPath,
    TravelReward,
    TravelSegment,
    TraverseAction,
    Waypoint,
    WorldMap,
    neighbours_for,
    heuristic_cost,
    encode_segment_key,
    manhattan_distance,
)
from ..models.players import PlayerProgress
from .events import maybe_enqueue_innate_soul_mutation

try:  # pragma: no cover - optional import for type checking
    from typing import TYPE_CHECKING
except ImportError:  # pragma: no cover - Python < 3.11 fallback
    TYPE_CHECKING = False  # type: ignore

if TYPE_CHECKING:  # pragma: no cover - imported lazily to avoid cycles
    from ..game import GameState
    from ..models.world import Location


# ---------------------------------------------------------------------------
# Movement modelling
# ---------------------------------------------------------------------------


class MovementCostModel:
    """Calculates the cost of moving through a tile."""

    def __init__(self, base_speed: float = 4.0) -> None:
        self.base_speed = base_speed

    def compute_step(
        self,
        tile: Tile,
        mode: TravelMode,
        *,
        inventory_load: float = 0.0,
        mastery_bonus: float = 0.0,
        noise_meter: Optional[NoiseMeter] = None,
    ) -> MovementCostBreakdown:
        traversal = max(0.1, tile.traversal_difficulty)
        base_seconds = self.base_speed * traversal
        base_seconds *= 1 + tile.environmental.hazard_level
        if inventory_load:
            base_seconds *= 1 + inventory_load * 0.2
        if mastery_bonus:
            base_seconds *= max(0.1, 1 - mastery_bonus)

        stamina_cost = traversal
        corruption = tile.environmental.corruption_level * traversal * 0.1
        noise = 0.25 + tile.environmental.hazard_level * 0.1

        if noise_meter is not None:
            noise_meter.add(noise)

        return MovementCostBreakdown(
            time_seconds=base_seconds,
            stamina_cost=stamina_cost,
            resource_cost={},
            noise_generated=noise,
            corruption_exposure=corruption,
        )


# ---------------------------------------------------------------------------
# Pathfinding
# ---------------------------------------------------------------------------


class Pathfinder:
    """A* pathfinder operating on the layered tile grid."""

    def __init__(self, world_map: WorldMap) -> None:
        self.world_map = world_map

    def heuristic(self, current: TileCoordinate, goal: TileCoordinate) -> float:
        return heuristic_cost(current, goal)

    def tile_for(self, coordinate: TileCoordinate) -> Optional[Tile]:
        return self.world_map.tile_for_key(coordinate.to_key())

    def find_path(
        self,
        start: TileCoordinate,
        goal: TileCoordinate,
        *,
        mode: TravelMode,
    ) -> List[TileCoordinate]:
        open_set: List[Tuple[float, int, TileCoordinate]] = []
        tie_breaker = count()
        start_key = start.to_key()
        g_score: Dict[str, float] = {start_key: 0.0}
        f_score: Dict[str, float] = {start_key: self.heuristic(start, goal)}
        came_from: Dict[str, TileCoordinate] = {}
        heapq.heappush(open_set, (f_score[start_key], next(tie_breaker), start))
        closed: Set[str] = set()

        while open_set:
            _, _, current = heapq.heappop(open_set)
            if current == goal:
                return self._reconstruct_path(came_from, current)
            current_key = current.to_key()
            if current_key in closed:
                continue
            closed.add(current_key)
            for neighbour in neighbours_for(current):
                tile = self.tile_for(neighbour)
                if tile is None or tile.traversal_difficulty <= 0:
                    continue
                traversal = max(0.1, tile.traversal_difficulty)
                tentative = g_score[current_key] + traversal
                neighbour_key = neighbour.to_key()
                if tentative >= g_score.get(neighbour_key, math.inf):
                    continue
                came_from[neighbour_key] = current
                g_score[neighbour_key] = tentative
                f_score[neighbour_key] = tentative + self.heuristic(neighbour, goal)
                heapq.heappush(
                    open_set,
                    (f_score[neighbour_key], next(tie_breaker), neighbour),
                )

        return []

    def _reconstruct_path(
        self, came_from: Dict[str, TileCoordinate], current: TileCoordinate
    ) -> List[TileCoordinate]:
        path: List[TileCoordinate] = [current]
        while current.to_key() in came_from:
            current = came_from[current.to_key()]
            path.append(current)
        return list(reversed(path))


# ---------------------------------------------------------------------------
# Encounter resolution
# ---------------------------------------------------------------------------


class EncounterResolver:
    """Produces encounter rolls based on spawn context and heat tracking."""

    def __init__(self, heat_tracker: TileHeatTracker | None = None) -> None:
        self.heat_tracker = heat_tracker or TileHeatTracker()

    def _base_weights(self, tile: Tile) -> EncounterWeightProfile:
        profile = EncounterWeightProfile()
        weights = profile.weights
        weights[EncounterBucket.AMBIENT_FAUNA] = 1.0
        weights[EncounterBucket.ENVIRONMENTAL_HAZARD] = tile.environmental.hazard_level
        if tile.category is TileCategory.POINT_OF_INTEREST:
            weights[EncounterBucket.ROGUE_CULTIVATOR] = 0.6
            weights[EncounterBucket.FACTION_AGENT] = max(
                weights.get(EncounterBucket.FACTION_AGENT, 0.0),
                0.45,
            )
        else:
            weights[EncounterBucket.HOSTILE_PATROL] = 0.6
        if tile.ownership.influence > 0.6:
            weights[EncounterBucket.FACTION_AGENT] = max(
                weights.get(EncounterBucket.FACTION_AGENT, 0.0),
                tile.ownership.influence,
            )
        if tile.dynamic.heat > 3:
            weights[EncounterBucket.WORLD_BOSS_SCOUT] = tile.dynamic.heat * 0.2
        return profile

    def _adjust_for_context(
        self, profile: EncounterWeightProfile, context: SpawnContext
    ) -> EncounterWeightProfile:
        weights = profile.weights
        noise = context.noise_level
        weights[EncounterBucket.HOSTILE_PATROL] += noise * 0.5
        weights[EncounterBucket.FACTION_AGENT] += context.player_notoriety * 0.3
        if context.tile.environmental.corruption_level > 0.5:
            weights[EncounterBucket.ENVIRONMENTAL_HAZARD] += 0.5
        for quest in context.active_quests:
            if "beast" in quest:
                weights[EncounterBucket.AMBIENT_FAUNA] += 0.2
        heat_penalty = self.heat_tracker.heat.get(context.tile.coordinate.to_key(), 0.0)
        if heat_penalty:
            weights[EncounterBucket.AMBIENT_FAUNA] *= max(0.1, 1 - heat_penalty * 0.1)
        return profile

    def _resolve_result(
        self,
        bucket: EncounterBucket,
        context: SpawnContext,
        location: "Location | None",
        rng: random.Random,
    ) -> tuple[EncounterKind, str, Dict[str, Any], List[str]]:
        tile = context.tile
        description = self._describe(bucket, context)
        kind = EncounterKind.COMBAT
        payload: Dict[str, Any] = {}
        options: Optional[List[str]] = None

        if bucket is EncounterBucket.AMBIENT_FAUNA:
            if location and location.wander_loot and rng.random() < 0.5:
                kind = EncounterKind.LOOT
                description = (
                    f"The underbrush around {location.name} hides promising spoils."
                )
                payload["loot_table"] = list(location.wander_loot.keys())
                options = []
            elif tile.points_of_interest:
                kind = EncounterKind.DISCOVERY
                point = rng.choice(tile.points_of_interest)
                payload["point_of_interest"] = point
                description = f"You uncover fresh insights about {point}."
                options = []
        elif bucket is EncounterBucket.HOSTILE_PATROL:
            if location and location.quests and rng.random() < 0.35:
                kind = EncounterKind.DISCOVERY
                quest_key = rng.choice(location.quests)
                payload["quest_key"] = quest_key
                description = (
                    "Trail signs hint at objectives tied to ongoing quests."
                )
                options = []
        elif bucket is EncounterBucket.FACTION_AGENT:
            if location and location.npcs and rng.random() < 0.45:
                npc = rng.choice(location.npcs)
                kind = EncounterKind.NPC
                description = (
                    f"{npc.name} from {location.name} intercepts you with new intelligence."
                )
                payload = {
                    "npc_name": npc.name,
                    "npc_type": npc.npc_type.name.lower()
                    if npc.npc_type is not None
                    else None,
                }
                if npc.dialogue:
                    payload["dialogue"] = npc.dialogue
                if npc.reference:
                    payload["reference"] = npc.reference
                if npc.shop_items:
                    payload["shop_items"] = list(npc.shop_items)
                options = []
        elif bucket is EncounterBucket.ENVIRONMENTAL_HAZARD:
            kind = EncounterKind.HAZARD
            payload = {
                "weather": context.weather,
                "hazard_level": tile.environmental.hazard_level,
            }
            options = ["Brace", "Evade", "Cleanse"]

        if options is None:
            options = self._options(bucket)

        return kind, description, payload, options

    def roll(
        self,
        context: SpawnContext,
        location: "Location | None" = None,
        *,
        rng: random.Random | None = None,
    ) -> Optional[EncounterRoll]:
        rng = rng or random
        profile = self._adjust_for_context(self._base_weights(context.tile), context)
        normalized = profile.normalized()
        threshold = rng.random()
        cumulative = 0.0
        for bucket, weight in normalized.items():
            cumulative += weight
            if threshold <= cumulative and weight > 0:
                kind, description, payload, options = self._resolve_result(
                    bucket, context, location, rng
                )
                tile = context.tile
                if kind is EncounterKind.COMBAT:
                    danger = weight * (1 + tile.dynamic.heat * 0.2)
                    heat_gain = 1.0
                elif kind is EncounterKind.HAZARD:
                    danger = max(0.2, tile.environmental.hazard_level * 1.5)
                    heat_gain = 0.75
                else:
                    ambient = tile.environmental.hazard_level * 0.1
                    notoriety = context.player_notoriety * 0.05
                    danger = max(0.05, ambient + notoriety)
                    heat_gain = 0.4
                self.heat_tracker.bump(tile.coordinate.to_key(), heat_gain)
                tile.dynamic.heat = min(10.0, tile.dynamic.heat + heat_gain * 0.5)
                return EncounterRoll(
                    bucket=bucket,
                    description=description,
                    danger_rating=danger,
                    preemptive_options=options,
                    kind=kind,
                    payload=payload,
                )
        return None

    def _describe(self, bucket: EncounterBucket, context: SpawnContext) -> str:
        tile_type = (
            "point of interest"
            if context.tile.category is TileCategory.POINT_OF_INTEREST
            else "area"
        )
        if bucket is EncounterBucket.AMBIENT_FAUNA:
            return f"The {tile_type} hums with curious spirit beasts."
        if bucket is EncounterBucket.HOSTILE_PATROL:
            return "A hostile patrol blocks the path, drawn by recent disturbances."
        if bucket is EncounterBucket.ROGUE_CULTIVATOR:
            return "A rogue cultivator lurks nearby, eyeing your valuables."
        if bucket is EncounterBucket.FACTION_AGENT:
            return "Faction agents establish a checkpoint demanding tribute."
        if bucket is EncounterBucket.WORLD_BOSS_SCOUT:
            return "A scout of a looming world boss surveys the area."
        if bucket is EncounterBucket.ENVIRONMENTAL_HAZARD:
            return "The environment lashes out—poisonous miasma swirls."
        return "The winds whisper of unforeseen trouble."

    def _options(self, bucket: EncounterBucket) -> List[str]:
        if bucket is EncounterBucket.ENVIRONMENTAL_HAZARD:
            return ["Brace", "Evade", "Cleanse"]
        if bucket is EncounterBucket.HOSTILE_PATROL:
            return ["Ambush", "Sneak", "Parley"]
        if bucket is EncounterBucket.ROGUE_CULTIVATOR:
            return ["Challenge", "Bargain", "Avoid"]
        return ["Advance", "Observe"]


# ---------------------------------------------------------------------------
# Travel engine
# ---------------------------------------------------------------------------


class TravelEngine:
    """Coordinates travel actions, event generation, and progression."""

    def __init__(self, state: "GameState") -> None:
        self.state = state
        self.cost_model = MovementCostModel()
        self.encounter_resolver = EncounterResolver(state.tile_heat_tracker)

    @contextmanager
    def _player_rng(self, player: PlayerProgress):
        """Yield a deterministic RNG tied to ``player`` and persist its state."""

        seed = player.travel_rng_seed
        if seed is None:
            seed = (player.user_id * 6364136223846793005 + 1442695040888963407) & (
                (1 << 64) - 1
            )
            player.travel_rng_seed = seed
        rng = random.Random(seed)
        state = player.travel_rng_state
        if state is not None:
            try:
                rng.setstate(state)
            except (TypeError, ValueError):
                rng.seed(seed)
        try:
            yield rng
        finally:
            player.travel_rng_state = rng.getstate()

    @property
    def world_map(self) -> WorldMap:
        if self.state.world_map is None:
            raise RuntimeError("World map not initialised")
        return self.state.world_map

    def travel_to(
        self,
        player: PlayerProgress,
        destination: TileCoordinate,
        *,
        mode: Optional[TravelMode] = None,
        journal: bool = True,
    ) -> Tuple[TravelPath, TravelEventQueue]:
        """Compute a path and resolve travel events towards a destination."""

        mode = mode or player.active_travel_mode
        energy_cap = self.state.ensure_player_energy(player)
        start = player.tile_coordinate()
        if start is None:
            start_key = player.world_position or "0:0:0"
            try:
                start = TileCoordinate.from_key(start_key)
            except ValueError:
                start = TileCoordinate(0, 0, 0)
            player.world_position = start.to_key()
        pathfinder = Pathfinder(self.world_map)
        coordinates = pathfinder.find_path(start, destination, mode=mode)
        if not coordinates:
            raise ValueError("No traversable path found")
        segments = self._build_segments(coordinates, mode, player)
        total_cost = self._aggregate_costs(segments)
        required_energy = max(0.0, float(total_cost.stamina_cost))
        available_energy = float(player.energy or 0.0)
        if required_energy > available_energy + 1e-6:
            raise ValueError(
                "You are too exhausted to travel that far. "
                f"{required_energy:.1f} energy is required but you only have {available_energy:.1f}. "
                "Rest to restore your strength."
            )
        if required_energy > 0 and not player.consume_energy(required_energy):
            raise ValueError(
                "You are too exhausted to travel that far. "
                f"{required_energy:.1f} energy is required but you only have {available_energy:.1f}."
            )
        remaining_energy = float(player.energy or 0.0)
        travel_path = TravelPath(segments, total_cost)
        queue = self._resolve_segments(player, segments)
        if required_energy > 0:
            queue.push(
                TravelEvent(
                    key="travel:energy",
                    description=(
                        f"Expended {required_energy:.1f} energy. "
                        f"{remaining_energy:.1f}/{energy_cap:.1f} energy remains."
                    ),
                    data={
                        "energy_spent": required_energy,
                        "energy_remaining": remaining_energy,
                        "energy_capacity": energy_cap,
                    },
                )
            )
        self._sync_player_location(player, destination)
        self._sync_waypoints(player, destination)
        if journal:
            self._record_journal_entry(
                player,
                destination,
                f"Traveled to {destination.to_key()}.",
                [],
            )
        return travel_path, queue

    def plan_expedition(
        self,
        player: PlayerProgress,
        destination: TileCoordinate,
        *,
        mode: Optional[TravelMode] = None,
        support_actions: Optional[Sequence[str]] = None,
        objectives: Optional[Sequence[str]] = None,
    ) -> ExpeditionPlan:
        """Create a multi-step expedition plan without mutating player state."""

        mode = mode or player.active_travel_mode
        self.state.ensure_player_energy(player)
        start = player.tile_coordinate()
        if start is None:
            start_key = player.world_position or "0:0:0"
            try:
                start = TileCoordinate.from_key(start_key)
            except ValueError:
                start = TileCoordinate(0, 0, 0)
        pathfinder = Pathfinder(self.world_map)
        coordinates = pathfinder.find_path(start, destination, mode=mode)
        if not coordinates:
            raise ValueError("No traversable path found")
        noise_snapshot = player.travel_noise.clone()
        segments = self._build_segments(
            coordinates,
            mode,
            player,
            noise_meter=noise_snapshot,
        )
        total_cost = self._aggregate_costs(segments)
        travel_step = ExpeditionStep(
            ExpeditionStepType.TRAVEL,
            destination,
            f"Traverse {max(0, len(coordinates) - 1)} tiles.",
            mode,
            data={"tile_count": max(0, len(coordinates) - 1)},
        )
        steps: List[ExpeditionStep] = [travel_step]
        desired_support = {action.strip().lower() for action in support_actions or () if action}
        dest_tile = self.world_map.tile_for_key(destination.to_key())
        fog = player.fog_of_war
        needs_scout = (
            "scout" in desired_support
            or (dest_tile is not None and destination.to_key() not in fog.surveyed_tiles)
        )
        if needs_scout:
            radius = 2 if "deep-scout" in desired_support else 1
            steps.append(
                ExpeditionStep(
                    ExpeditionStepType.SCOUT,
                    destination,
                    "Survey surrounding tiles for ambushes.",
                    data={"radius": radius, "stealth": 0.2},
                )
            )
        if dest_tile:
            hazard = dest_tile.environmental.hazard_level
            if "camp" in desired_support or hazard > 0.45:
                steps.append(
                    ExpeditionStep(
                        ExpeditionStepType.CAMP,
                        destination,
                        "Establish a protective warded camp.",
                        data={"duration": 360.0, "warding": 0.5},
                    )
                )
            if "forage" in desired_support or dest_tile.points_of_interest:
                steps.append(
                    ExpeditionStep(
                        ExpeditionStepType.FORAGE,
                        destination,
                        "Harvest local spirit resources.",
                        data={"yield": 1.0 + len(dest_tile.points_of_interest) * 0.25},
                    )
                )
            if "set_trap" in desired_support or hazard > 0.4:
                steps.append(
                    ExpeditionStep(
                        ExpeditionStepType.SET_TRAP,
                        destination,
                        "Prepare a spirit snare to ambush pursuers.",
                        data={"trap": "spirit-snare", "potency": 0.6, "duration": 900.0},
                    )
                )
            if dest_tile.points_of_interest:
                steps.append(
                    ExpeditionStep(
                        ExpeditionStepType.OBSERVE,
                        destination,
                        "Observe nearby points of interest and record insights.",
                        data={"points": dest_tile.points_of_interest[:3]},
                    )
                )
            risk = hazard + dest_tile.dynamic.heat * 0.1
        else:
            risk = 0.2
        plan = ExpeditionPlan(
            destination=destination,
            steps=steps,
            estimated_cost=total_cost,
            risk_rating=risk,
            objectives=list(objectives or []),
        )
        return plan

    def execute_plan(self, player: PlayerProgress, plan: ExpeditionPlan) -> ExpeditionOutcome:
        """Execute an expedition plan and collect its results."""

        events: List[TravelEvent] = []
        starting_rewards = len(player.travel_mastery.pending_rewards)
        starting_milestones = set(player.exploration_milestones)
        travel_path: TravelPath | None = None
        for step in plan.steps:
            if step.step_type is ExpeditionStepType.TRAVEL:
                travel_path, queue = self.travel_to(
                    player,
                    step.coordinate,
                    mode=step.mode,
                    journal=False,
                )
                events.extend(self._drain_queue(queue))
            elif step.step_type is ExpeditionStepType.SCOUT:
                queue = self.execute_action(
                    ScoutAction(
                        actor_id=player.user_id,
                        start=step.coordinate,
                        radius=int(step.data.get("radius", 1)),
                        stealth_bonus=float(step.data.get("stealth", 0.0)),
                    )
                )
                events.extend(self._drain_queue(queue))
            elif step.step_type is ExpeditionStepType.CAMP:
                queue = self.execute_action(
                    CampAction(
                        actor_id=player.user_id,
                        start=step.coordinate,
                        duration_seconds=float(step.data.get("duration", 300.0)),
                        warding_strength=float(step.data.get("warding", 0.3)),
                    )
                )
                events.extend(self._drain_queue(queue))
            elif step.step_type is ExpeditionStepType.FORAGE:
                queue = self.execute_action(
                    ForageAction(
                        actor_id=player.user_id,
                        start=step.coordinate,
                        yield_expectation=float(step.data.get("yield", 1.0)),
                        stealth_modifier=float(step.data.get("stealth", 0.0)),
                    )
                )
                events.extend(self._drain_queue(queue))
            elif step.step_type is ExpeditionStepType.SET_TRAP:
                queue = self.execute_action(
                    SetTrapAction(
                        actor_id=player.user_id,
                        start=step.coordinate,
                        trap_key=str(step.data.get("trap", "snare")),
                        potency=float(step.data.get("potency", 0.5)),
                        duration_seconds=float(step.data.get("duration", 600.0)),
                    )
                )
                events.extend(self._drain_queue(queue))
            elif step.step_type is ExpeditionStepType.OBSERVE:
                points = ", ".join(step.data.get("points", [])) or "the surroundings"
                events.append(
                    TravelEvent(
                        key=f"observe:{step.coordinate.to_key()}",
                        description=f"You take time to observe {points}.",
                    )
                )
        pending_rewards = player.travel_mastery.pending_rewards[starting_rewards:]
        new_milestones = sorted(player.exploration_milestones - starting_milestones)
        outcome = ExpeditionOutcome(
            plan=plan,
            events=events,
            rewards=list(pending_rewards),
            milestones=new_milestones,
            travel_path=travel_path,
        )
        player.remember_expedition(outcome)
        event_descriptions = [event.description for event in events[:8]]
        summary = f"Expedition to {plan.destination.to_key()} completed."
        self._record_journal_entry(player, plan.destination, summary, event_descriptions)
        return outcome

    def random_wander_plan(
        self, player: PlayerProgress, *, max_distance: int = 3
    ) -> ExpeditionPlan:
        """Create a spontaneous wandering expedition plan."""

        start = player.tile_coordinate()
        if start is None:
            try:
                start = TileCoordinate.from_key(player.world_position or "0:0:0")
            except ValueError:
                start = TileCoordinate(0, 0, 0)
        candidates: List[TileCoordinate] = []
        for tile in self.world_map.iter_tiles():
            if tile.coordinate == start:
                continue
            if manhattan_distance(tile.coordinate, start) <= max_distance:
                candidates.append(tile.coordinate)
        if not candidates:
            raise ValueError("No nearby tiles to wander towards.")
        with self._player_rng(player) as rng:
            destination = rng.choice(candidates)
            support_actions: List[str] = []
            if rng.random() < 0.7:
                support_actions.append("scout")
            if rng.random() < 0.6:
                support_actions.append("forage")
            if rng.random() < 0.45:
                support_actions.append("camp")
            if rng.random() < 0.25:
                support_actions.append("set_trap")
        return self.plan_expedition(
            player,
            destination,
            support_actions=support_actions,
            objectives=["Embrace wanderlust"],
        )

    def execute_action(
        self,
        action: TraverseAction
        | ScoutAction
        | CampAction
        | ForageAction
        | SetTrapAction,
    ) -> TravelEventQueue:
        """Resolve an arbitrary travel action and return resulting events."""

        queue = TravelEventQueue()
        player = self.state.players.get(action.actor_id)
        if player is None:
            return queue
        if isinstance(action, TraverseAction):
            destination = TileCoordinate(
                action.start.x,
                action.start.y + action.distance,
                action.start.z,
            )
            try:
                self.travel_to(player, destination, mode=action.mode)
            except ValueError as exc:
                queue.push(
                    TravelEvent(
                        key="travel:error",
                        description=str(exc),
                    )
                )
            else:
                queue.push(
                    TravelEvent(
                        key=encode_segment_key([action.start, destination], action.mode),
                        description=f"Traversed {action.distance} tiles.",
                    )
                )
        elif isinstance(action, ScoutAction):
            with self._player_rng(player) as rng:
                queue.push(self._handle_scout(player, action, rng=rng))
                self._maybe_queue_support_encounter(
                    queue, player, action.start, "scout", rng=rng
                )
        elif isinstance(action, CampAction):
            queue.push(self._handle_camp(player, action))
            with self._player_rng(player) as rng:
                self._maybe_queue_support_encounter(
                    queue, player, action.start, "camp", rng=rng
                )
        elif isinstance(action, ForageAction):
            with self._player_rng(player) as rng:
                queue.push(self._handle_forage(player, action, rng=rng))
                self._maybe_queue_support_encounter(
                    queue, player, action.start, "forage", rng=rng
                )
        elif isinstance(action, SetTrapAction):
            queue.push(self._handle_trap(player, action))
        return queue

    # ------------------------------------------------------------------
    # Segment handling
    # ------------------------------------------------------------------

    def _build_segments(
        self,
        coordinates: Sequence[TileCoordinate],
        mode: TravelMode,
        player: PlayerProgress,
        *,
        noise_meter: NoiseMeter | None = None,
    ) -> List[TravelSegment]:
        segments: List[TravelSegment] = []
        meter = noise_meter or player.travel_noise
        mastery_bonus = player.travel_mastery.level * 0.02
        if len(coordinates) < 2:
            return segments
        for previous, coordinate in zip(coordinates, coordinates[1:]):
            tile = self.world_map.tile_for_key(coordinate.to_key())
            if tile is None:
                continue
            cost = self.cost_model.compute_step(
                tile,
                mode,
                inventory_load=max(0.0, len(player.inventory) / 20.0),
                mastery_bonus=mastery_bonus,
                noise_meter=meter,
            )
            segment = TravelSegment([previous, coordinate], mode, cost)
            segments.append(segment)
        return segments

    def _aggregate_costs(self, segments: Sequence[TravelSegment]) -> MovementCostBreakdown:
        total_time = sum(segment.cost.time_seconds for segment in segments)
        total_stamina = sum(segment.cost.stamina_cost for segment in segments)
        total_noise = sum(segment.cost.noise_generated for segment in segments)
        total_corruption = sum(segment.cost.corruption_exposure for segment in segments)
        return MovementCostBreakdown(
            time_seconds=total_time,
            stamina_cost=total_stamina,
            resource_cost={},
            noise_generated=total_noise,
            corruption_exposure=total_corruption,
        )

    def _resolve_segments(
        self, player: PlayerProgress, segments: Sequence[TravelSegment]
    ) -> TravelEventQueue:
        queue = TravelEventQueue()
        now = time.time()
        for segment in segments:
            coordinate = segment.coordinates[-1]
            tile = self.world_map.tile_for_key(coordinate.to_key())
            if tile is None:
                continue
            player.travel_noise.tick(segment.cost.time_seconds)
            previous_position = player.world_position
            player.mark_tile_explored(coordinate, surveyed=True)
            self.state.update_player_position(
                player.user_id, previous_position, player.world_position
            )
            self._sync_player_location(player, coordinate)
            self._sync_waypoints(player, coordinate)
            phase_change, weather_changes, season_change = self.state.advance_world_time(
                segment.cost.time_seconds
            )
            if phase_change:
                queue.push(
                    TravelEvent(
                        key="time:phase",
                        description=(
                            f"Light shifts from {phase_change[0]} to {phase_change[1]} as the journey continues."
                        ),
                    )
                )
            if season_change:
                queue.push(
                    TravelEvent(
                        key=f"season:{season_change.key}",
                        description=f"The world enters {season_change.name}, altering regional weather.",
                    )
                )
            region = self._region_for_tile(tile)
            if region and region.region_id in weather_changes:
                weather_state = weather_changes[region.region_id]
                tile.environmental.weather = weather_state.pattern
                tile.environmental.hazard_level = max(
                    0.0,
                    min(
                        1.0,
                        tile.environmental.hazard_level
                        + (weather_state.intensity - 0.3) * (0.15 if weather_state.pattern in {"storm", "blizzard"} else 0.05),
                    ),
                )
                queue.push(
                    TravelEvent(
                        key=f"weather:{region.region_id}",
                        description=f"Weather shifts to {weather_state.pattern} over {region.name}.",
                        data={
                            "intensity": weather_state.intensity,
                            "temperature": weather_state.temperature,
                            "wind": weather_state.wind_speed,
                        },
                    )
                )
            location = self.state.location_for_tile(coordinate.to_key())
            if location:
                self._apply_location_effects(player, location, tile, queue)

            context = self._spawn_context_for(tile, player)
            with self._player_rng(player) as rng:
                encounter_triggered = False
                if self._should_roll_encounter(location, tile, context, player, rng=rng):
                    roll = self.encounter_resolver.roll(context, location, rng=rng)
                    if roll:
                        encounter_event = self._build_encounter_event(
                            roll,
                            location,
                            tile,
                            coordinate,
                            player,
                            rng=rng,
                        )
                        queue.push(encounter_event)
                        self._maybe_queue_innate_soul_mutation(
                            queue,
                            player,
                            encounter_event,
                            rng=rng,
                        )
                        encounter_triggered = True
                if not encounter_triggered:
                    self._maybe_queue_support_encounter(
                        queue, player, coordinate, "travel", rng=rng
                    )
                if not encounter_triggered and location:
                    self._maybe_surface_location_flavour(location, tile, queue, rng=rng)
            reward = TravelReward(
                exploration_xp=5,
                loot_keys=[f"resource:{node_key}" for node_key in tile.dynamic.active_nodes.keys()],
                milestone_name=f"tile:{coordinate.to_key()}",
            )
            player.grant_travel_reward(reward)
            tile.dynamic.noise = player.travel_noise.value
            self._advance_region_track(tile, now)
            self._handle_player_proximity(player, coordinate, queue)
        return queue

    def _spawn_context_for(
        self,
        tile: Tile,
        player: PlayerProgress,
        *,
        noise_level: float | None = None,
    ) -> SpawnContext:
        world_map = self.state.world_map
        time_of_day = world_map.clock.time_of_day() if world_map else "day"
        quests = set(getattr(player, "quests", ()))
        return SpawnContext(
            tile=tile,
            time_of_day=time_of_day,
            weather=tile.environmental.weather,
            player_notoriety=len(player.exploration_milestones) * 0.1,
            faction_alignment={},
            active_quests=quests,
            noise_level=noise_level if noise_level is not None else player.travel_noise.value,
        )

    def _should_roll_encounter(
        self,
        location: "Location | None",
        tile: Tile,
        context: SpawnContext,
        player: PlayerProgress,
        *,
        rng: random.Random,
    ) -> bool:
        if tile.is_safe:
            return False
        if location and location.is_safe:
            return False
        base = 0.25 + min(0.3, context.noise_level * 0.05)
        base += tile.environmental.hazard_level * 0.2
        if location:
            encounter_rate = float(location.encounter_rate or 0.0)
            base = max(base, encounter_rate)
        else:
            base += 0.05
        if player.travel_noise.value > 1.5:
            base += min(0.1, player.travel_noise.value * 0.02)
        return rng.random() <= min(0.95, base)

    def _maybe_queue_support_encounter(
        self,
        queue: TravelEventQueue,
        player: PlayerProgress,
        coordinate: TileCoordinate,
        action_kind: str,
        *,
        rng: random.Random,
    ) -> None:
        tile = self.world_map.tile_for_key(coordinate.to_key())
        if tile is None:
            return
        location = self.state.location_for_tile(coordinate.to_key())
        if tile.is_safe or (location and location.is_safe):
            return
        context = self._spawn_context_for(tile, player)
        base_map = {"scout": 0.18, "forage": 0.26, "camp": 0.2, "travel": 0.22}
        chance = base_map.get(action_kind, 0.15)
        if location:
            chance = max(chance, float(location.encounter_rate or 0.0))
        else:
            chance += 0.08
        chance += tile.environmental.hazard_level * 0.2
        chance += min(0.2, context.noise_level * 0.05)
        if rng.random() > min(0.95, chance):
            return
        roll = self.encounter_resolver.roll(context, location, rng=rng)
        if roll:
            encounter_event = self._build_encounter_event(
                roll,
                location,
                tile,
                coordinate,
                player,
                rng=rng,
            )
            queue.push(encounter_event)
            self._maybe_queue_innate_soul_mutation(
                queue,
                player,
                encounter_event,
                rng=rng,
            )

    def _maybe_queue_innate_soul_mutation(
        self,
        queue: TravelEventQueue,
        player: PlayerProgress,
        encounter_event: TravelEvent,
        *,
        rng: random.Random,
    ) -> None:
        maybe_enqueue_innate_soul_mutation(
            queue,
            player,
            encounter_event,
            rng,
            now=time.time(),
        )

    def _build_encounter_event(
        self,
        roll: EncounterRoll,
        location: "Location | None",
        tile: Tile,
        coordinate: TileCoordinate,
        player: PlayerProgress,
        *,
        rng: random.Random,
    ) -> TravelEvent:
        description = roll.description
        payload = dict(roll.payload)
        data: Dict[str, Any] = {
            "options": list(roll.preemptive_options),
            "danger": roll.danger_rating,
            "terrain": tile.category.value,
            "kind": roll.kind.value,
        }
        if payload:
            data["payload"] = payload
        if location:
            data["location"] = location.location_id

        location_label = location.name if location else coordinate.to_key()

        if roll.kind is EncounterKind.COMBAT:
            if location:
                if roll.bucket is EncounterBucket.WORLD_BOSS_SCOUT and location.bosses:
                    boss_key = rng.choice(location.bosses)
                    boss = self.state.bosses.get(boss_key)
                    if boss:
                        data["boss_key"] = boss.key
                        data["boss_name"] = boss.name
                        description = (
                            f"A herald of {boss.name} emerges within {location.name}. "
                            f"{roll.description}"
                        )
                else:
                    enemy_key = self._select_location_enemy(location, roll.bucket, rng=rng)
                    if enemy_key:
                        enemy = self.state.enemies.get(enemy_key)
                        if enemy:
                            data["enemy_key"] = enemy.key
                            data["enemy_name"] = enemy.name
                            description = (
                                f"{enemy.name} intercepts you near {location.name}. "
                                f"{roll.description}"
                            )
        elif roll.kind is EncounterKind.LOOT:
            loot_summary = self._resolve_loot_rewards(player, location, rng=rng)
            if loot_summary:
                obtained, skipped = loot_summary
                fragments: List[str] = []
                loot_data: Dict[str, Any] = {"obtained": obtained}
                for key, qty, kind in obtained:
                    if kind == "currency":
                        currency = self.state.currencies.get(key)
                        name = currency.name if currency else key
                    else:
                        item = self.state.items.get(key)
                        name = item.name if item else key
                    fragments.append(f"{qty}× {name}")
                if skipped:
                    loot_data["skipped"] = skipped
                data["loot"] = loot_data
                description = (
                    f"{description} You secure {', '.join(fragments)}."
                )
                if skipped:
                    description += " Some rewards could not be carried."
                haul = ", ".join(fragments)
                player.append_travel_log(
                    f"You comb the grounds around {location_label} and gather {haul}."
                )
            else:
                description = f"{description} The search yields nothing of value."
                player.append_travel_log(
                    f"You scour the surroundings of {location_label}, but the trail runs cold."
                )
        elif roll.kind is EncounterKind.NPC:
            npc_name = payload.get("npc_name") if payload else None
            if npc_name:
                player.append_travel_log(
                    f"You trade hushed words with {npc_name} near {location_label}."
                )
        elif roll.kind is EncounterKind.DISCOVERY:
            point = None
            if payload:
                point = payload.get("point_of_interest") or payload.get("quest_key")
                quest_key = payload.get("quest_key")
                if quest_key and quest_key in self.state.quests:
                    quest = self.state.quests[quest_key]
                    payload.setdefault("quest_name", quest.name)
                    description = (
                        f"{description} Leads toward the quest '{quest.name}' come into focus."
                    )
                    point = quest.name
            if point:
                player.append_travel_log(
                    f"You jot a field note about {point} beside {location_label}."
                )
            else:
                player.append_travel_log(
                    f"You sketch fresh observations about the terrain near {location_label}."
                )

        return TravelEvent(
            key=f"encounter:{coordinate.to_key()}",
            description=description,
            bucket=roll.bucket,
            data=data,
        )

    def _select_location_enemy(
        self, location: "Location", bucket: EncounterBucket, *, rng: random.Random
    ) -> Optional[str]:
        if location.enemies:
            return rng.choice(location.enemies)
        if bucket is EncounterBucket.FACTION_AGENT and location.npcs:
            # Return None to prefer narrative events when only NPCs are present.
            return None
        return None

    def _apply_location_effects(
        self,
        player: PlayerProgress,
        location: "Location",
        tile: Tile,
        queue: TravelEventQueue,
    ) -> None:
        if location.is_safe or tile.is_safe:
            max_hp, max_soul = player.max_health_caps()
            current_hp = player.current_hp if player.current_hp is not None else max_hp
            current_soul = (
                player.current_soul_hp if player.current_soul_hp is not None else max_soul
            )
            restored = False
            if current_hp < max_hp or current_soul < max_soul:
                player.restore_full_health()
                restored = True
            if player.travel_noise.value > 0:
                player.travel_noise.tick(180.0)
                restored = True
            if restored:
                queue.push(
                    TravelEvent(
                        key=f"rest:{location.location_id}",
                        description=(
                            f"The wards of {location.name} calm your meridians and restore your strength."
                        ),
                        data={"hp_full": True},
                    )
                )
                player.append_travel_log(
                    f"You bask in the wards of {location.name}, letting strength seep back into your meridians."
                )
            return

    def _resolve_loot_rewards(
        self,
        player: PlayerProgress,
        location: "Location | None",
        *,
        rng: random.Random,
    ) -> Optional[Tuple[List[Tuple[str, int, str]], List[Tuple[str, int, str]]]]:
        if location is None or not location.wander_loot:
            return None

        obtained: List[Tuple[str, int, str]] = []
        skipped: List[Tuple[str, int, str]] = []
        from .game import add_item_to_inventory

        for loot_key, drop in location.wander_loot.items():
            if rng.random() > drop.chance:
                continue
            amount = max(1, int(drop.amount))
            if (
                drop.kind == "currency"
                or (loot_key not in self.state.items and loot_key in self.state.currencies)
            ):
                player.currencies[loot_key] = player.currencies.get(loot_key, 0) + amount
                obtained.append((loot_key, amount, "currency"))
                continue
            added = add_item_to_inventory(player, loot_key, amount, self.state.items)
            if added > 0:
                obtained.append((loot_key, added, "item"))
            if added < amount:
                skipped.append((loot_key, amount - added, "item"))

        if not obtained:
            return None

        return obtained, skipped

    def _maybe_surface_location_flavour(
        self,
        location: "Location",
        tile: Tile,
        queue: TravelEventQueue,
        *,
        rng: random.Random,
    ) -> None:
        details: List[str] = []
        if location.quests:
            quest_names = [
                self.state.quests.get(key).name
                for key in location.quests
                if key in self.state.quests
            ][:2]
            if quest_names:
                details.append("Quests: " + ", ".join(quest_names))
        if location.npcs:
            npc_names = [npc.name for npc in location.npcs[:2]]
            if npc_names:
                details.append("Notable NPCs: " + ", ".join(npc_names))
        if not details or rng.random() > 0.65:
            return
        marker = f"flavour:{location.location_id}"
        if marker in tile.dynamic.recent_events:
            return
        tile.dynamic.recent_events.append(marker)
        queue.push(
            TravelEvent(
                key=marker,
                description=(
                    f"Insights from {location.name} reach you — " + " | ".join(details)
                ),
                data={"location": location.location_id},
            )
        )

    # ------------------------------------------------------------------
    # Additional actions
    # ------------------------------------------------------------------

    def _handle_scout(
        self, player: PlayerProgress, action: ScoutAction, *, rng: random.Random
    ) -> TravelEvent:
        radius = max(1, action.radius)
        discovered: List[str] = []
        origin_key = action.start.to_key()
        origin_tile = self.world_map.tile_for_key(origin_key)
        if origin_tile is None:
            return TravelEvent(key="scout:invalid", description="Scouting failed: invalid tile.")
        for neighbour in neighbours_for(action.start):
            tile = self.world_map.tile_for_key(neighbour.to_key())
            if tile is None:
                continue
            player.mark_tile_explored(neighbour, surveyed=True)
            discovered.append(neighbour.to_key())
        location = self.state.location_for_tile(origin_key)
        sightings: List[str] = []
        if location and location.enemies:
            for enemy_key in location.enemies[:3]:
                enemy = self.state.enemies.get(enemy_key)
                sightings.append(enemy.name if enemy else enemy_key)
        context = self._spawn_context_for(origin_tile, player)
        roll = self.encounter_resolver.roll(context, rng=rng)
        if roll:
            sightings.append(roll.description)
        player.travel_noise.add(max(0.0, 0.1 - action.stealth_bonus))
        if sightings:
            preview = "; ".join(sightings[:3])
            description = (
                f"Scouted radius {radius}, revealing {len(discovered)} tiles and spotting {preview}."
            )
        else:
            description = (
                f"Scouted radius {radius}, revealing {len(discovered)} tiles without detecting threats."
            )
        if sightings:
            if len(sightings) == 1:
                detail = sightings[0]
            else:
                detail = ", ".join(sightings[:-1]) + f", and {sightings[-1]}"
            log_entry = (
                f"You range around {origin_key}, marking hidden paths and noting {detail}."
            )
        else:
            log_entry = (
                f"You range around {origin_key}, but no hostile signatures stir the air."
            )
        player.append_travel_log(log_entry)
        return TravelEvent(
            key=f"scout:{origin_key}",
            description=description,
            data={"discoveries": discovered, "sightings": sightings},
        )

    def _handle_camp(self, player: PlayerProgress, action: CampAction) -> TravelEvent:
        max_hp, max_soul = player.max_health_caps()
        current_hp = player.current_hp if player.current_hp is not None else max_hp
        current_soul = (
            player.current_soul_hp if player.current_soul_hp is not None else max_soul
        )
        player.restore_full_health()
        player.travel_noise.tick(action.duration_seconds)
        restored_hp = max(0.0, max_hp - current_hp)
        restored_soul = max(0.0, max_soul - current_soul)
        if restored_hp or restored_soul:
            player.append_travel_log(
                "You settle into a wary camp, reclaiming "
                f"{restored_hp:.0f} vitality and {restored_soul:.0f} soul energy."
            )
        else:
            player.append_travel_log(
                "You keep a silent watch by the campfire, letting the stillness steady you."
            )
        if restored_hp or restored_soul:
            description = (
                f"A restorative camp replenishes {restored_hp:.0f} health and "
                f"{restored_soul:.0f} soul energy."
            )
        else:
            description = "A calm vigil keeps your strength steady as you rest."
        return TravelEvent(
            key=f"camp:{action.start.to_key()}",
            description=description,
            data={"restored_hp": restored_hp, "restored_soul": restored_soul},
        )

    def _handle_forage(
        self, player: PlayerProgress, action: ForageAction, *, rng: random.Random
    ) -> TravelEvent:
        tile = self.world_map.tile_for_key(action.start.to_key())
        if tile is None:
            return TravelEvent(key="forage:invalid", description="No resources to forage here.")
        base_yield = max(1.0, action.yield_expectation)
        quantity = max(1, int(round(base_yield * (1 + tile.environmental.qi_density * 0.1))))
        loot_table = [
            "Shimmering Herb",
            "Spirit Lotus",
            "Ancient Coin",
            "Runed Trinket",
            "Prismatic Shard",
        ]
        loot_labels = [rng.choice(loot_table) for _ in range(quantity)]
        loot_keys = [
            f"forage:{label.lower().replace(' ', '-')}:"
            f"{action.start.x}:{action.start.y}:{index}"
            for index, label in enumerate(loot_labels)
        ]
        reward = TravelReward(
            exploration_xp=quantity * 2,
            loot_keys=loot_keys,
        )
        player.grant_travel_reward(reward)
        noise_penalty = max(0.0, 0.2 - action.stealth_modifier)
        player.travel_noise.add(noise_penalty)
        tile_type = (
            "point of interest"
            if tile.category is TileCategory.POINT_OF_INTEREST
            else "tile"
        )
        description = (
            f"Foraged {quantity} find{'s' if quantity != 1 else ''} from the {tile_type}: "
            + ", ".join(loot_labels)
            + "."
        )
        tile.dynamic.recent_events.append("forage")
        gathered = ", ".join(loot_labels)
        player.append_travel_log(
            f"You sift through {action.start.to_key()}, gathering {gathered}."
        )
        return TravelEvent(
            key=f"forage:{tile.coordinate.to_key()}",
            description=description,
            data={"loot": loot_keys, "labels": loot_labels},
        )

    def _handle_trap(self, player: PlayerProgress, action: SetTrapAction) -> TravelEvent:
        tile = self.world_map.tile_for_key(action.start.to_key())
        if tile is None:
            return TravelEvent(key="trap:invalid", description="Trap fails to anchor without solid ground.")
        potency = max(0.1, action.potency)
        tile.dynamic.recent_events.append(f"trap:{action.trap_key}:{potency:.2f}")
        tile.dynamic.heat = max(0.0, tile.dynamic.heat - potency * 0.2)
        return TravelEvent(
            key=f"trap:{tile.coordinate.to_key()}",
            description=f"Set a {action.trap_key} trap, reducing local threats.",
            data={"potency": potency},
        )

    def handle_player_attack(
        self, player: PlayerProgress, target_id: int
    ) -> TravelEvent | None:
        target = self.state.players.get(target_id)
        if target is None:
            return TravelEvent(
                key="interaction:error",
                description="The targeted cultivator could not be located.",
                data={"reason": "missing"},
            )
        origin = player.tile_coordinate()
        destination = target.tile_coordinate()
        if origin is None or destination is None:
            return TravelEvent(
                key="interaction:error",
                description="Positions are unclear; the clash cannot begin.",
                data={"reason": "unknown_position"},
            )
        distance = manhattan_distance(origin, destination)
        if distance > 4:
            return TravelEvent(
                key="interaction:out_of_range",
                description=f"{target.name} is too far away to engage.",
                data={"distance": distance},
            )
        span = f"{distance} tile{'s' if distance != 1 else ''}"
        player.append_travel_log(
            f"You call out to {target.name}, challenging them from {span} away."
        )
        target.append_travel_log(
            f"{player.name} signals a duel from {span} in the distance."
        )
        return TravelEvent(
            key=f"interaction:attack:{player.user_id}:{target_id}",
            description=(
                f"You steel yourself to confront {target.name}. (Combat hook placeholder)"
            ),
            data={
                "interaction": "attack/evade",
                "target_id": target_id,
                "target_name": target.name,
                "distance": distance,
            },
        )

    def handle_player_trade(
        self, player: PlayerProgress, target_id: int
    ) -> TravelEvent | None:
        target = self.state.players.get(target_id)
        if target is None:
            return TravelEvent(
                key="interaction:error",
                description="Trading partner vanished into the mist.",
                data={"reason": "missing"},
            )
        origin = player.tile_coordinate()
        destination = target.tile_coordinate()
        if origin is None or destination is None:
            return TravelEvent(
                key="interaction:error",
                description="Neither of you can pinpoint the meeting ground.",
                data={"reason": "unknown_position"},
            )
        distance = manhattan_distance(origin, destination)
        if distance > 4:
            return TravelEvent(
                key="interaction:out_of_range",
                description=f"{target.name} is out of trading range.",
                data={"distance": distance},
            )
        span = f"{distance} tile{'s' if distance != 1 else ''}"
        player.append_travel_log(
            f"You signal {target.name} with an offer of trade from {span} away."
        )
        target.append_travel_log(
            f"{player.name} beckons you for trade from {span} across the wilds."
        )
        return TravelEvent(
            key=f"interaction:trade:{player.user_id}:{target_id}",
            description=(
                f"You propose an exchange with {target.name}. (Trading hook placeholder)"
            ),
            data={
                "interaction": "trade",
                "target_id": target_id,
                "target_name": target.name,
                "distance": distance,
            },
        )

    def _advance_region_track(self, tile: Tile, now: float) -> None:
        region = self._region_for_tile(tile)
        if not region:
            return
        track = self.state.region_tracks.setdefault(
            region.region_id,
            RegionEscalationTrack(region_id=region.region_id, progress=0.0, thresholds=[10, 25, 50]),
        )
        reached = track.advance(1.0)
        if reached:
            if region.conflict_timeline:
                region.current_escalation_index = min(
                    region.current_escalation_index + reached,
                    len(region.conflict_timeline) - 1,
                )
                stage = region.active_escalation_stage()
                if stage:
                    event = TravelEvent(
                        key=f"escalation:{region.region_id}:{stage.key}",
                        description=stage.description,
                        data={"modifiers": stage.spawn_table_modifiers},
                    )
                    self.state.travel_event_scheduler.schedule(
                        ScheduledTravelTask(execute_at=now + 5.0, event=event)
                    )

    def _region_for_tile(self, tile: Tile) -> Optional[Region]:
        for region in self.world_map.regions.values():
            for zone in region.zones.values():
                if tile.coordinate.to_key() in zone.tiles:
                    return region
        return None

    def _sync_waypoints(self, player: PlayerProgress, coordinate: TileCoordinate) -> None:
        for waypoint in self.world_map.waypoints_near(coordinate, radius=0):
            if waypoint.key not in player.known_waypoints:
                player.unlock_waypoint(waypoint.key)
                player.append_travel_log(
                    f"You mark a new waypoint in your journal: {waypoint.name}."
                )

    def _record_journal_entry(
        self,
        player: PlayerProgress,
        coordinate: TileCoordinate,
        summary: str,
        events: Sequence[str],
    ) -> None:
        tile = self.world_map.tile_for_key(coordinate.to_key())
        if tile is None:
            weather_pattern = "unknown"
            time_of_day = self.state.world_map.clock.time_of_day() if self.state.world_map else "day"
        else:
            weather = self.state.weather_for_tile(tile)
            weather_pattern = weather.pattern
            time_of_day = self.state.world_map.clock.time_of_day() if self.state.world_map else "day"
        entry = TravelJournalEntry(
            timestamp=time.time(),
            coordinate=coordinate,
            summary=summary,
            events=list(events),
            time_of_day=time_of_day,
            weather_pattern=weather_pattern,
        )
        player.record_journal_entry(entry)

    def _drain_queue(self, queue: TravelEventQueue) -> List[TravelEvent]:
        events: List[TravelEvent] = []
        while queue:
            event = queue.pop()
            if event is None:
                break
            events.append(event)
        return events

    def _sync_player_location(self, player: PlayerProgress, coordinate: TileCoordinate) -> None:
        key = coordinate.to_key()
        mapped = self.state.tile_locations.get(key)
        if mapped:
            player.location = mapped
            location = self.state.locations.get(mapped)
            if location and location.is_safe:
                player.last_safe_zone = mapped
        else:
            if mapped is None and player.location in self.state.tile_locations.values():
                player.location = None

    def _handle_player_proximity(
        self, player: PlayerProgress, coordinate: TileCoordinate, queue: TravelEventQueue
    ) -> None:
        radius = 4
        neighbours = self.state.players_within(
            coordinate, radius=radius, exclude=player.user_id
        )
        current_ids = {other.user_id for other in neighbours}
        previous_ids = set(player.visible_players.keys())
        stale_ids = previous_ids - current_ids
        for stale in stale_ids:
            player.visible_players.pop(stale, None)
            other_player = self.state.players.get(stale)
            if other_player:
                other_player.visible_players.pop(player.user_id, None)
        for other in neighbours:
            other_coord = other.tile_coordinate()
            if other_coord is None:
                continue
            distance = manhattan_distance(coordinate, other_coord)
            options = ["attack/evade", "trade"]
            info = {
                "name": other.name,
                "distance": distance,
                "coordinate": other_coord.to_key(),
                "options": options,
                "last_seen": time.time(),
            }
            existing = player.visible_players.get(other.user_id)
            player.visible_players[other.user_id] = dict(info)
            other.visible_players[player.user_id] = {
                "name": player.name,
                "distance": distance,
                "coordinate": coordinate.to_key(),
                "options": options,
                "last_seen": time.time(),
            }
            if not existing:
                plural = "s" if distance != 1 else ""
                description = (
                    f"{other.name} is within sight ({distance} tile{plural}). "
                    "Consider your next move."
                )
                event = TravelEvent(
                    key=f"proximity:{player.user_id}:{other.user_id}",
                    description=description,
                    data={
                        "interaction_options": options,
                        "target_id": other.user_id,
                        "target_name": other.name,
                        "distance": distance,
                    },
                )
                queue.push(event)
                player.append_travel_log(
                    f"You catch sight of {other.name} {distance} tile{plural} from {coordinate.to_key()}."
                )
                other.append_travel_log(
                    f"{player.name} drifts within {distance} tile{plural}, prowling the area."
                )


# ---------------------------------------------------------------------------
# Travel mastery utilities
# ---------------------------------------------------------------------------


def build_default_travel_mastery_tree() -> Dict[str, TravelMasteryNode]:
    """Create a baseline travel mastery tree covering mobility perks."""

    nodes = {
        "wind-step": TravelMasteryNode(
            key="wind-step",
            name="Wind Step",
            description="Unlocks a swift movement technique reducing traversal time.",
            prerequisites=[],
            bonuses={"time_multiplier": -0.1},
        ),
        "cloud-sailing": TravelMasteryNode(
            key="cloud-sailing",
            name="Cloud Sailing",
            description="Refines footwork to glide over terrain with minimal disturbance.",
            prerequisites=["wind-step"],
            bonuses={"time_multiplier": -0.05, "noise_multiplier": -0.1},
        ),
        "abyss-descent": TravelMasteryNode(
            key="abyss-descent",
            name="Abyss Descent",
            description="Improves vertical movement and grants hazard resistance underground.",
            prerequisites=["wind-step"],
            bonuses={"hazard_resistance": 0.2},
        ),
    }
    return nodes


# ---------------------------------------------------------------------------
# Mini map rendering
# ---------------------------------------------------------------------------


class MiniMapRenderer:
    """Renders ASCII mini-maps for Discord embeds."""

    NORMAL_GLYPH = "🟩"
    POI_GLYPH = "✴️"
    SAFE_GLYPH = "🏠"
    UNKNOWN_GLYPH = "⬛"

    def __init__(self, world_map: WorldMap) -> None:
        self.world_map = world_map

    def render(
        self,
        center: TileCoordinate,
        *,
        fog: Optional[FogOfWarState] = None,
        size: int = 14,
    ) -> str:
        """Render a square minimap centered on ``center``.

        The map highlights the player's tile with a location marker to make
        their position obvious at a glance.
        """

        fog = fog or FogOfWarState()
        dimension = max(1, size)
        half_width = dimension // 2
        half_height = dimension // 2
        x_min = center.x - half_width
        x_max = center.x + (dimension - half_width - 1)
        y_min = center.y - half_height
        y_max = center.y + (dimension - half_height - 1)

        rows: List[str] = []
        for y in range(y_max, y_min - 1, -1):
            glyphs: List[str] = []
            for x in range(x_min, x_max + 1):
                coordinate = TileCoordinate(x, y, center.z)
                key = coordinate.to_key()

                if coordinate == center:
                    glyphs.append("📍")
                    continue

                tile = self.world_map.tile_for_key(key)
                if tile is None:
                    glyphs.append(self.UNKNOWN_GLYPH)
                    continue
                if key not in fog.discovered_tiles:
                    glyphs.append(self.UNKNOWN_GLYPH)
                    continue
                if tile.is_safe:
                    glyphs.append(self.SAFE_GLYPH)
                elif tile.category is TileCategory.POINT_OF_INTEREST:
                    glyphs.append(self.POI_GLYPH)
                else:
                    glyphs.append(self.NORMAL_GLYPH)
            rows.append("".join(glyphs))
        return "\n".join(rows)


# ---------------------------------------------------------------------------
# Example travel flow for documentation and testing
# ---------------------------------------------------------------------------


def example_travel_turn_flow(engine: TravelEngine, player: PlayerProgress) -> List[str]:
    """Generate the narrative example described in the design proposal."""

    messages: List[str] = []
    start = player.tile_coordinate()
    if start is None:
        return ["Player has no starting tile."]
    wind_step_destination = TileCoordinate(start.x + 1, start.y + 1, start.z)
    path, queue = engine.travel_to(player, wind_step_destination, mode=TravelMode.MOVE)
    messages.append("Move northeast across gently rolling terrain.")
    while (event := queue.pop()) is not None:
        messages.append(event.description)
    messages.append("Noise attracts faction scout; stealth roll pending.")
    player.travel_mastery.add_experience(10)
    player.grant_travel_reward(
        TravelReward(exploration_xp=20, loot_keys=["hidden-relic"], milestone_name="hint:relic")
    )
    messages.append("Scouting perk reveals hidden relic coordinates two tiles away.")
    return messages

