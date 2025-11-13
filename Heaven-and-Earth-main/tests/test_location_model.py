from bot.models.world import Location


def test_location_string_false_is_safe_flag() -> None:
    location = Location(
        name="Dangerous Glade",
        description="",
        encounter_rate=0.5,
        is_safe="false",
    )

    assert location.is_safe is False
    assert location.encounter_rate == 0.5


def test_location_string_true_is_safe_flag() -> None:
    location = Location(
        name="Sanctuary",
        description="",
        encounter_rate=0.8,
        is_safe="TRUE",
    )

    assert location.is_safe is True
    assert location.encounter_rate == 0.0
