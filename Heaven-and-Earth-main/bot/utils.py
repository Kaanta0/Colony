"""Administrative CLI helpers for guild data management."""

from __future__ import annotations

import argparse
import shutil
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence, SupportsInt
import tomllib

from .storage import resolve_storage_root

PROJECT_BASE = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_BASE / "config" / "storage.toml"


def _default_storage_root() -> Path:
    return resolve_storage_root(PROJECT_BASE)


def format_number(value: SupportsInt) -> str:
    """Return ``value`` with ``'`` as the thousands separator."""

    integer = int(value)
    sign = "-" if integer < 0 else ""
    formatted = f"{abs(integer):,}".replace(",", "'")
    return f"{sign}{formatted}"


def build_travel_narrative(entries: Sequence[str]) -> str:
    """Compose a short narrative out of travel log fragments."""

    cleaned = [str(entry).strip() for entry in entries if str(entry).strip()]
    if not cleaned:
        return "No travel events recorded yet."

    def _ensure_sentence(text: str) -> str:
        text = text.strip()
        if not text:
            return text
        if text[-1] not in ".!?":
            text = f"{text}."
        return text

    def _lowercase_start(text: str) -> str:
        for index, char in enumerate(text):
            if char.isalpha():
                return text[:index] + char.lower() + text[index + 1 :]
        return text.lower()

    sentences: list[str] = []
    last_index = len(cleaned) - 1
    for index, entry in enumerate(cleaned):
        sentence = _ensure_sentence(entry)
        if index == 0:
            sentences.append(sentence)
            continue
        prefix = "Finally" if index == last_index else "Then"
        sentences.append(f"{prefix}, {_lowercase_start(sentence)}")

    return " ".join(sentences)


@dataclass(slots=True)
class ValidationIssue:
    """Represents a validation result for a single collection entry."""

    level: str
    path: Path
    message: str

    def display(self) -> str:
        return f"[{self.level.upper()}] {self.path}: {self.message}"


class _ExternalValidators:
    """Best-effort loader for the optional validation layer."""

    def __init__(self) -> None:
        self._mapping: dict[str, callable] = {}
        self._factory: callable | None = None
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            module = import_module("bot.validation")
        except ModuleNotFoundError:
            return

        mapping: dict[str, callable] = {}

        for attr_name in (
            "COLLECTION_VALIDATORS",
            "collection_validators",
            "VALIDATORS",
        ):
            value = getattr(module, attr_name, None)
            if isinstance(value, Mapping):
                mapping.update({
                    str(key): validator
                    for key, validator in value.items()
                    if callable(validator)
                })

        factory = None
        for attr_name in ("get_validator", "validator_for", "resolve_validator"):
            candidate = getattr(module, attr_name, None)
            if callable(candidate):
                factory = candidate
                break

        self._mapping = mapping
        self._factory = factory

    def run(self, collection: str, payload: Mapping[str, object], path: Path) -> list[ValidationIssue]:
        self._load()
        validator = self._mapping.get(collection)
        if validator is None and self._factory is not None:
            try:
                validator = self._factory(collection)
            except Exception:  # pragma: no cover - defensive guard
                validator = None
        if not callable(validator):
            return []
        try:
            result = validator(payload, path=path)
        except TypeError:
            result = validator(payload)  # type: ignore[misc]
        except Exception as exc:  # pragma: no cover - defensive guard
            return [ValidationIssue("error", path, f"validator crashed: {exc}")]
        return _normalise_validator_result(result, path)


def _normalise_validator_result(result: object, path: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if result is None:
        return issues
    if isinstance(result, ValidationIssue):
        return [result]
    if isinstance(result, str):
        return [ValidationIssue("warning", path, result)]
    if isinstance(result, Mapping):
        level = str(result.get("level", "warning"))
        message = str(result.get("message", ""))
        if message:
            issues.append(ValidationIssue(level, path, message))
        return issues
    if isinstance(result, Sequence) and not isinstance(result, (bytes, bytearray)):
        for item in result:
            issues.extend(_normalise_validator_result(item, path))
        return issues
    if isinstance(result, tuple) and len(result) >= 2:
        level, message = result[:2]
        return [ValidationIssue(str(level), path, str(message))]
    return [ValidationIssue("warning", path, str(result))]


@dataclass(slots=True)
class StorageCollection:
    name: str
    path: str
    format: str
    section: str | None
    migration: str | None


def _load_storage_config() -> dict[str, StorageCollection]:
    try:
        with CONFIG_PATH.open("rb") as handle:
            payload = tomllib.load(handle)
    except FileNotFoundError:
        return {}

    collections: dict[str, StorageCollection] = {}
    table = payload.get("collections") if isinstance(payload, Mapping) else None
    if not isinstance(table, Mapping):
        return collections

    for name, options in table.items():
        if not isinstance(options, Mapping):
            continue
        path = str(options.get("path", "")).strip()
        if not path:
            continue
        format_value = str(options.get("format", "toml")).strip().lower()
        section = options.get("section")
        if section is not None:
            section = str(section)
        migration = options.get("migration")
        if migration is not None:
            migration = str(migration)
        collections[str(name)] = StorageCollection(
            name=str(name),
            path=path,
            format=format_value,
            section=section,
            migration=migration,
        )
    return collections


def _resolve_storage_path(
    collection: StorageCollection,
    *,
    data_base: Path,
    player_base: Path,
    guild_id: str | None = None,
    key: str | None = None,
) -> Path:
    template = collection.path
    mapping: dict[str, str] = {}
    if "{guild_id}" in template:
        if guild_id is None:
            raise ValueError(f"Collection {collection.name!r} requires a guild id")
        mapping["guild_id"] = guild_id
    if "{key}" in template:
        if key is None:
            raise ValueError(f"Collection {collection.name!r} requires a key")
        mapping["key"] = key

    if template.startswith("data/"):
        base = data_base
        relative = template[len("data/"):]
    elif template.startswith("playerdata/"):
        base = player_base
        relative = template[len("playerdata/"):]
    else:
        base = PROJECT_BASE
        relative = template
    return base / relative.format(**mapping)


def _discover_world_data(
    data_base: Path,
    player_base: Path,
    config: Mapping[str, StorageCollection],
) -> tuple[
    dict[str, dict[str, tuple[Path, Mapping[str, Any]]]],
    list[ValidationIssue],
    dict[str, Path],
]:
    world_entries = {
        name: entry for name, entry in config.items() if entry.migration == "worlds"
    }
    if not world_entries:
        return {}, [], {}

    sample_entry = next(iter(world_entries.values()))
    parts = list(Path(sample_entry.path).parts)
    if parts and parts[0] == "data":
        parts = parts[1:]
    try:
        guild_index = parts.index("{guild_id}")
    except ValueError:
        return {}, []
    base_dir = data_base.joinpath(*parts[:guild_index])
    parse_issues: list[ValidationIssue] = []
    result: dict[str, dict[str, tuple[Path, Mapping[str, Any]]]] = {}
    world_paths: dict[str, Path] = {}
    if not base_dir.exists():
        return result, parse_issues, world_paths

    validators: dict[str, StorageCollection] = world_entries

    for guild_dir in sorted(p for p in base_dir.iterdir() if p.is_dir()):
        guild = guild_dir.name
        world_path = _resolve_storage_path(
            sample_entry,
            data_base=data_base,
            player_base=player_base,
            guild_id=guild,
        )
        world_paths[guild] = world_path
        try:
            with world_path.open("rb") as handle:
                document = tomllib.load(handle)
        except (FileNotFoundError, tomllib.TOMLDecodeError, OSError) as exc:
            parse_issues.append(
                ValidationIssue("error", world_path, f"failed to parse TOML: {exc}")
            )
            document = {}
        if not isinstance(document, Mapping):
            document = {}

        guild_collections: dict[str, tuple[Path, Mapping[str, Any]]] = {}
        for name, entry in validators.items():
            section = entry.section
            if section:
                payload = document.get(section)
            else:
                payload = document
            if isinstance(payload, Mapping):
                guild_collections[name] = (world_path, payload)
        result[guild] = guild_collections
    return result, parse_issues, world_paths


def _discover_player_files(player_base: Path) -> dict[str, set[Path]]:
    players: dict[str, set[Path]] = {}
    if not player_base.exists():
        return players
    for guild_dir in sorted(p for p in player_base.iterdir() if p.is_dir()):
        guild = guild_dir.name
        players_dir = guild_dir / "players"
        if not players_dir.is_dir():
            continue
        files = {path for path in sorted(players_dir.glob("*.toml")) if path.is_file()}
        players[guild] = files
    return players


def _discover_player_extras(player_base: Path) -> dict[str, list[Path]]:
    extras: dict[str, list[Path]] = {}
    if not player_base.exists():
        return extras
    for guild_dir in sorted(p for p in player_base.iterdir() if p.is_dir()):
        additional = [
            entry
            for entry in sorted(guild_dir.iterdir())
            if entry.is_dir() and entry.name != "players"
        ]
        if additional:
            extras[guild_dir.name] = additional
    return extras


def _default_data_base(path: str | None) -> Path:
    if path:
        return Path(path).resolve()
    return (_default_storage_root() / "data").resolve()


def _default_player_base(path: str | None) -> Path:
    if path:
        return Path(path).resolve()
    return (_default_storage_root() / "playerdata").resolve()


def _iter_guilds(
    world_data: Mapping[str, Mapping[str, tuple[Path, Mapping[str, Any]]]],
    player_data: Mapping[str, Iterable[Path]],
    world_paths: Mapping[str, Path],
) -> list[str]:
    guilds = set(world_data.keys()) | set(player_data.keys()) | set(world_paths.keys())
    return sorted(guilds, key=lambda value: int(value) if value.isdigit() else value)


def _parse_guild_filter(guilds: Iterable[str] | None) -> set[str]:
    if not guilds:
        return set()
    return {str(guild) for guild in guilds}


def _command_list(args: argparse.Namespace) -> int:
    data_base = _default_data_base(args.data_base)
    player_base = _default_player_base(args.player_base)
    config = _load_storage_config()
    world_data, _, world_paths = _discover_world_data(data_base, player_base, config)
    player_files = _discover_player_files(player_base)
    extras = _discover_player_extras(player_base)
    guild_filter = _parse_guild_filter(args.guild)

    guilds = _iter_guilds(world_data, player_files, world_paths)
    if guild_filter:
        guilds = [guild for guild in guilds if guild in guild_filter]
    if not guilds:
        print("No guild data found.")
        return 0

    print(f"Data base: {data_base}")
    print(f"Player base: {player_base}\n")

    for guild in guilds:
        collections = world_data.get(guild, {})
        players = player_files.get(guild, set())
        print(f"Guild {guild}:")
        if not collections:
            world_path = world_paths.get(guild)
            if world_path and world_path.exists():
                print("  - world data: world.toml present (0 record(s))")
            else:
                print("  - world data: none")
        else:
            for name, (_, records) in sorted(collections.items(), key=lambda item: item[0]):
                print(f"  - {name}: {len(records)} record(s)")
        if players:
            print(f"  - player saves: {len(players)} TOML file(s)")
        else:
            print("  - player saves: none")
        if extras.get(guild):
            extra_names = ", ".join(path.name for path in extras[guild])
            print(f"  - extra directories: {extra_names}")


def _command_delete_player(args: argparse.Namespace) -> int:
    player_base = _default_player_base(args.player_base)
    guild_id = str(args.guild)
    user_id = str(args.user)

    if not guild_id:
        print("A guild ID is required.", file=sys.stderr)
        return 2
    if not user_id:
        print("A user ID is required.", file=sys.stderr)
        return 2

    player_file = player_base / guild_id / "players" / f"{user_id}.toml"
    if not player_file.exists():
        print(
            f"No player save found for user {user_id} in guild {guild_id}.",
            file=sys.stderr,
        )
        return 1

    if not args.force:
        response = input(
            f"Delete {player_file}? This cannot be undone. Type 'yes' to confirm: "
        ).strip()
        if response.lower() != "yes":
            print("Aborted.")
            return 3

    player_file.unlink()
    print(f"Deleted player save: {player_file}")

    # Clean up empty directories to avoid leaving empty guild scaffolding.
    parent = player_file.parent
    for path in (parent, parent.parent):
        if path == player_base:
            break
        try:
            path.rmdir()
        except OSError:
            break

    return 0


def _command_validate(args: argparse.Namespace) -> int:
    data_base = _default_data_base(args.data_base)
    config = _load_storage_config()
    world_data, parse_issues, _ = _discover_world_data(
        data_base,
        _default_player_base(args.player_base),
        config,
    )
    guild_filter = _parse_guild_filter(args.guild)
    validators = _ExternalValidators()

    required_name = {
        "items",
        "skills",
        "traits",
        "races",
        "titles",
        "quests",
        "enemies",
        "bosses",
        "npcs",
        "locations",
        "currencies",
        "cultivation_techniques",
    }

    issues: list[ValidationIssue] = list(parse_issues)

    for guild, collections in world_data.items():
        if guild_filter and guild not in guild_filter:
            continue
        for collection, (world_path, records) in collections.items():
            for key, payload in records.items():
                if not isinstance(payload, Mapping):
                    issues.append(
                        ValidationIssue(
                            "error",
                            world_path,
                            f"[{collection}] {key}: entry must be a mapping",
                        )
                    )
                    continue
                stored_key = payload.get("key")
                if stored_key is not None and str(stored_key) != str(key):
                    issues.append(
                        ValidationIssue(
                            "warning",
                            world_path,
                            f"[{collection}] {key}: payload key does not match identifier",
                        )
                    )
                if collection in required_name and not str(payload.get("name", "")).strip():
                    issues.append(
                        ValidationIssue(
                            "warning",
                            world_path,
                            f"[{collection}] {key}: missing or empty 'name' field",
                        )
                    )
                issues.extend(
                    validators.run(collection, payload, world_path)
                )

    if not issues:
        print("All guild collections parsed successfully.")
        return 0

    issues.sort(key=lambda issue: (issue.level != "error", str(issue.path)))
    error_count = 0
    warning_count = 0
    for issue in issues:
        print(issue.display())
        if issue.level.lower() == "error":
            error_count += 1
        else:
            warning_count += 1

    summary_parts = []
    if error_count:
        summary_parts.append(f"{error_count} error(s)")
    if warning_count:
        summary_parts.append(f"{warning_count} warning(s)")
    print("\nValidation complete: " + ", ".join(summary_parts))
    return 1 if error_count else 0


def _command_export(args: argparse.Namespace) -> int:
    data_base = _default_data_base(args.data_base)
    player_base = _default_player_base(args.player_base)
    config = _load_storage_config()
    world_data, _, world_paths = _discover_world_data(data_base, player_base, config)
    player_files = _discover_player_files(player_base)
    guild_filter = _parse_guild_filter(args.guild)

    guilds = _iter_guilds(world_data, player_files, world_paths)
    if guild_filter:
        guilds = [guild for guild in guilds if guild in guild_filter]
    if not guilds:
        print("No guild data found to export.")
        return 1

    output = Path(args.output).resolve()
    if output.exists() and not args.force:
        print(f"Refusing to overwrite existing archive: {output}", file=sys.stderr)
        return 2

    output.parent.mkdir(parents=True, exist_ok=True)

    with tarfile.open(output, "w:gz") as archive:
        for guild in guilds:
            world_path = world_paths.get(guild)
            if world_path and world_path.exists():
                arcname = Path("data") / "worlds" / guild / world_path.name
                archive.add(world_path, arcname=str(arcname))
        for guild in guilds:
            guild_dir = player_base / guild
            if not guild_dir.exists():
                continue
            for path in guild_dir.rglob("*"):
                if path.is_file():
                    arcname = Path("playerdata") / guild / path.relative_to(guild_dir)
                    archive.add(path, arcname=str(arcname))

    print(f"Exported {len(guilds)} guild(s) to {output}")
    return 0


def _safe_tar_members(tar: tarfile.TarFile, guild_filter: set[str]) -> Iterator[tarfile.TarInfo]:
    for member in tar.getmembers():
        path = Path(member.name)
        if path.is_absolute() or ".." in path.parts:
            continue
        if member.issym() or member.islnk():  # pragma: no cover - safety guard
            continue
        parts = path.parts
        if not parts:
            continue
        base = parts[0]
        if base == "data":
            if len(parts) < 4 or parts[1] != "worlds":
                continue
            guild = parts[2]
            if guild_filter and guild not in guild_filter:
                continue
        elif base == "playerdata":
            if len(parts) < 2:
                continue
            guild = parts[1]
            if guild_filter and guild not in guild_filter:
                continue
        else:
            continue
        yield member


def _command_import(args: argparse.Namespace) -> int:
    data_base = _default_data_base(args.data_base)
    player_base = _default_player_base(args.player_base)
    guild_filter = _parse_guild_filter(args.guild)
    archive_path = Path(args.input).resolve()
    if not archive_path.exists():
        print(f"Archive not found: {archive_path}", file=sys.stderr)
        return 2

    with tarfile.open(archive_path, "r:*") as archive:
        members = list(_safe_tar_members(archive, guild_filter))
        if not members:
            print("No matching guild data found in the archive.")
            return 1

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            archive.extractall(tmp_path, members=members)

            world_source = tmp_path / "data" / "worlds"
            if world_source.exists():
                for guild_dir in world_source.iterdir():
                    if not guild_dir.is_dir():
                        continue
                    guild = guild_dir.name
                    if guild_filter and guild not in guild_filter:
                        continue
                    destination = data_base / "worlds" / guild
                    destination.mkdir(parents=True, exist_ok=True)
                    for file_path in guild_dir.glob("*.toml"):
                        target = destination / file_path.name
                        if target.exists() and not args.force:
                            print(
                                f"Skipping existing world file: {target}",
                                file=sys.stderr,
                            )
                            continue
                        shutil.copy2(file_path, target)

            player_source = tmp_path / "playerdata"
            if player_source.exists():
                for guild_dir in player_source.iterdir():
                    if not guild_dir.is_dir():
                        continue
                    guild = guild_dir.name
                    if guild_filter and guild not in guild_filter:
                        continue
                    destination = player_base / guild
                    for path in guild_dir.rglob("*"):
                        if not path.is_file():
                            continue
                        target = destination / path.relative_to(guild_dir)
                        if target.exists() and not args.force:
                            print(
                                f"Skipping existing file: {target}",
                                file=sys.stderr,
                            )
                            continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(path, target)

    print(f"Imported data from {archive_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Administrative utilities for guild data.")
    parser.add_argument("--data-base", help="Path to the world data directory (default: ./data)")
    parser.add_argument(
        "--player-base",
        help="Path to the player data directory (default: ./playerdata)",
    )

    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser("list", help="Show stored guild collections")
    list_parser.add_argument("--guild", action="append", help="Filter to one or more guild IDs")
    list_parser.set_defaults(func=_command_list)

    validate_parser = subparsers.add_parser(
        "validate",
        aliases=["lint"],
        help="Parse guild collections and surface structural issues",
    )
    validate_parser.add_argument("--guild", action="append", help="Filter to one or more guild IDs")
    validate_parser.set_defaults(func=_command_validate)

    export_parser = subparsers.add_parser(
        "export-data",
        help="Create a tar.gz archive containing guild world/player data",
    )
    export_parser.add_argument("--guild", action="append", help="Filter to one or more guild IDs")
    export_parser.add_argument("--output", required=True, help="Destination archive path")
    export_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the destination archive if it already exists",
    )
    export_parser.set_defaults(func=_command_export)

    import_parser = subparsers.add_parser(
        "import-data",
        help="Extract a guild archive into the current data directories",
    )
    import_parser.add_argument("--guild", action="append", help="Filter to one or more guild IDs")
    import_parser.add_argument("--input", required=True, help="Path to the archive to import")
    import_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files",
    )
    import_parser.set_defaults(func=_command_import)

    delete_parser = subparsers.add_parser(
        "delete-player",
        help="Remove a player's save file so they can register again",
    )
    delete_parser.add_argument(
        "--guild",
        required=True,
        help="Guild ID that owns the player save",
    )
    delete_parser.add_argument(
        "--user",
        required=True,
        help="Discord user ID whose save should be deleted",
    )
    delete_parser.add_argument(
        "--force",
        action="store_true",
        help="Skip the confirmation prompt",
    )
    delete_parser.set_defaults(func=_command_delete_player)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


__all__ = ["format_number", "ValidationIssue", "build_parser", "main"]


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
