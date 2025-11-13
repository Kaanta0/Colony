"""Tests covering encounter personalisation and player proximity logic."""

from __future__ import annotations

import random

from bot.game import GameState
from bot.models.map import TileCoordinate, TravelEvent, TravelEventQueue
from bot.models.players import PlayerProgress
from bot.travel import TravelEngine
from bot.travel.events import maybe_enqueue_innate_soul_mutation


def _make_player(user_id: int, name: str, coordinate: str = "0:0:0") -> PlayerProgress:
    return PlayerProgress(
        user_id=user_id,
        name=name,
        cultivation_stage="foundation-establishment",
        world_position=coordinate,
        energy=100.0,
    )


def test_player_rng_state_persists_and_is_player_specific() -> None:
    state = GameState()
    state.ensure_world_map()
    alpha = _make_player(1, "Alpha")
    beta = _make_player(2, "Beta")
    state.register_player(alpha)
    state.register_player(beta)
    engine = TravelEngine(state)

    with engine._player_rng(alpha) as rng_alpha:
        alpha_values = [rng_alpha.random() for _ in range(3)]

    assert isinstance(alpha.travel_rng_state, tuple)
    seed_alpha = alpha.travel_rng_seed
    assert seed_alpha is not None

    manual_rng = random.Random(seed_alpha)
    manual_rng.setstate(alpha.travel_rng_state)
    manual_next = manual_rng.random()

    with engine._player_rng(alpha) as rng_alpha_again:
        continued_value = rng_alpha_again.random()

    assert continued_value == manual_next

    with engine._player_rng(beta) as rng_beta:
        beta_values = [rng_beta.random() for _ in range(3)]

    assert alpha_values != beta_values


def test_proximity_detection_records_visibility() -> None:
    state = GameState()
    state.ensure_world_map()
    coordinate = TileCoordinate(0, 0, 0)
    alpha = _make_player(1, "Alpha", coordinate.to_key())
    beta = _make_player(2, "Beta", coordinate.to_key())
    state.register_player(alpha)
    state.register_player(beta)
    engine = TravelEngine(state)
    queue = TravelEventQueue()

    engine._handle_player_proximity(alpha, coordinate, queue)

    event = queue.pop()
    assert event is not None
    assert event.key.startswith("proximity:")
    assert "interaction_options" in event.data
    assert event.data["target_id"] == beta.user_id

    assert beta.user_id in alpha.visible_players
    assert alpha.user_id in beta.visible_players


def test_player_interaction_stubs_generate_events() -> None:
    state = GameState()
    state.ensure_world_map()
    coordinate = TileCoordinate(0, 0, 0)
    alpha = _make_player(1, "Alpha", coordinate.to_key())
    beta = _make_player(2, "Beta", coordinate.to_key())
    state.register_player(alpha)
    state.register_player(beta)
    engine = TravelEngine(state)

    attack_event = engine.handle_player_attack(alpha, beta.user_id)
    assert attack_event is not None
    assert attack_event.data["interaction"] == "attack/evade"

    trade_event = engine.handle_player_trade(alpha, beta.user_id)
    assert trade_event is not None
    assert trade_event.data["interaction"] == "trade"


def test_mutation_opportunity_enqueued_into_travel_queue() -> None:
    state = GameState()
    state.ensure_world_map()
    alpha = _make_player(1, "Alpha")
    state.register_player(alpha)
    engine = TravelEngine(state)
    queue = TravelEventQueue()
    event = TravelEvent(
        key="encounter:test",
        description="A strange bloom of qi floods the air.",
        data={"kind": "discovery"},
    )
    with engine._player_rng(alpha) as rng:
        rng.random = lambda: 0.0  # type: ignore[assignment]
        maybe_enqueue_innate_soul_mutation(queue, alpha, event, rng, now=0.0)

    mutation_event = queue.pop()
    assert mutation_event is not None
    assert mutation_event.key.startswith("soul:ring:")
    ring_payload = mutation_event.data.get("ring") if mutation_event.data else None
    assert isinstance(ring_payload, dict)
    assert ring_payload["martial_soul"] == alpha.get_active_martial_souls()[0].name
