from __future__ import annotations

import math
import re
import shlex
from dataclasses import asdict
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import MISSING

from ..game import (
    add_item_to_inventory,
    inventory_capacity,
    inventory_load,
    roll_talent_stats,
)
from ..models import ModelValidationError
from ..models.combat import (
    DamageType,
    Skill,
    SkillCategory,
    SpiritualAffinity,
    Stats,
    WeaponType,
    PLAYER_STAT_NAMES,
    AFFINITY_RELATIONSHIPS,
)
from ..models.players import (
    PlayerProgress,
    add_equipped_item,
    remove_equipped_item,
)
from ..models.progression import (
    CultivationPath,
    CultivationPhase,
    CultivationStage,
    CultivationTechnique,
    EquipmentSlot,
    Race,
    SpecialTrait,
    STAGE_STAT_TARGETS,
    TechniqueStatMode,
    Title,
    TitlePosition,
)
from ..models.world import (
    Boss,
    Currency,
    Enemy,
    Item,
    Location,
    LocationNPC,
    LocationNPCType,
    LootDrop,
    Quest,
    QuestObjective,
    ShopItem,
)
from .base import HeavenCog, load_dataclass
from ..constants import DEFAULT_CULTIVATION_COOLDOWN


ALLOWED_SKILL_DAMAGE_TYPES: tuple[DamageType, ...] = (
    DamageType.PHYSICAL,
    DamageType.SOUL,
)


def parse_mapping(
    payload: str,
    value_parser,
    *,
    allow_empty: bool = True,
    delimiters: Sequence[str] = ("=",),
):
    if not payload:
        if allow_empty:
            return {}
        raise app_commands.AppCommandError("Input is required")

    lexer = shlex.shlex(payload, posix=True)
    lexer.whitespace += ","
    lexer.whitespace_split = True

    tokens = list(lexer)
    if not tokens:
        if allow_empty:
            return {}
        raise app_commands.AppCommandError("Input is required")

    # Preserve delimiter order while removing duplicates so we always prefer
    # the first provided separator when a token contains multiple delimiters.
    delimiter_order: list[str] = []
    for delimiter in delimiters:
        if delimiter not in delimiter_order:
            delimiter_order.append(delimiter)
    if not delimiter_order:
        delimiter_order.append("=")

    result = {}
    key_parts: list[str] = []

    soul_error = "Expected key=value pairs separated by spaces or commas"
    if ":" in delimiter_order:
        soul_error += " (colon separators like key:value are also accepted)"

    def _commit_entry(raw_key: str, raw_value: str) -> None:
        key = " ".join(part for part in raw_key.split()).strip()
        value_text = raw_value.strip()
        if not key:
            raise app_commands.AppCommandError(base_error)
        try:
            result[key] = value_parser(value_text)
        except ValueError as exc:
            raise app_commands.AppCommandError(
                f"Invalid value for {key!r}: {value_text}"
            ) from exc

    idx = 0
    token_count = len(tokens)
    while idx < token_count:
        token = tokens[idx]
        idx += 1

        if token in delimiter_order:
            if not key_parts:
                raise app_commands.AppCommandError(base_error)
            if idx >= token_count:
                raise app_commands.AppCommandError(
                    f"Missing value after '{token}' in mapping input"
                )
            _commit_entry(" ".join(key_parts), tokens[idx])
            idx += 1
            key_parts = []
            continue

        delimiter = next((d for d in delimiter_order if d in token), None)
        if delimiter is not None:
            if (
                delimiter == ":"
                and idx < token_count
                and tokens[idx] == "="
            ):
                key_parts.append(token)
                continue
            head, tail = token.split(delimiter, 1)
            raw_key = " ".join(key_parts + [head])
            if tail:
                _commit_entry(raw_key, tail)
            else:
                if idx >= token_count:
                    raise app_commands.AppCommandError(
                        f"Missing value after '{delimiter}' in mapping input"
                    )
                _commit_entry(raw_key, tokens[idx])
                idx += 1
            key_parts = []
            continue

        key_parts.append(token)

    if key_parts:
        raise app_commands.AppCommandError(base_error)

    return result


def parse_list(payload: str) -> list[str]:
    if not payload:
        return []
    return [item for item in shlex.split(payload) if item]


CULTIVATION_PHASE_CHOICES: list[app_commands.Choice[str]] = [
    app_commands.Choice(name=phase.display_name, value=phase.value)
    for phase in CultivationPhase
]


def parse_affinity_list(payload: str) -> list[SpiritualAffinity]:
    entries = parse_list(payload)
    affinities: list[SpiritualAffinity] = []
    for token in entries:
        normalized = token.replace(" ", "_").replace("-", "_").lower()
        try:
            affinities.append(SpiritualAffinity(normalized))
        except ValueError as exc:
            raise app_commands.AppCommandError(
                f"Unknown affinity: {token}"
            ) from exc
    return affinities


def stat_kwargs_from_locals(
    namespace: Mapping[str, Any], *, default: float = 0.0
) -> dict[str, float]:
    values: dict[str, float] = {}
    for name in PLAYER_STAT_NAMES:
        raw = namespace.get(name, default)
        try:
            values[name] = float(raw)
        except (TypeError, ValueError):
            values[name] = float(default)
    return values


def _split_simple_list(payload: str) -> list[str]:
    if not payload:
        return []
    normalized = payload.replace(",", " ")
    return [token for token in parse_list(normalized) if token]


def parse_location_npcs(
    payload: str, *, library: Mapping[str, LocationNPC] | None = None
) -> list[LocationNPC]:
    entries = parse_list(payload)
    npcs: list[LocationNPC] = []
    for token in entries:
        if library and token in library:
            npcs.append(LocationNPC.from_mapping(asdict(library[token])))
            continue
        if ":" in token:
            type_token, remainder = token.split(":", 1)
            type_token = type_token.strip()
            name_payload = remainder.strip()
        else:
            type_token = LocationNPCType.DIALOG.value
            name_payload = token.strip()
        if not name_payload:
            raise app_commands.AppCommandError("NPC name cannot be empty")
        try:
            npc_type = LocationNPCType.from_value(type_token)
        except ValueError as exc:
            raise app_commands.AppCommandError(
                f"Unknown NPC type: {type_token}"
            ) from exc
        description = ""
        reference = None
        if "|" in name_payload:
            name_payload, description = name_payload.split("|", 1)
        if "@" in name_payload:
            name_payload, reference = name_payload.split("@", 1)
        name = name_payload.strip()
        if not name:
            raise app_commands.AppCommandError("NPC name cannot be empty")
        description_text = description.strip()
        reference_value = reference.strip() if reference else None
        shop_items = []
        if npc_type is LocationNPCType.SHOP and reference_value:
            shop_items = _split_simple_list(reference_value)
        npcs.append(
            LocationNPC(
                name=name,
                npc_type=npc_type,
                description=description_text,
                reference=reference_value,
                dialogue=description_text if npc_type is LocationNPCType.DIALOG else "",
                shop_items=shop_items,
            )
        )
    return npcs

def _npc_type_choices(current: str) -> list[app_commands.Choice[str]]:
    options = [
        ("Dialog", LocationNPCType.DIALOG.value),
        ("Shop", LocationNPCType.SHOP.value),
        ("Hostile", LocationNPCType.ENEMY.value),
    ]
    needle = current.lower()
    results: list[app_commands.Choice[str]] = []
    for name, value in options:
        if not needle or needle in name.lower() or needle in value:
            results.append(app_commands.Choice(name=name, value=value))
    return results


def parse_percentage(value: str) -> float:
    raw = value.strip().rstrip("%")
    if not raw:
        raise ValueError("Percentage value required")
    amount = float(raw)
    if amount < 0:
        raise ValueError("Percentage cannot be negative")
    return amount


def require_admin() -> app_commands.Check:
    """Check ensuring the invoker is a guild administrator or bot admin."""

    async def predicate(interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        if guild is None:
            raise app_commands.CheckFailure("This command can only be used in a guild.")

        member: discord.Member | None
        user = interaction.user
        if isinstance(user, discord.Member):
            member = user
        else:
            member = guild.get_member(user.id)
            if member is None:
                try:
                    member = await guild.fetch_member(user.id)
                except discord.HTTPException:
                    member = None

        if member is not None and member.guild_permissions.administrator:
            return True

        store = getattr(interaction.client, "store", None)
        if store is None:
            raise app_commands.AppCommandError("Bot datastore is not configured.")

        admins = await store.get(guild.id, "admins")
        if str(interaction.user.id) in admins:
            return True

        raise app_commands.CheckFailure(
            "Only server administrators or bot admins may use this command."
        )

    return app_commands.check(predicate)


_SIMPLE_LOOT_ENTRY_RE = re.compile(
    r"""
    ^
    (?P<name>.+?)
    (?:\s*@\s*(?P<chance1>[\d.]+%?))?
    (?:\s*\(\s*(?P<amount>\d+)\s*\))?
    (?:\s*@\s*(?P<chance2>[\d.]+%?))?
    $
    """,
    re.VERBOSE,
)


def _parse_chance_value(raw_chance: str, *, key: str) -> float:
    raw = raw_chance.strip().rstrip("%")
    if not raw:
        raise app_commands.AppCommandError(
            f"Invalid drop chance for {key!r}: {raw_chance}"
        )
    try:
        chance_value = float(raw)
    except ValueError as exc:
        raise app_commands.AppCommandError(
            f"Invalid drop chance for {key!r}: {raw_chance}"
        ) from exc
    if chance_value > 1:
        chance_value /= 100.0
    if chance_value < 0 or chance_value > 1:
        raise app_commands.AppCommandError(
            "Drop chance must be between 0 and 100 percent"
        )
    return chance_value


def _tokenize_loot_entries(payload: str) -> list[str]:
    raw_entries: list[str] = []
    current: list[str] = []
    in_quote = False
    quote_char = ""

    for char in payload:
        if char in {'"', "'"}:
            if in_quote and char == quote_char:
                in_quote = False
                quote_char = ""
            elif not in_quote:
                in_quote = True
                quote_char = char
        if char in {",", ";"} and not in_quote:
            entry = "".join(current).strip()
            if entry:
                raw_entries.append(entry)
            current = []
            continue
        current.append(char)

    if current:
        entry = "".join(current).strip()
        if entry:
            raw_entries.append(entry)

    entries: list[str] = []
    for raw in raw_entries:
        parts = shlex.split(raw)
        if not parts:
            continue

        buffer: list[str] = []
        for part in parts:
            token = part.strip()
            if not token:
                continue
            buffer.append(token)
            if "=" in token or token.endswith(")"):
                entry = " ".join(buffer).strip()
                if entry:
                    entries.append(entry)
                buffer = []
        if buffer:
            entry = " ".join(buffer).strip()
            if entry:
                entries.append(entry)

    return entries


def _normalize_loot_target(raw_key: str, *, allow_partial: bool = False) -> tuple[str, str]:
    key = raw_key.strip()
    if not key:
        raise app_commands.AppCommandError("Loot target cannot be empty")

    if ":" in key:
        prefix, remainder = key.split(":", 1)
        normalized_prefix = prefix.strip().lower()
        remainder = remainder.strip()
        if normalized_prefix in {"currency", "curr", "money"}:
            if not remainder:
                if allow_partial:
                    return "currency", remainder
                raise app_commands.AppCommandError("Currency key cannot be empty")
            return "currency", remainder
        if normalized_prefix in {"item", "items"}:
            if not remainder:
                if allow_partial:
                    return "item", remainder
                raise app_commands.AppCommandError("Item key cannot be empty")
            return "item", remainder

    return "item", key


def _loot_token_target(token: str) -> tuple[str, str]:
    prefix = token.split("=", 1)[0]
    try:
        return _normalize_loot_target(prefix, allow_partial=True)
    except app_commands.AppCommandError:
        # Fall back to treating the token as a partial item key while typing.
        return "item", prefix.strip()


def parse_loot_entries(payload: str) -> dict[str, LootDrop]:
    if not payload:
        raise app_commands.AppCommandError("At least one loot entry is required")

    entries = _tokenize_loot_entries(payload)
    if not entries:
        raise app_commands.AppCommandError(
            "Expected at least one loot entry"
        )

    result: dict[str, LootDrop] = {}
    for entry in entries:
        if "=" in entry:
            key, raw_value = entry.split("=", 1)
            kind, key = _normalize_loot_target(key)
            chance_part, amount_part = (raw_value.split(":", 1) + ["1"])[:2]
            chance_value = _parse_chance_value(chance_part, key=key)
            try:
                amount_value = int(amount_part)
            except ValueError as exc:
                raise app_commands.AppCommandError(
                    f"Invalid drop amount for {key!r}: {amount_part}"
                ) from exc
            if amount_value <= 0:
                raise app_commands.AppCommandError(
                    "Drop amount must be at least 1"
                )
            drop = LootDrop(chance=chance_value, amount=amount_value, kind=kind)
            existing = result.get(key)
            if existing and existing.kind != drop.kind:
                raise app_commands.AppCommandError(
                    f"Conflicting loot type for {key!r}."
                )
            result[key] = drop
            continue

        match = _SIMPLE_LOOT_ENTRY_RE.match(entry)
        if not match:
            raise app_commands.AppCommandError(
                "Expected entries formatted as item=chance:amount or 'Item Name (amount)'"
            )

        kind, key = _normalize_loot_target(match.group("name"))

        raw_chance = match.group("chance2") or match.group("chance1")
        chance_value = 1.0
        if raw_chance is not None:
            chance_value = _parse_chance_value(raw_chance, key=key)

        amount_text = match.group("amount") or "1"
        try:
            amount_value = int(amount_text)
        except ValueError as exc:
            raise app_commands.AppCommandError(
                f"Invalid drop amount for {key!r}: {amount_text}"
            ) from exc
        if amount_value <= 0:
            raise app_commands.AppCommandError(
                "Drop amount must be at least 1"
            )

        drop = LootDrop(chance=chance_value, amount=amount_value, kind=kind)
        existing = result.get(key)
        if existing and existing.kind != drop.kind:
            raise app_commands.AppCommandError(
                f"Conflicting loot type for {key!r}."
            )
        result[key] = drop

    return result


def parse_affinity_list(payload: str) -> list[SpiritualAffinity]:
    affinities: list[SpiritualAffinity] = []
    for token in parse_list(payload):
        try:
            affinities.append(SpiritualAffinity(token.lower()))
        except ValueError as exc:
            raise app_commands.AppCommandError(f"Unknown affinity: {token}") from exc
    return affinities


class AdminCog(HeavenCog):

    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.config = bot.config  # type: ignore[assignment]
        self._admin_access_group: app_commands.Group | None = None

    def _build_admin_access_group(self) -> app_commands.Group:
        group = app_commands.Group(
            name="bot_admin", description="Manage bot administrator access"
        )

        @group.command(
            name="grant",
            description="Grant bot admin access to a server member",
        )
        @require_admin()
        @app_commands.describe(
            member="The member who should receive bot admin access"
        )
        async def admin_access_grant(
            interaction: discord.Interaction, member: discord.Member
        ) -> None:
            message = await self._grant_admin_message(interaction, member)
            await interaction.response.send_message(message, ephemeral=True)

        @group.command(
            name="revoke",
            description="Revoke bot admin access from a server member",
        )
        @require_admin()
        @app_commands.describe(
            member="The member whose bot admin access should be revoked"
        )
        async def admin_access_revoke(
            interaction: discord.Interaction, member: discord.Member
        ) -> None:
            message = await self._revoke_admin_message(interaction, member)
            await interaction.response.send_message(message, ephemeral=True)

        return group

    async def cog_load(self) -> None:
        await super().cog_load()
        group = self._build_admin_access_group()
        self.bot.tree.remove_command(
            group.name, type=discord.AppCommandType.chat_input
        )
        self.bot.tree.add_command(group)
        self._admin_access_group = group

    async def cog_unload(self) -> None:
        if self._admin_access_group is not None:
            self.bot.tree.remove_command(
                self._admin_access_group.name,
                type=discord.AppCommandType.chat_input,
            )
            self._admin_access_group = None
        await super().cog_unload()

    async def _ensure(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            raise app_commands.AppCommandError("Guild context required")
        await self.ensure_guild_loaded(interaction.guild.id)

    async def _store_entity(self, interaction: discord.Interaction, collection: str, key: str, data: dict) -> None:
        assert interaction.guild
        await self.store.set(interaction.guild.id, collection, key, data)

    async def _validate_and_store(
        self,
        interaction: discord.Interaction,
        *,
        cls,
        payload: Mapping[str, Any],
        collection: str,
        key: str,
        entity_label: str,
        dump: Callable[[Any], Dict[str, Any]] | None = None,
    ):
        try:
            entity = load_dataclass(cls, dict(payload))
        except ModelValidationError as exc:
            await self.send_validation_error(interaction, entity_label, exc)
            return None
        except (TypeError, ValueError) as exc:
            await self.send_validation_error(
                interaction,
                entity_label,
                ModelValidationError(cls, [str(exc)]),
            )
            return None

        serialized = dump(entity) if dump else asdict(entity)
        await self._store_entity(interaction, collection, key, serialized)
        return entity

    async def _fetch_player(self, guild_id: int, user_id: int) -> PlayerProgress | None:
        data = await self.store.get_player(guild_id, user_id)
        if not data:
            return None
        player = PlayerProgress(**data)
        self.state.register_player(player)
        return player

    async def _clear_player_profile(self, guild_id: int, user_id: int) -> bool:
        existing = await self.store.get_player(guild_id, user_id)
        await self.store.delete(guild_id, "players", str(user_id))
        self.state.forget_player(user_id)

        player_cog = self.bot.get_cog("PlayerCog")
        cache = getattr(player_cog, "_player_cache", None) if player_cog else None
        if isinstance(cache, dict):
            cache.pop((guild_id, user_id), None)

        return bool(existing)

    async def _load_innate_soul_exp_config(
        self, guild_id: int
    ) -> Dict[str, Dict[str, float]]:
        bucket = await self.store.get(guild_id, "config")
        payload = bucket.get("innate_soul_exp_ranges", {})
        result: Dict[str, Dict[str, float]] = {}
        if not isinstance(payload, Mapping):
            return result
        for key, value in payload.items():
            if not isinstance(value, Mapping):
                continue
            try:
                grade = int(key)
            except (TypeError, ValueError):
                continue
            if not 1 <= grade <= 9:
                continue
            lower = value.get("min")
            if lower is None:
                lower = value.get("minimum", value.get("low"))
            upper = value.get("max")
            if upper is None:
                upper = value.get("maximum", value.get("high"))
            try:
                min_value = float(lower) if lower is not None else None
                max_value = float(upper) if upper is not None else None
            except (TypeError, ValueError):
                continue
            if min_value is None or not math.isfinite(min_value):
                continue
            if max_value is None or not math.isfinite(max_value):
                max_value = min_value
            if min_value > max_value:
                min_value, max_value = max_value, min_value
            result[str(grade)] = {"min": min_value, "max": max_value}
        return result

    def _iter_entity_choices(
        self,
        options: Mapping[str, object],
        *,
        current: str,
        multi: bool = False,
    ) -> list[app_commands.Choice[str]]:
        """Generate autocomplete choices for entities keyed by ID.

        Parameters
        ----------
        options:
            Mapping from entity keys to instances with a ``name`` attribute.
        current:
            The current user input provided by Discord.
        multi:
            Whether the value accepts multiple tokens separated by spaces.
        """

        if not options:
            return []

        tokens = [token for token in current.replace(",", " ").split() if token]
        editing: str | None = None
        confirmed: list[str] = []

        if tokens and multi:
            if not current.endswith((" ", ",")):
                editing = tokens[-1]
                confirmed = tokens[:-1]
            else:
                confirmed = tokens

        search = (editing or ("" if multi else current)).lower()
        used = set(confirmed)

        choices: list[app_commands.Choice[str]] = []
        for key, obj in options.items():
            if multi and key in used:
                continue

            name = getattr(obj, "name", key)
            if search and search not in key.lower() and search not in name.lower():
                continue

            value = key
            if multi:
                payload = confirmed + [key]
                value = " ".join(payload)

            choices.append(app_commands.Choice(name=name, value=value))
            if len(choices) >= 25:
                break

        return choices

    def _title_positions(self, title_keys: Sequence[str]) -> dict[str, TitlePosition]:
        positions: dict[str, TitlePosition] = {}
        for key in title_keys:
            title = self.state.titles.get(key)
            if title:
                positions[key] = title.position
        return positions

    def _iter_loot_choices(
        self,
        items: Mapping[str, object],
        currencies: Mapping[str, object],
        *,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if not items and not currencies:
            return []

        tokens = [token for token in current.split() if token]
        editing = ""
        confirmed = tokens
        if tokens and not current.endswith(" "):
            editing = tokens[-1]
            confirmed = tokens[:-1]

        existing_targets = {_loot_token_target(token) for token in confirmed}
        edit_kind, search_key = ("item", "")
        if editing:
            edit_kind, search_key = _loot_token_target(editing)
            existing_targets.add((edit_kind, search_key))

        search = search_key.lower()
        kind_filter: str | None = None
        if editing:
            raw_prefix = editing.split("=", 1)[0]
            if ":" in raw_prefix:
                prefix_name = raw_prefix.split(":", 1)[0].strip().lower()
                if prefix_name in {"currency", "curr", "money", "item", "items"}:
                    kind_filter = edit_kind

        choices: list[app_commands.Choice[str]] = []

        def _append_choice(kind: str, key: str, label: str) -> bool:
            if (kind, key) in existing_targets:
                return False
            if search and search not in key.lower() and search not in label.lower():
                return False
            prefix = f"{kind}:" if kind == "currency" else ""
            suggestion_token = f"{prefix}{key}=100:1"
            payload = confirmed + [suggestion_token]
            value = " ".join(payload).strip()
            if kind == "currency":
                display_name = f"{label} ({key}, currency)"
            else:
                display_name = f"{label} ({key})"
            choices.append(app_commands.Choice(name=display_name, value=value))
            return len(choices) >= 25

        def _iter_source(kind: str, source: Mapping[str, object]) -> bool:
            for key, obj in source.items():
                if kind_filter and kind_filter != kind:
                    continue
                label = getattr(obj, "name", key)
                if _append_choice(kind, key, label):
                    return True
            return False

        if _iter_source("item", items):
            return choices
        _iter_source("currency", currencies)
        return choices

    def _iter_loot_table_choices(
        self,
        table: Mapping[str, LootDrop],
        *,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if not table:
            return []
        search = current.lower()
        choices: list[app_commands.Choice[str]] = []
        for key, drop in table.items():
            label = f"{key} ({drop.chance * 100:.2f}% ×{drop.amount})"
            if search and search not in key.lower() and search not in label.lower():
                continue
            choices.append(app_commands.Choice(name=label, value=key))
            if len(choices) >= 25:
                break
        return choices

    def _iter_reward_choices(
        self,
        items: Mapping[str, object],
        currencies: Mapping[str, object] | None = None,
        *,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if not items and not currencies:
            return []

        def _split_tokens(payload: str) -> tuple[list[str], str]:
            lexer = shlex.shlex(payload, posix=True)
            lexer.whitespace += ","
            lexer.whitespace_split = True
            tokens: list[str] = []
            editing_token = ""
            try:
                for token in lexer:
                    tokens.append(token)
                if payload and payload[-1] not in {" ", "\t", ","} and tokens:
                    editing_token = tokens.pop()
            except ValueError:
                editing_token = lexer.token or ""
            return tokens, editing_token

        confirmed, editing = _split_tokens(current)

        existing_keys = {token.split("=", 1)[0] for token in confirmed if "=" in token}
        if "=" in editing:
            existing_keys.add(editing.split("=", 1)[0])

        prefix = editing.split("=", 1)[0] if editing else ""
        search = prefix.lower()

        def _build_value(suggestion: str) -> str:
            tokens = confirmed + [suggestion]
            return " ".join(shlex.quote(token) for token in tokens)

        combined: list[tuple[str, str, object]] = []
        for key, obj in items.items():
            combined.append(("item", key, obj))
        if currencies:
            for key, obj in currencies.items():
                combined.append(("currency", key, obj))

        choices: list[app_commands.Choice[str]] = []
        for kind, key, obj in combined:
            if key in existing_keys:
                continue
            name = getattr(obj, "name", key)
            if search and search not in key.lower() and search not in name.lower():
                continue
            value = _build_value(f"{key}=1")
            if kind == "currency":
                label = f"{name} ({key}, currency)"
            else:
                label = f"{name} ({key})"
            choices.append(app_commands.Choice(name=label, value=value))
            if len(choices) >= 25:
                break
        return choices

    def _iter_affinity_choices(
        self, current: str, *, multi: bool = False
    ) -> list[app_commands.Choice[str]]:
        options: dict[str, SimpleNamespace] = {}
        if not multi:
            options[""] = SimpleNamespace(name="None")
        for affinity in SpiritualAffinity:
            options[affinity.value] = SimpleNamespace(name=affinity.display_name)
        return self._iter_entity_choices(options, current=current, multi=multi)

    async def _affinity_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_affinity_choices(current)

    def _iter_objective_choices(
        self, *, current: str
    ) -> list[app_commands.Choice[str]]:
        groups: list[tuple[str, Mapping[str, object]]] = []
        enemy_group = ("enemy", self.state.enemies)
        boss_group = ("boss", self.state.bosses)

        raw = current.strip()
        type_hint: str | None = None
        search = ""

        if ":" in raw:
            prefix, remainder = raw.split(":", 1)
            prefix = prefix.strip().lower()
            if prefix in {"enemy", "boss"}:
                type_hint = prefix
            search = remainder.strip().lower()
        else:
            tokens = raw.split()
            remaining = tokens
            if tokens:
                first = tokens[0].lower()
                if first in {"enemy", "boss"}:
                    type_hint = first
                    remaining = tokens[1:]
            search = remaining[-1].lower() if remaining else ""

        if type_hint == "enemy":
            groups.append(enemy_group)
        elif type_hint == "boss":
            groups.append(boss_group)
        else:
            groups.extend([enemy_group, boss_group])

        choices: list[app_commands.Choice[str]] = []
        for kind, collection in groups:
            if not collection:
                continue
            for key, obj in collection.items():
                name = getattr(obj, "name", key)
                if search and search not in key.lower() and search not in name.lower():
                    continue
                label = f"{name} ({'Enemy' if kind == 'enemy' else 'Boss'})"
                value = f"{kind}:{key}"
                choices.append(app_commands.Choice(name=label, value=value))
                if len(choices) >= 25:
                    return choices
        return choices

    async def _sync_stage_roles(
        self,
        guild: discord.Guild,
        stage: CultivationStage,
        previous_role_id: int | None,
    ) -> None:
        if not stage.role_id and not previous_role_id:
            return

        players = await self.store.get(guild.id, "players")
        if not players:
            return

        new_role = guild.get_role(stage.role_id) if stage.role_id else None
        old_role = guild.get_role(previous_role_id) if previous_role_id else None

        for payload in players.values():
            if payload.get("cultivation_stage") != stage.key:
                continue

            user_id = int(payload.get("user_id", 0))
            if not user_id:
                continue

            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except discord.HTTPException:
                    continue

            updates: list[tuple[str, discord.Role]] = []
            if old_role and old_role in member.roles and old_role != new_role:
                updates.append(("remove", old_role))
            if new_role and new_role not in member.roles:
                updates.append(("add", new_role))

            for action, role in updates:
                try:
                    if action == "add":
                        await member.add_roles(role, reason="Cultivation stage role sync")
                    else:
                        await member.remove_roles(role, reason="Cultivation stage role sync")
                except (discord.Forbidden, discord.HTTPException):
                    continue

    @app_commands.command(
        name="set_affinity_rarity",
        description="Adjust the selection weights for spiritual affinities",
    )
    @require_admin()
    @app_commands.describe(
        overrides=(
            "Space or comma separated affinity=weight pairs (e.g. fire=10,"
            " lightning=5)."
        ),
        clear_existing="Clear saved overrides before applying new values",
    )
    async def set_affinity_rarity(
        self,
        interaction: discord.Interaction,
        overrides: str = "",
        clear_existing: bool = False,
    ) -> None:
        await self._ensure(interaction)
        guild = interaction.guild
        assert guild is not None

        try:
            raw_updates = parse_mapping(
                overrides,
                float,
                allow_empty=True,
                delimiters=("=", ":"),
            )
        except app_commands.AppCommandError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        invalid_affinities: list[str] = []
        updates: dict[str, float] = {}
        for key, amount in raw_updates.items():
            normalized = key.replace(" ", "_").replace("-", "_").lower()
            try:
                affinity = SpiritualAffinity(normalized)
            except ValueError:
                invalid_affinities.append(key)
                continue
            updates[affinity.value] = max(0.0, float(amount))

        if invalid_affinities:
            labels = ", ".join(sorted(invalid_affinities))
            await interaction.response.send_message(
                f"Unknown affinity names: {labels}.", ephemeral=True
            )
            return

        if updates:
            existing: dict[str, float] = {}
            if not clear_existing:
                config_bucket = await self.store.get(guild.id, "config")
                bucket_value = config_bucket.get("affinity_weights", {})
                if isinstance(bucket_value, dict):
                    existing.update(
                        {
                            key: float(amount)
                            for key, amount in bucket_value.items()
                            if isinstance(key, str)
                        }
                    )
            merged: dict[str, float] = {}
            merged.update(existing)
            merged.update(updates)
            await self.store.set(guild.id, "config", "affinity_weights", merged)
            heading = "Affinity rarity overrides updated."
            changed_entries: list[tuple[str, float]] = []
            for key, amount in updates.items():
                try:
                    affinity_enum = SpiritualAffinity(key)
                except ValueError:
                    continue
                changed_entries.append((affinity_enum.display_name, amount))
            changed_text = ", ".join(
                f"{name}: {amount}" for name, amount in sorted(changed_entries)
            )
        else:
            await self.store.delete(guild.id, "config", "affinity_weights")
            heading = "Affinity rarity overrides cleared; defaults restored."
            changed_text = ""

        summary_lines: list[str] = []
        player_cog = self.bot.get_cog("PlayerCog")
        if player_cog:
            refresher = getattr(player_cog, "refresh_rarity_config", None)
            getter = getattr(player_cog, "get_affinity_weights", None)
            if callable(refresher) and callable(getter):
                try:
                    await refresher(guild.id)
                    weights_map = await getter(guild.id)
                except Exception:
                    weights_map = None
                else:
                    total = sum(weights_map.values())
                    if total > 0:
                        summary_lines = [
                            f"{affinity.display_name}: {weight / total * 100:.2f}%"
                            for affinity, weight in sorted(
                                weights_map.items(),
                                key=lambda item: item[0].display_name,
                            )
                        ]

        message = heading
        if changed_text:
            message += "\nUpdated overrides: " + changed_text
        if summary_lines:
            message += "\nCurrent distribution: " + ", ".join(summary_lines)
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(
        name="set_innate_soul_exp_range",
        description=(
            "Adjust the cultivation experience multiplier range for a innate soul grade"
        ),
    )
    @require_admin()
    @app_commands.describe(
        grade="Innate soul grade to configure (1-9)",
        minimum="Minimum multiplier applied to the soul cultivation experience gain",
        maximum="Maximum multiplier applied to the soul cultivation experience gain",
    )
    async def set_innate_soul_exp_range(
        self,
        interaction: discord.Interaction,
        grade: app_commands.Range[int, 1, 9],
        minimum: float,
        maximum: float,
    ) -> None:
        await self._ensure(interaction)
        guild = interaction.guild
        assert guild is not None
        grade_value = int(grade)
        self.state.set_innate_soul_exp_range(grade_value, minimum, maximum)
        lower, upper = self.state.innate_soul_exp_multiplier_ranges[grade_value]
        payload = await self._load_innate_soul_exp_config(guild.id)
        payload[str(grade_value)] = {"min": lower, "max": upper}
        await self.store.set(guild.id, "config", "innate_soul_exp_ranges", payload)
        soul_gain = getattr(self.config, "cultivation_tick", 0)
        min_gain = max(1, int(round(soul_gain * lower)))
        max_gain = max(min_gain, int(round(soul_gain * upper)))
        range_text = f"{min_gain}" if min_gain == max_gain else f"{min_gain}-{max_gain}"
        await interaction.response.send_message(
            (
                f"Grade {grade_value} innate souls will now grant {range_text} cultivation exp "
                f"per successful session (multipliers {lower:.2f}×–{upper:.2f}×)."
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="reset_innate_soul_exp_range",
        description="Restore default cultivation experience multipliers for innate souls",
    )
    @require_admin()
    @app_commands.describe(
        grade="Optional soul grade to reset. Leave blank to reset all overrides.",
    )
    async def reset_innate_soul_exp_range(
        self,
        interaction: discord.Interaction,
        grade: app_commands.Range[int, 1, 9] | None = None,
    ) -> None:
        await self._ensure(interaction)
        guild = interaction.guild
        assert guild is not None
        payload = await self._load_innate_soul_exp_config(guild.id)
        if grade is None:
            self.state.reset_innate_soul_exp_range()
            if payload:
                await self.store.delete(guild.id, "config", "innate_soul_exp_ranges")
            message = (
                "All innate soul grades now use the default cultivation experience "
                "multiplier."
            )
        else:
            grade_value = int(grade)
            self.state.reset_innate_soul_exp_range(grade_value)
            removed = payload.pop(str(grade_value), None)
            if payload:
                await self.store.set(guild.id, "config", "innate_soul_exp_ranges", payload)
            else:
                await self.store.delete(guild.id, "config", "innate_soul_exp_ranges")
            default_lower, default_upper = self.state.innate_soul_exp_multiplier_ranges[grade_value]
            soul_gain = getattr(self.config, "cultivation_tick", 0)
            default_gain = max(1, int(round(soul_gain * default_upper)))
            if removed is None:
                message = (
                    f"Grade {grade_value} innate souls were already using the default "
                    f"multiplier ({default_lower:.2f}×)."
                )
            else:
                message = (
                    f"Grade {grade_value} innate souls now use the default {default_gain} cultivation "
                    f"exp per session ({default_lower:.2f}×)."
                )
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(
        name="admin_help",
        description="Display detailed information for every admin command",
    )
    @require_admin()
    async def admin_help(self, interaction: discord.Interaction) -> None:
        await self._ensure(interaction)
        commands_to_report: list[app_commands.Command] = []
        for node in self.__cog_app_commands__:
            if isinstance(node, app_commands.Command):
                commands_to_report.append(node)
            elif isinstance(node, app_commands.Group):
                commands_to_report.extend(list(node.walk_commands()))

        if self._admin_access_group is not None:
            commands_to_report.extend(list(self._admin_access_group.walk_commands()))

        commands_to_report.sort(key=lambda cmd: cmd.qualified_name)

        lines: List[str] = []
        for command in commands_to_report:
            description = command.description or "No description provided."
            lines.append(f"**/{command.qualified_name}** — {description}")
            if not command.parameters:
                lines.append("• No options available.")
                continue
            for param in command.parameters:
                type_name = getattr(param.type, "name", str(param.type)).replace("_", " ").title()
                requirement = "Required" if param.required else "Optional"
                param_desc = param.description or "No description provided."
                extra_parts: List[str] = []
                if param.choices:
                    choice_labels: List[str] = []
                    for choice in param.choices:
                        label = choice.name
                        if str(choice.value) != choice.name:
                            label = f"{choice.name} ({choice.value})"
                        choice_labels.append(label)
                    if choice_labels:
                        extra_parts.append("Choices: " + ", ".join(choice_labels))
                if getattr(param, "min_value", None) is not None:
                    extra_parts.append(f"Min: {param.min_value}")
                if getattr(param, "max_value", None) is not None:
                    extra_parts.append(f"Max: {param.max_value}")
                channel_types = getattr(param, "channel_types", None)
                if channel_types:
                    extra_parts.append(
                        "Channel types: "
                        + ", ".join(channel_type.name.title() for channel_type in channel_types)
                    )
                if not param.required:
                    default = param.default if param.default is not MISSING else None
                    extra_parts.append(f"Default: {default!r}")
                detail_parts = [requirement]
                if extra_parts:
                    detail_parts.extend(extra_parts)
                detail_text = "; ".join(detail_parts)
                lines.append(
                    f"• `{param.name}` [{type_name}] — {param_desc} ({detail_text})"
                )

        if not lines:
            lines.append("No admin commands are currently registered.")

        chunks: List[str] = []
        buffer = ""
        for line in lines:
            if not buffer:
                buffer = line
                continue
            if len(buffer) + 1 + len(line) > 3900:
                chunks.append(buffer)
                buffer = line
            else:
                buffer = f"{buffer}\n{line}"
        if buffer:
            chunks.append(buffer)

        embeds: List[discord.Embed] = []
        for index, chunk in enumerate(chunks):
            embed = discord.Embed(
                title="Admin Command Reference" if index == 0 else None,
                description=chunk,
                colour=discord.Colour.blurple(),
            )
            embeds.append(embed)

        if embeds:
            await interaction.response.send_message(embed=embeds[0], ephemeral=True)
            for embed in embeds[1:]:
                await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(
                "No admin commands are currently registered.", ephemeral=True
            )

    async def _grant_admin_message(
        self, interaction: discord.Interaction, member: discord.Member
    ) -> str:
        await self._ensure(interaction)
        guild = interaction.guild
        assert guild is not None

        if member.guild.id != guild.id:
            raise app_commands.AppCommandError(
                "You can only grant admin access to members of this server."
            )
        if member.bot:
            return "Bots cannot receive bot admin access."

        admins = await self.store.get(guild.id, "admins")
        key = str(member.id)
        if key in admins:
            return f"{member.mention} already has bot admin access."

        payload = {
            "granted_by": interaction.user.id,
        }
        await self.store.set(guild.id, "admins", key, payload)
        return f"{member.mention} has been granted bot admin access."

    @app_commands.command(
        name="grant_admin",
        description="Grant bot admin access to a server member",
    )
    @require_admin()
    @app_commands.describe(member="The member who should receive bot admin access")
    async def grant_admin(
        self, interaction: discord.Interaction, member: discord.Member
    ) -> None:
        message = await self._grant_admin_message(interaction, member)
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(
        name="revoke_admin",
        description="Revoke bot admin access from a server member",
    )
    @require_admin()
    @app_commands.describe(member="The member whose bot admin access should be revoked")
    async def revoke_admin(
        self, interaction: discord.Interaction, member: discord.Member
    ) -> None:
        message = await self._revoke_admin_message(interaction, member)
        await interaction.response.send_message(message, ephemeral=True)

    async def _revoke_admin_message(
        self, interaction: discord.Interaction, member: discord.Member
    ) -> str:
        await self._ensure(interaction)
        guild = interaction.guild
        assert guild is not None

        if member.guild.id != guild.id:
            raise app_commands.AppCommandError(
                "You can only revoke admin access from members of this server."
            )

        admins = await self.store.get(guild.id, "admins")
        key = str(member.id)
        if key not in admins:
            return f"{member.mention} does not have bot admin access."

        await self.store.delete(guild.id, "admins", key)
        return f"Bot admin access has been revoked from {member.mention}."

    @app_commands.command(
        name="set_innate_soul_grade_rarity",
        description="Adjust the selection weights for innate soul grades",
    )
    @require_admin()
    @app_commands.describe(
        grade_1="Weight override for grade 1 souls (percentage value)",
        grade_2="Weight override for grade 2 souls (percentage value)",
        grade_3="Weight override for grade 3 souls (percentage value)",
        grade_4="Weight override for grade 4 souls (percentage value)",
        grade_5="Weight override for grade 5 souls (percentage value)",
        grade_6="Weight override for grade 6 souls (percentage value)",
        grade_7="Weight override for grade 7 souls (percentage value)",
        grade_8="Weight override for grade 8 souls (percentage value)",
        grade_9="Weight override for grade 9 souls (percentage value)",
        count_1="Weight override for receiving 1 innate soul",
        count_2="Weight override for receiving 2 innate souls",
        count_3="Weight override for receiving 3 innate souls",
        count_4="Weight override for receiving 4 innate souls",
        count_5="Weight override for receiving 5 innate souls",
        count_6="Weight override for receiving 6 innate souls",
        count_7="Weight override for receiving 7 innate souls",
        count_8="Weight override for receiving 8 innate souls",
        count_9="Weight override for receiving 9 innate souls",
        reset_grades="Clear all grade overrides and restore defaults",
        reset_counts="Clear all soul count overrides and restore defaults",
    )
    async def set_innate_soul_grade_rarity(
        self,
        interaction: discord.Interaction,
        grade_1: app_commands.Range[float, 0, 1000] | None = None,
        grade_2: app_commands.Range[float, 0, 1000] | None = None,
        grade_3: app_commands.Range[float, 0, 1000] | None = None,
        grade_4: app_commands.Range[float, 0, 1000] | None = None,
        grade_5: app_commands.Range[float, 0, 1000] | None = None,
        grade_6: app_commands.Range[float, 0, 1000] | None = None,
        grade_7: app_commands.Range[float, 0, 1000] | None = None,
        grade_8: app_commands.Range[float, 0, 1000] | None = None,
        grade_9: app_commands.Range[float, 0, 1000] | None = None,
        count_1: app_commands.Range[float, 0, 1000] | None = None,
        count_2: app_commands.Range[float, 0, 1000] | None = None,
        count_3: app_commands.Range[float, 0, 1000] | None = None,
        count_4: app_commands.Range[float, 0, 1000] | None = None,
        count_5: app_commands.Range[float, 0, 1000] | None = None,
        count_6: app_commands.Range[float, 0, 1000] | None = None,
        count_7: app_commands.Range[float, 0, 1000] | None = None,
        count_8: app_commands.Range[float, 0, 1000] | None = None,
        count_9: app_commands.Range[float, 0, 1000] | None = None,
        reset_grades: bool = False,
        reset_counts: bool = False,
    ) -> None:
        await self._ensure(interaction)
        guild = interaction.guild
        assert guild is not None

        grade_inputs = {
            1: grade_1,
            2: grade_2,
            3: grade_3,
            4: grade_4,
            5: grade_5,
            6: grade_6,
            7: grade_7,
            8: grade_8,
            9: grade_9,
        }
        grade_overrides = {
            grade: max(0.0, float(value))
            for grade, value in grade_inputs.items()
            if value is not None
        }

        count_inputs = {
            1: count_1,
            2: count_2,
            3: count_3,
            4: count_4,
            5: count_5,
            6: count_6,
            7: count_7,
            8: count_8,
            9: count_9,
        }
        count_overrides = {
            count: max(0.0, float(value))
            for count, value in count_inputs.items()
            if value is not None
        }

        config_bucket = await self.store.get(guild.id, "config")
        config_data: Mapping[str, Any] = config_bucket

        messages: list[str] = []
        detail_lines: list[str] = []

        if grade_overrides:
            existing_grade_payload = config_data.get("innate_soul_grade_weights", {})
            merged_grades: dict[int, float] = {}
            if isinstance(existing_grade_payload, dict):
                for key, amount in existing_grade_payload.items():
                    try:
                        grade_key = int(key)
                    except (TypeError, ValueError):
                        continue
                    if 1 <= grade_key <= 9:
                        merged_grades[grade_key] = float(amount)
            merged_grades.update(grade_overrides)
            await self.store.set(
                guild.id, "config", "innate_soul_grade_weights", merged_grades
            )
            messages.append("Soul grade rarity overrides updated.")
            detail_lines.append(
                "Grades updated: "
                + ", ".join(
                    f"Grade {grade}: {amount}"
                    for grade, amount in sorted(grade_overrides.items())
                )
            )
        elif reset_grades:
            await self.store.delete(guild.id, "config", "innate_soul_grade_weights")
            messages.append("Soul grade rarity overrides cleared; defaults restored.")

        if count_overrides:
            existing_count_payload = config_data.get("innate_soul_count_weights", {})
            merged_counts: dict[int, float] = {}
            if isinstance(existing_count_payload, dict):
                for key, amount in existing_count_payload.items():
                    try:
                        count_key = int(key)
                    except (TypeError, ValueError):
                        continue
                    if count_key >= 1:
                        merged_counts[count_key] = float(amount)
            merged_counts.update(count_overrides)
            await self.store.set(
                guild.id, "config", "innate_soul_count_weights", merged_counts
            )
            messages.append("Soul count rarity overrides updated.")
            detail_lines.append(
                "Soul counts updated: "
                + ", ".join(
                    f"{count} soul(s): {amount}"
                    for count, amount in sorted(count_overrides.items())
                )
            )
        elif reset_counts:
            await self.store.delete(guild.id, "config", "innate_soul_count_weights")
            messages.append("Soul count rarity overrides cleared; defaults restored.")

        summary_lines: list[str] = []
        player_cog = self.bot.get_cog("PlayerCog")
        if player_cog:
            refresher = getattr(player_cog, "refresh_rarity_config", None)
            grade_getter = getattr(player_cog, "get_innate_soul_grade_weights", None)
            count_getter = getattr(player_cog, "get_innate_soul_count_weights", None)
            if callable(refresher) and callable(grade_getter) and callable(count_getter):
                try:
                    await refresher(guild.id)
                    grade_weights_map = await grade_getter(guild.id)
                    count_weights_map = await count_getter(guild.id)
                except Exception:
                    grade_weights_map = None
                    count_weights_map = None
                else:
                    total_grades = (
                        sum(grade_weights_map.values()) if grade_weights_map else 0
                    )
                    if grade_weights_map and total_grades > 0:
                        summary_lines.extend(
                            f"Grade {grade}: {weight / total_grades * 100:.2f}%"
                            for grade, weight in sorted(grade_weights_map.items())
                        )
                    total_counts = (
                        sum(count_weights_map.values()) if count_weights_map else 0
                    )
                    if count_weights_map and total_counts > 0:
                        summary_lines.append(
                            "Soul counts: "
                            + ", ".join(
                                f"{count} souls → {weight / total_counts * 100:.2f}%"
                                for count, weight in sorted(count_weights_map.items())
                            )
                        )

        message = " ".join(messages) if messages else "No changes applied."
        if detail_lines:
            message += "\n" + "\n".join(detail_lines)
        if summary_lines:
            message += "\nCurrent distribution: " + ", ".join(summary_lines)
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(
        name="birth_trait", description="Configure the distribution of birth traits"
    )
    @require_admin()
    @app_commands.describe(
        chance="Percentage chance (0-100) for a new player to receive a birth trait",
        trait_key="Trait key to adjust in the birth pool",
        weight="Relative weight for the specified trait (0 removes it)",
        reset="Clear all configured birth trait settings",
    )
    async def birth_trait(
        self,
        interaction: discord.Interaction,
        chance: float | None = None,
        trait_key: str | None = None,
        weight: float | None = None,
        reset: bool = False,
    ) -> None:
        await self._ensure(interaction)
        guild = interaction.guild
        assert guild is not None

        if reset:
            await self.store.delete(guild.id, "config", "birth_trait")
            player_cog = self.bot.get_cog("PlayerCog")
            refresher = getattr(player_cog, "refresh_birth_trait_config", None)
            if callable(refresher):
                await refresher(guild.id)
            await interaction.response.send_message(
                "Birth trait configuration cleared; defaults restored.", ephemeral=True
            )
            return

        config_bucket = await self.store.get(guild.id, "config")
        existing_payload = config_bucket.get("birth_trait", {})
        if not isinstance(existing_payload, Mapping):
            existing_payload = {}
        current_chance = float(existing_payload.get("chance", 0.0))
        current_weights: dict[str, float] = {}
        weights_payload = existing_payload.get("weights", {})
        if isinstance(weights_payload, Mapping):
            for key, value in weights_payload.items():
                try:
                    current_weights[str(key)] = float(value)
                except (TypeError, ValueError):
                    continue

        messages: list[str] = []
        if chance is not None:
            clamped = max(0.0, min(100.0, float(chance)))
            current_chance = clamped
            messages.append(f"Birth trait chance set to {clamped:.2f}%.")

        if trait_key:
            if weight is None:
                await interaction.response.send_message(
                    "You must provide a weight when specifying a trait.", ephemeral=True
                )
                return
            normalized_key = trait_key.strip()
            if not normalized_key:
                await interaction.response.send_message(
                    "Trait key cannot be empty.", ephemeral=True
                )
                return
            trait_obj = self.state.traits.get(normalized_key)
            if weight > 0 and not trait_obj:
                await interaction.response.send_message(
                    "That trait key is unknown.", ephemeral=True
                )
                return
            if weight <= 0:
                current_weights.pop(normalized_key, None)
                display = trait_obj.name if trait_obj else normalized_key
                messages.append(f"Removed {display} from the birth trait pool.")
            else:
                current_weights[normalized_key] = float(weight)
                display = trait_obj.name if trait_obj else normalized_key
                messages.append(
                    f"Set birth trait weight for {display} to {float(weight):.2f}."
                )

        payload: dict[str, float | dict[str, float]] = {
            "chance": current_chance,
            "weights": current_weights,
        }
        if current_chance <= 0 and not current_weights:
            await self.store.delete(guild.id, "config", "birth_trait")
        else:
            await self.store.set(guild.id, "config", "birth_trait", payload)

        player_cog = self.bot.get_cog("PlayerCog")
        refresher = getattr(player_cog, "refresh_birth_trait_config", None)
        if callable(refresher):
            await refresher(guild.id)

        if not messages:
            messages.append("No changes applied.")
        await interaction.response.send_message("\n".join(messages), ephemeral=True)

    @birth_trait.autocomplete("trait_key")
    async def birth_trait_trait_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.traits, current=current)

    @app_commands.command(
        name="birth_race", description="Configure the distribution of birth races"
    )
    @require_admin()
    @app_commands.describe(
        chance=(
            "Percentage chance (0-100) to draw a race from the configured birth pool "
            "when no race role is matched"
        ),
        race_key="Race key to adjust in the birth pool",
        weight="Relative weight for the specified race (0 removes it)",
        reset="Clear all configured birth race settings",
    )
    async def birth_race(
        self,
        interaction: discord.Interaction,
        chance: float | None = None,
        race_key: str | None = None,
        weight: float | None = None,
        reset: bool = False,
    ) -> None:
        await self._ensure(interaction)
        guild = interaction.guild
        assert guild is not None

        if reset:
            await self.store.delete(guild.id, "config", "birth_race")
            player_cog = self.bot.get_cog("PlayerCog")
            refresher = getattr(player_cog, "refresh_birth_race_config", None)
            if callable(refresher):
                await refresher(guild.id)
            await interaction.response.send_message(
                "Birth race configuration cleared; defaults restored.", ephemeral=True
            )
            return

        config_bucket = await self.store.get(guild.id, "config")
        existing_payload = config_bucket.get("birth_race", {})
        if not isinstance(existing_payload, Mapping):
            existing_payload = {}
        current_chance = float(existing_payload.get("chance", 0.0))
        current_weights: dict[str, float] = {}
        weights_payload = existing_payload.get("weights", {})
        if isinstance(weights_payload, Mapping):
            for key, value in weights_payload.items():
                try:
                    current_weights[str(key)] = float(value)
                except (TypeError, ValueError):
                    continue

        messages: list[str] = []
        if chance is not None:
            clamped = max(0.0, min(100.0, float(chance)))
            current_chance = clamped
            messages.append(f"Birth race chance set to {clamped:.2f}%.")

        if race_key:
            if weight is None:
                await interaction.response.send_message(
                    "You must provide a weight when specifying a race.", ephemeral=True
                )
                return
            normalized_key = race_key.strip()
            if not normalized_key:
                await interaction.response.send_message(
                    "Race key cannot be empty.", ephemeral=True
                )
                return
            race_obj = self.state.races.get(normalized_key)
            if weight > 0 and not race_obj:
                await interaction.response.send_message(
                    "That race key is unknown.", ephemeral=True
                )
                return
            if weight <= 0:
                current_weights.pop(normalized_key, None)
                display = race_obj.name if race_obj else normalized_key
                messages.append(f"Removed {display} from the birth race pool.")
            else:
                current_weights[normalized_key] = float(weight)
                display = race_obj.name if race_obj else normalized_key
                messages.append(
                    f"Set birth race weight for {display} to {float(weight):.2f}."
                )

        payload: dict[str, float | dict[str, float]] = {
            "chance": current_chance,
            "weights": current_weights,
        }
        if current_chance <= 0 and not current_weights:
            await self.store.delete(guild.id, "config", "birth_race")
        else:
            await self.store.set(guild.id, "config", "birth_race", payload)

        player_cog = self.bot.get_cog("PlayerCog")
        refresher = getattr(player_cog, "refresh_birth_race_config", None)
        if callable(refresher):
            await refresher(guild.id)

        if not messages:
            messages.append("No changes applied.")
        await interaction.response.send_message("\n".join(messages), ephemeral=True)

    @birth_race.autocomplete("race_key")
    async def birth_race_race_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.races, current=current)

    @app_commands.command(
        name="set_training_cooldown",
        description="Adjust the cooldown before cultivators can train again",
    )
    @require_admin()
    @app_commands.describe(
        seconds="Cooldown duration in seconds (0 removes the timer)",
        user="Optional cultivator to apply a personal cooldown override",
    )
    async def set_training_cooldown(
        self,
        interaction: discord.Interaction,
        seconds: int,
        user: discord.User | None = None,
    ) -> None:
        await self._ensure(interaction)
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command must be used inside a guild.", ephemeral=True
            )
            return

        try:
            cooldown_value = max(0, int(seconds))
        except (TypeError, ValueError):
            cooldown_value = 0

        bucket = await self.store.get(guild.id, "config")
        payload = bucket.get("cultivation_cooldown", {})
        default_value = DEFAULT_CULTIVATION_COOLDOWN
        overrides: dict[int, int] = {}
        if isinstance(payload, Mapping):
            try:
                default_value = max(0, int(payload.get("default", default_value)))
            except (TypeError, ValueError):
                default_value = DEFAULT_CULTIVATION_COOLDOWN
            override_payload = payload.get("overrides", {})
            if isinstance(override_payload, Mapping):
                for raw_user, raw_value in override_payload.items():
                    try:
                        user_id = int(raw_user)
                        value = max(0, int(raw_value))
                    except (TypeError, ValueError):
                        continue
                    if value > 0:
                        overrides[user_id] = value

        response_lines: list[str] = []
        if user is not None:
            if cooldown_value <= 0:
                removed = overrides.pop(user.id, None)
                if removed is not None:
                    response_lines.append(
                        f"Removed the personal training cooldown for {user.mention}."
                    )
                else:
                    response_lines.append(
                        f"{user.mention} did not have a personal cooldown configured."
                    )
            else:
                overrides[user.id] = cooldown_value
                response_lines.append(
                    f"Set {user.mention}'s training cooldown to {cooldown_value} seconds."
                )
        else:
            default_value = cooldown_value
            response_lines.append(
                f"Global training cooldown updated to {cooldown_value} seconds."
            )

        store_payload = {
            "default": default_value,
            "overrides": {str(uid): value for uid, value in overrides.items()},
        }
        await self.store.set(guild.id, "config", "cultivation_cooldown", store_payload)

        player_cog = self.bot.get_cog("PlayerCog")
        setter = getattr(player_cog, "set_cultivation_cooldown_config", None)
        if callable(setter):
            setter(guild.id, default_value, overrides)

        if not response_lines:
            response_lines.append("No changes were applied.")

        await interaction.response.send_message(
            "\n".join(response_lines), ephemeral=True
        )

    @app_commands.command(
        name="set_enemy_loot_chance",
        description="Adjust the drop chance for an enemy's loot entry",
    )
    @require_admin()
    @app_commands.describe(
        enemy="Enemy key to update",
        loot="Loot entry key to adjust",
        chance="New drop chance percentage (0-100)",
    )
    async def set_enemy_loot_chance(
        self, interaction: discord.Interaction, enemy: str, loot: str, chance: float
    ) -> None:
        await self._ensure(interaction)
        enemy_obj = self.state.enemies.get(enemy)
        if not enemy_obj:
            await interaction.response.send_message(
                "Unknown enemy key.", ephemeral=True
            )
            return
        drop = enemy_obj.loot_table.get(loot)
        if not drop:
            await interaction.response.send_message(
                "That enemy has no loot entry with that key.", ephemeral=True
            )
            return
        if chance < 0 or chance > 100:
            await interaction.response.send_message(
                "Chance must be between 0 and 100.", ephemeral=True
            )
            return

        fraction = chance / 100.0
        if fraction <= 0:
            enemy_obj.loot_table.pop(loot, None)
            await self._store_entity(
                interaction, "enemies", enemy_obj.key, asdict(enemy_obj)
            )
            message = f"Removed loot {loot} from {enemy_obj.name}."
        else:
            enemy_obj.loot_table[loot] = LootDrop(
                chance=fraction, amount=drop.amount, kind=drop.kind
            )
            await self._store_entity(
                interaction, "enemies", enemy_obj.key, asdict(enemy_obj)
            )
            message = (
                f"Updated {loot} drop for {enemy_obj.name} to {fraction * 100:.2f}%."
            )
        await interaction.response.send_message(message, ephemeral=True)

    @set_enemy_loot_chance.autocomplete("enemy")
    async def set_enemy_loot_enemy_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.enemies, current=current)

    @set_enemy_loot_chance.autocomplete("loot")
    async def set_enemy_loot_entry_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        enemy_key = getattr(interaction.namespace, "enemy", "")
        enemy_obj = self.state.enemies.get(enemy_key)
        if not enemy_obj:
            return []
        return self._iter_loot_table_choices(enemy_obj.loot_table, current=current)

    @app_commands.command(
        name="set_boss_loot_chance",
        description="Adjust the drop chance for a boss's loot entry",
    )
    @require_admin()
    @app_commands.describe(
        boss="Boss key to update",
        loot="Loot entry key to adjust",
        chance="New drop chance percentage (0-100)",
    )
    async def set_boss_loot_chance(
        self, interaction: discord.Interaction, boss: str, loot: str, chance: float
    ) -> None:
        await self._ensure(interaction)
        boss_obj = self.state.bosses.get(boss)
        if not boss_obj:
            await interaction.response.send_message(
                "Unknown boss key.", ephemeral=True
            )
            return
        drop = boss_obj.loot_table.get(loot)
        if not drop:
            await interaction.response.send_message(
                "That boss has no loot entry with that key.", ephemeral=True
            )
            return
        if chance < 0 or chance > 100:
            await interaction.response.send_message(
                "Chance must be between 0 and 100.", ephemeral=True
            )
            return

        fraction = chance / 100.0
        if fraction <= 0:
            boss_obj.loot_table.pop(loot, None)
            await self._store_entity(
                interaction, "bosses", boss_obj.key, asdict(boss_obj)
            )
            message = f"Removed loot {loot} from {boss_obj.name}."
        else:
            boss_obj.loot_table[loot] = LootDrop(
                chance=fraction, amount=drop.amount, kind=drop.kind
            )
            await self._store_entity(
                interaction, "bosses", boss_obj.key, asdict(boss_obj)
            )
            message = (
                f"Updated {loot} drop for {boss_obj.name} to {fraction * 100:.2f}%."
            )
        await interaction.response.send_message(message, ephemeral=True)

    @set_boss_loot_chance.autocomplete("boss")
    async def set_boss_loot_boss_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.bosses, current=current)

    @set_boss_loot_chance.autocomplete("loot")
    async def set_boss_loot_entry_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        boss_key = getattr(interaction.namespace, "boss", "")
        boss_obj = self.state.bosses.get(boss_key)
        if not boss_obj:
            return []
        return self._iter_loot_table_choices(boss_obj.loot_table, current=current)

    @app_commands.command(
        name="set_wander_loot_chance",
        description="Adjust the drop chance for wandering loot",
    )
    @require_admin()
    @app_commands.describe(
        channel="Channel whose wander loot should be adjusted",
        loot="Loot entry key to adjust",
        chance="New drop chance percentage (0-100)",
    )
    async def set_wander_loot_chance(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        loot: str,
        chance: float,
    ) -> None:
        await self._ensure(interaction)
        location_obj = self.state.get_location_for_channel(channel.id)
        if not location_obj:
            await interaction.response.send_message(
                "That location has not been configured yet.", ephemeral=True
            )
            return
        drop = location_obj.wander_loot.get(loot)
        if not drop:
            await interaction.response.send_message(
                "That location has no wander loot entry with that key.", ephemeral=True
            )
            return
        if chance < 0 or chance > 100:
            await interaction.response.send_message(
                "Chance must be between 0 and 100.", ephemeral=True
            )
            return

        fraction = chance / 100.0
        if fraction <= 0:
            location_obj.wander_loot.pop(loot, None)
            await self._store_entity(
                interaction, "locations", str(channel.id), asdict(location_obj)
            )
            message = f"Removed wander loot {loot} from {location_obj.name}."
        else:
            location_obj.wander_loot[loot] = LootDrop(
                chance=fraction, amount=drop.amount, kind=drop.kind
            )
            await self._store_entity(
                interaction, "locations", str(channel.id), asdict(location_obj)
            )
            message = (
                f"Updated wander loot {loot} for {location_obj.name} to {fraction * 100:.2f}%."
            )
        await interaction.response.send_message(message, ephemeral=True)

    @set_wander_loot_chance.autocomplete("loot")
    async def set_wander_loot_entry_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        channel_param = getattr(interaction.namespace, "channel", None)
        channel_id: int | None = None
        if isinstance(channel_param, discord.abc.GuildChannel):
            channel_id = channel_param.id
        elif isinstance(channel_param, int):
            channel_id = channel_param
        if channel_id is None:
            return []
        location_obj = self.state.get_location_for_channel(channel_id)
        if not location_obj:
            return []
        return self._iter_loot_table_choices(location_obj.wander_loot, current=current)

    async def _persist_cultivation_stage(
        self,
        interaction: discord.Interaction,
        *,
        key: str,
        name: str,
        success_rate: float,
        path: str,
        soul_stat: float = 10.0,
        stat_overrides: Mapping[str, float] | None = None,
        breakthrough_failure_loss: float = 0.75,
        exp_required: int = 100,
        role: discord.Role | None = None,
        realm: str = "",
        phase: CultivationPhase | str = CultivationPhase.INITIAL,
        realm_order: int = 0,
        step: int = 1,
    ) -> CultivationStage:
        guild = interaction.guild
        assert guild is not None
        path_value = CultivationPath.from_value(path)
        previous = self.state.get_stage(key, path_value)
        previous_storage_key: str | None = None
        if previous is not None:
            previous_storage_key = self.state.resolve_stage_storage_key(path_value, key)
        allowed_stats = STAGE_STAT_TARGETS.get(path_value, ())
        stat_payload = {stat: 0.0 for stat in PLAYER_STAT_NAMES}
        if stat_overrides:
            for stat_name, value in stat_overrides.items():
                if stat_name not in allowed_stats:
                    continue
                try:
                    stat_payload[stat_name] = float(value)
                except (TypeError, ValueError):
                    continue
        try:
            required_exp = max(1, int(exp_required))
        except (TypeError, ValueError):
            required_exp = 100
        try:
            soul_value = float(base_stat)
        except (TypeError, ValueError):
            soul_value = 10.0
        try:
            success_value = max(0.0, min(1.0, float(success_rate)))
        except (TypeError, ValueError):
            success_value = 0.5
        try:
            failure_loss = max(0.0, min(1.0, float(breakthrough_failure_loss)))
        except (TypeError, ValueError):
            failure_loss = 0.75
        realm_name = " ".join(str(realm).split()) if realm else ""
        phase_value = CultivationPhase.from_value(phase)
        storage_key = f"{path_value.value}:{key}"
        payload = {
            "key": key,
            "name": name,
            "success_rate": success_value,
            "path": path_value.value,
            "base_stat": soul_value,
            **stat_payload,
            "breakthrough_failure_loss": failure_loss,
            "exp_required": required_exp,
            "role_id": role.id if role else None,
            "realm": realm_name or name,
            "phase": phase_value,
            "realm_order": realm_order,
            "step": step,
        }

        def _stage_dump(stage: CultivationStage) -> Dict[str, Any]:
            dumped = asdict(stage)
            dumped["phase"] = stage.phase.value
            return dumped

        stage = await self._validate_and_store(
            interaction,
            cls=CultivationStage,
            payload=payload,
            collection="cultivation_stages",
            key=storage_key,
            entity_label=f"cultivation stage '{name}'",
            dump=_stage_dump,
        )
        if stage is None:
            return None

        self.state.register_stage(stage, storage_key=storage_key)
        if previous_storage_key and previous_storage_key != storage_key:
            await self.store.delete(guild.id, "cultivation_stages", previous_storage_key)
        previous_role = previous.role_id if previous else None
        await self._sync_stage_roles(guild, stage, previous_role)
        return stage

    async def _create_cultivation_realm(
        self,
        interaction: discord.Interaction,
        *,
        key: str,
        name: str,
        path: CultivationPath,
        soul_stat: float = 10.0,
        stat_overrides: Mapping[str, float] | None = None,
        breakthrough_failure_loss: float = 0.75,
        exp_required: int = 100,
        success_rate: float = 0.5,
        role: discord.Role | None = None,
        realm_order: int = 0,
        soul_stat_step: float = 0.0,
        exp_required_step: int = 0,
        success_rate_step: float = 0.0,
        role_phase: CultivationPhase | None = None,
        step: int = 1,
    ) -> None:
        await self._ensure(interaction)
        await interaction.response.defer(ephemeral=True)
        realm_label = " ".join(name.split()) if name else key
        created_keys: list[str] = []
        for phase in CultivationPhase:
            index = phase.order_index
            stage_key = f"{key}_{phase.value}"
            stage_name = f"{realm_label} ({phase.display_name})"
            stage_role = role if role and (role_phase is None or phase is role_phase) else None
            stage = await self._persist_cultivation_stage(
                interaction,
                key=stage_key,
                name=stage_name,
                success_rate=success_rate + success_rate_step * index,
                path=path.value,
                soul_stat=base_stat + soul_stat_step * index,
                stat_overrides=stat_overrides,
                breakthrough_failure_loss=breakthrough_failure_loss,
                exp_required=exp_required + exp_required_step * index,
                role=stage_role,
                realm=realm_label,
                phase=phase,
                realm_order=realm_order,
                step=step,
            )
            if stage is None:
                return
            created_keys.append(stage.key)
        summary = ", ".join(phase.display_name for phase in CultivationPhase)
        await interaction.followup.send(
            f"Realm {realm_label or key} stored with stages: {summary}.",
            ephemeral=True,
        )

    @app_commands.command(
        name="create_qi_realm",
        description="Create or update a qi cultivation realm",
    )
    @require_admin()
    @app_commands.describe(
        soul_stat="Soul stat value applied when players reach the initial stage",
        spirit="Bonus spirit granted at each stage",
        qi_control="Bonus qi control granted at each stage",
        breakthrough_failure_loss="Percentage of progress lost on failed breakthroughs",
        exp_required="Experience required to attempt the next breakthrough",
        role="Role granted when reaching the configured stage milestone",
        realm_order="Ordering index used to sort realms in catalogues",
        soul_stat_step="Additional soul stat added per stage progression",
        exp_required_step="Additional EXP required added at each successive stage",
        success_rate_step="Additional success chance added per stage (0-1 scale)",
        role_phase="Stage milestone that should receive the configured role",
        step="Step number associated with this realm (0 for mortal, 1 for first step, etc.)",
    )
    @app_commands.choices(role_phase=CULTIVATION_PHASE_CHOICES)
    async def create_qi_realm(
        self,
        interaction: discord.Interaction,
        key: str,
        name: str,
        success_rate: float,
        exp_required: app_commands.Range[int, 1, 1_000_000] = 100,
        soul_stat: float = 10.0,
        spirit: float = 0.0,
        qi_control: float = 0.0,
        breakthrough_failure_loss: float = 0.75,
        role: discord.Role | None = None,
        realm_order: int = 0,
        soul_stat_step: float = 0.0,
        exp_required_step: int = 0,
        success_rate_step: float = 0.0,
        role_phase: Optional[app_commands.Choice[str]] = None,
        step: app_commands.Range[int, 0, 1_000_000] = 1,
    ) -> None:
        role_phase_value = (
            CultivationPhase.from_value(role_phase.value)
            if isinstance(role_phase, app_commands.Choice)
            else None
        )
        await self._create_cultivation_realm(
            interaction,
            key=key,
            name=name,
            path=CultivationPath.QI,
            soul_stat=base_stat,
            stat_overrides={
                "spirit": spirit,
                "qi_control": qi_control,
            },
            breakthrough_failure_loss=breakthrough_failure_loss,
            exp_required=exp_required,
            success_rate=success_rate,
            role=role,
            realm_order=realm_order,
            soul_stat_step=base_stat_step,
            exp_required_step=exp_required_step,
            success_rate_step=success_rate_step,
            role_phase=role_phase_value,
            step=int(step),
        )

    @app_commands.command(
        name="create_body_realm",
        description="Create or update a body refinement realm",
    )
    @require_admin()
    @app_commands.describe(
        soul_stat="Soul stat value applied when players reach the initial stage",
        physical_health="Bonus physical health granted at each stage",
        strength="Bonus strength granted at each stage",
        defense="Bonus defense granted at each stage",
        agility="Bonus agility granted at each stage",
        breakthrough_failure_loss="Percentage of progress lost on failed breakthroughs",
        exp_required="Experience required to attempt the next breakthrough",
        role="Role granted when reaching the configured stage milestone",
        realm_order="Ordering index used to sort realms in catalogues",
        soul_stat_step="Additional soul stat added per stage progression",
        exp_required_step="Additional EXP required added at each successive stage",
        success_rate_step="Additional success chance added per stage (0-1 scale)",
        role_phase="Stage milestone that should receive the configured role",
        step="Step number associated with this realm (0 for mortal, 1 for first step, etc.)",
    )
    @app_commands.choices(role_phase=CULTIVATION_PHASE_CHOICES)
    async def create_body_realm(
        self,
        interaction: discord.Interaction,
        key: str,
        name: str,
        success_rate: float,
        exp_required: app_commands.Range[int, 1, 1_000_000] = 100,
        soul_stat: float = 10.0,
        physical_health: float = 0.0,
        strength: float = 0.0,
        defense: float = 0.0,
        agility: float = 0.0,
        breakthrough_failure_loss: float = 0.75,
        role: discord.Role | None = None,
        realm_order: int = 0,
        soul_stat_step: float = 0.0,
        exp_required_step: int = 0,
        success_rate_step: float = 0.0,
        role_phase: Optional[app_commands.Choice[str]] = None,
        step: app_commands.Range[int, 0, 1_000_000] = 1,
    ) -> None:
        role_phase_value = (
            CultivationPhase.from_value(role_phase.value)
            if isinstance(role_phase, app_commands.Choice)
            else None
        )
        await self._create_cultivation_realm(
            interaction,
            key=key,
            name=name,
            path=CultivationPath.BODY,
            soul_stat=base_stat,
            stat_overrides={
                "physical_health": physical_health,
                "strength": strength,
                "defense": defense,
                "agility": agility,
            },
            breakthrough_failure_loss=breakthrough_failure_loss,
            exp_required=exp_required,
            success_rate=success_rate,
            role=role,
            realm_order=realm_order,
            soul_stat_step=base_stat_step,
            exp_required_step=exp_required_step,
            success_rate_step=success_rate_step,
            role_phase=role_phase_value,
            step=int(step),
        )

    @app_commands.command(
        name="create_soul_realm",
        description="Create or update a soul purification realm",
    )
    @require_admin()
    @app_commands.describe(
        soul_stat="Soul stat value applied when players reach the initial stage",
        spiritual_health="Bonus spiritual health granted at each stage",
        soul_power="Bonus soul power granted at each stage",
        breakthrough_failure_loss="Percentage of progress lost on failed breakthroughs",
        exp_required="Experience required to attempt the next breakthrough",
        role="Role granted when reaching the configured stage milestone",
        realm_order="Ordering index used to sort realms in catalogues",
        soul_stat_step="Additional soul stat added per stage progression",
        exp_required_step="Additional EXP required added at each successive stage",
        success_rate_step="Additional success chance added per stage (0-1 scale)",
        role_phase="Stage milestone that should receive the configured role",
        step="Step number associated with this realm (0 for mortal, 1 for first step, etc.)",
    )
    @app_commands.choices(role_phase=CULTIVATION_PHASE_CHOICES)
    async def create_soul_realm(
        self,
        interaction: discord.Interaction,
        key: str,
        name: str,
        success_rate: float,
        exp_required: app_commands.Range[int, 1, 1_000_000] = 100,
        soul_stat: float = 10.0,
        spiritual_health: float = 0.0,
        soul_power: float = 0.0,
        breakthrough_failure_loss: float = 0.75,
        role: discord.Role | None = None,
        realm_order: int = 0,
        soul_stat_step: float = 0.0,
        exp_required_step: int = 0,
        success_rate_step: float = 0.0,
        role_phase: Optional[app_commands.Choice[str]] = None,
        step: app_commands.Range[int, 0, 1_000_000] = 1,
    ) -> None:
        role_phase_value = (
            CultivationPhase.from_value(role_phase.value)
            if isinstance(role_phase, app_commands.Choice)
            else None
        )
        await self._create_cultivation_realm(
            interaction,
            key=key,
            name=name,
            path=CultivationPath.SOUL,
            soul_stat=base_stat,
            stat_overrides={
                "spiritual_health": spiritual_health,
                "soul_power": soul_power,
            },
            breakthrough_failure_loss=breakthrough_failure_loss,
            exp_required=exp_required,
            success_rate=success_rate,
            role=role,
            realm_order=realm_order,
            soul_stat_step=base_stat_step,
            exp_required_step=exp_required_step,
            success_rate_step=success_rate_step,
            role_phase=role_phase_value,
            step=int(step),
        )

    @app_commands.command(name="delete_profile", description="Remove a player's cultivation profile")
    @require_admin()
    @app_commands.guild_only()
    async def delete_profile(
        self, interaction: discord.Interaction, member: discord.Member
    ) -> None:
        await self._ensure(interaction)
        guild = interaction.guild
        assert guild is not None
        removed = await self._clear_player_profile(guild.id, member.id)
        if removed:
            message = (
                f"Cleared the cultivation record for {member.display_name}. "
                "They can register again whenever they're ready."
            )
        else:
            message = f"{member.display_name} does not have a cultivation profile."
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(
        name="sync",
        description="Refresh the bot's slash commands for this server",
    )
    @require_admin()
    @app_commands.guild_only()
    async def sync_app_commands(self, interaction: discord.Interaction) -> None:
        await self._ensure(interaction)
        guild = interaction.guild
        assert guild is not None

        await interaction.response.defer(ephemeral=True, thinking=True)
        global_synced = await self.bot.tree.sync()
        guild_synced = await self.bot.tree.sync(guild=guild)
        summary_bits = [
            f"{len(global_synced)} global command{'s' if len(global_synced) != 1 else ''}",
            f"{len(guild_synced)} guild command{'s' if len(guild_synced) != 1 else ''}",
        ]
        message = (
            "Synced "
            + " and ".join(summary_bits)
            + f" for {discord.utils.escape_markdown(guild.name)}."
        )
        await interaction.followup.send(message, ephemeral=True)

    @app_commands.command(
        name="set_profile_image",
        description="Assign a custom profile image to a cultivator",
    )
    @require_admin()
    @app_commands.guild_only()
    @app_commands.describe(
        player="Select the cultivator whose profile image should change",
        image="Upload the image file; leave blank to clear the override",
    )
    async def set_profile_image(
        self,
        interaction: discord.Interaction,
        player: discord.Member,
        image: Optional[discord.Attachment] = None,
    ) -> None:
        await self._ensure(interaction)
        guild = interaction.guild
        assert guild is not None
        target = await self._fetch_player(guild.id, player.id)
        if not target:
            await interaction.response.send_message(
                f"{player.mention} has not registered as a cultivator.",
                ephemeral=True,
            )
            return

        normalized_url: str | None
        if image is None:
            normalized_url = None
        else:
            content_type = image.content_type
            if content_type is not None and not content_type.startswith("image/"):
                await interaction.response.send_message(
                    "Only image files can be used as profile pictures.",
                    ephemeral=True,
                )
                return
            normalized_url = image.url

        target.profile_image_url = normalized_url
        player_cog = self.bot.get_cog("PlayerCog")
        if player_cog is not None and hasattr(player_cog, "_save_player"):
            await player_cog._save_player(guild.id, target)  # type: ignore[attr-defined]
        else:
            await self.store.upsert_player(guild.id, asdict(target))

        if normalized_url:
            message = f"Set a custom profile image for {player.mention}."
        else:
            message = f"Cleared the custom profile image for {player.mention}."
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(
        name="view_profiles",
        description="Review the profile of a registered cultivator",
    )
    @app_commands.describe(player="Select the cultivator to review")
    @require_admin()
    async def view_profiles(
        self, interaction: discord.Interaction, player: discord.User
    ) -> None:
        await self._ensure(interaction)
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command must be used in a guild.", ephemeral=True
            )
            return
        player_cog = self.bot.get_cog("PlayerCog")
        if player_cog is None or not hasattr(player_cog, "_build_profile_view"):
            await interaction.response.send_message(
                "The player module is not loaded.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        target = await self._fetch_player(guild.id, player.id)
        if not target:
            await interaction.followup.send(
                f"{player.mention} has not registered as a cultivator.",
                ephemeral=True,
            )
            return

        # Ensure shared state is available for profile rendering.
        try:
            await player_cog.ensure_guild_loaded(guild.id)  # type: ignore[attr-defined]
        except AttributeError:
            pass

        bot_user = interaction.client.user
        bot_avatar_url = (
            bot_user.display_avatar.url
            if bot_user is not None
            else interaction.user.display_avatar.url
        )
        try:
            awaitable = guild.fetch_member
        except AttributeError:
            awaitable = None

        member = guild.get_member(player.id)
        if member is None and awaitable is not None:
            try:
                member = await awaitable(player.id)  # type: ignore[operator]
            except discord.HTTPException:
                member = None
        avatar_source: discord.abc.User | None = member or player
        if avatar_source is None:
            avatar_source = interaction.client.get_user(player.id)
        if avatar_source is None:
            avatar_source = bot_user
        if target.profile_image_url:
            avatar_url = target.profile_image_url
        elif avatar_source is not None:
            avatar_url = avatar_source.display_avatar.url
        elif guild.icon:
            avatar_url = guild.icon.url
        else:
            avatar_url = bot_avatar_url

        avatar_url = player_cog._resize_avatar_url(  # type: ignore[attr-defined]
            avatar_url, size=1024
        )

        try:
            view, embed = player_cog._build_profile_view(  # type: ignore[attr-defined]
                target,
                guild=guild,
                owner_id=interaction.user.id,
                avatar_url=avatar_url,
                main_menu_callback=None,
            )
        except LookupError:
            await interaction.followup.send(
                f"{player.mention}'s cultivation record is misconfigured.",
                ephemeral=True,
            )
            return

        message = await interaction.followup.send(
            embed=embed,
            view=view,
            ephemeral=True,
        )
        view.message = message

    ITEM_TYPE_CHOICES = [
        app_commands.Choice(name="Equipment", value="equipment"),
        app_commands.Choice(name="Material", value="material"),
        app_commands.Choice(name="Consumable", value="consumable"),
        app_commands.Choice(name="Quest Item", value="quest"),
    ]

    EQUIPMENT_SLOT_CHOICES = [
        app_commands.Choice(name="Necklace", value=EquipmentSlot.NECKLACE.value),
        app_commands.Choice(name="Armor", value=EquipmentSlot.ARMOR.value),
        app_commands.Choice(name="Belt", value=EquipmentSlot.BELT.value),
        app_commands.Choice(name="Boots", value=EquipmentSlot.BOOTS.value),
        app_commands.Choice(name="Accessories", value=EquipmentSlot.ACCESSORY.value),
        app_commands.Choice(name="Weapon", value=EquipmentSlot.WEAPON.value),
    ]

    WEAPON_TYPE_CHOICES = [
        app_commands.Choice(name="Bare-Handed", value=WeaponType.BARE_HAND.value),
        app_commands.Choice(name="Sword", value=WeaponType.SWORD.value),
        app_commands.Choice(name="Spear", value=WeaponType.SPEAR.value),
        app_commands.Choice(name="Bow", value=WeaponType.BOW.value),
        app_commands.Choice(name="Instrument", value=WeaponType.INSTRUMENT.value),
        app_commands.Choice(name="Brush", value=WeaponType.BRUSH.value),
        app_commands.Choice(name="Whip", value=WeaponType.WHIP.value),
        app_commands.Choice(name="Fan", value=WeaponType.FAN.value),
        app_commands.Choice(name="Hammer", value=WeaponType.HAMMER.value),
        app_commands.Choice(name="Trident", value=WeaponType.TRIDENT.value),
    ]

    @app_commands.command(name="create_skill", description="Create or update a skill")
    @require_admin()
    @app_commands.describe(
        requirements="Space-separated key=value pairs such as stage=qi_condensation",
        element="Elemental affinity associated with this skill",
        skill_type="Damage type dealt when the skill lands",
        damage_ratio=(
            "Damage multiplier applied to the user's stat as a percentage"
            " (e.g. 100 for 100%)"
        ),
        trigger_chance="Chance (0-100%) that the skill activates each turn",
        weapon="Weapon type required to use the skill",
        description="Short lore-friendly summary shown alongside the skill",
        category="Choose whether this is an active technique or passive skill",
        stat_bonuses="Comma-separated stat=value bonuses granted by the skill",
        heal_amount="Passive heal amount restored when the effect triggers",
        heal_interval="Rounds between each passive heal trigger",
        heal_pool="Health pool restored by the passive heal",
    )
    @app_commands.choices(
        weapon=WEAPON_TYPE_CHOICES,
        category=[
            app_commands.Choice(name="Active", value=SkillCategory.ACTIVE.value),
            app_commands.Choice(name="Passive", value=SkillCategory.PASSIVE.value),
        ],
        heal_pool=[
            app_commands.Choice(name="HP", value="hp"),
            app_commands.Choice(name="Soul", value="soul"),
        ],
    )
    @app_commands.autocomplete(element=_affinity_autocomplete)
    async def create_skill(
        self,
        interaction: discord.Interaction,
        key: str,
        name: str,
        description: str,
        grade: str,
        skill_type: str,
        element: str,
        damage_ratio: float,
        proficiency_max: int,
        weapon: str = "",
        trigger_chance: app_commands.Range[float, 0.0, 100.0] = 20.0,
        evolves_to: str = "",
        requirements: str = "",
        category: str = SkillCategory.ACTIVE.value,
        stat_bonuses: str = "",
        heal_amount: float = 0.0,
        heal_interval: int = 0,
        heal_pool: str = "hp",
    ) -> None:
        await self._ensure(interaction)
        reqs = parse_mapping(requirements, str)
        element_choice = SpiritualAffinity(element) if element else None
        damage_type = DamageType.from_value(skill_type.lower())
        stat_mapping = (
            parse_mapping(stat_bonuses, float)
            if stat_bonuses
            else {}
        )
        stat_bonus = Stats.from_mapping(stat_mapping) if stat_mapping else None
        category_value = SkillCategory.from_value(category)
        heal_pool_value = heal_pool.lower().strip()
        if heal_pool_value not in {"hp", "soul"}:
            heal_pool_value = "hp"

        percentage_ratio = float(damage_ratio)
        if percentage_ratio < 0:
            raise app_commands.AppCommandError(
                "Damage ratio percentage must be zero or greater"
            )
        normalised_damage_ratio = percentage_ratio / 100.0

        payload: Dict[str, Any] = {
            "key": key,
            "name": name,
            "description": description,
            "grade": grade,
            "skill_type": damage_type,
            "element": element_choice,
            "damage_ratio": normalised_damage_ratio,
            "proficiency_max": proficiency_max,
            "trigger_chance": trigger_chance,
            "evolves_to": evolves_to or None,
            "evolution_requirements": reqs,
            "weapon": weapon or None,
            "category": category_value,
            "passive_heal_amount": heal_amount,
            "passive_heal_interval": heal_interval,
            "passive_heal_pool": heal_pool_value,
        }
        if stat_bonus is not None:
            payload["stat_bonuses"] = stat_bonus

        skill = await self._validate_and_store(
            interaction,
            cls=Skill,
            payload=payload,
            collection="skills",
            key=key,
            entity_label=f"skill '{name}'",
        )
        if skill is None:
            return

        self.state.skills[key] = skill
        ratio_text = f"{percentage_ratio:.3f}% ({normalised_damage_ratio:.3f}x multiplier)"
        await interaction.response.send_message(
            f"Skill {name} stored with damage ratio {ratio_text}.",
            ephemeral=True,
        )

    @create_skill.autocomplete("skill_type")
    async def create_skill_type_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        allowed_values = {damage_type.value for damage_type in ALLOWED_SKILL_DAMAGE_TYPES}
        known: dict[str, str] = {
            damage_type.value: damage_type.name.replace("_", " ").title()
            for damage_type in ALLOWED_SKILL_DAMAGE_TYPES
        }
        for skill in self.state.skills.values():
            try:
                damage_type = DamageType.from_value(skill.skill_type)
            except ValueError:
                damage_type = None

            if damage_type is not None:
                value = damage_type.value
            else:
                value = str(skill.skill_type).strip().lower()

            if value not in allowed_values or value in known:
                continue

            known[value] = value.replace("_", " ").title()
        query = current.lower()
        choices: list[app_commands.Choice[str]] = []
        if current:
            normalized = current.lower()
            if normalized not in known:
                choices.append(
                    app_commands.Choice(name=f'Use "{current}"', value=normalized)
                )
        for value, label in sorted(known.items()):
            if query and query not in value and query not in label.lower():
                continue
            choices.append(app_commands.Choice(name=label, value=value))
            if len(choices) >= 25:
                break
        return choices[:25]

    @create_skill.autocomplete("evolves_to")
    async def create_skill_evolves_to_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        choices = self._iter_entity_choices(self.state.skills, current=current)
        choices.insert(0, app_commands.Choice(name="None", value=""))
        return choices[:25]

    @app_commands.command(
        name="create_cultivation_technique",
        description="Create or update a cultivation technique",
    )
    @require_admin()
    @app_commands.describe(
        description="Flavour text describing the technique",
        grade="Grade label such as mortal, heaven, or saint",
        path="Cultivation path this technique enhances",
        affinity="Optional spiritual affinity requirement",
        skills="Space-separated skill keys granted when mastering the technique",
        stat_mode="Whether stat values act as additions or multipliers",
        experience_addition="Flat EXP bonus awarded when training the matching path",
        experience_multiplier="Percentage EXP bonus when training the matching path",
        physical_health="Physical health value provided by this technique",
        strength="Strength value provided by this technique",
        agility="Agility value provided by this technique",
        defense="Defense value provided by this technique",
        spirit="Spirit value provided by this technique",
        spiritual_health="Spiritual health value provided by this technique",
        soul_power="Soul power value provided by this technique",
        qi_control="Qi control value provided by this technique",
    )
    @app_commands.choices(
        path=[
            app_commands.Choice(name=path.value.title(), value=path.value)
            for path in CultivationPath
        ],
        stat_mode=[
            app_commands.Choice(
                name="Addition", value=TechniqueStatMode.ADDITION.value
            ),
            app_commands.Choice(
                name="Multiplier", value=TechniqueStatMode.MULTIPLIER.value
            ),
        ],
    )
    @app_commands.autocomplete(affinity=_affinity_autocomplete)
    async def create_cultivation_technique(
        self,
        interaction: discord.Interaction,
        key: str,
        name: str,
        description: str,
        grade: str,
        path: str,
        affinity: str = "",
        skills: str = "",
        stat_mode: str = TechniqueStatMode.ADDITION.value,
        experience_addition: float = 0.0,
        experience_multiplier: float = 0.0,
        physical_health: float = 0.0,
        strength: float = 0.0,
        agility: float = 0.0,
        defense: float = 0.0,
        spirit: float = 0.0,
        spiritual_health: float = 0.0,
        soul_power: float = 0.0,
        qi_control: float = 0.0,
    ) -> None:
        await self._ensure(interaction)
        path_value = CultivationPath.from_value(path).value
        affinity_value: SpiritualAffinity | None = None
        if affinity:
            try:
                affinity_value = SpiritualAffinity(affinity)
            except ValueError:
                await interaction.response.send_message(
                    "Unknown affinity provided.", ephemeral=True
                )
                return
        skill_keys = parse_list(skills)
        unknown_skills = [key for key in skill_keys if key not in self.state.skills]
        if unknown_skills:
            missing = ", ".join(unknown_skills)
            await interaction.response.send_message(
                f"Unknown skill keys: {missing}", ephemeral=True
            )
            return
        mode = TechniqueStatMode.from_value(stat_mode)
        multiplier = float(experience_multiplier)
        if multiplier > 1:
            multiplier /= 100.0
        stats = Stats.from_mapping(stat_kwargs_from_locals(locals()))
        payload: Dict[str, Any] = {
            "key": key,
            "name": name,
            "description": description,
            "grade": grade,
            "path": path_value,
            "affinity": affinity_value,
            "skills": skill_keys,
            "stat_mode": mode,
            "stats": stats,
            "experience_addition": experience_addition,
            "experience_multiplier": multiplier,
        }

        technique = await self._validate_and_store(
            interaction,
            cls=CultivationTechnique,
            payload=payload,
            collection="cultivation_techniques",
            key=key,
            entity_label=f"cultivation technique '{name}'",
        )
        if technique is None:
            return

        self.state.cultivation_techniques[key] = technique
        await interaction.response.send_message(
            f"Cultivation technique {name} stored.", ephemeral=True
        )

    @create_cultivation_technique.autocomplete("skills")
    async def create_cultivation_technique_skills_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.skills, current=current, multi=True)

    @app_commands.command(name="create_item", description="Create or update an item")
    @require_admin()
    @app_commands.describe(
        item_type="High level category such as equipment, material, or quest",
        equipment_slot="Equipment slot occupied when this item is equipped",
        slots_required="Number of slot units consumed when equipped",
        weapon_type="Weapon type provided while equipped",
        physical_health="Physical health granted when equipped",
        strength="Strength granted when equipped",
        agility="Agility granted when equipped",
        defense="Defense granted when equipped",
        spirit="Spirit granted when equipped",
        spiritual_health="Spiritual health granted when equipped",
        soul_power="Soul power granted when equipped",
        qi_control="Qi control granted when equipped",
        inventory_space_bonus="Extra inventory slots granted while equipped",
        skill_unlocks="List of skill keys separated by spaces",
        grants_titles="Space-separated title keys granted when the item is acquired",
        requirements="Evolution requirements as key=value pairs",
        race_transformation="Race key this consumable transforms the user into",
    )
    @app_commands.choices(
        item_type=ITEM_TYPE_CHOICES,
        weapon_type=WEAPON_TYPE_CHOICES,
    )
    async def create_item(
        self,
        interaction: discord.Interaction,
        key: str,
        name: str,
        description: str,
        item_type: str,
        equipment_slot: str = "",
        slots_required: int = 1,
        weapon_type: str = "",
        physical_health: float = 0.0,
        strength: float = 0.0,
        agility: float = 0.0,
        defense: float = 0.0,
        spirit: float = 0.0,
        spiritual_health: float = 0.0,
        soul_power: float = 0.0,
        qi_control: float = 0.0,
        inventory_space_bonus: int = 0,
        skill_unlocks: str = "",
        evolves_to: str = "",
        requirements: str = "",
        grants_titles: str = "",
        race_transformation: str = "",
    ) -> None:
        await self._ensure(interaction)
        slot_value = equipment_slot or None
        weapon_value = weapon_type or None
        stat_values = stat_kwargs_from_locals(locals())
        payload: Dict[str, Any] = {
            "key": key,
            "name": name,
            "description": description,
            "item_type": item_type,
            "equipment_slot": slot_value,
            "slots_required": slots_required,
            "weapon_type": weapon_value,
            **stat_values,
            "inventory_space_bonus": inventory_space_bonus,
            "skill_unlocks": parse_list(skill_unlocks),
            "evolves_to": evolves_to or None,
            "evolution_requirements": parse_mapping(requirements, str),
            "grants_titles": parse_list(grants_titles),
            "race_transformation": race_transformation or None,
        }

        item = await self._validate_and_store(
            interaction,
            cls=Item,
            payload=payload,
            collection="items",
            key=key,
            entity_label=f"item '{name}'",
        )
        if item is None:
            return

        self.state.items[key] = item
        await interaction.response.send_message(f"Item {name} stored.", ephemeral=True)

    @create_item.autocomplete("equipment_slot")
    async def create_item_equipment_slot_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        query = current.strip().lower()
        candidates: list[app_commands.Choice[str]] = [
            app_commands.Choice(name="None", value=""),
            *self.EQUIPMENT_SLOT_CHOICES,
        ]

        results: list[app_commands.Choice[str]] = []
        for choice in candidates:
            if query and query not in choice.name.lower() and query not in choice.value.lower():
                continue
            results.append(choice)
            if len(results) >= 25:
                break
        return results

    @create_item.autocomplete("skill_unlocks")
    async def create_item_skill_unlocks_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.skills, current=current, multi=True)

    @create_item.autocomplete("grants_titles")
    async def create_item_titles_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.titles, current=current, multi=True)

    @create_item.autocomplete("evolves_to")
    async def create_item_evolves_to_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        choices = self._iter_entity_choices(self.state.items, current=current)
        choices.insert(0, app_commands.Choice(name="None", value=""))
        return choices[:25]

    @create_item.autocomplete("race_transformation")
    async def create_item_race_transformation_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        choices = self._iter_entity_choices(self.state.races, current=current)
        choices.insert(0, app_commands.Choice(name="None", value=""))
        return choices[:25]

    @app_commands.command(name="create_trait", description="Create a special trait")
    @require_admin()
    @app_commands.describe(
        physical_health="Physical health multiplier (1.0 means unchanged)",
        strength="Strength multiplier (1.0 means unchanged)",
        agility="Agility multiplier (1.0 means unchanged)",
        defense="Defense multiplier (1.0 means unchanged)",
        spirit="Spirit multiplier (1.0 means unchanged)",
        spiritual_health="Spiritual health multiplier (1.0 means unchanged)",
        soul_power="Soul power multiplier (1.0 means unchanged)",
        qi_control="Qi control multiplier (1.0 means unchanged)",
        grants_titles="Space-separated title keys granted when the trait is acquired",
        grants_affinities="Space-separated affinity names granted while the trait is active",
        role="Discord role granted while the trait is active",
    )
    async def create_trait(
        self,
        interaction: discord.Interaction,
        key: str,
        name: str,
        description: str,
        physical_health: float = 1.0,
        strength: float = 1.0,
        agility: float = 1.0,
        defense: float = 1.0,
        spirit: float = 1.0,
        spiritual_health: float = 1.0,
        soul_power: float = 1.0,
        qi_control: float = 1.0,
        grants_titles: str = "",
        grants_affinities: str = "",
        role: discord.Role | None = None,
    ) -> None:
        await self._ensure(interaction)
        stat_values = stat_kwargs_from_locals(locals(), default=1.0)
        payload: Dict[str, Any] = {
            "key": key,
            "name": name,
            "description": description,
            **stat_values,
            "grants_titles": parse_list(grants_titles),
            "grants_affinities": parse_affinity_list(grants_affinities),
            "role_id": role.id if role else None,
        }

        trait = await self._validate_and_store(
            interaction,
            cls=SpecialTrait,
            payload=payload,
            collection="traits",
            key=key,
            entity_label=f"trait '{name}'",
        )
        if trait is None:
            return

        self.state.traits[key] = trait
        await interaction.response.send_message(f"Trait {name} stored.", ephemeral=True)

    @create_trait.autocomplete("grants_titles")
    async def create_trait_titles_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.titles, current=current, multi=True)

    @create_trait.autocomplete("grants_affinities")
    async def create_trait_affinities_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_affinity_choices(current, multi=True)

    @app_commands.command(name="create_race", description="Create or update a race")
    @require_admin()
    @app_commands.describe(
        description="Optional flavour text describing this race",
        innate_dice_count="Number of dice rolled when determining innate stats",
        innate_dice_faces="Number of faces on each innate die",
        innate_drop_lowest="How many of the lowest dice to drop",
        physical_health="Physical health multiplier (default 1.0)",
        strength="Strength multiplier (default 1.0)",
        agility="Agility multiplier (default 1.0)",
        defense="Defense multiplier (default 1.0)",
        spirit="Spirit multiplier (default 1.0)",
        spiritual_health="Spiritual health multiplier (default 1.0)",
        soul_power="Soul power multiplier (default 1.0)",
        qi_control="Qi control multiplier (default 1.0)",
        role="Discord role granted to cultivators of this race",
    )
    async def create_race(
        self,
        interaction: discord.Interaction,
        key: str,
        name: str,
        description: str = "",
        innate_dice_count: int = 5,
        innate_dice_faces: int = 5,
        innate_drop_lowest: int = 1,
        physical_health: float = 1.0,
        strength: float = 1.0,
        agility: float = 1.0,
        defense: float = 1.0,
        spirit: float = 1.0,
        spiritual_health: float = 1.0,
        soul_power: float = 1.0,
        qi_control: float = 1.0,
        role: discord.Role | None = None,
    ) -> None:
        await self._ensure(interaction)
        try:
            dice_count = max(1, int(innate_dice_count))
        except (TypeError, ValueError):
            dice_count = 5
        try:
            dice_faces = max(1, int(innate_dice_faces))
        except (TypeError, ValueError):
            dice_faces = 5
        try:
            drop_lowest = max(0, int(innate_drop_lowest))
        except (TypeError, ValueError):
            drop_lowest = 1
        if drop_lowest >= dice_count:
            drop_lowest = max(0, dice_count - 1)
        stat_values = stat_kwargs_from_locals(locals(), default=1.0)
        payload: Dict[str, Any] = {
            "key": key,
            "name": name,
            "description": description,
            "innate_dice_count": dice_count,
            "innate_dice_faces": dice_faces,
            "innate_drop_lowest": drop_lowest,
            **stat_values,
            "role_id": role.id if role else None,
        }

        race = await self._validate_and_store(
            interaction,
            cls=Race,
            payload=payload,
            collection="races",
            key=key,
            entity_label=f"race '{name}'",
        )
        if race is None:
            return

        self.state.races[key] = race
        await interaction.response.send_message(f"Race {name} stored.", ephemeral=True)

    @app_commands.command(name="create_quest", description="Create a quest")
    @require_admin()
    @app_commands.describe(
        objective="Enemy or boss that must be defeated",
        kill_count="How many times the target must be defeated",
        rewards="Space separated item=amount pairs describing rewards",
    )
    async def create_quest(
        self,
        interaction: discord.Interaction,
        key: str,
        name: str,
        description: str,
        objective: str,
        kill_count: int,
        rewards: str,
    ) -> None:
        await self._ensure(interaction)
        objective_value = objective.strip()
        if ":" in objective_value:
            raw_group, raw_key = objective_value.split(":", 1)
        else:
            tokens = objective_value.split()
            raw_group = tokens[0] if tokens else ""
            raw_key = tokens[1] if len(tokens) > 1 else ""

        target_group = raw_group.lower()
        target_key = raw_key.strip()

        if target_group not in {"enemy", "boss"} or not target_key:
            await interaction.response.send_message(
                "Objective must be selected from the autocomplete list.",
                ephemeral=True,
            )
            return

        collection = self.state.enemies if target_group == "enemy" else self.state.bosses
        if target_key not in collection:
            await interaction.response.send_message(
                "That target key is unknown.", ephemeral=True
            )
            return
        if kill_count <= 0:
            await interaction.response.send_message(
                "Kill count must be at least 1.", ephemeral=True
            )
            return

        reward_mapping = parse_mapping(
            rewards, int, allow_empty=False, delimiters=("=", ":")
        )
        valid_reward_keys = set(self.state.items) | set(self.state.currencies)
        missing_items = [item for item in reward_mapping if item not in valid_reward_keys]
        if missing_items:
            missing = ", ".join(missing_items)
            await interaction.response.send_message(
                f"Unknown reward keys: {missing}", ephemeral=True
            )
            return

        payload: Dict[str, Any] = {
            "key": key,
            "name": name,
            "description": description,
            "objective": {
                "target_type": target_group,
                "target_key": target_key,
                "kill_count": kill_count,
            },
            "rewards": reward_mapping,
        }

        quest = await self._validate_and_store(
            interaction,
            cls=Quest,
            payload=payload,
            collection="quests",
            key=key,
            entity_label=f"quest '{name}'",
        )
        if quest is None:
            return

        self.state.quests[key] = quest
        await interaction.response.send_message(f"Quest {name} stored.", ephemeral=True)

    @create_quest.autocomplete("objective")
    async def create_quest_objective_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_objective_choices(current=current)

    @create_quest.autocomplete("rewards")
    async def create_quest_rewards_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_reward_choices(
            self.state.items, self.state.currencies, current=current
        )

    @app_commands.command(name="create_enemy", description="Create an enemy")
    @require_admin()
    @app_commands.describe(
        cultivation_stage="Cultivation stage key representing this enemy's realm",
        body_stage="Body cultivation stage key for this enemy",
        soul_stage="Soul purification stage key for this enemy",
        affinity="Elemental affinity attuned to this enemy",
        race="Race key determining talent dice",
        physical_health="Physical health for this enemy",
        strength="Strength for this enemy",
        agility="Agility for this enemy",
        defense="Defense for this enemy",
        spirit="Spirit for this enemy",
        spiritual_health="Spiritual health for this enemy",
        soul_power="Soul power for this enemy",
        qi_control="Qi control for this enemy",
        skills="Optional list of skill keys separated by spaces",
        loot_table="Optional entries formatted as item=chance:amount",
        resistances="Space-separated elemental affinities for 25% damage resistance",
        titles="Space-separated title keys awarded when this enemy is defeated",
        escape_chance="Chance from 0-100 that players can flee this enemy",
        decision_prompt_chance="Chance from 0-100 each round to ask players whether to continue",
    )
    @app_commands.autocomplete(affinity=_affinity_autocomplete)
    async def create_enemy(
        self,
        interaction: discord.Interaction,
        key: str,
        name: str,
        cultivation_stage: str,
        body_stage: str = "",
        soul_stage: str = "",
        affinity: str = "",
        race: str = "",
        skills: str = "",
        loot_table: str = "",
        physical_health: int = 10,
        strength: int = 10,
        agility: int = 10,
        defense: int = 10,
        spirit: int = 10,
        spiritual_health: int = 10,
        soul_power: int = 10,
        qi_control: int = 10,
        resistances: str = "",
        titles: str = "",
        escape_chance: float = 25.0,
        decision_prompt_chance: float = 0.0,
    ) -> None:
        await self._ensure(interaction)
        race_key = race.strip()
        race_template = self.state.races.get(race_key) if race_key else None
        if race_template is None:
            available_races = list(self.state.races.values())
            if len(available_races) == 1:
                race_template = available_races[0]
            else:
                await interaction.response.send_message(
                    "Unknown race provided.", ephemeral=True
                )
                return
        drop_lowest = race_template.innate_drop_lowest
        innate_stats = roll_talent_stats(
            self.config.innate_stat_min,
            self.config.innate_stat_max,
            dice_count=race_template.innate_dice_count,
            dice_faces=race_template.innate_dice_faces,
            drop_lowest=drop_lowest,
        )
        skill_keys = parse_list(skills)
        loot_entries = parse_loot_entries(loot_table) if loot_table else {}
        try:
            affinity_choice = SpiritualAffinity(affinity) if affinity else None
        except ValueError:
            await interaction.response.send_message(
                "Unknown affinity provided.", ephemeral=True
            )
            return

        stat_values = stat_kwargs_from_locals(locals(), default=10.0)
        payload: Dict[str, Any] = {
            "key": key,
            "name": name,
            "cultivation_stage": cultivation_stage,
            "body_cultivation_stage": body_stage or cultivation_stage,
            "soul_cultivation_stage": soul_stage or cultivation_stage,
            **stat_values,
            "affinity": affinity_choice,
            "skills": skill_keys,
            "loot_table": loot_entries,
            "elemental_resistances": parse_affinity_list(resistances),
            "title_rewards": parse_list(titles),
            "innate_stats": innate_stats,
            "race_key": race_template.key,
            "escape_chance": escape_chance,
            "decision_prompt_chance": decision_prompt_chance,
        }

        enemy = await self._validate_and_store(
            interaction,
            cls=Enemy,
            payload=payload,
            collection="enemies",
            key=key,
            entity_label=f"enemy '{name}'",
        )
        if enemy is None:
            return

        self.state.enemies[key] = enemy
        await interaction.response.send_message(f"Enemy {name} stored.", ephemeral=True)

    @create_enemy.autocomplete("skills")
    async def create_enemy_skills_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.skills, current=current, multi=True)

    @create_enemy.autocomplete("race")
    async def create_enemy_race_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.races, current=current)

    @create_enemy.autocomplete("cultivation_stage")
    async def create_enemy_stage_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(
            self.state.qi_cultivation_stages, current=current
        )

    @create_enemy.autocomplete("body_stage")
    async def create_enemy_body_stage_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(
            self.state.body_cultivation_stages, current=current
        )

    @create_enemy.autocomplete("soul_stage")
    async def create_enemy_soul_stage_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(
            self.state.soul_cultivation_stages, current=current
        )

    @create_enemy.autocomplete("loot_table")
    async def create_enemy_loot_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_loot_choices(
            self.state.items, self.state.currencies, current=current
        )

    @create_enemy.autocomplete("resistances")
    async def create_enemy_resistances_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_affinity_choices(current, multi=True)

    @create_enemy.autocomplete("titles")
    async def create_enemy_titles_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.titles, current=current, multi=True)

    @app_commands.command(name="create_boss", description="Create a boss")
    @require_admin()
    @app_commands.describe(
        cultivation_stage="Cultivation stage key representing this boss's realm",
        body_stage="Body cultivation stage key representing this boss",
        soul_stage="Soul purification stage key representing this boss",
        affinity="Elemental affinity attuned to this boss",
        race="Race key determining talent dice",
        physical_health="Physical health for this boss",
        strength="Strength for this boss",
        agility="Agility for this boss",
        defense="Defense for this boss",
        spirit="Spirit for this boss",
        spiritual_health="Spiritual health for this boss",
        soul_power="Soul power for this boss",
        qi_control="Qi control for this boss",
        skills="Optional list of skill keys separated by spaces",
        loot_table="Optional entries formatted as item=chance:amount",
        resistances="Space-separated elemental affinities for 25% damage resistance",
        titles="Space-separated title keys awarded when this boss is defeated",
        mechanics="Free-form text describing unique boss mechanics",
        escape_chance="Chance from 0-100 that players can flee this boss",
        decision_prompt_chance="Chance from 0-100 each round to ask players whether to continue",
    )
    @app_commands.autocomplete(affinity=_affinity_autocomplete)
    async def create_boss(
        self,
        interaction: discord.Interaction,
        key: str,
        name: str,
        cultivation_stage: str,
        body_stage: str = "",
        soul_stage: str = "",
        affinity: str = "",
        race: str = "",
        skills: str = "",
        loot_table: str = "",
        physical_health: int = 10,
        strength: int = 10,
        agility: int = 10,
        defense: int = 10,
        spirit: int = 10,
        spiritual_health: int = 10,
        soul_power: int = 10,
        qi_control: int = 10,
        mechanics: str = "",
        resistances: str = "",
        titles: str = "",
        escape_chance: float = 10.0,
        decision_prompt_chance: float = 0.0,
    ) -> None:
        await self._ensure(interaction)
        race_key = race.strip()
        race_template = self.state.races.get(race_key) if race_key else None
        if race_template is None:
            available_races = list(self.state.races.values())
            if len(available_races) == 1:
                race_template = available_races[0]
            else:
                await interaction.response.send_message(
                    "Unknown race provided.", ephemeral=True
                )
                return
        drop_lowest = race_template.innate_drop_lowest
        innate_stats = roll_talent_stats(
            self.config.innate_stat_min,
            self.config.innate_stat_max,
            dice_count=race_template.innate_dice_count,
            dice_faces=race_template.innate_dice_faces,
            drop_lowest=drop_lowest,
        )
        skill_keys = parse_list(skills)
        loot_entries = parse_loot_entries(loot_table) if loot_table else {}
        try:
            affinity_choice = SpiritualAffinity(affinity) if affinity else None
        except ValueError:
            await interaction.response.send_message(
                "Unknown affinity provided.", ephemeral=True
            )
            return

        stat_values = stat_kwargs_from_locals(locals(), default=10.0)
        payload: Dict[str, Any] = {
            "key": key,
            "name": name,
            "cultivation_stage": cultivation_stage,
            "body_cultivation_stage": body_stage or cultivation_stage,
            "soul_cultivation_stage": soul_stage or cultivation_stage,
            **stat_values,
            "affinity": affinity_choice,
            "skills": skill_keys,
            "loot_table": loot_entries,
            "elemental_resistances": parse_affinity_list(resistances),
            "special_mechanics": mechanics,
            "title_rewards": parse_list(titles),
            "innate_stats": innate_stats,
            "race_key": race_template.key,
            "escape_chance": escape_chance,
            "decision_prompt_chance": decision_prompt_chance,
        }

        boss = await self._validate_and_store(
            interaction,
            cls=Boss,
            payload=payload,
            collection="bosses",
            key=key,
            entity_label=f"boss '{name}'",
        )
        if boss is None:
            return

        self.state.bosses[key] = boss
        await interaction.response.send_message(f"Boss {name} stored.", ephemeral=True)

    @create_boss.autocomplete("skills")
    async def create_boss_skills_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.skills, current=current, multi=True)

    @create_boss.autocomplete("race")
    async def create_boss_race_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.races, current=current)

    @create_boss.autocomplete("cultivation_stage")
    async def create_boss_stage_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(
            self.state.qi_cultivation_stages, current=current
        )

    @create_boss.autocomplete("body_stage")
    async def create_boss_body_stage_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(
            self.state.body_cultivation_stages, current=current
        )

    @create_boss.autocomplete("soul_stage")
    async def create_boss_soul_stage_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(
            self.state.soul_cultivation_stages, current=current
        )

    @create_boss.autocomplete("loot_table")
    async def create_boss_loot_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_loot_choices(
            self.state.items, self.state.currencies, current=current
        )

    @create_boss.autocomplete("resistances")
    async def create_boss_resistances_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_affinity_choices(current, multi=True)

    @create_boss.autocomplete("titles")
    async def create_boss_titles_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.titles, current=current, multi=True)

    @app_commands.command(name="create_npc", description="Create a reusable NPC")
    @require_admin()
    @app_commands.describe(
        key="Unique identifier for this NPC",
        name="Display name for the NPC",
        npc_type="Choose between dialog, shop, or hostile NPCs",
        description="Short description that appears in listings",
        reference="Optional reference key (enemy, shop inventory, quest, etc.)",
        dialogue="Optional dialogue shown when speaking to this NPC",
        shop_items="Space or comma separated shop item keys sold by this NPC",
    )
    async def create_npc(
        self,
        interaction: discord.Interaction,
        key: str,
        name: str,
        npc_type: str = LocationNPCType.DIALOG.value,
        description: str = "",
        reference: str = "",
        dialogue: str = "",
        shop_items: str = "",
    ) -> None:
        await self._ensure(interaction)
        key = key.strip()
        if not key:
            await interaction.response.send_message(
                "Provide a unique key for this NPC.", ephemeral=True
            )
            return
        try:
            npc_type_value = LocationNPCType.from_value(npc_type)
        except ValueError:
            await interaction.response.send_message(
                "Unknown NPC type. Choose dialog, shop, or hostile.", ephemeral=True
            )
            return
        reference_value = reference.strip() or None
        shop_item_keys = _split_simple_list(shop_items)
        if (
            npc_type_value is LocationNPCType.SHOP
            and not shop_item_keys
            and not reference_value
        ):
            await interaction.response.send_message(
                "Shopkeepers require at least one shop item key.", ephemeral=True
            )
            return
        payload: Dict[str, Any] = {
            "name": name,
            "npc_type": npc_type_value,
            "description": description,
            "reference": reference_value,
            "dialogue": dialogue,
            "shop_items": shop_item_keys,
        }

        npc = await self._validate_and_store(
            interaction,
            cls=LocationNPC,
            payload=payload,
            collection="npcs",
            key=key,
            entity_label=f"NPC '{name}'",
        )
        if npc is None:
            return

        self.state.npcs[key] = npc
        await interaction.response.send_message(
            f"NPC {name} stored as `{key}`.", ephemeral=True
        )

    @create_npc.autocomplete("npc_type")
    async def create_npc_type_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return _npc_type_choices(current)

    @create_npc.autocomplete("shop_items")
    async def create_npc_shop_items_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(
            self.state.shop_items, current=current, multi=True
        )

    @app_commands.command(
        name="create_zone", description="Designate a channel as a travel zone"
    )
    @require_admin()
    @app_commands.describe(
        channel="Channel that should become a travel zone",
        name="Optional display name for the zone",
        description="Optional description for the zone",
        is_safe="Mark the zone as a sanctuary without encounters",
    )
    async def create_zone(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        *,
        name: str | None = None,
        description: str | None = None,
        is_safe: bool | None = None,
    ) -> None:
        await self._ensure(interaction)

        existing = self.state.get_location_for_channel(channel.id)
        anchor_key = (
            existing.map_coordinate
            if existing and existing.map_coordinate
            else self.state.channel_anchor(channel.id).to_key()
        )

        default_name = getattr(channel, "name", None) or f"Channel {channel.id}"
        zone_name = (name or (existing.name if existing else None) or default_name).strip()
        if not zone_name:
            zone_name = default_name

        zone_description = (
            description.strip()
            if description is not None
            else (existing.description if existing else "")
        )

        safe_flag = is_safe if is_safe is not None else (existing.is_safe if existing else False)

        payload: Dict[str, Any] = {
            "name": zone_name,
            "description": zone_description,
            "enemies": list(existing.enemies) if existing else [],
            "bosses": list(existing.bosses) if existing else [],
            "quests": list(existing.quests) if existing else [],
            "encounter_rate": existing.encounter_rate if existing else 0.0,
            "wander_loot": dict(existing.wander_loot) if existing else {},
            "npcs": list(existing.npcs) if existing else [],
            "is_safe": safe_flag,
            "channel_id": channel.id,
            "map_coordinate": anchor_key,
        }
        if existing and existing.location_id:
            payload["location_id"] = existing.location_id

        location = await self._validate_and_store(
            interaction,
            cls=Location,
            payload=payload,
            collection="locations",
            key=str(channel.id),
            entity_label=f"location '{zone_name}'",
        )
        if location is None:
            return

        self.state.register_location(location, storage_key=str(channel.id))

        await interaction.response.send_message(
            f"{channel.mention} is now configured as the {zone_name!r} travel zone.",
            ephemeral=True,
        )

    @app_commands.command(name="create_location", description="Configure a channel location")
    @require_admin()
    @app_commands.describe(
        channel="Channel that should serve as the location",
        enemies="Optional list of enemy keys separated by spaces",
        bosses="Optional list of boss keys separated by spaces",
        quests="Optional list of quest keys separated by spaces",
        encounter_rate="Chance from 0-100 to trigger encounters",
        wander_loot="Loot entries for wandering rewards",
        npcs="Space separated entries like type:name[@reference][|description]",
        is_safe="Whether the location should be treated as a sanctuary",
    )
    async def create_location(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        name: str,
        description: str,
        *,
        encounter_rate: float | None = None,
        enemies: str = "",
        bosses: str = "",
        quests: str = "",
        wander_loot: str = "",
        npcs: str = "",
        is_safe: bool | None = None,
    ) -> None:
        await self._ensure(interaction)
        loot_entries: dict[str, LootDrop] = {}
        if wander_loot:
            loot_entries = parse_loot_entries(wander_loot)
        npc_entries = (
            parse_location_npcs(npcs, library=self.state.npcs) if npcs else []
        )
        existing = self.state.get_location_for_channel(channel.id)

        safe_flag = is_safe if is_safe is not None else (existing.is_safe if existing else False)

        if not safe_flag:
            if encounter_rate is None and existing is None:
                await interaction.response.send_message(
                    "Provide an encounter rate for non-sanctuary locations.",
                    ephemeral=True,
                )
                return
            encounter_value = (
                float(encounter_rate)
                if encounter_rate is not None
                else float(existing.encounter_rate if existing else 0.0)
            )
        else:
            encounter_value = 0.0

        enemy_list = parse_list(enemies) if enemies or existing is None else list(existing.enemies)
        boss_list = parse_list(bosses) if bosses or existing is None else list(existing.bosses)
        quest_list = parse_list(quests) if quests or existing is None else list(existing.quests)
        wander_dict: dict[str, LootDrop]
        if wander_loot:
            wander_dict = loot_entries
        elif existing is not None:
            wander_dict = dict(existing.wander_loot)
        else:
            wander_dict = {}
        npc_list: list[LocationNPC]
        if npc_entries:
            npc_list = npc_entries
        elif existing is not None:
            npc_list = list(existing.npcs)
        else:
            npc_list = []

        payload: Dict[str, Any] = {
            "name": name,
            "description": description,
            "enemies": enemy_list,
            "bosses": boss_list,
            "quests": quest_list,
            "encounter_rate": encounter_value,
            "wander_loot": wander_dict,
            "npcs": npc_list,
            "is_safe": safe_flag,
            "channel_id": channel.id,
        }

        if existing and existing.map_coordinate:
            payload["map_coordinate"] = existing.map_coordinate

        location = await self._validate_and_store(
            interaction,
            cls=Location,
            payload=payload,
            collection="locations",
            key=str(channel.id),
            entity_label=f"location '{name}'",
        )
        if location is None:
            return

        self.state.register_location(location, storage_key=str(channel.id))

        await interaction.response.send_message(
            f"Location {name} configured for {channel.mention}.", ephemeral=True
        )

    @create_location.autocomplete("enemies")
    async def create_location_enemies_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.enemies, current=current, multi=True)

    @create_location.autocomplete("bosses")
    async def create_location_bosses_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.bosses, current=current, multi=True)

    @create_location.autocomplete("quests")
    async def create_location_quests_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.quests, current=current, multi=True)

    @app_commands.command(
        name="add_location_content", description="Add encounters to a channel location"
    )
    @require_admin()
    @app_commands.describe(
        channel="Channel that should receive the content",
        enemies="Space separated enemy keys to add",
        bosses="Space separated boss keys to add",
        quests="Space separated quest keys to add",
        wander_loot="Additional wandering loot entries to merge",
        npcs="Additional NPC entries like type:name[@reference][|description]",
        is_safe="Set to true to mark the location as a sanctuary",
    )
    async def add_location_content(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        enemies: str = "",
        bosses: str = "",
        quests: str = "",
        wander_loot: str = "",
        npcs: str = "",
        is_safe: bool | None = None,
    ) -> None:
        await self._ensure(interaction)
        location = self.state.get_location_for_channel(channel.id)
        if not location:
            await interaction.response.send_message(
                "That location has not been created yet.", ephemeral=True
            )
            return

        enemy_keys = parse_list(enemies)
        boss_keys = parse_list(bosses)
        quest_keys = parse_list(quests)

        loot_updates: dict[str, LootDrop] = {}
        if wander_loot:
            loot_updates = parse_loot_entries(wander_loot)

        npc_entries = (
            parse_location_npcs(npcs, library=self.state.npcs) if npcs else []
        )

        if not any(
            [
                enemy_keys,
                boss_keys,
                quest_keys,
                loot_updates,
                npc_entries,
                is_safe is not None,
            ]
        ):
            await interaction.response.send_message(
                "Provide at least one enemy, boss, quest, loot entry, or NPC to add.",
                ephemeral=True,
            )
            return

        missing_enemies = [value for value in enemy_keys if value not in self.state.enemies]
        missing_bosses = [value for value in boss_keys if value not in self.state.bosses]
        missing_quests = [value for value in quest_keys if value not in self.state.quests]

        missing_parts = []
        if missing_enemies:
            missing_parts.append(f"enemies: {', '.join(missing_enemies)}")
        if missing_bosses:
            missing_parts.append(f"bosses: {', '.join(missing_bosses)}")
        if missing_quests:
            missing_parts.append(f"quests: {', '.join(missing_quests)}")

        if missing_parts:
            details = "; ".join(missing_parts)
            await interaction.response.send_message(
                f"Unknown entity keys - {details}.", ephemeral=True
            )
            return

        def _extend_unique(collection: list[str], additions: list[str]) -> list[str]:
            added: list[str] = []
            for entry in additions:
                if entry not in collection:
                    collection.append(entry)
                    added.append(entry)
            return added

        added_enemies = _extend_unique(location.enemies, enemy_keys)
        added_bosses = _extend_unique(location.bosses, boss_keys)
        added_quests = _extend_unique(location.quests, quest_keys)
        added_loot: list[str] = []
        if loot_updates:
            for key, drop in loot_updates.items():
                location.wander_loot[key] = drop
                added_loot.append(key)

        added_npcs: list[str] = []
        if npc_entries:
            existing_pairs = {
                (npc.name.lower(), npc.npc_type) for npc in location.npcs
            }
            for npc in npc_entries:
                pair = (npc.name.lower(), npc.npc_type)
                if pair in existing_pairs:
                    continue
                location.npcs.append(npc)
                existing_pairs.add(pair)
                descriptor = npc.npc_type.value
                if npc.description:
                    descriptor = f"{descriptor} ({npc.description})"
                added_npcs.append(f"{npc.name} - {descriptor}")

        if is_safe is not None:
            location.is_safe = is_safe
            if is_safe:
                location.encounter_rate = 0.0

        if not any(
            [
                added_enemies,
                added_bosses,
                added_quests,
                added_loot,
                added_npcs,
                is_safe is not None,
            ]
        ):
            await interaction.response.send_message(
                "All provided entries were already present in this location.",
                ephemeral=True,
            )
            return

        self.state.register_location(location, storage_key=str(channel.id))
        await self._store_entity(
            interaction, "locations", str(channel.id), asdict(location)
        )

        summary = []
        if is_safe is not None:
            summary.append("marked safe" if is_safe else "marked hazardous")
        if added_enemies:
            summary.append(f"enemies: {', '.join(added_enemies)}")
        if added_bosses:
            summary.append(f"bosses: {', '.join(added_bosses)}")
        if added_quests:
            summary.append(f"quests: {', '.join(added_quests)}")
        if added_loot:
            summary.append(f"wander loot: {', '.join(added_loot)}")
        if added_npcs:
            summary.append(f"NPCs: {', '.join(added_npcs)}")
        details = "; ".join(summary)
        await interaction.response.send_message(
            f"Updated {channel.mention} with {details}.", ephemeral=True
        )

    @add_location_content.autocomplete("enemies")
    async def add_location_content_enemies_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.enemies, current=current, multi=True)

    @add_location_content.autocomplete("bosses")
    async def add_location_content_bosses_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.bosses, current=current, multi=True)

    @add_location_content.autocomplete("quests")
    async def add_location_content_quests_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.quests, current=current, multi=True)

    async def _save_shop_listing(
        self,
        interaction: discord.Interaction,
        item_key: str,
        currency_key: str,
        price: int,
    ) -> ShopItem | None:
        payload = {"item_key": item_key, "currency_key": currency_key, "price": price}
        shop_item = await self._validate_and_store(
            interaction,
            cls=ShopItem,
            payload=payload,
            collection="shop",
            key=item_key,
            entity_label=f"shop item '{item_key}'",
        )
        if shop_item is None:
            return None

        self.state.shop_items[item_key] = shop_item
        return shop_item

    @app_commands.command(name="create_shop_item", description="Add an item to the store")
    @require_admin()
    async def create_shop_item(
        self,
        interaction: discord.Interaction,
        item_key: str,
        currency_key: str,
        price: int,
    ) -> None:
        await self._ensure(interaction)
        shop_item = await self._save_shop_listing(
            interaction, item_key, currency_key, price
        )
        if shop_item is None:
            return
        await interaction.response.send_message("Shop item stored.", ephemeral=True)

    @create_shop_item.autocomplete("item_key")
    async def create_shop_item_key_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.items, current=current)

    @create_shop_item.autocomplete("currency_key")
    async def create_shop_item_currency_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.currencies, current=current)

    @app_commands.command(name="stock_shop", description="List an item for sale in the shop")
    @require_admin()
    async def stock_shop(
        self,
        interaction: discord.Interaction,
        item_key: str,
        currency_key: str,
        price: int,
    ) -> None:
        await self._ensure(interaction)
        shop_item = await self._save_shop_listing(
            interaction, item_key, currency_key, price
        )
        if shop_item is None:
            return
        item = self.state.items.get(item_key)
        currency = self.state.currencies.get(currency_key)
        item_name = item.name if item else item_key
        currency_name = currency.name if currency else currency_key
        await interaction.response.send_message(
            (
                f"{item_name} ({item_key}) is now available for {shop_item.price} "
                f"{currency_name}."
            ),
            ephemeral=True,
        )

    @stock_shop.autocomplete("item_key")
    async def stock_shop_item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self.create_shop_item_key_autocomplete(interaction, current)

    @stock_shop.autocomplete("currency_key")
    async def stock_shop_currency_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self.create_shop_item_currency_autocomplete(interaction, current)

    @app_commands.command(name="create_currency", description="Create a currency")
    @require_admin()
    async def create_currency(
        self,
        interaction: discord.Interaction,
        key: str,
        name: str,
        description: str,
    ) -> None:
        await self._ensure(interaction)
        payload = {"key": key, "name": name, "description": description}
        currency = await self._validate_and_store(
            interaction,
            cls=Currency,
            payload=payload,
            collection="currencies",
            key=key,
            entity_label=f"currency '{name}'",
        )
        if currency is None:
            return

        self.state.currencies[key] = currency
        await interaction.response.send_message(f"Currency {name} stored.", ephemeral=True)

    @app_commands.command(name="create_title", description="Create a player title")
    @require_admin()
    @app_commands.choices(
        position=[
            app_commands.Choice(name="Prefix", value="prefix"),
            app_commands.Choice(name="Suffix", value="suffix"),
        ]
    )
    async def create_title(
        self,
        interaction: discord.Interaction,
        key: str,
        name: str,
        description: str,
        *,
        position: app_commands.Choice[str] | None = None,
    ) -> None:
        await self._ensure(interaction)
        pos_value = position.value if position else TitlePosition.PREFIX.value
        try:
            title_position = TitlePosition.from_value(pos_value)
        except ValueError:
            await interaction.response.send_message(
                "Invalid title position provided. Choose prefix or suffix.",
                ephemeral=True,
            )
            return
        payload = {
            "key": key,
            "name": name,
            "description": description,
            "position": title_position,
        }

        title = await self._validate_and_store(
            interaction,
            cls=Title,
            payload=payload,
            collection="titles",
            key=key,
            entity_label=f"title '{name}'",
        )
        if title is None:
            return

        self.state.titles[key] = title
        await interaction.response.send_message(
            f"Title {name} stored as a {title_position.value}.",
            ephemeral=True,
        )

    @app_commands.command(name="grant_currency", description="Grant currency to a player")
    @require_admin()
    async def grant_currency(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        currency_key: str,
        amount: int,
    ) -> None:
        await self._ensure(interaction)
        if amount <= 0:
            await interaction.response.send_message(
                "Amount must be at least 1.", ephemeral=True
            )
            return
        currency = self.state.currencies.get(currency_key)
        if not currency:
            await interaction.response.send_message(
                "Unknown currency key.", ephemeral=True
            )
            return
        guild = interaction.guild
        assert guild is not None
        player = await self._fetch_player(guild.id, member.id)
        if not player:
            await interaction.response.send_message(
                "That player has not registered.", ephemeral=True
            )
            return
        current_amount = player.currencies.get(currency_key, 0)
        player.currencies[currency_key] = current_amount + amount
        await self.store.upsert_player(guild.id, asdict(player))
        await interaction.response.send_message(
            f"Granted {amount} {currency.name} to {member.display_name}.",
            ephemeral=True,
        )

    @grant_currency.autocomplete("currency_key")
    async def grant_currency_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.currencies, current=current)

    @app_commands.command(name="revoke_currency", description="Remove currency from a player")
    @require_admin()
    async def revoke_currency(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        currency_key: str,
        amount: int,
    ) -> None:
        await self._ensure(interaction)
        if amount <= 0:
            await interaction.response.send_message(
                "Amount must be at least 1.", ephemeral=True
            )
            return
        guild = interaction.guild
        assert guild is not None
        player = await self._fetch_player(guild.id, member.id)
        if not player:
            await interaction.response.send_message(
                "That player has not registered.", ephemeral=True
            )
            return
        current_amount = player.currencies.get(currency_key, 0)
        if current_amount <= 0:
            await interaction.response.send_message(
                f"{member.display_name} has no {currency_key} to revoke.",
                ephemeral=True,
            )
            return
        new_amount = max(0, current_amount - amount)
        player.currencies[currency_key] = new_amount
        if new_amount == 0:
            player.currencies.pop(currency_key, None)
        await self.store.upsert_player(guild.id, asdict(player))
        currency = self.state.currencies.get(currency_key)
        currency_name = currency.name if currency else currency_key
        await interaction.response.send_message(
            f"Removed {amount} {currency_name} from {member.display_name}.",
            ephemeral=True,
        )

    @revoke_currency.autocomplete("currency_key")
    async def revoke_currency_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        guild = interaction.guild
        if guild is None:
            return []
        member = getattr(interaction.namespace, "member", None)
        if not isinstance(member, discord.Member):
            return []
        player = await self._fetch_player(guild.id, member.id)
        if not player:
            return []
        query = current.lower()
        choices: list[app_commands.Choice[str]] = []
        for key, amount in player.currencies.items():
            if amount <= 0:
                continue
            currency = self.state.currencies.get(key)
            name = currency.name if currency else key
            if query and query not in key.lower() and query not in name.lower():
                continue
            label = f"{name} ({format_number(amount)})"
            choices.append(app_commands.Choice(name=label, value=key))
            if len(choices) >= 25:
                break
        return choices

    @app_commands.command(name="grant_item", description="Grant items to a player")
    @require_admin()
    async def grant_item(
        self, interaction: discord.Interaction, member: discord.Member, item_key: str, amount: int = 1
    ) -> None:
        await self._ensure(interaction)
        if amount <= 0:
            await interaction.response.send_message("Amount must be at least 1.", ephemeral=True)
            return
        if item_key not in self.state.items:
            await interaction.response.send_message("That item key is unknown.", ephemeral=True)
            return

        guild = interaction.guild
        assert guild is not None
        player = await self._fetch_player(guild.id, member.id)
        if not player:
            await interaction.response.send_message("That player has not registered.", ephemeral=True)
            return

        added = add_item_to_inventory(player, item_key, amount, self.state.items)
        if added <= 0:
            capacity = inventory_capacity(player, self.state.items)
            load = inventory_load(player)
            await interaction.response.send_message(
                (
                    "Player inventory is full. "
                    f"{member.display_name} is using {load}/{capacity} slots."
                ),
                ephemeral=True,
            )
            return

        await self.store.upsert_player(guild.id, asdict(player))

        item_name = self.state.items[item_key].name
        message = f"Granted {added} x {item_name} to {member.display_name}."
        if added < amount:
            message += " Remaining quantity could not be delivered due to inventory limits."
        await interaction.response.send_message(message, ephemeral=True)

    @grant_item.autocomplete("item_key")
    async def grant_item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.items, current=current)

    @app_commands.command(name="revoke_item", description="Remove items from a player")
    @require_admin()
    async def revoke_item(
        self, interaction: discord.Interaction, member: discord.Member, item_key: str, amount: int = 1
    ) -> None:
        await self._ensure(interaction)
        if amount <= 0:
            await interaction.response.send_message("Amount must be at least 1.", ephemeral=True)
            return

        guild = interaction.guild
        assert guild is not None
        player = await self._fetch_player(guild.id, member.id)
        if not player:
            await interaction.response.send_message("That player has not registered.", ephemeral=True)
            return

        current = player.inventory.get(item_key, 0)
        if current <= 0:
            await interaction.response.send_message(
                f"{member.display_name} does not possess that item.", ephemeral=True
            )
            return

        removed = min(amount, current)
        remaining = current - removed
        if remaining <= 0:
            player.inventory.pop(item_key, None)
        else:
            player.inventory[item_key] = remaining

        await self.store.upsert_player(guild.id, asdict(player))
        item = self.state.items.get(item_key)
        item_name = item.name if item else item_key
        await interaction.response.send_message(
            f"Removed {removed} x {item_name} from {member.display_name}.", ephemeral=True
        )

    @revoke_item.autocomplete("item_key")
    async def revoke_item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        guild = interaction.guild
        if guild is None:
            return []

        member = getattr(interaction.namespace, "member", None)
        if not isinstance(member, discord.Member):
            return []

        player = await self._fetch_player(guild.id, member.id)
        if not player:
            return []

        query = current.lower()
        choices: list[app_commands.Choice[str]] = []
        for key, amount in player.inventory.items():
            if amount <= 0:
                continue
            item = self.state.items.get(key)
            name = item.name if item else key
            if query and query not in key.lower() and query not in name.lower():
                continue
            choices.append(app_commands.Choice(name=f"{name} ({amount})", value=key))
            if len(choices) >= 25:
                break
        return choices

    @app_commands.command(
        name="revoke_equipment", description="Force a player to unequip an item"
    )
    @require_admin()
    async def revoke_equipment(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        item_key: str,
        return_to_inventory: bool = True,
    ) -> None:
        await self._ensure(interaction)
        guild = interaction.guild
        assert guild is not None
        player = await self._fetch_player(guild.id, member.id)
        if not player:
            await interaction.response.send_message(
                "That player has not registered.", ephemeral=True
            )
            return
        if item_key not in player.equipped_item_keys():
            await interaction.response.send_message(
                f"{member.display_name} does not have that item equipped.",
                ephemeral=True,
            )
            return
        item_obj = self.state.items.get(item_key)
        item_name = item_obj.name if item_obj else item_key
        slot_for_item: EquipmentSlot | None = None
        for slot_key, values in player.equipment.items():
            if item_key in values:
                try:
                    slot_for_item = EquipmentSlot.from_value(
                        slot_key, default=EquipmentSlot.ACCESSORY
                    )
                except ValueError:
                    slot_for_item = EquipmentSlot.ACCESSORY
                break

        if return_to_inventory:
            bonus = 0
            if item_obj is not None:
                try:
                    bonus = int(getattr(item_obj, "inventory_space_bonus", 0))
                except (TypeError, ValueError):
                    bonus = 0
            bonus = max(0, bonus)
            load_before = inventory_load(player)
            capacity_before = inventory_capacity(player, self.state.items)
            capacity_after = max(0, capacity_before - bonus)
            if load_before + 1 > capacity_after:
                await interaction.response.send_message(
                    (
                        "Their inventory is full. Clear space or set"
                        " `return_to_inventory` to `False`."
                    ),
                    ephemeral=True,
                )
                return

        removed = remove_equipped_item(player, item_key)
        if not removed:
            await interaction.response.send_message(
                "Unable to unequip that item.", ephemeral=True
            )
            return

        if return_to_inventory:
            added = add_item_to_inventory(player, item_key, 1, self.state.items)
            if added <= 0:
                if slot_for_item is not None:
                    add_equipped_item(player, slot_for_item, item_key)
                await interaction.response.send_message(
                    (
                        "Their inventory overflowed before the equipment"
                        " could be stored. Free up slots or unequip without"
                        " returning it."
                    ),
                    ephemeral=True,
                )
                return

        await self.store.upsert_player(guild.id, asdict(player))
        if return_to_inventory:
            message = (
                f"Removed {item_name} from {member.display_name} and returned it to their inventory."
            )
        else:
            message = f"Removed {item_name} from {member.display_name}."
        await interaction.response.send_message(message, ephemeral=True)

    @revoke_equipment.autocomplete("item_key")
    async def revoke_equipment_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        guild = interaction.guild
        if guild is None:
            return []
        member = getattr(interaction.namespace, "member", None)
        if not isinstance(member, discord.Member):
            return []
        player = await self._fetch_player(guild.id, member.id)
        if not player:
            return []
        query = current.lower()
        counts: dict[str, int] = {}
        for key in player.iter_equipped_item_keys():
            counts[key] = counts.get(key, 0) + 1
        choices: list[app_commands.Choice[str]] = []
        for key, amount in counts.items():
            item = self.state.items.get(key)
            name = item.name if item else key
            if query and query not in key.lower() and query not in name.lower():
                continue
            choices.append(app_commands.Choice(name=f"{name} ({amount})", value=key))
            if len(choices) >= 25:
                break
        return choices

    @app_commands.command(
        name="grant_cultivation_technique",
        description="Grant a cultivation technique to a player",
    )
    @require_admin()
    async def grant_cultivation_technique(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        technique_key: str,
    ) -> None:
        await self._ensure(interaction)
        technique = self.state.cultivation_techniques.get(technique_key)
        if technique is None:
            await interaction.response.send_message(
                "That technique key is unknown.", ephemeral=True
            )
            return

        guild = interaction.guild
        assert guild is not None
        player = await self._fetch_player(guild.id, member.id)
        if not player:
            await interaction.response.send_message(
                "That player has not registered.", ephemeral=True
            )
            return

        normalized_key = str(technique_key).strip()
        already = normalized_key in player.cultivation_technique_keys
        if not already:
            player.cultivation_technique_keys.append(normalized_key)

        granted_skills: list[str] = []
        for skill_key in technique.skills:
            if skill_key not in player.skill_proficiency:
                player.skill_proficiency[skill_key] = 0
                granted_skills.append(skill_key)

        await self.store.upsert_player(guild.id, asdict(player))

        technique_label = technique.name or technique.key
        if already:
            message = (
                f"{member.display_name} already mastered {technique_label}; ensured associated skills."
            )
        else:
            message = f"{technique_label} granted to {member.display_name}."

        if granted_skills:
            skill_names = []
            for skill_key in granted_skills:
                skill = self.state.skills.get(skill_key)
                skill_names.append(skill.name if skill else skill_key)
            message += f" Skills unlocked: {', '.join(skill_names)}."

        await interaction.response.send_message(message, ephemeral=True)

    @grant_cultivation_technique.autocomplete("technique_key")
    async def grant_cultivation_technique_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(
            self.state.cultivation_techniques, current=current
        )

    @app_commands.command(
        name="revoke_cultivation_technique",
        description="Remove a cultivation technique from a player",
    )
    @require_admin()
    @app_commands.describe(
        remove_skills="Also revoke skills granted exclusively by this technique",
    )
    async def revoke_cultivation_technique(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        technique_key: str,
        remove_skills: bool = False,
    ) -> None:
        await self._ensure(interaction)
        guild = interaction.guild
        assert guild is not None
        player = await self._fetch_player(guild.id, member.id)
        if not player:
            await interaction.response.send_message(
                "That player has not registered.", ephemeral=True
            )
            return

        normalized_key = str(technique_key).strip()
        if normalized_key not in player.cultivation_technique_keys:
            await interaction.response.send_message(
                f"{member.display_name} has not mastered that technique.",
                ephemeral=True,
            )
            return

        player.cultivation_technique_keys = [
            key for key in player.cultivation_technique_keys if key != normalized_key
        ]

        technique = self.state.cultivation_techniques.get(normalized_key)
        removed_skills: list[str] = []
        if remove_skills and technique is not None and technique.skills:
            remaining_skills: set[str] = set()
            for other_key in player.cultivation_technique_keys:
                other = self.state.cultivation_techniques.get(other_key)
                if other is None:
                    continue
                remaining_skills.update(other.skills)
            for skill_key in technique.skills:
                if (
                    skill_key in player.skill_proficiency
                    and skill_key not in remaining_skills
                ):
                    player.skill_proficiency.pop(skill_key, None)
                    removed_skills.append(skill_key)

        await self.store.upsert_player(guild.id, asdict(player))

        technique_label = technique.name if technique else normalized_key
        message = f"{technique_label} revoked from {member.display_name}."
        if removed_skills:
            skill_names = []
            for skill_key in removed_skills:
                skill = self.state.skills.get(skill_key)
                skill_names.append(skill.name if skill else skill_key)
            message += f" Skills revoked: {', '.join(skill_names)}."

        await interaction.response.send_message(message, ephemeral=True)

    @revoke_cultivation_technique.autocomplete("technique_key")
    async def revoke_cultivation_technique_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(
            self.state.cultivation_techniques, current=current
        )

    @app_commands.command(name="grant_skill", description="Grant a skill to a player")
    @require_admin()
    async def grant_skill(
        self, interaction: discord.Interaction, member: discord.Member, skill_key: str
    ) -> None:
        await self._ensure(interaction)
        if skill_key not in self.state.skills:
            await interaction.response.send_message("That skill key is unknown.", ephemeral=True)
            return

        guild = interaction.guild
        assert guild is not None
        player = await self._fetch_player(guild.id, member.id)
        if not player:
            await interaction.response.send_message("That player has not registered.", ephemeral=True)
            return

        already = skill_key in player.skill_proficiency
        player.skill_proficiency[skill_key] = 0
        await self.store.upsert_player(guild.id, asdict(player))

        skill_name = self.state.skills[skill_key].name
        message = (
            f"{skill_name} granted to {member.display_name}."
            if not already
            else f"{member.display_name} already knew {skill_name}; proficiency reset to 0."
        )
        await interaction.response.send_message(message, ephemeral=True)

    @grant_skill.autocomplete("skill_key")
    async def grant_skill_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.skills, current=current)

    @app_commands.command(name="revoke_skill", description="Remove a skill from a player")
    @require_admin()
    async def revoke_skill(
        self, interaction: discord.Interaction, member: discord.Member, skill_key: str
    ) -> None:
        await self._ensure(interaction)
        guild = interaction.guild
        assert guild is not None
        player = await self._fetch_player(guild.id, member.id)
        if not player:
            await interaction.response.send_message("That player has not registered.", ephemeral=True)
            return

        removed = player.skill_proficiency.pop(skill_key, None)
        if removed is None:
            await interaction.response.send_message(
                f"{member.display_name} does not know that skill.", ephemeral=True
            )
            return

        await self.store.upsert_player(guild.id, asdict(player))
        skill_name = self.state.skills.get(skill_key)
        pretty = skill_name.name if skill_name else skill_key
        await interaction.response.send_message(
            f"{pretty} revoked from {member.display_name}.", ephemeral=True
        )

    @revoke_skill.autocomplete("skill_key")
    async def revoke_skill_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        guild = interaction.guild
        if guild is None:
            return []

        member = getattr(interaction.namespace, "member", None)
        if not isinstance(member, discord.Member):
            return []

        player = await self._fetch_player(guild.id, member.id)
        if not player:
            return []

        query = current.lower()
        choices: list[app_commands.Choice[str]] = []
        for key in player.skill_proficiency.keys():
            skill = self.state.skills.get(key)
            name = skill.name if skill else key
            if query and query not in key.lower() and query not in name.lower():
                continue
            choices.append(app_commands.Choice(name=name, value=key))
            if len(choices) >= 25:
                break
        return choices

    @app_commands.command(name="grant_title", description="Grant a title to a player")
    @require_admin()
    async def grant_title(
        self, interaction: discord.Interaction, member: discord.Member, title_key: str
    ) -> None:
        await self._ensure(interaction)
        if title_key not in self.state.titles:
            await interaction.response.send_message("That title key is unknown.", ephemeral=True)
            return

        guild = interaction.guild
        assert guild is not None
        player = await self._fetch_player(guild.id, member.id)
        if not player:
            await interaction.response.send_message("That player has not registered.", ephemeral=True)
            return

        title = self.state.titles[title_key]
        granted = player.grant_title(title_key, position=title.position)
        await self.store.upsert_player(guild.id, asdict(player))
        title_name = title.name
        if granted:
            message = f"{title_name} granted to {member.display_name}."
        else:
            message = f"{member.display_name} already possesses {title_name}."
        await interaction.response.send_message(message, ephemeral=True)

    @grant_title.autocomplete("title_key")
    async def grant_title_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.titles, current=current)

    @app_commands.command(name="revoke_title", description="Remove a title from a player")
    @require_admin()
    async def revoke_title(
        self, interaction: discord.Interaction, member: discord.Member, title_key: str
    ) -> None:
        await self._ensure(interaction)
        guild = interaction.guild
        assert guild is not None
        player = await self._fetch_player(guild.id, member.id)
        if not player:
            await interaction.response.send_message("That player has not registered.", ephemeral=True)
            return

        removed = player.revoke_title(title_key)
        if not removed:
            await interaction.response.send_message(
                f"{member.display_name} does not hold that title.", ephemeral=True
            )
            return

        await self.store.upsert_player(guild.id, asdict(player))
        title = self.state.titles.get(title_key)
        pretty = title.name if title else title_key
        await interaction.response.send_message(
            f"{pretty} revoked from {member.display_name}.", ephemeral=True
        )

    @revoke_title.autocomplete("title_key")
    async def revoke_title_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        guild = interaction.guild
        if guild is None:
            return []

        member = getattr(interaction.namespace, "member", None)
        if not isinstance(member, discord.Member):
            return []

        player = await self._fetch_player(guild.id, member.id)
        if not player:
            return []

        query = current.lower()
        choices: list[app_commands.Choice[str]] = []
        for key in player.titles:
            title = self.state.titles.get(key)
            name = title.name if title else key
            if query and query not in key.lower() and query not in name.lower():
                continue
            choices.append(app_commands.Choice(name=name, value=key))
            if len(choices) >= 25:
                break
        return choices

    @app_commands.command(name="grant_trait", description="Grant a special trait to a player")
    @require_admin()
    async def grant_trait(
        self, interaction: discord.Interaction, member: discord.Member, trait_key: str
    ) -> None:
        await self._ensure(interaction)
        if trait_key not in self.state.traits:
            await interaction.response.send_message("That trait key is unknown.", ephemeral=True)
            return

        guild = interaction.guild
        assert guild is not None
        player = await self._fetch_player(guild.id, member.id)
        if not player:
            await interaction.response.send_message("That player has not registered.", ephemeral=True)
            return

        if trait_key in player.trait_keys:
            await interaction.response.send_message(
                f"{member.display_name} already has that trait.", ephemeral=True
            )
            return

        player.trait_keys.append(trait_key)
        trait = self.state.traits[trait_key]
        positions = self._title_positions(trait.grants_titles)
        new_titles = player.grant_titles(trait.grants_titles, positions=positions)
        for title_key in new_titles:
            title_obj = self.state.titles.get(title_key)
            if title_obj:
                player.auto_equip_title(title_obj)
        await self.store.upsert_player(guild.id, asdict(player))
        player_cog = self.bot.get_cog("PlayerCog")
        sync_traits = getattr(player_cog, "_sync_trait_roles", None)
        if callable(sync_traits):
            await sync_traits(guild, member, player.trait_keys)

        trait_message = f"Trait {trait.name} granted to {member.display_name}."
        if new_titles:
            title_names = [
                self.state.titles.get(key).name if key in self.state.titles else key
                for key in new_titles
            ]
            trait_message += f" New titles: {', '.join(title_names)}."
        if trait.grants_affinities:
            affinity_names = ", ".join(
                describe_affinity(affinity)
                for affinity in trait.grants_affinities
                if isinstance(affinity, SpiritualAffinity)
            )
            trait_message += f" Granted affinities: {affinity_names}."
        await interaction.response.send_message(trait_message, ephemeral=True)

    @grant_trait.autocomplete("trait_key")
    async def grant_trait_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.traits, current=current)

    @app_commands.command(name="revoke_trait", description="Remove a special trait from a player")
    @require_admin()
    async def revoke_trait(
        self, interaction: discord.Interaction, member: discord.Member, trait_key: str
    ) -> None:
        await self._ensure(interaction)
        guild = interaction.guild
        assert guild is not None
        player = await self._fetch_player(guild.id, member.id)
        if not player:
            await interaction.response.send_message("That player has not registered.", ephemeral=True)
            return

        if trait_key not in player.trait_keys:
            await interaction.response.send_message(
                f"{member.display_name} does not possess that trait.", ephemeral=True
            )
            return

        player.trait_keys.remove(trait_key)
        trait = self.state.traits.get(trait_key)
        removed_titles: list[str] = []
        if trait:
            for title_key in trait.grants_titles:
                title = self.state.titles.get(title_key)
                if player.revoke_title(title_key):
                    removed_titles.append(title.name if title else title_key)
        await self.store.upsert_player(guild.id, asdict(player))
        player_cog = self.bot.get_cog("PlayerCog")
        sync_traits = getattr(player_cog, "_sync_trait_roles", None)
        if callable(sync_traits):
            await sync_traits(guild, member, player.trait_keys)

        trait_name = trait.name if trait else trait_key
        message = f"Trait {trait_name} removed from {member.display_name}."
        if removed_titles:
            message += f" Titles revoked: {', '.join(removed_titles)}."
        if trait and trait.grants_affinities:
            affinity_names = ", ".join(
                describe_affinity(affinity)
                for affinity in trait.grants_affinities
                if isinstance(affinity, SpiritualAffinity)
            )
            message += f" Affinities withdrawn: {affinity_names}."
        await interaction.response.send_message(message, ephemeral=True)

    @revoke_trait.autocomplete("trait_key")
    async def revoke_trait_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        guild = interaction.guild
        if guild is None:
            return []

        member = getattr(interaction.namespace, "member", None)
        if not isinstance(member, discord.Member):
            return []

        player = await self._fetch_player(guild.id, member.id)
        if not player:
            return []

        query = current.lower()
        choices: list[app_commands.Choice[str]] = []
        for key in player.trait_keys:
            trait = self.state.traits.get(key)
            name = trait.name if trait else key
            if query and query not in key.lower() and query not in name.lower():
                continue
            choices.append(app_commands.Choice(name=name, value=key))
            if len(choices) >= 25:
                break
        return choices


    @app_commands.command(
        name="grant_legacy_trait",
        description="Imprint a trait onto a cultivator's legacy",
    )
    @require_admin()
    async def grant_legacy_trait(
        self, interaction: discord.Interaction, member: discord.Member, trait_key: str
    ) -> None:
        await self._ensure(interaction)
        trait = self.state.traits.get(trait_key)
        if not trait:
            await interaction.response.send_message(
                "That trait key is unknown.", ephemeral=True
            )
            return

        guild = interaction.guild
        assert guild is not None
        player = await self._fetch_player(guild.id, member.id)
        if not player:
            await interaction.response.send_message(
                "That player has not registered.", ephemeral=True
            )
            return

        if not player.add_legacy_trait(trait_key):
            await interaction.response.send_message(
                f"{member.display_name}'s legacy already bears that trait.",
                ephemeral=True,
            )
            return

        await self.store.upsert_player(guild.id, asdict(player))
        await interaction.response.send_message(
            (
                f"{trait.name} now slumbers within {member.display_name}'s "
                "legacy tablet."
            ),
            ephemeral=True,
        )

    @grant_legacy_trait.autocomplete("trait_key")
    async def grant_legacy_trait_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        return self._iter_entity_choices(self.state.traits, current=current)

    @app_commands.command(
        name="revoke_legacy_trait",
        description="Remove a trait from a cultivator's legacy",
    )
    @require_admin()
    async def revoke_legacy_trait(
        self, interaction: discord.Interaction, member: discord.Member, trait_key: str
    ) -> None:
        await self._ensure(interaction)
        guild = interaction.guild
        assert guild is not None
        player = await self._fetch_player(guild.id, member.id)
        if not player:
            await interaction.response.send_message(
                "That player has not registered.", ephemeral=True
            )
            return

        if not player.remove_legacy_trait(trait_key):
            await interaction.response.send_message(
                f"{member.display_name}'s legacy does not contain that trait.",
                ephemeral=True,
            )
            return

        await self.store.upsert_player(guild.id, asdict(player))
        trait = self.state.traits.get(trait_key)
        trait_name = trait.name if trait else trait_key
        await interaction.response.send_message(
            (
                f"The echo of {trait_name} has been lifted from "
                f"{member.display_name}'s legacy."
            ),
            ephemeral=True,
        )

    @revoke_legacy_trait.autocomplete("trait_key")
    async def revoke_legacy_trait_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        await self._ensure(interaction)
        guild = interaction.guild
        if guild is None:
            return []

        member = getattr(interaction.namespace, "member", None)
        if not isinstance(member, discord.Member):
            return []

        player = await self._fetch_player(guild.id, member.id)
        if not player:
            return []

        query = current.lower()
        choices: list[app_commands.Choice[str]] = []
        for key in player.legacy_traits:
            trait = self.state.traits.get(key)
            name = trait.name if trait else key
            if query and query not in key.lower() and query not in name.lower():
                continue
            choices.append(app_commands.Choice(name=name, value=key))
            if len(choices) >= 25:
                break
        return choices


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
def describe_affinity(affinity: SpiritualAffinity) -> str:
    label = affinity.display_name
    if affinity.is_mixed:
        components = " + ".join(component.display_name for component in affinity.components)
        label = f"{label} ({components})"
    return label


