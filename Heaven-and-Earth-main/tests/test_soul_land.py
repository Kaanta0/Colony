import math
import re

import random

import pytest

from bot.cogs.player import _format_martial_soul_block
from bot.game import GameState
from bot.models.combat import (
    DamageType,
    Skill,
    SkillArchetype,
    SkillCategory,
    SpiritualAffinity,
    Stats,
    WeaponType,
    coerce_affinity,
)
from bot.models.players import PlayerProgress
from bot.models.progression import CultivationStage
from bot.models.soul_land import (
    MartialSoul,
    MartialSoulType,
    MartialSoulRarity,
    SpiritRing,
    SpiritRingColor,
)


def test_martial_soul_schema_with_evolution():
    evolution = {
        "name": "Heavenly Ascension",
        "required_level": 45,
        "required_rings": 3,
        "stat_bonus": {"strength": 2.0, "agility": 1.5},
        "ability_unlocks": 1,
        "description": "Awakens latent power.",
    }
    payload = {
        "name": "Storm Wolf",
        "category": "beast",
        "grade": 8,
        "innate_attributes": {"strength": 5.0, "physique": 3.5, "agility": 4.0},
        "signature_abilities": ["tempest_fang"],
        "evolution_paths": [evolution],
        "base_ability_slots": 2,
        "rarity": "legendary",
    }
    soul = MartialSoul.from_mapping(payload)
    assert soul.rarity is MartialSoulRarity.LEGENDARY
    assert soul.base_ability_slots == 2
    assert soul.evolution_paths and soul.evolution_paths[0].ability_unlocks == 1

    total_stats = soul.total_innate_stats(level=50, ring_count=3)
    assert pytest.approx(total_stats.strength) == 7.0
    assert pytest.approx(total_stats.agility) == 5.5

    ability_slots = soul.ability_slots(level=50, ring_count=3)
    assert ability_slots == 3


def test_spirit_ring_age_tier_unlocks():
    ring = SpiritRing(
        slot_index=0,
        color=SpiritRingColor.PURPLE,
        age=1_200,
        ability_keys=("storm_burst", "sky_step"),
    )
    assert ring.age_tier == "veteran"
    assert ring.ability_slots == 2
    assert ring.unlocked_abilities == ("storm_burst", "sky_step")

    base = Stats(strength=4.0, physique=2.0, agility=3.0)
    bonus = ring.stat_bonus(base)
    assert pytest.approx(bonus.strength) == pytest.approx(4.0 * ring.stat_multiplier)


def test_player_twin_martial_souls_balance():
    dragon = MartialSoul(
        name="Azure Dragon",
        category=MartialSoulType.BEAST,
        grade=7,
        affinities=(),
        innate_attributes=Stats(strength=5.0, physique=4.0, agility=3.0),
        base_ability_slots=2,
        signature_abilities=("dragon_roar",),
    )
    phoenix = MartialSoul(
        name="Silver Phoenix",
        category=MartialSoulType.BEAST,
        grade=6,
        affinities=(),
        innate_attributes=Stats(strength=3.0, physique=5.0, agility=4.0),
        base_ability_slots=1,
        signature_abilities=("phoenix_cry",),
    )
    ring_one = SpiritRing(
        slot_index=0,
        color=SpiritRingColor.YELLOW,
        age=1_500,
        martial_soul="Azure Dragon",
        ability_keys=("dragon_breath",),
    )
    ring_two = SpiritRing(
        slot_index=1,
        color=SpiritRingColor.BLACK,
        age=12_000,
        martial_soul="Silver Phoenix",
        ability_keys=("phoenix_flare", "wind_dance"),
    )
    player = PlayerProgress(
        user_id=1,
        name="Tester",
        cultivation_stage="Novice",
        martial_souls=[dragon, phoenix],
        active_martial_soul_names=["Azure Dragon", "Silver Phoenix"],
        soul_rings=[ring_one, ring_two],
    )
    stage = CultivationStage(key="novice", name="Novice", success_rate=1.0)
    player.recalculate_stage_stats(stage, None, None)

    bonus = player.total_martial_soul_stats()
    assert bonus.strength == pytest.approx(0.0)
    assert bonus.physique == pytest.approx(0.0)
    assert bonus.agility == pytest.approx(0.0)

    abilities = player.unlocked_spirit_abilities()
    assert "dragon_roar" in abilities
    assert "dragon_breath" in abilities
    assert "phoenix_flare" in abilities
    assert player.total_spirit_ability_slots() >= len(abilities)


def test_martial_soul_from_mapping_strips_trailing_soul():
    payload = {
        "name": "Flickering Water Soul",
        "category": "beast",
        "grade": 2,
        "affinities": ("water",),
    }
    soul = MartialSoul.from_mapping(payload)
    assert soul.name == "Flickering Water Leviathan"


def test_martial_soul_from_mapping_removes_status_prefix():
    payload = {
        "name": "Dormant Resonant Earth Soul",
        "category": "beast",
        "grade": 5,
        "affinities": ("earth",),
    }
    soul = MartialSoul.from_mapping(payload)
    assert soul.name == "Resonant Earth Jade Qilin"


def test_martial_soul_from_mapping_uses_weapon_manifest_for_tools():
    payload = {
        "name": "Resonant Lightning Soul",
        "category": "tool",
        "grade": 5,
        "affinities": ("lightning",),
        "favoured_weapons": ("spear",),
    }
    soul = MartialSoul.from_mapping(payload)
    assert soul.name == "Resonant Lightning Spear"


def test_martial_soul_block_displays_star_grade_and_coloured_name():
    soul = MartialSoul(
        name="Lava Dragon",
        category=MartialSoulType.BEAST,
        grade=7,
        affinities=(SpiritualAffinity.LAVA,),
    )
    lines = _format_martial_soul_block(soul, status="Mythic")

    ansi_escape = re.compile("\\x1b\\[[0-9;]*m")
    stripped_grade = ansi_escape.sub("", lines[0])
    assert stripped_grade == "⭐⭐⭐⭐⭐⭐⭐(7)"

    assert "\x1b[1;31mLava Dragon\x1b[0m" in lines[1]
    stripped_name = ansi_escape.sub("", lines[1])
    assert stripped_name == "Mythic Lava Dragon"


def test_martial_soul_block_hides_default_status_prefix():
    soul = MartialSoul(
        name="Resonant Earth Jade Qilin",
        category=MartialSoulType.BEAST,
        grade=5,
        affinities=(SpiritualAffinity.EARTH,),
    )
    lines = _format_martial_soul_block(soul, status="Dormant")

    ansi_escape = re.compile("\x1b\[[0-9;]*m")
    stripped_name = ansi_escape.sub("", lines[1])
    assert stripped_name == "Resonant Earth Jade Qilin"


def test_martial_soul_block_dims_description_with_spacing():
    soul = MartialSoul(
        name="Verdantrun Halberd",
        category=MartialSoulType.TOOL,
        grade=6,
        affinities=(
            SpiritualAffinity.LIFE,
            SpiritualAffinity.EARTH,
            SpiritualAffinity.METAL,
        ),
        description=(
            "A halberd draped in living vines blooms when cutting through stale air."
        ),
    )

    lines = _format_martial_soul_block(soul, status="Active")

    assert lines[-2].strip() == ""
    assert lines[-1].startswith("\x1b[2;3;90m")
    ansi_escape = re.compile("\x1b\[[0-9;]*m")
    stripped_summary = ansi_escape.sub("", lines[-1])
    assert (
        stripped_summary
        == "A halberd draped in living vines blooms when cutting through stale air."
    )


def test_default_supports_tool_category() -> None:
    for _ in range(25):
        soul = MartialSoul.default(category=MartialSoulType.TOOL)
        assert soul.category is MartialSoulType.TOOL


def test_martial_soul_enforces_affinities_for_grade() -> None:
    soul = MartialSoul(
        name="Entropy Drake",
        category=MartialSoulType.BEAST,
        grade=4,
        affinities=(
            SpiritualAffinity.DARKNESS,
            SpiritualAffinity.ENTROPY,
            SpiritualAffinity.EARTH,
        ),
    )

    assert soul.affinities == (
        SpiritualAffinity.DARKNESS,
        SpiritualAffinity.ENTROPY,
    )


def test_default_grade_filter_returns_matching_entry() -> None:
    pool = MartialSoul.default_pool()
    assert pool
    target_grade = pool[-1].grade

    soul = MartialSoul.default(
        grade=target_grade,
        category="any",
        rng=random.Random(1),
    )

    assert soul.grade == target_grade


def test_default_grade_filter_falls_back_when_affinity_lacks_grade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bot.models import soul_land as soul_land_module

    fire_only = MartialSoul(
        name="Smoldering Ember Wolf",
        category=MartialSoulType.BEAST,
        grade=3,
        affinities=(SpiritualAffinity.FIRE,),
    )
    high_grade = MartialSoul(
        name="Celestial Tempest Wyvern",
        category=MartialSoulType.BEAST,
        grade=7,
        affinities=(SpiritualAffinity.LIGHTNING,),
    )

    monkeypatch.setattr(
        soul_land_module,
        "_MARTIAL_SOUL_CATALOG",
        (fire_only, high_grade),
        raising=False,
    )

    soul = MartialSoul.default(
        affinity=SpiritualAffinity.FIRE,
        grade=7,
        category="any",
        rng=random.Random(2),
    )

    assert soul.grade == 7
    assert soul.name == high_grade.name


def test_roll_martial_souls_respects_grade_weights(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bot.cogs import player as player_module
    from bot.models import soul_land as soul_land_module

    fire_only = MartialSoul(
        name="Smoldering Ember Wolf",
        category=MartialSoulType.BEAST,
        grade=3,
        affinities=(SpiritualAffinity.FIRE,),
    )
    high_grade = MartialSoul(
        name="Celestial Tempest Wyvern",
        category=MartialSoulType.BEAST,
        grade=7,
        affinities=(SpiritualAffinity.LIGHTNING,),
    )

    monkeypatch.setattr(
        soul_land_module,
        "_MARTIAL_SOUL_CATALOG",
        (fire_only, high_grade),
        raising=False,
    )

    souls = player_module.roll_martial_souls(
        grade_weights={3: 0.0, 7: 5.0},
        count_weights={1: 1.0},
        category="any",
        rng=random.Random(5),
    )

    assert len(souls) == 1
    assert souls[0].grade == 7
    assert souls[0].name == high_grade.name


def test_default_pool_covers_all_categories() -> None:
    pool = MartialSoul.default_pool()
    assert pool
    categories = {soul.category for soul in pool}
    assert MartialSoulType.BEAST in categories
    assert MartialSoulType.TOOL in categories


def test_default_allows_any_category_sampling() -> None:
    categories = {MartialSoul.default(category="any").category for _ in range(100)}
    assert MartialSoulType.BEAST in categories
    assert MartialSoulType.TOOL in categories


@pytest.mark.parametrize("affinity", [SpiritualAffinity.FIRE, "lightning"])
def test_default_honours_affinity_filters(affinity: SpiritualAffinity | str) -> None:
    soul = MartialSoul.default(affinity=affinity, category="any")
    normalized = coerce_affinity(affinity)
    assert normalized in soul.affinities


def test_default_preset_index_is_stable() -> None:
    soul_one = MartialSoul.default(preset_index=5, category="any")
    soul_two = MartialSoul.default(preset_index=5, category="any")
    assert soul_one.name == soul_two.name


def test_register_martial_soul_skills_refreshes_existing_entries() -> None:
    state = GameState()
    soul = MartialSoul(
        name="Frostbite Shark",
        category=MartialSoulType.BEAST,
        grade=7,
        affinities=(SpiritualAffinity.ICE,),
    )
    key = soul.signature_skill_key()

    state.skills[key] = Skill(
        key=key,
        name="Stale Frost Snap",
        grade="tier 3",
        skill_type=DamageType.PHYSICAL,
        damage_ratio=0.30,
        proficiency_max=180,
        description="Old description",
        element=SpiritualAffinity.ICE,
        trigger_chance=0.32,
        weapon=WeaponType.BARE_HAND,
        archetype=SkillArchetype.BEAST,
        category=SkillCategory.ACTIVE,
    )

    state.register_martial_soul_skills(soul)
    skill = state.skills[key]

    assert skill.grade == "tier 7"
    assert math.isclose(skill.damage_ratio, 0.70)
    assert math.isclose(skill.trigger_chance, 0.24)
    assert skill.proficiency_max == 225
    assert "\u001b[1;36mFrostbite Shark\u001b[0m" in skill.description


def test_default_returns_fresh_copy_from_catalog() -> None:
    pool = MartialSoul.default_pool()
    index = min(5, len(pool) - 1)
    template = pool[index]

    soul = MartialSoul.default(preset_index=index, category="any")

    assert soul == template
    assert soul is not template
