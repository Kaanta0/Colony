import math
import sys
from pathlib import Path
from typing import Sequence

PROJECT_BASE = Path(__file__).resolve().parents[1]
if str(PROJECT_BASE) not in sys.path:
    sys.path.insert(0, str(PROJECT_BASE))

import pytest

from bot.game import attempt_breakthrough, perform_cultivation, resolve_damage
from bot.models.combat import (
    DamageType,
    Skill,
    SpiritualAffinity,
    WeaponType,
    resistance_reduction_fraction,
)
from bot.models.players import PlayerProgress
from bot.models.progression import CultivationPath, CultivationStage


@pytest.fixture
def baseline_player() -> PlayerProgress:
    player = PlayerProgress(
        user_id=100,
        name="Regression Tester",
        cultivation_stage="qi-condensation",
    )
    player.stats.strength = 5.0
    player.stats.physique = 3.0
    player.stats.agility = 2.0
    return player


def test_perform_cultivation_tracks_breakdown(baseline_player: PlayerProgress) -> None:
    innate_soul_grades = {soul.grade for soul in baseline_player.martial_souls}
    ranges = {grade: (1.0, 1.0) for grade in innate_soul_grades}

    breakdown = perform_cultivation(
        baseline_player,
        gain=10,
        multiplier_ranges=ranges,
        now=1234.5,
    )

    assert breakdown
    assert all(entry.exp == 10 for entry in breakdown)
    assert baseline_player.cultivation_exp == 10 * len(breakdown)
    assert pytest.approx(baseline_player.last_cultivate_ts) == 1234.5
    assert pytest.approx(baseline_player.last_train_ts) == 1234.5
    assert pytest.approx(baseline_player.last_temper_ts) == 1234.5


def test_attempt_breakthrough_handles_success_and_failure(
    monkeypatch: pytest.MonkeyPatch, baseline_player: PlayerProgress
) -> None:
    stage = CultivationStage(
        key="foundation-establishment",
        name="Foundation Establishment",
        success_rate=0.4,
        path=CultivationPath.QI.value,
        breakthrough_failure_loss=0.5,
    )
    baseline_player.cultivation_exp_required = 100
    baseline_player.cultivation_exp = 120

    monkeypatch.setattr("random.random", lambda: 0.1)
    assert attempt_breakthrough(baseline_player, stage, path=CultivationPath.QI.value)

    baseline_player.cultivation_exp = 120
    monkeypatch.setattr("random.random", lambda: 0.95)
    assert not attempt_breakthrough(baseline_player, stage, path=CultivationPath.QI.value)
    assert baseline_player.cultivation_exp == 60


def test_resolve_damage_applies_affinity_resistances(
    monkeypatch: pytest.MonkeyPatch, baseline_player: PlayerProgress
) -> None:
    skill = Skill(
        key="phoenix-strike",
        name="Phoenix Strike",
        grade="earth",
        skill_type=DamageType.PHYSICAL,
        damage_ratio=1.0,
        proficiency_max=100,
        element=SpiritualAffinity.FIRE,
    )
    baseline_player.skill_proficiency[skill.key] = 100

    stage = CultivationStage(
        key="qi-condensation",
        name="Qi Condensation",
        success_rate=1.0,
        path=CultivationPath.QI.value,
    )

    monkeypatch.setattr("random.uniform", lambda _a, _b: 1.0)
    resistances: Sequence[SpiritualAffinity] = (SpiritualAffinity.FIRE,)

    result = resolve_damage(
        baseline_player,
        defender_hp=40.0,
        defender_soul_hp=10.0,
        skill=skill,
        qi_stage=stage,
        body_stage=None,
        soul_stage=None,
        race=None,
        traits=[],
        items=None,
        resistances=resistances,
        weapon_types={WeaponType.BARE_HAND},
    )

    assert result.pool == "hp"

    innate = baseline_player.combined_innate_soul([])
    attack_elements = skill.elements or (() if skill.element is None else (skill.element,))
    multiplier = innate.damage_multiplier(attack_elements) if innate else 1.0
    martial_multiplier = baseline_player.martial_soul_damage_multiplier(
        skill, {WeaponType.BARE_HAND}
    )
    reduction = resistance_reduction_fraction(attack_elements, resistances)
    base_damage = skill.damage_for(baseline_player.stats.attacks, 100)
    expected_damage = (
        base_damage
        * multiplier
        * martial_multiplier
        * max(0.0, 1 - 0.25 * reduction)
    )
    expected_value = math.floor(expected_damage + 0.5)

    assert result.rolled == expected_value
    assert result.applied == expected_value
    assert result.minimum == math.floor(max(0.0, expected_damage * 0.8) + 0.5)
    assert result.maximum == math.floor(max(0.0, expected_damage * 1.2) + 0.5)
