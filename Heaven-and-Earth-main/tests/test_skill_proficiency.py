from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

PROJECT_BASE = Path(__file__).resolve().parents[1]
if str(PROJECT_BASE) not in sys.path:
    sys.path.insert(0, str(PROJECT_BASE))

from bot.cogs import combat
from bot.cogs.combat import CombatCog, FighterState
from bot.game import gain_skill_proficiency
from bot.models.combat import DamageType, Skill, Stats, WeaponType
from bot.models.players import PlayerProgress, PronounSet


def _make_skill(**overrides) -> Skill:
    params = dict(
        key="test-skill",
        name="Test Skill",
        grade="Common",
        skill_type=DamageType.PHYSICAL,
        damage_ratio=1.0,
        proficiency_max=3,
    )
    params.update(overrides)
    return Skill(**params)


def _make_player() -> PlayerProgress:
    return PlayerProgress(user_id=1, name="Tester", cultivation_stage="1")


def _make_fighter(player: PlayerProgress, skill: Skill, proficiency: int) -> FighterState:
    return FighterState(
        identifier=str(player.user_id),
        name=player.name,
        is_player=True,
        stats=Stats(),
        hp=20.0,
        max_hp=20.0,
        soul_hp=20.0,
        max_soul_hp=20.0,
        agility=10.0,
        skills=[skill],
        proficiency={skill.key: proficiency},
        resistances=[],
        player=player,
        qi_stage=None,
        body_stage=None,
        soul_stage=None,
        race=None,
        traits=[],
        items=[],
        weapon_types={WeaponType.BARE_HAND},
        passive_heals=[],
        pronouns=PronounSet.neutral(),
    )


def _make_enemy() -> FighterState:
    return FighterState(
        identifier="enemy",
        name="Enemy",
        is_player=False,
        stats=Stats(),
        hp=30.0,
        max_hp=30.0,
        soul_hp=30.0,
        max_soul_hp=30.0,
        agility=5.0,
        skills=[],
        proficiency={},
        resistances=[],
        player=None,
        qi_stage=None,
        body_stage=None,
        soul_stage=None,
        race=None,
        traits=[],
        items=[],
        weapon_types={WeaponType.BARE_HAND},
        passive_heals=[],
        pronouns=PronounSet.neutral(),
    )


def _make_cog() -> CombatCog:
    cog = CombatCog.__new__(CombatCog)
    cog.config = SimpleNamespace()
    return cog


def test_gain_skill_proficiency_increments_and_caps() -> None:
    player = _make_player()
    skill = _make_skill(proficiency_max=2)

    gain_skill_proficiency(player, skill)
    assert player.skill_proficiency[skill.key] == 1

    gain_skill_proficiency(player, skill)
    assert player.skill_proficiency[skill.key] == 2

    gain_skill_proficiency(player, skill)
    assert player.skill_proficiency[skill.key] == 2


def test_skill_activation_chance_doubles_at_max_proficiency(monkeypatch: pytest.MonkeyPatch) -> None:
    player = _make_player()
    skill = _make_skill(trigger_chance=0.4, proficiency_max=4)

    monkeypatch.setattr(combat.random, "random", lambda: 0.5)

    cog = _make_cog()

    below_cap = _make_fighter(player, skill, proficiency=2)
    assert cog._attempt_skill(below_cap) is None

    at_cap = _make_fighter(player, skill, proficiency=4)
    assert cog._attempt_skill(at_cap) is skill


def test_apply_damage_advances_player_proficiency(monkeypatch: pytest.MonkeyPatch) -> None:
    player = _make_player()
    skill = _make_skill(proficiency_max=5)
    attacker = _make_fighter(player, skill, proficiency=0)
    defender = _make_enemy()

    cog = _make_cog()
    monkeypatch.setattr(cog, "_player_skill_attack", lambda *args, **kwargs: (5.0, "hp"))

    damage, pool = cog._apply_damage(attacker, defender, skill)

    assert damage == 5.0
    assert pool == "hp"
    assert player.skill_proficiency[skill.key] == 1
    assert attacker.proficiency[skill.key] == 1
