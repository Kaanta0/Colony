"""Unified persistence layer driven by ``config/storage.toml``.

This module centralises all disk I/O behind :class:`DataStore`.  Collections are
looked up from ``config/storage.toml`` which specifies their relative path,
serialisation format, and schema version.  The datastore automatically
coordinates schema migrations via ``migrations/<collection>/`` scripts and keeps
track of per-collection versions in ``schema_version.toml`` files that live next
to the stored data.
"""

from __future__ import annotations

import asyncio
import importlib.util
import math
import os
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Dict, Iterable, Mapping, MutableMapping, Optional
from urllib.parse import quote, unquote

import tomllib


_STORAGE_LOCK = asyncio.Lock()


def _is_site_packages(path: Path) -> bool:
    """Return ``True`` if ``path`` is inside a site/dist-packages directory."""

    normalized = {part.lower() for part in path.parts}
    return "site-packages" in normalized or "dist-packages" in normalized


def resolve_storage_root(package_root: Path) -> Path:
    """Determine where mutable data should be stored.

    The default behaviour keeps data alongside the source tree when the project
    is executed from a checkout.  When the package is installed into a
    site-packages directory (which is usually read-only and prone to being
    replaced on upgrades) or when an explicit override is provided, the storage
    root is relocated accordingly.
    """

    override = os.getenv("HEAVEN_DATA_ROOT") or os.getenv("HEAVEN_STORAGE_ROOT")
    if override:
        return Path(override).expanduser().resolve()

    if _is_site_packages(package_root) or not os.access(package_root, os.W_OK):
        return Path.cwd().resolve()

    return package_root


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _normalize_for_toml(value: Any) -> Any:
    if isinstance(value, MappingProxyType):
        value = dict(value)
    if isinstance(value, Mapping):
        normalized: Dict[str, Any] = {}
        for key, item in value.items():
            if item is None:
                continue
            normalized[str(key)] = _normalize_for_toml(item)
        return normalized
    if isinstance(value, set):
        items = [_normalize_for_toml(item) for item in value if item is not None]
        return sorted(items, key=lambda item: repr(item))
    if isinstance(value, (list, tuple)):
        items = []
        for item in value:
            if item is None:
                continue
            items.append(_normalize_for_toml(item))
        return items
    if isinstance(value, Enum):
        enum_value = value.value
        if isinstance(enum_value, (str, bool)):
            return enum_value
        if isinstance(enum_value, (int, float)):
            return value.name.lower()
        return str(enum_value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return 0.0
        return value
    if isinstance(value, bytes):
        return value.decode("utf8", "replace")
    return str(value)


def _quote_string(value: str) -> str:
    replacements = {
        "\\": "\\\\",
        '"': '\\"',
        "\b": "\\b",
        "\t": "\\t",
        "\n": "\\n",
        "\f": "\\f",
        "\r": "\\r",
    }

    def _escape_char(char: str) -> str:
        if char in replacements:
            return replacements[char]
        code = ord(char)
        if 0x20 <= code <= 0x7E:
            return char
        return f"\\u{code:04x}"

    return '"' + "".join(_escape_char(char) for char in value) + '"'


def _format_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = f"{value:.10g}"
        if "e" not in text and "E" not in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"
    if isinstance(value, str):
        return _quote_string(value)
    if isinstance(value, list):
        if value and all(isinstance(item, Mapping) for item in value):
            raise TypeError("Nested table arrays handled separately")
        return "[" + ", ".join(_format_toml_value(item) for item in value) + "]"
    if isinstance(value, Mapping):
        raise TypeError("Mappings must be serialised via table handlers")
    return _quote_string(str(value))


def _serialize_table(
    data: Mapping[str, Any],
    *,
    parent: tuple[str, ...] | None = None,
    output: list[str],
) -> None:
    parent = parent or ()
    simple_items: list[tuple[str, Any]] = []
    tables: list[tuple[str, Mapping[str, Any]]] = []
    array_tables: list[tuple[str, list[Mapping[str, Any]]]] = []

    for key, value in data.items():
        if isinstance(value, Mapping):
            tables.append((key, value))
        elif isinstance(value, list) and value and all(
            isinstance(item, Mapping) for item in value
        ):
            array_tables.append((key, value))
        else:
            simple_items.append((key, value))

    simple_items.sort(key=lambda item: item[0])
    tables.sort(key=lambda item: item[0])
    array_tables.sort(key=lambda item: item[0])

    for key, value in simple_items:
        output.append(f"{key} = {_format_toml_value(value)}")

    for key, value in tables:
        header = ".".join((*parent, key))
        if output and output[-1] != "":
            output.append("")
        output.append(f"[{header}]")
        _serialize_table(value, parent=(*parent, key), output=output)

    for key, items in array_tables:
        header = ".".join((*parent, key))
        for item in items:
            if output and output[-1] != "":
                output.append("")
            output.append(f"[[{header}]]")
            _serialize_table(item, parent=(*parent, key), output=output)


def _toml_dumps(data: Mapping[str, Any]) -> str:
    normalized = _normalize_for_toml(data)
    if not isinstance(normalized, Mapping):
        raise TypeError("Top level TOML document must be a mapping")
    ordered = dict(sorted(normalized.items(), key=lambda item: item[0]))
    output: list[str] = []
    _serialize_table(ordered, output=output)
    return "\n".join(output) + "\n"


def _read_toml(path: Path) -> Any:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (FileNotFoundError, tomllib.TOMLDecodeError, OSError):
        return None


def _write_toml(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    data = _toml_dumps(payload)
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf8", dir=path.parent, delete=False
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------------------
# Configuration handling
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CollectionConfig:
    name: str
    path: str
    version: int
    section: str | None = None
    version_scope: str | None = None
    migration_key: str | None = None

    def requires_key(self) -> bool:
        return "{key}" in self.path

    def requires_guild(self) -> bool:
        return "{guild_id}" in self.path or (
            self.version_scope is not None and "{guild_id}" in self.version_scope
        )

    def build_relative_path(
        self,
        *,
        guild_id: str | None = None,
        key: str | None = None,
    ) -> str:
        mapping: dict[str, str] = {}
        if "{guild_id}" in self.path:
            if guild_id is None:
                raise ValueError(f"Collection {self.name!r} requires a guild id")
            mapping["guild_id"] = guild_id
        if "{key}" in self.path:
            if key is None:
                raise ValueError(f"Collection {self.name!r} requires a key")
            mapping["key"] = key
        return self.path.format(**mapping)

    def resolve_path(
        self,
        base: Path,
        *,
        guild_id: str | None = None,
        key: str | None = None,
    ) -> Path:
        relative = self.build_relative_path(guild_id=guild_id, key=key)
        return base / relative

    def resolve_scope_path(self, base: Path, *, guild_id: str | None = None) -> Path:
        template = self.version_scope or self.path
        mapping: dict[str, str] = {}
        if "{guild_id}" in template:
            if guild_id is None:
                raise ValueError(f"Collection {self.name!r} requires a guild id")
            mapping["guild_id"] = guild_id
        return (base / template.format(**mapping)).resolve()

    def record_directory(self, base: Path, *, guild_id: str | None = None) -> Path:
        if not self.requires_key():
            raise ValueError(f"Collection {self.name!r} does not store records per key")
        dummy_path = self.resolve_path(base, guild_id=guild_id, key="__dummy__")
        return dummy_path.parent


def _load_storage_config(path: Path) -> dict[str, CollectionConfig]:
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing storage configuration at {path}") from exc

    collections: dict[str, CollectionConfig] = {}
    raw_collections = payload.get("collections") if isinstance(payload, Mapping) else None
    if not isinstance(raw_collections, Mapping):
        raise RuntimeError("storage configuration must define a [collections] table")

    for name, options in raw_collections.items():
        if not isinstance(options, Mapping):
            continue
        path_value = str(options.get("path", "")).strip()
        if not path_value:
            raise RuntimeError(f"Collection {name!r} is missing a path entry")
        version_value = int(options.get("version", 0))
        section_value = options.get("section")
        if section_value is not None:
            section_value = str(section_value)
        version_scope = options.get("version_scope")
        if version_scope is not None:
            version_scope = str(version_scope)
        migration_key = options.get("migration")
        if migration_key is not None:
            migration_key = str(migration_key)
        collections[str(name)] = CollectionConfig(
            name=str(name),
            path=path_value,
            version=version_value,
            section=section_value,
            version_scope=version_scope,
            migration_key=migration_key or str(name),
        )
    return collections


# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MigrationModule:
    from_version: int
    to_version: int
    apply: Callable[["MigrationContext"], None]
    description: str


@dataclass(slots=True)
class MigrationContext:
    guild_id: str | None
    collection: CollectionConfig
    config: Mapping[str, CollectionConfig]
    base: Path
    scope_path: Path

    def resolve(self, template: str, **extra: str) -> Path:
        mapping: dict[str, str] = {}
        if "{guild_id}" in template:
            if self.guild_id is None:
                raise ValueError("guild_id is required for this migration")
            mapping["guild_id"] = self.guild_id
        mapping.update(extra)
        return (self.base / template.format(**mapping)).resolve()

    def log(self, message: str) -> None:
        print(f"[migration:{self.collection.name}] {message}")


class MissingMigrationError(RuntimeError):
    pass


class VersionManager:
    def __init__(
        self,
        *,
        base: Path,
        collections: Mapping[str, CollectionConfig],
        migrations_base: Path,
    ) -> None:
        self._base = base
        self._collections = collections
        self._migrations_base = migrations_base
        self._cache: dict[tuple[str, str | None], int] = {}
        self._modules: dict[str, list[MigrationModule]] = {}

    def ensure(self, collection: CollectionConfig, guild_id: str | None) -> None:
        migration_key = collection.migration_key or collection.name
        scope_key = (migration_key, guild_id)
        current = self._cache.get(scope_key)
        if current is None:
            current = self._read_version(collection, guild_id)
            self._cache[scope_key] = current
        target = collection.version
        if current >= target:
            return

        migrations = self._load_migrations(migration_key)
        plan: list[MigrationModule] = []
        version = current
        while version < target:
            step = next((m for m in migrations if m.from_version == version), None)
            if step is None:
                raise MissingMigrationError(
                    f"Missing migration for {collection.name!r}: {version} -> {target}"
                )
            plan.append(step)
            version = step.to_version

        if version != target:
            raise MissingMigrationError(
                f"Incomplete migration chain for {collection.name!r}: {current} -> {target}"
            )

        scope_path = collection.resolve_scope_path(self._base, guild_id=guild_id)
        scope_path.mkdir(parents=True, exist_ok=True)
        context = MigrationContext(
            guild_id=guild_id,
            collection=collection,
            config=self._collections,
            base=self._base,
            scope_path=scope_path,
        )
        for step in plan:
            step.apply(context)
            self._cache[scope_key] = step.to_version
        self._write_version(collection, guild_id, target)
        self._cache[scope_key] = target

    def _versions_file(self, collection: CollectionConfig, guild_id: str | None) -> Path:
        scope_path = collection.resolve_scope_path(self._base, guild_id=guild_id)
        return scope_path / "schema_version.toml"

    def _read_version(self, collection: CollectionConfig, guild_id: str | None) -> int:
        path = self._versions_file(collection, guild_id)
        payload = _read_toml(path)
        if not isinstance(payload, Mapping):
            return 0
        collections = payload.get("collections")
        if not isinstance(collections, Mapping):
            return 0
        key = collection.migration_key or collection.name
        version = collections.get(key)
        if isinstance(version, int):
            return version
        try:
            return int(version)
        except (TypeError, ValueError):
            return 0

    def _write_version(self, collection: CollectionConfig, guild_id: str | None, version: int) -> None:
        path = self._versions_file(collection, guild_id)
        payload = _read_toml(path)
        if not isinstance(payload, MutableMapping):
            payload = {"collections": {}}
        collections = payload.setdefault("collections", {})
        if not isinstance(collections, MutableMapping):
            collections = {}
            payload["collections"] = collections
        key = collection.migration_key or collection.name
        collections[key] = int(version)
        _write_toml(path, payload)

    def _load_migrations(self, collection: str) -> list[MigrationModule]:
        cached = self._modules.get(collection)
        if cached is not None:
            return cached
        directory = self._migrations_base / collection
        modules: list[MigrationModule] = []
        if directory.is_dir():
            for path in sorted(directory.glob("*.py")):
                if path.name.startswith("__"):
                    continue
                spec = importlib.util.spec_from_file_location(
                    f"migrations.{collection}.{path.stem}", path
                )
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                try:
                    spec.loader.exec_module(module)  # type: ignore[assignment]
                except Exception:
                    continue
                from_version = getattr(module, "FROM_VERSION", None)
                to_version = getattr(module, "TO_VERSION", None)
                apply = getattr(module, "apply", None) or getattr(module, "migrate", None)
                if not isinstance(from_version, int) or not isinstance(to_version, int):
                    continue
                if not callable(apply):
                    continue
                description = getattr(module, "DESCRIPTION", path.stem)
                modules.append(
                    MigrationModule(
                        from_version=from_version,
                        to_version=to_version,
                        apply=apply,
                        description=str(description),
                    )
                )
        modules.sort(key=lambda module: module.from_version)
        self._modules[collection] = modules
        return modules


# ---------------------------------------------------------------------------
# DataStore implementation
# ---------------------------------------------------------------------------


class DataStore:
    """Asynchronous datastore routing collections based on configuration."""

    def __init__(self) -> None:
        self._package_root = Path(__file__).resolve().parent.parent
        self._storage_root = resolve_storage_root(self._package_root)
        self._config_path = self._package_root / "config" / "storage.toml"
        self._collections = _load_storage_config(self._config_path)
        self._versions = VersionManager(
            base=self._storage_root,
            collections=self._collections,
            migrations_base=self._package_root / "migrations",
        )

    async def get(self, guild_id: int | str | None, collection: str) -> Mapping[str, Any]:
        async with _STORAGE_LOCK:
            config = self._collection(collection)
            guild_key = self._guild_key(guild_id, config)
            bucket = self._read_collection(config, guild_key)
            return MappingProxyType(bucket)

    async def get_many(
        self, guild_id: int | str | None, collections: Iterable[str]
    ) -> dict[str, Mapping[str, Any]]:
        async with _STORAGE_LOCK:
            result: dict[str, Mapping[str, Any]] = {}
            for name in dict.fromkeys(collections):
                config = self._collection(name)
                guild_key = self._guild_key(guild_id, config)
                result[name] = MappingProxyType(self._read_collection(config, guild_key))
            return result

    async def set(
        self,
        guild_id: int | str | None,
        collection: str,
        key: str,
        value: Any,
    ) -> None:
        async with _STORAGE_LOCK:
            config = self._collection(collection)
            guild_key = self._guild_key(guild_id, config)
            payload = deepcopy(value)
            self._write_entry(config, guild_key, key, payload)

    async def delete(self, guild_id: int | str | None, collection: str, key: str) -> None:
        async with _STORAGE_LOCK:
            config = self._collection(collection)
            guild_key = self._guild_key(guild_id, config)
            self._delete_entry(config, guild_key, key)

    async def bulk_set(
        self, guild_id: int | str | None, collection: str, values: Iterable[tuple[str, Any]]
    ) -> None:
        async with _STORAGE_LOCK:
            config = self._collection(collection)
            guild_key = self._guild_key(guild_id, config)
            items = [(key, deepcopy(value)) for key, value in values]
            self._write_many(config, guild_key, items)

    async def upsert_player(self, guild_id: int | str, player_data: Dict[str, Any]) -> None:
        await self.set(guild_id, "players", str(player_data.get("user_id")), player_data)

    async def get_player(self, guild_id: int | str, user_id: int | str) -> Optional[Dict[str, Any]]:
        async with _STORAGE_LOCK:
            config = self._collection("players")
            guild_key = self._guild_key(guild_id, config)
            return self._read_record_entry(config, guild_key, str(user_id))

    async def get_player_revision(self, guild_id: int | str, user_id: int | str) -> float:
        async with _STORAGE_LOCK:
            config = self._collection("players")
            guild_key = self._guild_key(guild_id, config)
            return self._record_revision(config, guild_key, str(user_id))

    async def flush(self, collection: str | None = None) -> None:  # pragma: no cover - compatibility stub
        return None

    def _collection(self, name: str) -> CollectionConfig:
        try:
            return self._collections[name]
        except KeyError as exc:
            raise KeyError(f"Unknown collection: {name}") from exc

    def _guild_key(self, guild_id: int | str | None, config: CollectionConfig) -> str | None:
        if config.requires_guild():
            if guild_id is None:
                raise ValueError(f"Collection {config.name!r} requires a guild id")
            return str(guild_id)
        return str(guild_id) if guild_id is not None else None

    def _read_collection(self, config: CollectionConfig, guild_id: str | None) -> dict[str, Any]:
        self._versions.ensure(config, guild_id)
        if config.requires_key():
            return self._read_record_collection(config, guild_id)
        return self._read_document_collection(config, guild_id)

    def _read_document_collection(
        self, config: CollectionConfig, guild_id: str | None
    ) -> dict[str, Any]:
        path = config.resolve_path(self._storage_root, guild_id=guild_id)
        payload = _read_toml(path)
        if not isinstance(payload, MutableMapping):
            payload = {}
        if config.section:
            section = payload.get(config.section)
            if not isinstance(section, MutableMapping):
                section = {}
            return {str(key): value for key, value in section.items()}
        return {str(key): value for key, value in payload.items()}

    def _read_record_collection(
        self, config: CollectionConfig, guild_id: str | None
    ) -> dict[str, Any]:
        directory = config.record_directory(self._storage_root, guild_id=guild_id)
        if not directory.exists():
            return {}
        result: dict[str, Any] = {}
        for path in sorted(directory.glob("*.toml")):
            key = _decode_collection_key(path.stem)
            payload = _read_toml(path)
            if isinstance(payload, MutableMapping):
                result[key] = payload
        return result

    def _write_entry(
        self, config: CollectionConfig, guild_id: str | None, key: str, value: Any
    ) -> None:
        self._versions.ensure(config, guild_id)
        if config.requires_key():
            self._write_record_entry(config, guild_id, key, value)
            return
        document, section = self._load_document(config, guild_id)
        section[str(key)] = value
        self._save_document(config, guild_id, document)

    def _write_many(
        self,
        config: CollectionConfig,
        guild_id: str | None,
        values: Iterable[tuple[str, Any]],
    ) -> None:
        self._versions.ensure(config, guild_id)
        if config.requires_key():
            for key, value in values:
                self._write_record_entry(config, guild_id, key, value)
            return
        document, section = self._load_document(config, guild_id)
        for key, value in values:
            section[str(key)] = value
        self._save_document(config, guild_id, document)

    def _delete_entry(self, config: CollectionConfig, guild_id: str | None, key: str) -> None:
        self._versions.ensure(config, guild_id)
        if config.requires_key():
            directory = config.record_directory(self._storage_root, guild_id=guild_id)
            encoded = self._encode_collection_key(str(key))
            path = directory / f"{encoded}.toml"
            try:
                path.unlink()
            except FileNotFoundError:
                return
            return
        document, section = self._load_document(config, guild_id)
        if str(key) in section:
            section.pop(str(key), None)
            self._save_document(config, guild_id, document)

    def _load_document(
        self, config: CollectionConfig, guild_id: str | None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        path = config.resolve_path(self._storage_root, guild_id=guild_id)
        payload = _read_toml(path)
        if not isinstance(payload, MutableMapping):
            payload = {}
        if config.section:
            section = payload.setdefault(config.section, {})
            if not isinstance(section, MutableMapping):
                section = {}
                payload[config.section] = section
        else:
            section = payload
        return payload, section

    def _save_document(
        self, config: CollectionConfig, guild_id: str | None, document: Mapping[str, Any]
    ) -> None:
        path = config.resolve_path(self._storage_root, guild_id=guild_id)
        _write_toml(path, document)

    def _write_record_entry(
        self, config: CollectionConfig, guild_id: str | None, key: str, value: Any
    ) -> None:
        directory = config.record_directory(self._storage_root, guild_id=guild_id)
        directory.mkdir(parents=True, exist_ok=True)
        encoded = self._encode_collection_key(str(key))
        path = directory / f"{encoded}.toml"
        _write_toml(path, value)

    def _read_record_entry(
        self, config: CollectionConfig, guild_id: str | None, key: str
    ) -> Optional[Dict[str, Any]]:
        directory = config.record_directory(self._storage_root, guild_id=guild_id)
        encoded = self._encode_collection_key(str(key))
        path = directory / f"{encoded}.toml"
        payload = _read_toml(path)
        if isinstance(payload, MutableMapping):
            return dict(payload)
        return None

    def _record_revision(
        self, config: CollectionConfig, guild_id: str | None, key: str
    ) -> float:
        directory = config.record_directory(self._storage_root, guild_id=guild_id)
        encoded = self._encode_collection_key(str(key))
        path = directory / f"{encoded}.toml"
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    @staticmethod
    def _encode_collection_key(key: str) -> str:
        return quote(str(key), safe="")


def _decode_collection_key(filename: str) -> str:
    return unquote(filename)


__all__ = ["DataStore", "resolve_storage_root"]
