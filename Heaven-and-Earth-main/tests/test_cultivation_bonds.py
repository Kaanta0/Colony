import sys
from pathlib import Path

import pytest

PROJECT_BASE = Path(__file__).resolve().parents[1]
if str(PROJECT_BASE) not in sys.path:
    sys.path.insert(0, str(PROJECT_BASE))

from bot.game import GameState, complete_bond_mission
from bot.models.players import BondProfile, PlayerProgress
from bot.models.progression import CultivationPath, CultivationTechnique


def _make_players(stage_key: str) -> tuple[PlayerProgress, PlayerProgress]:
    player = PlayerProgress(user_id=1, name="Alpha", cultivation_stage=stage_key)
    partner = PlayerProgress(user_id=2, name="Beta", cultivation_stage=stage_key)
    return player, partner


def test_bond_bonus_applies_when_requirements_met() -> None:
    state = GameState()
    state.ensure_default_stages()

    bond = BondProfile(
        key="soul-sync",
        name="Soul Sync",
        path=CultivationPath.QI.value,
        min_stage="foundation-establishment",
        exp_multiplier=0.5,
        flat_bonus=10,
    )
    state.bonds[bond.key] = bond

    player, partner = _make_players("foundation-establishment")
    player.bond_key = bond.key
    partner.bond_key = bond.key
    player.bond_partner_id = partner.user_id
    partner.bond_partner_id = player.user_id

    bonus_player, bonus_partner = state.bond_bonuses(
        bond, player, partner, 40, 32
    )

    expected_player = bond.bonus_amount(40)
    expected_partner = bond.bonus_amount(32)
    assert bonus_player == expected_player
    assert bonus_partner == expected_partner
    assert bonus_player > 0 and bonus_partner > 0


def test_bond_bonus_requires_both_meet_stage() -> None:
    state = GameState()
    state.ensure_default_stages()

    bond = BondProfile(
        key="heavenly-oath",
        name="Heavenly Oath",
        path=CultivationPath.QI.value,
        min_stage="foundation-establishment",
        exp_multiplier=0.2,
        flat_bonus=0,
    )
    state.bonds[bond.key] = bond

    player, partner = _make_players("foundation-establishment")
    partner.cultivation_stage = "qi-condensation"
    player.bond_key = bond.key
    partner.bond_key = bond.key
    player.bond_partner_id = partner.user_id
    partner.bond_partner_id = player.user_id

    bonus_player, bonus_partner = state.bond_bonuses(
        bond, player, partner, 40, 40
    )

    assert bonus_player == 0
    assert bonus_partner == 0


def test_complete_bond_mission_grants_shared_rewards() -> None:
    state = GameState()
    technique = CultivationTechnique(
        key="soul-harmony",
        name="Soul Harmony",
        grade="earth",
        path=CultivationPath.SOUL.value,
        skills=["soul-chorus"],
    )
    state.cultivation_techniques[technique.key] = technique

    bond = BondProfile(
        key="soul-sync",
        name="Soul Sync",
        path=CultivationPath.QI.value,
        bond_mission_key="shared-insight",
        bond_soul_techniques=[technique.key],
    )

    player, partner = _make_players("foundation-establishment")
    player.bond_key = bond.key
    partner.bond_key = bond.key
    player.bond_partner_id = partner.user_id
    partner.bond_partner_id = player.user_id

    outcome = complete_bond_mission(
        state, player, partner, bond, now=1234.0
    )

    assert outcome.mission_key == "shared-insight"
    assert outcome.player_new_techniques == [technique.key]
    assert outcome.partner_new_techniques == [technique.key]
    assert outcome.player_new_skills == technique.skills
    assert outcome.partner_new_skills == technique.skills
    assert outcome.player_updated and outcome.partner_updated
    assert not outcome.already_completed

    assert player.bond_missions["shared-insight"] == 1234.0
    assert partner.bond_missions["shared-insight"] == 1234.0
    assert technique.key in player.cultivation_technique_keys
    assert technique.key in partner.cultivation_technique_keys
    assert "soul-chorus" in player.skill_proficiency
    assert "soul-chorus" in partner.skill_proficiency


def test_complete_bond_mission_subsequent_runs_detect_completion() -> None:
    state = GameState()
    technique = CultivationTechnique(
        key="soul-harmony",
        name="Soul Harmony",
        grade="earth",
        path=CultivationPath.SOUL.value,
        skills=["soul-chorus"],
    )
    state.cultivation_techniques[technique.key] = technique

    bond = BondProfile(
        key="soul-sync",
        name="Soul Sync",
        path=CultivationPath.QI.value,
        bond_mission_key="shared-insight",
        bond_soul_techniques=[technique.key],
    )

    player, partner = _make_players("foundation-establishment")
    player.bond_key = bond.key
    partner.bond_key = bond.key
    player.bond_partner_id = partner.user_id
    partner.bond_partner_id = player.user_id

    complete_bond_mission(state, player, partner, bond, now=1000.0)
    outcome = complete_bond_mission(state, player, partner, bond, now=2000.0)

    assert outcome.already_completed
    assert not outcome.player_new_techniques
    assert not outcome.partner_new_techniques
    assert player.bond_missions["shared-insight"] == 1000.0
    assert partner.bond_missions["shared-insight"] == 1000.0
