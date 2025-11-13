from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

PROJECT_BASE = Path(__file__).resolve().parents[1]
if str(PROJECT_BASE) not in sys.path:
    sys.path.insert(0, str(PROJECT_BASE))

from bot.cogs.player import talent_field_entries
from bot.energy import ENERGY_DAILY_REFILL_SECONDS, REALM_ENERGY_CAPS
from bot.game import GameState
from bot.models.map import TileCoordinate
from bot.models.players import PlayerProgress
from bot.models.combat import Stats
from bot.models.progression import (
    CultivationPath,
    CultivationPhase,
    CultivationStage,
    DEFAULT_STAGE_BASE_STAT,
    MORTAL_REALM_KEY,
    MORTAL_REALM_NAME,
)
from bot.models.soul_land import MartialSoul
from bot.travel import TravelEngine


def _make_state_with_player(stage_key: str = "mortal") -> tuple[GameState, PlayerProgress]:
    state = GameState()
    state.ensure_default_stages()
    player = PlayerProgress(user_id=1, name="Tester", cultivation_stage=stage_key)
    state.register_player(player)
    return state, player


def test_mortal_stage_seeded_when_missing() -> None:
    state = GameState()

    qi_stage = CultivationStage(
        key="qi-condensation",
        name="Qi Condensation",
        success_rate=0.45,
        path=CultivationPath.QI.value,
        realm="Qi Condensation",
        phase=CultivationPhase.INITIAL.value,
        realm_order=0,
        step=1,
        exp_required=200,
    )
    state.register_stage(qi_stage)

    assert not any(stage.is_mortal for stage in state.qi_cultivation_stages.values())

    state.ensure_default_stages()

    mortal_stage = state.get_stage(MORTAL_REALM_KEY, CultivationPath.QI)
    assert mortal_stage is not None
    assert mortal_stage.is_mortal

    ordered = sorted(
        state.qi_cultivation_stages.values(), key=lambda stage: stage.ordering_tuple
    )
    assert ordered[0].key == MORTAL_REALM_KEY


def test_energy_scales_with_realm() -> None:
    state, player = _make_state_with_player()

    initial_cap = state.ensure_player_energy(player, now=0.0, force_refill=True)
    expected_initial = REALM_ENERGY_CAPS["mortal"]
    assert initial_cap == pytest.approx(expected_initial)
    assert player.energy == pytest.approx(expected_initial)

    player.cultivation_stage = "nascent-soul"
    advanced_cap = state.ensure_player_energy(player, now=0.0, force_refill=True)
    expected_advanced = REALM_ENERGY_CAPS["nascent soul"]
    assert advanced_cap == pytest.approx(expected_advanced)
    assert player.energy == pytest.approx(expected_advanced)
    assert advanced_cap > initial_cap


def test_travel_consumes_energy_and_blocks_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    state, player = _make_state_with_player()
    state.ensure_world_map()
    engine = TravelEngine(state)

    fixed_now = 1_000.0
    monkeypatch.setattr(time, "time", lambda: fixed_now)
    cap = state.ensure_player_energy(player, now=fixed_now, force_refill=True)

    destination = TileCoordinate(1, 0, 0)
    path, _ = engine.travel_to(player, destination)
    expected_remaining = cap - path.total_cost.stamina_cost
    assert player.energy == pytest.approx(expected_remaining)

    player.energy = 0.1
    player.last_energy_refresh = fixed_now
    with pytest.raises(ValueError):
        engine.travel_to(player, TileCoordinate(2, 0, 0))


def test_daily_energy_refill() -> None:
    state, player = _make_state_with_player()
    cap = state.ensure_player_energy(player, now=0.0, force_refill=True)

    player.energy = cap / 4
    player.last_energy_refresh = 0.0
    state.ensure_player_energy(player, now=ENERGY_DAILY_REFILL_SECONDS + 10.0)

    assert player.energy == pytest.approx(cap)


def test_mortal_combined_name_has_no_phase() -> None:
    state = GameState()
    state.ensure_default_stages()

    mortal_stage = state.get_stage(MORTAL_REALM_KEY, CultivationPath.QI)
    assert mortal_stage is not None
    assert mortal_stage.phase_display == ""
    assert mortal_stage.combined_name == MORTAL_REALM_NAME


def test_mortal_stats_mask_until_qi_stage() -> None:
    state = GameState()
    state.ensure_default_stages()

    mortal_stage = state.get_stage(MORTAL_REALM_KEY, CultivationPath.QI)
    qi_stage = state.get_stage("qi-condensation", CultivationPath.QI)

    assert mortal_stage is not None
    assert qi_stage is not None

    player = PlayerProgress(
        user_id=42,
        name="Prospect",
        cultivation_stage=MORTAL_REALM_KEY,
        innate_stats=Stats(strength=5, physique=7, agility=9),
    )

    player.recalculate_stage_stats(mortal_stage, None, None)
    assert player.stats.strength == pytest.approx(1.0)
    assert player.stats.physique == pytest.approx(1.0)
    assert player.stats.agility == pytest.approx(1.0)

    player.cultivation_stage = qi_stage.key
    player.recalculate_stage_stats(qi_stage, None, None)

    multiplier = qi_stage.base_stat / DEFAULT_STAGE_BASE_STAT
    expected_strength = multiplier * player.innate_stats.strength
    expected_physique = multiplier * player.innate_stats.physique
    expected_agility = multiplier * player.innate_stats.agility

    assert player.stats.strength == pytest.approx(expected_strength)
    assert player.stats.physique == pytest.approx(expected_physique)
    assert player.stats.agility == pytest.approx(expected_agility)


def test_martial_souls_hidden_until_qi_stage() -> None:
    state = GameState()
    state.ensure_default_stages()

    mortal_stage = state.get_stage(MORTAL_REALM_KEY, CultivationPath.QI)
    qi_stage = state.get_stage("qi-condensation", CultivationPath.QI)

    assert mortal_stage is not None
    assert qi_stage is not None

    player = PlayerProgress(
        user_id=77,
        name="Soulbound", 
        cultivation_stage=MORTAL_REALM_KEY,
    )
    player.martial_souls = [MartialSoul.default()]

    mortal_fields = talent_field_entries(
        player,
        _innate_min=None,
        innate_max=None,
        qi_stage=mortal_stage,
    )
    martial_field = next(
        field for field in mortal_fields if "martial" in field[0].lower()
    )
    assert (
        "Martial souls remain sealed until you reach Qi Condensation."
        in martial_field[1]
    )

    player.cultivation_stage = qi_stage.key
    qi_fields = talent_field_entries(
        player,
        _innate_min=None,
        innate_max=None,
        qi_stage=qi_stage,
    )
    qi_martial_field = next(
        field for field in qi_fields if "martial" in field[0].lower()
    )
    assert (
        "Martial souls remain sealed until you reach Qi Condensation."
        not in qi_martial_field[1]
    )
    assert player.martial_souls[0].name in qi_martial_field[1]
