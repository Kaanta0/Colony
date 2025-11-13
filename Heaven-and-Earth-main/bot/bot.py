"""Entry point for the Heaven and Earth Xianxia cultivation Discord bot."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from .config import BotConfig
from .game import GameState
from .storage import DataStore

log = logging.getLogger(__name__)


class HeavenAndEarth(commands.Bot):
    def __init__(self, config: BotConfig):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.config = config
        self.state = GameState()
        self.store = DataStore()
        self._synced = False

    async def setup_hook(self) -> None:
        await self.load_extension("bot.cogs.player")
        await self.load_extension("bot.cogs.party")
        await self.load_extension("bot.cogs.combat")
        await self.load_extension("bot.cogs.economy")
        await self.load_extension("bot.cogs.admin")

    async def on_ready(self) -> None:
        if not self._synced:
            await self.tree.sync()
            for guild in self.guilds:
                await self.tree.sync(guild=guild)
            self._synced = True
            log.info("Application commands synced")
        if self.user:
            log.info("Connected as %s (%s)", self.user, self.user.id)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.tree.sync()
        await self.tree.sync(guild=guild)
        log.info("Synced application commands for guild %s (%s)", guild.name, guild.id)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = BotConfig.from_env()
    bot = HeavenAndEarth(config)
    async with bot:
        await bot.start(config.token)


if __name__ == "__main__":
    asyncio.run(main())
