"""Shared helpers for cogs."""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Type, TypeVar

import discord
from discord.ext import commands

from ..game import GameState
from ..models import ModelValidationError, validate_dataclass_payload
from ..models.combat import PassiveHealEffect, Skill, SkillCategory, Stats
from ..models.players import BondProfile, PlayerProgress
from ..models.progression import (
    CultivationPath,
    CultivationStage,
    CultivationTechnique,
    Race,
    SpecialTrait,
    Title,
)
from ..models.world import (
    Boss,
    Currency,
    Enemy,
    Item,
    Location,
    LocationNPC,
    Quest,
    ShopItem,
)
from ..storage import DataStore

log = logging.getLogger(__name__)

T = TypeVar("T")


def load_dataclass(cls: Type[T], data: Dict[str, Any]) -> T:
    payload = validate_dataclass_payload(cls, data)
    factory = getattr(cls, "from_dict", None)
    if callable(factory):
        return factory(payload)
    return cls(**payload)


class HeavenCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._loaded_collections: dict[str, set[int]] = {}

    @property
    def store(self) -> DataStore:
        return self.bot.store  # type: ignore[return-value]

    @property
    def state(self) -> GameState:
        return self.bot.state  # type: ignore[return-value]

    async def send_validation_error(
        self,
        interaction: discord.Interaction,
        model_name: str,
        error: ModelValidationError,
    ) -> None:
        header = f"Unable to save {model_name}:"
        details = error.errors or [str(error)]
        bullet_list = "\n".join(f"• {entry}" for entry in details)
        message = f"{header}\n{bullet_list}" if bullet_list else header
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    async def ensure_guild_loaded(self, guild_id: int) -> None:
        preload = await self.store.get_many(
            guild_id,
            (
                "cultivation_stages",
                "races",
                "traits",
                "skills",
                "cultivation_techniques",
                "items",
                "quests",
                "enemies",
                "bosses",
                "locations",
                "npcs",
                "currencies",
                "shop",
                "titles",
                "bonds",
                "config",
            ),
        )

        await self._load_cultivation_stages(
            guild_id, bucket=preload.get("cultivation_stages")
        )
        self.state.ensure_default_stages()
        await self._load_collection(
            guild_id,
            "races",
            Race,
            self.state.races,
            bucket=preload.get("races"),
        )
        self.state.ensure_default_race()
        await self._load_collection(
            guild_id,
            "traits",
            SpecialTrait,
            self.state.traits,
            bucket=preload.get("traits"),
        )
        await self._load_collection(
            guild_id,
            "skills",
            Skill,
            self.state.skills,
            bucket=preload.get("skills"),
        )
        await self._load_collection(
            guild_id,
            "cultivation_techniques",
            CultivationTechnique,
            self.state.cultivation_techniques,
            bucket=preload.get("cultivation_techniques"),
        )
        await self._load_core_config(guild_id, bucket=preload.get("config"))
        self.state.ensure_martial_soul_signature_skills()
        await self._load_collection(
            guild_id,
            "items",
            Item,
            self.state.items,
            bucket=preload.get("items"),
        )
        self.state.ensure_default_equipment()
        await self._load_collection(
            guild_id,
            "quests",
            Quest,
            self.state.quests,
            bucket=preload.get("quests"),
        )
        await self._load_collection(
            guild_id,
            "enemies",
            Enemy,
            self.state.enemies,
            bucket=preload.get("enemies"),
        )
        self.state.ensure_default_enemies()
        await self._load_collection(
            guild_id,
            "bosses",
            Boss,
            self.state.bosses,
            bucket=preload.get("bosses"),
        )
        await self._load_collection(
            guild_id,
            "locations",
            Location,
            self.state.locations,
            bucket=preload.get("locations"),
        )
        await self._load_collection(
            guild_id,
            "npcs",
            LocationNPC,
            self.state.npcs,
            bucket=preload.get("npcs"),
        )
        await self._load_collection(
            guild_id,
            "currencies",
            Currency,
            self.state.currencies,
            bucket=preload.get("currencies"),
        )
        self.state.ensure_default_currency()
        await self._load_collection(
            guild_id,
            "shop",
            ShopItem,
            self.state.shop_items,
            bucket=preload.get("shop"),
        )
        await self._load_collection(
            guild_id,
            "titles",
            Title,
            self.state.titles,
            bucket=preload.get("titles"),
        )
        await self._load_collection(
            guild_id,
            "bonds",
            BondProfile,
            self.state.bonds,
            bucket=preload.get("bonds"),
        )
        self.state.ensure_world_map()

    async def _load_collection(
        self,
        guild_id: int,
        collection: str,
        cls: Type[T],
        target: Dict[str, T],
        *,
        bucket: Mapping[str, Any] | None = None,
    ) -> None:
        loaded = self._loaded_collections.setdefault(collection, set())
        if guild_id in loaded:
            return
        if bucket is None:
            bucket = await self.store.get(guild_id, collection)
        if cls is Location:
            for key, value in bucket.items():
                try:
                    location = load_dataclass(cls, value)
                except ModelValidationError as exc:
                    log.error(
                        "Failed to load %s '%s' for guild %s: %s",
                        collection,
                        key,
                        guild_id,
                        "; ".join(exc.errors) or exc,
                    )
                    continue
                storage_key = str(key)
                location.apply_storage_key(storage_key)
                self.state.register_location(location, storage_key=storage_key)
        else:
            for key, value in bucket.items():
                try:
                    target[key] = load_dataclass(cls, value)
                except ModelValidationError as exc:
                    log.error(
                        "Failed to load %s '%s' for guild %s: %s",
                        collection,
                        key,
                        guild_id,
                        "; ".join(exc.errors) or exc,
                    )
                    continue
        loaded.add(guild_id)

    async def _load_cultivation_stages(
        self, guild_id: int, *, bucket: Mapping[str, Any] | None = None
    ) -> None:
        loaded = self._loaded_collections.setdefault("cultivation_stages", set())
        if guild_id in loaded:
            return
        if bucket is None:
            bucket = await self.store.get(guild_id, "cultivation_stages")
        for storage_key, payload in bucket.items():
            try:
                stage = load_dataclass(CultivationStage, payload)
            except ModelValidationError as exc:
                log.error(
                    "Failed to load cultivation stage '%s' for guild %s: %s",
                    storage_key,
                    guild_id,
                    "; ".join(exc.errors) or exc,
                )
                continue
            stored_path = None
            if ":" in storage_key:
                prefix, suffix = storage_key.split(":", 1)
                try:
                    stored_path = CultivationPath.from_value(prefix)
                except ValueError:
                    stored_path = None
                if not stage.key:
                    stage.key = suffix
            path_value = stored_path or CultivationPath.from_value(stage.path)
            stage.path = path_value.value
            self.state.register_stage(stage, storage_key=storage_key)
        loaded.add(guild_id)

    async def _load_core_config(
        self, guild_id: int, *, bucket: Mapping[str, Any] | None = None
    ) -> None:
        need_base_ranges = not getattr(self.state, "innate_soul_exp_ranges_loaded", False)
        if not need_base_ranges:
            return
        if bucket is None:
            bucket = await self.store.get(guild_id, "config")
        if need_base_ranges:
            ranges_payload = bucket.get("innate_soul_exp_ranges", {})
            if isinstance(ranges_payload, Mapping):
                self.state.update_innate_soul_exp_ranges(ranges_payload)
            else:
                self.state.innate_soul_exp_ranges_loaded = True

    def passive_skill_bonuses(self, player: PlayerProgress) -> dict[str, Stats]:
        """Return the current stat bonuses granted by passive skills."""

        bonuses: dict[str, Stats] = {}
        for key, proficiency in player.skill_proficiency.items():
            skill = self.state.skills.get(key)
            if not skill or skill.category is not SkillCategory.PASSIVE:
                continue
            base_bonus = skill.stat_bonuses
            has_bonus = any(value for _, value in base_bonus.items())
            if not has_bonus:
                continue
            ratio = 1.0
            if skill.proficiency_max > 0:
                ratio = min(max(proficiency, 0), skill.proficiency_max) / float(
                    skill.proficiency_max
                )
            if ratio <= 0:
                continue
            scaled = Stats()
            for stat_name, value in base_bonus.items():
                if not value:
                    continue
                current = getattr(scaled, stat_name)
                setattr(scaled, stat_name, current + value * ratio)
            bonuses[key] = scaled
        return bonuses

    def passive_skill_heals(self, player: PlayerProgress) -> dict[str, PassiveHealEffect]:
        """Return the passive healing effects granted by the cultivator's skills."""

        heals: dict[str, PassiveHealEffect] = {}
        for key, proficiency in player.skill_proficiency.items():
            skill = self.state.skills.get(key)
            if not skill or skill.category is not SkillCategory.PASSIVE:
                continue
            effect = skill.passive_heal_effect()
            if effect is None:
                continue
            ratio = 1.0
            if skill.proficiency_max > 0:
                ratio = min(max(proficiency, 0), skill.proficiency_max) / float(
                    skill.proficiency_max
                )
            amount = effect.amount * ratio
            if amount <= 0:
                continue
            heals[key] = PassiveHealEffect(
                skill_key=effect.skill_key,
                amount=amount,
                interval=effect.interval,
                pool=effect.pool,
            )
        return heals

    def passive_skill_bonus(self, player: PlayerProgress) -> Stats:
        """Aggregate passive skill bonuses into a single stat block."""

        total = Stats()
        for bonus in self.passive_skill_bonuses(player).values():
            total.add_in_place(bonus)
        return total
