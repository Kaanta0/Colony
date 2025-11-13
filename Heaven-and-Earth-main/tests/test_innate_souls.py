from __future__ import annotations

from bot.models.combat import SpiritualAffinity, affinity_relationship_modifier
from bot.models.players import PlayerProgress
from bot.models.innate_souls import InnateSoulMutation, InnateSoul, InnateSoulSet


def test_innate_soul_set_mutation_extends_affinities() -> None:
    base = InnateSoul(
        name="Azure Base",
        grade=3,
        affinities=(SpiritualAffinity.WATER,),
    )
    variant = InnateSoul(
        name="Azure Base (Lightning)",
        grade=4,
        affinities=(SpiritualAffinity.WATER, SpiritualAffinity.LIGHTNING),
    )
    mutation = InnateSoulMutation(variant=variant, hybridized=True)
    innate_soul_set = InnateSoulSet([base], mutations=[mutation])

    assert SpiritualAffinity.LIGHTNING in innate_soul_set.affinities
    assert innate_soul_set.matches(SpiritualAffinity.LIGHTNING)
    assert mutation in innate_soul_set.mutations


def test_innate_soul_set_grade_tracks_highest_source() -> None:
    empty = InnateSoulSet()
    assert empty.grade == 1

    lower = InnateSoul(
        name="Earthen Base",
        grade=3,
        affinities=(SpiritualAffinity.EARTH,),
    )
    higher = InnateSoul(
        name="Heavenly Base",
        grade=7,
        affinities=(SpiritualAffinity.LIGHT,),
    )
    variant = InnateSoul(
        name="Heavenly Base (Solar)",
        grade=8,
        affinities=(SpiritualAffinity.LIGHT, SpiritualAffinity.FIRE),
    )
    mutated_set = InnateSoulSet(
        [lower, higher],
        mutations=[InnateSoulMutation(variant=variant, hybridized=True)],
    )

    assert mutated_set.grade == 8


def test_player_exposes_derived_innate_souls() -> None:
    player = PlayerProgress(
        user_id=1,
        name="Tester",
        cultivation_stage="foundation-establishment",
        martial_souls=[
            {
                "name": "Verdant Earth Dragon",
                "grade": 2,
                "affinities": [SpiritualAffinity.EARTH.value],
                "category": "beast",
            }
        ],
        primary_martial_soul="Verdant Earth Dragon",
    )

    bases = player.innate_souls
    assert bases
    assert bases[0].name == "Verdant Earth Dragon"
    assert bases[0].grade == 2
    assert SpiritualAffinity.EARTH in bases[0].affinities


def test_affinity_relationship_modifier_respects_relationships() -> None:
    assert affinity_relationship_modifier(SpiritualAffinity.FIRE, SpiritualAffinity.WATER) < 0
    assert affinity_relationship_modifier(SpiritualAffinity.FIRE, SpiritualAffinity.LIGHTNING) > 0


def test_innate_soul_affinity_modifiers_affect_damage() -> None:
    base = InnateSoul(
        name="Crimson Base",
        grade=3,
        affinities=(SpiritualAffinity.FIRE,),
        affinity_modifiers={SpiritualAffinity.WATER: -0.5},
    )
    fire_multiplier = base.damage_multiplier(SpiritualAffinity.FIRE)
    water_multiplier = base.damage_multiplier(SpiritualAffinity.WATER)

    assert fire_multiplier > 1.0
    assert water_multiplier < fire_multiplier
    assert water_multiplier < 1.0
    assert water_multiplier >= 0.1


def test_innate_soul_grade_deepens_affinity_resonance() -> None:
    lower = InnateSoul(
        name="Kindled Ember",
        grade=2,
        affinities=(SpiritualAffinity.FIRE,),
    )
    higher = InnateSoul(
        name="Inferno Core",
        grade=8,
        affinities=(SpiritualAffinity.FIRE,),
    )

    assert higher.damage_multiplier(SpiritualAffinity.FIRE) > lower.damage_multiplier(
        SpiritualAffinity.FIRE
    )


def test_mixed_affinity_souls_gain_synergy_bonus() -> None:
    single = InnateSoul(
        name="Tempest Seed",
        grade=6,
        affinities=(SpiritualAffinity.LIGHTNING,),
    )
    dual = InnateSoul(
        name="Storm Core",
        grade=6,
        affinities=(SpiritualAffinity.LIGHTNING, SpiritualAffinity.WIND),
    )

    assert dual.damage_multiplier(SpiritualAffinity.LIGHTNING) > single.damage_multiplier(
        SpiritualAffinity.LIGHTNING
    )


def test_innate_soul_modifiers_round_trip() -> None:
    base = InnateSoul(
        name="Aurora Base",
        grade=5,
        affinities=(SpiritualAffinity.LIGHT,),
        affinity_modifiers={SpiritualAffinity.DARKNESS: -0.25},
    )
    payload = base.to_mapping()
    restored = InnateSoul.from_mapping(payload)
    assert restored.affinity_modifiers[SpiritualAffinity.DARKNESS] == -0.25


