import sys
from pathlib import Path

PROJECT_BASE = Path(__file__).resolve().parents[1]
if str(PROJECT_BASE) not in sys.path:
    sys.path.insert(0, str(PROJECT_BASE))

from bot.models.players import PlayerProgress, grant_legacy_to_heir


def make_player(user_id: int = 1) -> PlayerProgress:
    return PlayerProgress(user_id=user_id, name=f"Cultivator {user_id}", cultivation_stage="qi-condensation")


def test_player_progress_has_legacy_defaults() -> None:
    player = make_player()
    assert player.legacy_techniques == []
    assert player.legacy_traits == []
    assert player.legacy_heirs == []
    assert player.retired_at is None
    assert player.martial_souls
    assert player.primary_martial_soul == player.martial_souls[0].name
    assert player.soul_rings == []
    assert player.soul_bones == []
    assert player.soul_power_level == 1
    assert player.max_spirit_ring_slots >= 1
    assert player.required_spirit_rings == 1


def test_add_legacy_technique_validates_uniqueness() -> None:
    player = make_player()
    assert player.add_legacy_technique("moon-saber") is True
    assert player.add_legacy_technique("moon-saber") is False
    assert player.add_legacy_technique("") is False
    assert player.legacy_techniques == ["moon-saber"]


def test_designate_legacy_heir_normalizes_ids() -> None:
    player = PlayerProgress(
        user_id=10,
        name="Ancestor",
        cultivation_stage="qi-condensation",
        legacy_heirs={"42": True, "ignored": False, 77: True},
    )
    assert player.legacy_heirs == [42, 77]
    assert player.designate_legacy_heir(42) is False  # already present
    assert player.designate_legacy_heir("101") is True
    assert player.legacy_heirs == [42, 77, 101]


def test_grant_legacy_to_heir_adds_new_entries() -> None:
    ancestor = PlayerProgress(
        user_id=1,
        name="Ancestor",
        cultivation_stage="qi-condensation",
        legacy_techniques=["starfall"],
        legacy_traits=["phoenix-heart"],
    )
    heir = PlayerProgress(user_id=2, name="Heir", cultivation_stage="qi-condensation")

    techniques, traits = grant_legacy_to_heir(ancestor, heir)

    assert techniques == ["starfall"]
    assert traits == ["phoenix-heart"]
    assert "starfall" in heir.cultivation_technique_keys
    assert "phoenix-heart" in heir.trait_keys


def test_grant_legacy_to_heir_skips_duplicates() -> None:
    ancestor = PlayerProgress(
        user_id=1,
        name="Ancestor",
        cultivation_stage="qi-condensation",
        legacy_techniques=["starfall"],
        legacy_traits=["phoenix-heart"],
    )
    heir = PlayerProgress(
        user_id=2,
        name="Heir",
        cultivation_stage="qi-condensation",
        cultivation_technique_keys=["starfall"],
        trait_keys=["phoenix-heart"],
    )

    techniques, traits = grant_legacy_to_heir(ancestor, heir)

    assert techniques == []
    assert traits == []
    assert heir.cultivation_technique_keys.count("starfall") == 1
    assert heir.trait_keys.count("phoenix-heart") == 1
