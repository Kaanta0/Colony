from __future__ import annotations

from dataclasses import asdict
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ..game import add_item_to_inventory, inventory_capacity, inventory_load
from ..models.players import PlayerProgress
from ..views import ShopEntry, ShopPurchaseResult, ShopView
from ..utils import format_number
from .base import HeavenCog


class EconomyCog(HeavenCog):
    async def _fetch_player(self, guild_id: int, user_id: int) -> Optional[PlayerProgress]:
        data = await self.store.get_player(guild_id, user_id)
        if not data:
            return None
        player = PlayerProgress(**data)
        self.state.register_player(player)
        return player

    async def _save_player(self, guild_id: int, player: PlayerProgress) -> None:
        await self.store.upsert_player(guild_id, asdict(player))

    async def _execute_purchase(
        self,
        guild_id: int,
        player: PlayerProgress,
        item_key: str,
        quantity: int,
    ) -> ShopPurchaseResult:
        if item_key not in self.state.shop_items:
            return ShopPurchaseResult(False, "That item is not sold here.")
        shop_item = self.state.shop_items[item_key]
        currency = self.state.currencies.get(shop_item.currency_key)
        if currency is None:
            return ShopPurchaseResult(
                False, "The currency for this item is not configured."
            )
        item = self.state.items.get(item_key)
        if item is None:
            return ShopPurchaseResult(False, "That item no longer exists.")
        amount = max(1, quantity)
        total_cost = shop_item.price * amount
        balance = int(player.currencies.get(shop_item.currency_key, 0))
        if balance < total_cost:
            return ShopPurchaseResult(False, "You cannot afford that purchase.")
        capacity = inventory_capacity(player, self.state.items)
        load = inventory_load(player)
        available = capacity - load
        if available < amount:
            remaining = max(0, available)
            return ShopPurchaseResult(
                False, f"You can only carry {format_number(remaining)} more items."
            )
        player.currencies[shop_item.currency_key] = balance - total_cost
        added = add_item_to_inventory(player, item_key, amount, self.state.items)
        if added < amount:
            player.currencies[shop_item.currency_key] = balance
            return ShopPurchaseResult(
                False, "Inventory limits prevented the purchase."
            )
        await self._save_player(guild_id, player)
        item_name = item.name
        purchase_text = (
            f"You purchase {format_number(amount)}x {item_name} "
            f"for {format_number(total_cost)} {currency.name}."
        )
        return ShopPurchaseResult(True, purchase_text)

    @app_commands.command(name="shop", description="View the sect's marketplace offerings")
    @app_commands.guild_only()
    async def shop(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        await self.ensure_guild_loaded(guild.id)
        if not self.state.shop_items:
            await interaction.response.send_message("The shop has no wares yet.", ephemeral=True)
            return
        entries: dict[str, ShopEntry] = {}
        for item_key, shop_item in sorted(self.state.shop_items.items()):
            item = self.state.items.get(item_key)
            currency = self.state.currencies.get(shop_item.currency_key)
            if not item or not currency:
                continue
            entries[item_key] = ShopEntry(
                key=item_key,
                name=item.name,
                description=item.description or "No description provided.",
                price=shop_item.price,
                currency_key=shop_item.currency_key,
                currency_name=currency.name,
            )
        if not entries:
            await interaction.response.send_message(
                "No valid items configured.", ephemeral=True
            )
            return
        player = await self._fetch_player(guild.id, interaction.user.id)

        async def handle_purchase(
            purchase_interaction: discord.Interaction,
            view: ShopView,
            selected_key: str,
            amount: int,
        ) -> ShopPurchaseResult:
            if view.player is None:
                return ShopPurchaseResult(False, "Register first with /register.")
            return await self._execute_purchase(
                guild.id, view.player, selected_key, amount
            )

        view = ShopView(entries, player=player, purchase_callback=handle_purchase)
        embed = view.build_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        try:
            view.message = await interaction.original_response()
        except discord.HTTPException:
            view.message = None

    @app_commands.command(name="buy", description="Purchase an item from the shop")
    @app_commands.describe(item_key="Key of the item to buy", amount="Quantity to purchase")
    @app_commands.guild_only()
    async def buy(self, interaction: discord.Interaction, item_key: str, amount: int = 1) -> None:
        guild = interaction.guild
        assert guild is not None
        await self.ensure_guild_loaded(guild.id)
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            await interaction.response.send_message("Register first with /register", ephemeral=True)
            return
        result = await self._execute_purchase(guild.id, player, item_key, amount)
        await interaction.response.send_message(
            result.message, ephemeral=not result.success
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EconomyCog(bot))
