import sys
from pathlib import Path
import importlib

PROJECT_BASE = Path(__file__).resolve().parents[1]
if str(PROJECT_BASE) not in sys.path:
    sys.path.insert(0, str(PROJECT_BASE))

import tomllib

from bot.storage import CollectionConfig, MigrationContext, _write_toml
from bot.models.players import PlayerProgress


def test_player_progress_normalizes_legacy_martial_soul_names():
    player = PlayerProgress(
        user_id=1,
        name="Tester",
        cultivation_stage="Novice",
        martial_souls=[
            {
                "name": "Flickering Water Soul",
                "category": "beast",
                "grade": 2,
                "affinities": ("water",),
            }
        ],
        active_martial_soul_names=["Flickering Water Soul"],
        soul_rings=[
            {
                "slot_index": 0,
                "color": "yellow",
                "age": 200,
                "martial_soul": "Flickering Water Soul",
            }
        ],
    )

    assert player.martial_souls[0].name == "Flickering Water Leviathan"
    assert player.active_martial_soul_names == ["Flickering Water Leviathan"]
    assert player.soul_rings[0].martial_soul == "Flickering Water Leviathan"


def test_player_progress_normalizes_user_id_to_int():
    player = PlayerProgress(
        user_id="12345",
        name="Tester",
        cultivation_stage="Novice",
    )

    assert isinstance(player.user_id, int)
    assert player.user_id == 12345


def test_player_migration_normalizes_sequences(tmp_path: Path) -> None:
    migration = importlib.import_module("migrations.players.0005_martial_schema")

    guild_id = "42"
    players_config = CollectionConfig(
        name="players",
        path="playerdata/{guild_id}/players/{key}.toml",
        version=5,
        version_scope="playerdata/{guild_id}",
        migration_key="players",
    )

    base = tmp_path
    player_dir = base / f"playerdata/{guild_id}/players"
    player_dir.mkdir(parents=True, exist_ok=True)

    legacy_payload = {
        "martial_souls": {"name": "Flame Serpent"},
        "spirit_rings": {"color": "red"},
        "spirit_bones": {"slot": "head", "age": 1200},
        "soul_power_level": "not-a-number",
    }
    player_path = player_dir / "100.toml"
    _write_toml(player_path, legacy_payload)

    context = MigrationContext(
        guild_id=guild_id,
        collection=players_config,
        config={"players": players_config},
        base=base,
        scope_path=base / f"playerdata/{guild_id}",
    )

    migration.apply(context)

    updated = tomllib.load(player_path.open("rb"))

    assert isinstance(updated["martial_souls"], list)
    assert updated["martial_souls"] and updated["martial_souls"][0]["name"] == "Flame Serpent"
    assert isinstance(updated["soul_rings"], list)
    assert isinstance(updated["soul_bones"], list)
    assert updated["soul_power_level"] == 1
