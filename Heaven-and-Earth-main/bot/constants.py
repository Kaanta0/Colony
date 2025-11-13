"""Shared constants used across bot cogs and utilities."""

from __future__ import annotations

import discord

from .models.soul_land import SOUL_POWER_RANK_TABLE

# Default cooldown (in seconds) before a player may cultivate again if the
# guild has not configured an override. This is referenced by multiple cogs,
# so it lives in a shared module to avoid circular imports.
DEFAULT_CULTIVATION_COOLDOWN = 300

# Unified emoji used for travel and wander actions across the bot.
CURRENCY_EMOJI = discord.PartialEmoji(name="tael", id=1433474377755660403)
CURRENCY_EMOJI_TEXT = str(CURRENCY_EMOJI)

WANDER_TRAVEL_EMOJI = discord.PartialEmoji(name="wander", id=1433177999234306129)
WANDER_TRAVEL_EMOJI_TEXT = str(WANDER_TRAVEL_EMOJI)

# Ordered list of Soul Land rank titles for quick reference in embeds.
SOUL_POWER_TITLES = tuple(rank.title for rank in SOUL_POWER_RANK_TABLE)

# Maximum number of spirit rings a cultivator may eventually unlock.
MAX_SPIRIT_RING_SLOTS = SOUL_POWER_RANK_TABLE[-1].ring_slots

