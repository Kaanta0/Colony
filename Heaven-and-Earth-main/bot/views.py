"""Discord UI components for user interactions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Iterable, Mapping, Optional, Sequence, Literal, TYPE_CHECKING

import discord

from .constants import WANDER_TRAVEL_EMOJI, WANDER_TRAVEL_EMOJI_TEXT
from .models.players import PlayerProgress
from .models.world import Currency, Item, LocationNPC, LocationNPCType
from .models.map import (
    CampAction,
    ForageAction,
    SetTrapAction,
    TileCategory,
    TileCoordinate,
    TravelEvent,
    TravelPath,
)
from .utils import build_travel_narrative, format_number

if TYPE_CHECKING:  # pragma: no cover
    from .game import GameState
    from .travel import TravelEngine

LocationCallback = Callable[[discord.Interaction, str], Awaitable[None]]
CultivateCallback = Callable[[discord.Interaction], Awaitable[None]]
CombatCallback = Callable[[discord.Interaction, str], Awaitable[None]]
ProfilePageBuilder = Callable[[], discord.Embed]
SimpleCallback = Callable[[discord.Interaction], Awaitable[None]]
OptionCallback = Callable[[discord.Interaction, str], Awaitable[None]]


@dataclass(slots=True)
class ShopEntry:
    key: str
    name: str
    description: str
    price: int
    currency_key: str
    currency_name: str


@dataclass(slots=True)
class ShopPurchaseResult:
    success: bool
    message: str


@dataclass(slots=True)
class TradePartnerOption:
    user_id: int
    display_name: str
    description: str | None = None


@dataclass(slots=True)
class TradeOfferDetails:
    items: dict[str, int] = field(default_factory=dict)
    currencies: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class TradeProposal:
    initiator_id: int
    initiator_name: str
    partner_id: int
    partner_name: str
    offer: TradeOfferDetails
    request: TradeOfferDetails


def _fallback_trade_name(key: str) -> str:
    text = key.replace("_", " ").replace("-", " ")
    return text.title() if text else key


def _trade_item_name(items: Mapping[str, Item], item_key: str) -> str:
    item = items.get(item_key)
    if item and item.name:
        return item.name
    return _fallback_trade_name(item_key)


def _trade_currency_name(currencies: Mapping[str, Currency], currency_key: str) -> str:
    currency = currencies.get(currency_key)
    if currency and currency.name:
        return currency.name
    return _fallback_trade_name(currency_key)


def _format_trade_section(
    *,
    items: Mapping[str, int],
    currencies: Mapping[str, int],
    item_lookup: Mapping[str, Item],
    currency_lookup: Mapping[str, Currency],
    empty_message: str,
) -> str:
    lines: list[str] = []
    for key, amount in sorted(
        items.items(), key=lambda entry: _trade_item_name(item_lookup, entry[0]).lower()
    ):
        if amount <= 0:
            continue
        lines.append(
            f"• {format_number(amount)}× {_trade_item_name(item_lookup, key)}"
        )
    for key, amount in sorted(
        currencies.items(),
        key=lambda entry: _trade_currency_name(currency_lookup, entry[0]).lower(),
    ):
        if amount <= 0:
            continue
        lines.append(
            f"• {format_number(amount)} {_trade_currency_name(currency_lookup, key)}"
        )
    return "\n".join(lines) if lines else empty_message


ShopPurchaseCallback = Callable[
    [discord.Interaction, "ShopView", str, int], Awaitable[ShopPurchaseResult]
]


class ShopItemSelect(discord.ui.Select):
    def __init__(self, entries: Mapping[str, ShopEntry]):
        options: list[discord.SelectOption] = []
        for key, entry in entries.items():
            description = entry.description.strip()
            if description:
                description = description[:95] + "…" if len(description) > 95 else description
            options.append(
                discord.SelectOption(
                    label=entry.name[:100],
                    value=key,
                    description=description or None,
                )
            )
        super().__init__(
            placeholder="Browse marketplace wares",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if isinstance(view, ShopView):
            view.selected_key = self.values[0]
            await view.update_shop_message(interaction)


class ShopQuantitySelect(discord.ui.Select):
    def __init__(self) -> None:
        options = [
            discord.SelectOption(label="×1", value="1", description="Purchase a single item"),
            discord.SelectOption(label="×5", value="5", description="Purchase five at once"),
            discord.SelectOption(label="×10", value="10", description="Purchase a bundle of ten"),
            discord.SelectOption(label="×25", value="25", description="Stock up with twenty-five"),
            discord.SelectOption(label="×50", value="50", description="Mass purchase of fifty"),
        ]
        super().__init__(
            placeholder="Select purchase quantity",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if isinstance(view, ShopView):
            try:
                amount = int(self.values[0])
            except (TypeError, ValueError):
                amount = 1
            view.quantity = max(1, amount)
            await view.update_shop_message(interaction)


class ShopBuyButton(discord.ui.Button["ShopView"]):
    def __init__(self) -> None:
        super().__init__(style=discord.ButtonStyle.success, label="Purchase", emoji="🛒")

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if isinstance(view, ShopView):
            await view.handle_purchase(interaction)


class ShopReturnButton(discord.ui.Button["ShopView"]):
    def __init__(self) -> None:
        super().__init__(style=discord.ButtonStyle.secondary, label="Back", emoji="↩️")

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if not isinstance(view, ShopView):
            await interaction.response.send_message(
                "This shop is no longer available.", ephemeral=True
            )
            return
        if view._return_callback is None:
            await interaction.response.send_message(
                "This shop is no longer available.", ephemeral=True
            )
            return
        await view._return_callback(interaction)


class OwnedView(discord.ui.View):
    """Base view that restricts interactions to a single Discord user."""

    def __init__(self, owner_id: int | None, *, timeout: float = 120.0) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:  # type: ignore[override]
        if self.owner_id is None or interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Only the cultivator who summoned these controls may use them.",
            ephemeral=True,
        )
        return False


class SharedOwnedView(OwnedView):
    """View that allows a predefined set of cultivators to interact."""

    def __init__(
        self, owner_ids: Iterable[int] | None, *, timeout: float = 120.0
    ) -> None:
        super().__init__(owner_id=None, timeout=timeout)
        self.owner_ids = frozenset(owner_ids or ())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:  # type: ignore[override]
        if not self.owner_ids or interaction.user.id in self.owner_ids:
            return True
        await interaction.response.send_message(
            "Only the cultivators invited to this negotiation may use these controls.",
            ephemeral=True,
        )
        return False


class LootDetailsButton(discord.ui.Button["LootDetailsView"]):
    def __init__(self) -> None:
        super().__init__(
            style=discord.ButtonStyle.primary,
            label="Loots",
            emoji="🎁",
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if not isinstance(view, LootDetailsView):
            await interaction.response.send_message(
                "Loot details are no longer available.", ephemeral=True
            )
            return
        await view.show_details(interaction)


class LootDetailsView(OwnedView):
    """View that reveals formatted loot details on demand."""

    def __init__(
        self,
        owner_id: int | None,
        detail_blocks: Sequence[str],
        *,
        timeout: float = 180.0,
    ) -> None:
        super().__init__(owner_id, timeout=timeout)
        self._detail_blocks: list[str] = [block for block in detail_blocks if block]
        self._message: discord.Message | None = None
        if self._detail_blocks:
            self.add_item(LootDetailsButton())

    def bind_message(self, message: discord.Message) -> None:
        self._message = message

    async def show_details(self, interaction: discord.Interaction) -> None:
        if not self._detail_blocks:
            await interaction.response.send_message(
                "No loot details are available to display.", ephemeral=True
            )
            return
        chunks = self._chunk_blocks()
        first = chunks[0]
        await interaction.response.send_message(first, ephemeral=True)
        for extra in chunks[1:]:
            await interaction.followup.send(extra, ephemeral=True)

    def _chunk_blocks(self, *, limit: int = 1800) -> list[str]:
        chunks: list[str] = []
        current: list[str] = []
        length = 0
        for block in self._detail_blocks:
            block_length = len(block)
            separator = 2 if current else 0
            if current and length + separator + block_length > limit:
                chunks.append("\n\n".join(current))
                current = [block]
                length = block_length
            else:
                if current:
                    length += 2
                current.append(block)
                length += block_length
        if current:
            chunks.append("\n\n".join(current))
        return chunks or ["Loot details are unavailable."]

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self._message is not None:
            try:
                await self._message.edit(view=self)
            except discord.HTTPException:
                pass
        self.stop()


class ReturnButtonView(OwnedView):
    """Simple view offering a single return-to-menu button."""

    def __init__(
        self,
        owner_id: int,
        *,
        callback: SimpleCallback,
        timeout: float = 90.0,
    ) -> None:
        super().__init__(owner_id, timeout=timeout)
        self.add_item(
            CallbackButton(
                label=None,
                emoji="🏠",
                style=discord.ButtonStyle.secondary,
                callback=callback,
            )
        )


class CallbackButton(discord.ui.Button[OwnedView]):
    """Reusable button that forwards interactions to a coroutine callback."""

    def __init__(
        self,
        *,
        label: str | None,
        callback: SimpleCallback,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
        emoji: str | None = None,
    ) -> None:
        super().__init__(label=label, style=style, emoji=emoji)
        self._callback = callback

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await self._callback(interaction)


class CallbackSelect(discord.ui.Select[OwnedView]):
    """Reusable select component that forwards the chosen option to a callback."""

    def __init__(
        self,
        *,
        options: Sequence[discord.SelectOption],
        callback: OptionCallback,
        placeholder: str = "Make a selection",
        min_values: int = 1,
        max_values: int = 1,
    ) -> None:
        super().__init__(
            options=list(options),
            placeholder=placeholder,
            min_values=min_values,
            max_values=max_values,
        )
        self._callback = callback

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not self.values:
            await interaction.response.send_message(
                "Select an option first.", ephemeral=True
            )
            return
        await self._callback(interaction, self.values[0])


class ShopView(discord.ui.View):
    def __init__(
        self,
        entries: Mapping[str, ShopEntry],
        *,
        player: Optional[PlayerProgress],
        purchase_callback: ShopPurchaseCallback | None = None,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.entries = dict(entries)
        self.player = player
        self._purchase_callback = purchase_callback
        self.quantity = 1
        self.message: Optional[discord.Message] = None
        self.selected_key = next(iter(self.entries)) if self.entries else None
        self._return_callback: Callable[[discord.Interaction], Awaitable[None]] | None = None

        self._item_select = ShopItemSelect(self.entries)
        self._quantity_select = ShopQuantitySelect()
        self._return_button = ShopReturnButton()
        self._buy_button = ShopBuyButton()

        self.add_item(self._item_select)
        self.add_item(self._quantity_select)
        self.add_item(self._return_button)
        self.add_item(self._buy_button)
        self._refresh_controls()

    def _current_entry(self) -> ShopEntry | None:
        if not self.selected_key:
            return None
        return self.entries.get(self.selected_key)

    def _balance_for_currency(self, currency_key: str) -> int:
        if self.player is None:
            return 0
        return int(self.player.currencies.get(currency_key, 0))

    def _total_cost(self) -> int:
        entry = self._current_entry()
        if entry is None:
            return 0
        return max(0, entry.price * max(1, self.quantity))

    def _can_purchase(self) -> bool:
        entry = self._current_entry()
        if not entry:
            return False
        if self.player is None:
            return False
        balance = self._balance_for_currency(entry.currency_key)
        return balance >= self._total_cost()

    def _refresh_controls(self) -> None:
        for option in self._item_select.options:
            option.default = option.value == self.selected_key
        for option in self._quantity_select.options:
            option.default = option.value == str(self.quantity)
        self._buy_button.label = f"Buy ×{format_number(self.quantity)}"
        self._buy_button.disabled = (
            self._purchase_callback is None
            or self.player is None
            or not self._can_purchase()
        )
        self._return_button.disabled = self._return_callback is None

    def set_return_callback(
        self, callback: Callable[[discord.Interaction], Awaitable[None]] | None
    ) -> None:
        self._return_callback = callback
        self._refresh_controls()

    def build_embed(self) -> discord.Embed:
        entry = self._current_entry()
        embed = discord.Embed(
            title="Sect Marketplace",
            colour=discord.Colour.from_str("#2ecc71"),
        )
        if entry is None:
            embed.description = "No items are currently for sale."
            return embed
        embed.add_field(
            name="Item",
            value=f"**{entry.name}**\n`{entry.key}`",
            inline=False,
        )
        description = entry.description.strip()
        if description:
            embed.add_field(name="Details", value=description, inline=False)
        embed.add_field(
            name="Price",
            value=f"{format_number(entry.price)} {entry.currency_name}",
            inline=True,
        )
        total_cost = self._total_cost()
        embed.add_field(
            name="Total Cost",
            value=f"{format_number(total_cost)} {entry.currency_name}",
            inline=True,
        )
        if self.player is None:
            embed.add_field(
                name="Status",
                value=(
                    "Register with `/register` to make purchases."
                ),
                inline=False,
            )
        else:
            balance = self._balance_for_currency(entry.currency_key)
            balance_label = f"{format_number(balance)} {entry.currency_name}"
            embed.add_field(name="You Have", value=balance_label, inline=True)
            if not self._can_purchase():
                embed.add_field(
                    name="Tip",
                    value="You don't have enough currency for this purchase.",
                    inline=False,
                )
        embed.set_footer(text="Use the selectors below to browse and purchase items.")
        return embed

    async def update_shop_message(self, interaction: discord.Interaction) -> None:
        self._refresh_controls()
        embed = self.build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def handle_purchase(self, interaction: discord.Interaction) -> None:
        if self._purchase_callback is None:
            await interaction.response.send_message(
                "Purchasing is currently disabled.", ephemeral=True
            )
            return
        if self.player is None:
            await interaction.response.send_message(
                "Register with `/register` before attempting to buy items.",
                ephemeral=True,
            )
            return
        entry = self._current_entry()
        if entry is None:
            await interaction.response.send_message(
                "No item selected for purchase.", ephemeral=True
            )
            return
        await interaction.response.defer(thinking=False)
        result = await self._purchase_callback(
            interaction, self, entry.key, max(1, self.quantity)
        )
        self._refresh_controls()
        embed = self.build_embed()
        try:
            await interaction.message.edit(embed=embed, view=self)
        except discord.HTTPException:
            pass
        await interaction.followup.send(result.message, ephemeral=True)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


TradeSelectionKind = Literal["offer", "request"]
TradeEntryType = Literal["item", "currency"]
_MAX_TRADE_OPTIONS = 25
_EMPTY_SELECT_VALUE = "__disabled__"


class TradePartnerSelect(discord.ui.Select["TradeBuilderView"]):
    def __init__(self, partners: Sequence[TradePartnerOption]) -> None:
        options: list[discord.SelectOption] = []
        for partner in partners[:_MAX_TRADE_OPTIONS]:
            description = (partner.description or "").strip()
            if description:
                description = (
                    description[:95] + "…" if len(description) > 95 else description
                )
            options.append(
                discord.SelectOption(
                    label=partner.display_name[:100],
                    value=str(partner.user_id),
                    description=description or None,
                )
            )
        if not options:
            options = [
                discord.SelectOption(
                    label="No cultivators available",
                    value=_EMPTY_SELECT_VALUE,
                )
            ]
        super().__init__(
            placeholder="Choose a trading partner",
            options=options,
            min_values=1,
            max_values=1,
        )
        self.disabled = not partners

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if isinstance(view, TradeBuilderView):
            try:
                partner_id = int(self.values[0])
            except (TypeError, ValueError):
                await interaction.response.send_message(
                    "Unable to determine the selected cultivator.", ephemeral=True
                )
                return
            await view.set_partner(interaction, partner_id)


class TradeEntrySelect(discord.ui.Select["TradeBuilderView"]):
    def __init__(self, *, kind: TradeSelectionKind) -> None:
        placeholder = (
            "Select something to offer"
            if kind == "offer"
            else "Request an item or currency"
        )
        empty_label = (
            "No offer options available"
            if kind == "offer"
            else "No request options available"
        )
        super().__init__(
            placeholder=placeholder,
            options=[
                discord.SelectOption(label=empty_label, value=_EMPTY_SELECT_VALUE)
            ],
            min_values=1,
            max_values=1,
            disabled=True,
        )
        self.kind: TradeSelectionKind = kind

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if isinstance(view, TradeBuilderView):
            if not self.values or self.values[0] == _EMPTY_SELECT_VALUE:
                await interaction.response.send_message(
                    "Select an item or currency first.", ephemeral=True
                )
                return
            selected_value = self.values[0]
            mapping = (
                view._offer_entry_map
                if self.kind == "offer"
                else view._request_entry_map
            )
            entry = mapping.get(selected_value)
            if entry is None:
                view.refresh_selects()
                await view._edit(interaction)
                await interaction.followup.send(
                    "That option is no longer available to trade.", ephemeral=True
                )
                return
            entry_type, key = entry
            await view.handle_entry_selection(
                interaction, self.kind, entry_type, key
            )


class TradeQuantitySelect(discord.ui.Select["TradeBuilderView"]):
    def __init__(self) -> None:
        options = [
            discord.SelectOption(label="×1", value="1"),
            discord.SelectOption(label="×5", value="5"),
            discord.SelectOption(label="×10", value="10"),
            discord.SelectOption(label="×25", value="25"),
            discord.SelectOption(label="×50", value="50"),
            discord.SelectOption(
                label="All Available",
                value="all",
                description="Use the maximum quantity you can trade",
            ),
        ]
        super().__init__(
            placeholder="Choose a quantity to apply",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if isinstance(view, TradeBuilderView):
            if not self.values:
                await interaction.response.send_message(
                    "Select a quantity first.", ephemeral=True
                )
                return
            await view.apply_quantity(interaction, self.values[0])


class TradeClearSelectionButton(discord.ui.Button["TradeBuilderView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Clear Selection",
            style=discord.ButtonStyle.secondary,
            emoji="🧹",
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if isinstance(view, TradeBuilderView):
            await view.clear_selection(interaction)


class TradeSubmitButton(discord.ui.Button["TradeBuilderView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Send Trade Request",
            style=discord.ButtonStyle.success,
            emoji="📨",
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if isinstance(view, TradeBuilderView):
            await view.submit_trade(interaction)


class TradeBuilderView(OwnedView):
    def __init__(
        self,
        owner_id: int,
        *,
        player: PlayerProgress,
        items: Mapping[str, Item],
        currencies: Mapping[str, Currency],
        partner_options: Sequence[TradePartnerOption],
        partner_loader: Callable[[int], Awaitable[Optional[PlayerProgress]]],
        submit_callback: Callable[[discord.Interaction, "TradeBuilderView"], Awaitable[None]],
        return_callback: SimpleCallback | None = None,
        timeout: float = 180.0,
    ) -> None:
        super().__init__(owner_id, timeout=timeout)
        self.initiator: PlayerProgress = player
        self.partner: PlayerProgress | None = None
        self.partner_id: int | None = None
        self.items = items
        self.currencies = currencies
        self._partner_options = list(partner_options)
        self._partner_loader = partner_loader
        self._submit_callback = submit_callback
        self._return_callback = return_callback
        self.offer_items: dict[str, int] = {}
        self.offer_currencies: dict[str, int] = {}
        self.request_items: dict[str, int] = {}
        self.request_currencies: dict[str, int] = {}
        self.pending_selection: tuple[
            TradeSelectionKind, TradeEntryType, str
        ] | None = None
        self.status_message: str | None = None
        self.message: Optional[discord.Message] = None
        self._offer_inventory_truncated = False
        self._offer_currency_truncated = False
        self._request_inventory_truncated = False
        self._request_currency_truncated = False
        self._offer_entry_map: dict[str, tuple[TradeEntryType, str]] = {}
        self._request_entry_map: dict[str, tuple[TradeEntryType, str]] = {}

        self.partner_select = TradePartnerSelect(self._partner_options)
        self.partner_select.row = 0
        self.offer_entry_select = TradeEntrySelect(kind="offer")
        self.offer_entry_select.row = 1
        self.request_entry_select = TradeEntrySelect(kind="request")
        self.request_entry_select.row = 2
        self.quantity_select = TradeQuantitySelect()
        self.quantity_select.row = 3
        self.clear_button = TradeClearSelectionButton()
        self.clear_button.row = 4
        self.submit_button = TradeSubmitButton()
        self.submit_button.row = 4

        self.add_item(self.partner_select)
        self.add_item(self.offer_entry_select)
        self.add_item(self.request_entry_select)
        self.add_item(self.quantity_select)
        self.add_item(self.clear_button)
        self.add_item(self.submit_button)

        if self._return_callback is not None:
            return_button = CallbackButton(
                label="Main Menu",
                emoji="🏠",
                style=discord.ButtonStyle.secondary,
                callback=self._handle_return,
            )
            return_button.row = 4
            self.add_item(return_button)

        self.refresh_selects()

    async def _handle_return(self, interaction: discord.Interaction) -> None:
        if self._return_callback:
            await self._return_callback(interaction)

    def has_selection(self) -> bool:
        return any(
            container
            for container in (
                self.offer_items,
                self.offer_currencies,
                self.request_items,
                self.request_currencies,
            )
        )

    def refresh_selects(self) -> None:
        partner_lookup = str(self.partner_id) if self.partner_id is not None else None
        options: list[discord.SelectOption] = []
        for partner in self._partner_options[:_MAX_TRADE_OPTIONS]:
            description = (partner.description or "").strip()
            if description:
                description = (
                    description[:95] + "…" if len(description) > 95 else description
                )
            option = discord.SelectOption(
                label=partner.display_name[:100],
                value=str(partner.user_id),
                description=description or None,
            )
            option.default = option.value == partner_lookup
            options.append(option)
        self._set_select_options(
            self.partner_select,
            options,
            empty_label="No cultivators available",
            enabled=bool(options),
        )

        (
            offer_options,
            offer_mapping,
            offer_items_truncated,
            offer_currencies_truncated,
        ) = self._build_entry_options(self.initiator)
        self._offer_entry_map = offer_mapping
        self._set_select_options(
            self.offer_entry_select,
            offer_options,
            empty_label="No offer options available",
            enabled=bool(offer_options),
        )
        self._offer_inventory_truncated = offer_items_truncated
        self._offer_currency_truncated = offer_currencies_truncated

        if self.partner is None:
            self._request_entry_map = {}
            self._set_select_options(
                self.request_entry_select,
                [],
                empty_label="Select a partner to request items",
                enabled=False,
            )
            self._request_inventory_truncated = False
            self._request_currency_truncated = False
        else:
            (
                request_options,
                request_mapping,
                request_items_truncated,
                request_currencies_truncated,
            ) = self._build_entry_options(self.partner)
            self._request_entry_map = request_mapping
            self._set_select_options(
                self.request_entry_select,
                request_options,
                empty_label="No request options available",
                enabled=bool(request_options),
            )
            self._request_inventory_truncated = request_items_truncated
            self._request_currency_truncated = request_currencies_truncated

        self.submit_button.disabled = not self.has_selection() or self.partner is None

    def update_profiles(
        self,
        *,
        initiator: PlayerProgress,
        partner: Optional[PlayerProgress] = None,
    ) -> None:
        self.initiator = initiator
        self._clamp_offer_to_inventory()
        if partner is not None:
            self.partner = partner
            self.partner_id = partner.user_id
            self._clamp_request_to_partner()
        self.refresh_selects()

    def _set_select_options(
        self,
        select: discord.ui.Select[Any],
        options: list[discord.SelectOption],
        *,
        empty_label: str,
        enabled: bool,
    ) -> None:
        if options:
            select.options = options
        else:
            select.options = [
                discord.SelectOption(
                    label=empty_label[:100],
                    value=_EMPTY_SELECT_VALUE,
                )
            ]
        select.disabled = not enabled

    async def set_partner(self, interaction: discord.Interaction, partner_id: int) -> None:
        partner = await self._partner_loader(partner_id)
        if partner is None:
            self.status_message = "The selected cultivator could not be loaded."
            await self._edit(interaction)
            return
        if partner.user_id == self.initiator.user_id:
            self.status_message = "You cannot trade with yourself."
            self.partner = None
            self.partner_id = None
            self.request_items.clear()
            self.request_currencies.clear()
            await self._edit(interaction)
            return
        self.partner = partner
        self.partner_id = partner.user_id
        self.pending_selection = None
        self._clamp_request_to_partner()
        self.status_message = f"You are now preparing a trade with {partner.name}."
        self.refresh_selects()
        await self._edit(interaction)

    async def handle_entry_selection(
        self,
        interaction: discord.Interaction,
        kind: TradeSelectionKind,
        entry_type: TradeEntryType,
        key: str,
    ) -> None:
        if kind == "request" and self.partner is None:
            self.status_message = "Select a trading partner first."
            await self._edit(interaction)
            return
        self.pending_selection = (kind, entry_type, key)
        action = "offer" if kind == "offer" else "request"
        label = (
            self._item_name(key)
            if entry_type == "item"
            else self._currency_name(key)
        )
        quantity_word = "many" if entry_type == "item" else "much"
        self.status_message = (
            f"Selected {label}. Choose how {quantity_word} to {action}."
        )
        await self._edit(interaction)

    async def apply_quantity(
        self, interaction: discord.Interaction, raw_value: str
    ) -> None:
        if self.pending_selection is None:
            self.status_message = "Select an item or currency first."
            await self._edit(interaction)
            return
        kind, entry_type, key = self.pending_selection
        owner = self.initiator if kind == "offer" else self.partner
        if owner is None:
            self.status_message = "Select a trading partner first."
            await self._edit(interaction)
            return
        max_available = self._max_available(owner, entry_type, key)
        if max_available <= 0:
            target = self._selection_target(entry_type, kind)
            target.pop(key, None)
            self.pending_selection = None
            self.status_message = "That selection is no longer available."
            await self._edit(interaction)
            return
        if raw_value == "all":
            desired = max_available
        else:
            try:
                desired = int(raw_value)
            except (TypeError, ValueError):
                desired = 1
        desired = max(0, min(desired, max_available))
        target = self._selection_target(entry_type, kind)
        if desired <= 0:
            target.pop(key, None)
            note = "Removed from the trade."
        else:
            target[key] = desired
            label = (
                self._item_name(key)
                if entry_type == "item"
                else self._currency_name(key)
            )
            action = "offer" if kind == "offer" else "request"
            note = (
                f"Set to {format_number(desired)} to {action} {label}."
            )
        self.pending_selection = None
        self.status_message = note
        self.refresh_selects()
        await self._edit(interaction)

    async def clear_selection(self, interaction: discord.Interaction) -> None:
        cleared = False
        if self.pending_selection is not None:
            kind, entry_type, key = self.pending_selection
            target = self._selection_target(entry_type, kind)
            target.pop(key, None)
            self.pending_selection = None
            self.status_message = "Cleared the pending selection."
            cleared = True
        elif self.has_selection():
            self.offer_items.clear()
            self.offer_currencies.clear()
            self.request_items.clear()
            self.request_currencies.clear()
            self.status_message = "Cleared all trade selections."
            cleared = True
        if not cleared:
            self.status_message = "There is nothing to clear."
            await self._edit(interaction)
            return
        self.refresh_selects()
        await self._edit(interaction)

    async def submit_trade(self, interaction: discord.Interaction) -> None:
        if self.partner is None:
            self.status_message = "Select a trading partner first."
            await self._edit(interaction)
            return
        if not self.has_selection():
            self.status_message = (
                "Select at least one item or currency before sending a trade."
            )
            await self._edit(interaction)
            return
        await self._submit_callback(interaction, self)

    def as_proposal(self) -> TradeProposal:
        partner = self.partner
        if partner is None:
            raise RuntimeError("Cannot build a trade proposal without a partner")
        return TradeProposal(
            initiator_id=self.initiator.user_id,
            initiator_name=self.initiator.name,
            partner_id=partner.user_id,
            partner_name=partner.name,
            offer=TradeOfferDetails(
                items=dict(self.offer_items),
                currencies=dict(self.offer_currencies),
            ),
            request=TradeOfferDetails(
                items=dict(self.request_items),
                currencies=dict(self.request_currencies),
            ),
        )

    def build_embed(self) -> discord.Embed:
        description_lines = [
            "Use the controls below to configure your trade."
        ]
        if self.status_message:
            description_lines.append(f"**Status:** {self.status_message}")
        embed = discord.Embed(
            title="Trade Negotiation",
            description="\n\n".join(description_lines),
            colour=discord.Colour.gold(),
        )
        if self.partner is None:
            partner_text = "Select a trading partner using the dropdown above."
        else:
            partner_text = f"Trading with **{self.partner.name}**."
        embed.add_field(name="Trading Partner", value=partner_text, inline=False)
        offer_text = self._format_offer_section(
            items=self.offer_items,
            currencies=self.offer_currencies,
            empty_message="You have not offered anything yet.",
        )
        embed.add_field(name="You Will Offer", value=offer_text, inline=False)
        if self.partner is None:
            request_text = (
                "Select a trading partner before requesting items or currency."
            )
        else:
            request_text = self._format_offer_section(
                items=self.request_items,
                currencies=self.request_currencies,
                empty_message="You are not requesting anything.",
            )
        embed.add_field(name="You Request", value=request_text, inline=False)

        notes: list[str] = []
        if (
            self._offer_inventory_truncated
            or self._offer_currency_truncated
            or self._request_inventory_truncated
            or self._request_currency_truncated
        ):
            notes.append("Only the first 25 entries appear in each selector.")
        if self.pending_selection:
            kind, entry_type, key = self.pending_selection
            label = (
                self._item_name(key)
                if entry_type == "item"
                else self._currency_name(key)
            )
            action = "offer" if kind == "offer" else "request"
            notes.append(f"Pending selection: {label} to {action}. Choose a quantity.")
        if notes:
            embed.set_footer(text=" ".join(notes))
        else:
            embed.set_footer(
                text="When ready, send the trade request for your partner to review."
            )
        return embed

    async def _edit(self, interaction: discord.Interaction) -> None:
        embed = self.build_embed()
        try:
            if interaction.response.is_done():
                if self.message is not None:
                    await interaction.followup.edit_message(
                        self.message.id, embed=embed, view=self
                    )
            else:
                await interaction.response.edit_message(embed=embed, view=self)
        except discord.HTTPException:
            pass
        if self.message is None:
            message = interaction.message
            if message is not None:
                self.message = message

    def _build_inventory_options(
        self, player: PlayerProgress
    ) -> tuple[list[discord.SelectOption], bool]:
        entries: list[tuple[str, discord.SelectOption]] = []
        for key, amount in player.inventory.items():
            try:
                count = max(0, int(amount))
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            item = self.items.get(key)
            name = _trade_item_name(self.items, key)
            label = f"{name} (×{format_number(count)})"
            description = ""
            if item and item.description:
                description = item.description.strip()
            if description:
                description = (
                    description[:95] + "…" if len(description) > 95 else description
                )
            option = discord.SelectOption(
                label=label[:100],
                value=key,
                description=description or None,
            )
            entries.append((name.lower(), option))
        entries.sort(key=lambda entry: entry[0])
        options = [option for _, option in entries[:_MAX_TRADE_OPTIONS]]
        return options, len(entries) > _MAX_TRADE_OPTIONS

    def _build_currency_options(
        self, player: PlayerProgress
    ) -> tuple[list[discord.SelectOption], bool]:
        entries: list[tuple[str, discord.SelectOption]] = []
        for key, amount in player.currencies.items():
            try:
                balance = max(0, int(amount))
            except (TypeError, ValueError):
                continue
            if balance <= 0:
                continue
            name = _trade_currency_name(self.currencies, key)
            label = f"{name} — {format_number(balance)}"
            option = discord.SelectOption(
                label=label[:100],
                value=key,
            )
            entries.append((name.lower(), option))
        entries.sort(key=lambda entry: entry[0])
        options = [option for _, option in entries[:_MAX_TRADE_OPTIONS]]
        return options, len(entries) > _MAX_TRADE_OPTIONS

    def _build_entry_options(
        self, player: PlayerProgress
    ) -> tuple[
        list[discord.SelectOption],
        dict[str, tuple[TradeEntryType, str]],
        bool,
        bool,
    ]:
        inventory_options, inventory_truncated = self._build_inventory_options(player)
        currency_options, currency_truncated = self._build_currency_options(player)
        options: list[discord.SelectOption] = []
        mapping: dict[str, tuple[TradeEntryType, str]] = {}
        inventory_overflow = inventory_truncated
        currency_overflow = currency_truncated
        counter = 0

        for option in inventory_options:
            if len(options) >= _MAX_TRADE_OPTIONS:
                inventory_overflow = True
                break
            value = f"item:{counter}"
            counter += 1
            new_option = discord.SelectOption(
                label=option.label,
                value=value,
                description=option.description,
                emoji=option.emoji,
            )
            new_option.default = option.default
            options.append(new_option)
            mapping[value] = ("item", option.value)

        for option in currency_options:
            if len(options) >= _MAX_TRADE_OPTIONS:
                currency_overflow = True
                break
            value = f"currency:{counter}"
            counter += 1
            new_option = discord.SelectOption(
                label=option.label,
                value=value,
                description=option.description,
                emoji=option.emoji,
            )
            new_option.default = option.default
            options.append(new_option)
            mapping[value] = ("currency", option.value)

        return options, mapping, inventory_overflow, currency_overflow

    def _format_offer_section(
        self,
        *,
        items: Mapping[str, int],
        currencies: Mapping[str, int],
        empty_message: str,
    ) -> str:
        return _format_trade_section(
            items=items,
            currencies=currencies,
            item_lookup=self.items,
            currency_lookup=self.currencies,
            empty_message=empty_message,
        )

    def _item_name(self, item_key: str) -> str:
        return _trade_item_name(self.items, item_key)

    def _currency_name(self, currency_key: str) -> str:
        return _trade_currency_name(self.currencies, currency_key)

    @staticmethod
    def _fallback_name(key: str) -> str:
        return _fallback_trade_name(key)

    def _max_available(
        self,
        player: PlayerProgress,
        entry_type: TradeEntryType,
        key: str,
    ) -> int:
        source = player.inventory if entry_type == "item" else player.currencies
        try:
            amount = int(source.get(key, 0))
        except (TypeError, ValueError):
            return 0
        return max(0, amount)

    def _selection_target(
        self, entry_type: TradeEntryType, kind: TradeSelectionKind
    ) -> dict[str, int]:
        if entry_type == "item":
            return self.offer_items if kind == "offer" else self.request_items
        return self.offer_currencies if kind == "offer" else self.request_currencies

    def _clamp_offer_to_inventory(self) -> None:
        available_items = {
            key: self._max_available(self.initiator, "item", key)
            for key in self.offer_items.keys()
        }
        for key, amount in list(self.offer_items.items()):
            maximum = available_items.get(key, 0)
            if maximum <= 0:
                self.offer_items.pop(key, None)
            else:
                self.offer_items[key] = min(amount, maximum)
        for key, amount in list(self.offer_currencies.items()):
            maximum = self._max_available(self.initiator, "currency", key)
            if maximum <= 0:
                self.offer_currencies.pop(key, None)
            else:
                self.offer_currencies[key] = min(amount, maximum)

    def _clamp_request_to_partner(self) -> None:
        if self.partner is None:
            self.request_items.clear()
            self.request_currencies.clear()
            return
        for key, amount in list(self.request_items.items()):
            maximum = self._max_available(self.partner, "item", key)
            if maximum <= 0:
                self.request_items.pop(key, None)
            else:
                self.request_items[key] = min(amount, maximum)
        for key, amount in list(self.request_currencies.items()):
            maximum = self._max_available(self.partner, "currency", key)
            if maximum <= 0:
                self.request_currencies.pop(key, None)
            else:
                self.request_currencies[key] = min(amount, maximum)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
        self.stop()


class TradeConfirmationView(OwnedView):
    def __init__(
        self,
        owner_id: int,
        proposal: TradeProposal,
        *,
        on_accept: Callable[[discord.Interaction, "TradeConfirmationView"], Awaitable[None]],
        on_decline: Callable[[discord.Interaction, "TradeConfirmationView"], Awaitable[None]],
        timeout: float = 180.0,
    ) -> None:
        super().__init__(owner_id, timeout=timeout)
        self.proposal = proposal
        self._on_accept = on_accept
        self._on_decline = on_decline
        self.message: Optional[discord.Message] = None

    def disable(self) -> None:
        for child in self.children:
            child.disabled = True

    async def on_timeout(self) -> None:
        self.disable()
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
        self.stop()

    @discord.ui.button(label="Accept Trade", style=discord.ButtonStyle.success, emoji="🤝")
    async def accept_trade(  # type: ignore[override]
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._on_accept(interaction, self)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="✋")
    async def decline_trade(  # type: ignore[override]
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._on_decline(interaction, self)


class TradeNegotiationEntrySelect(discord.ui.Select["TradeNegotiationView"]):
    def __init__(self, *, owner_id: int, owner_name: str) -> None:
        super().__init__(
            placeholder=f"{owner_name}: Offer an item or currency",
            options=[
                discord.SelectOption(
                    label="No trade options available",
                    value=_EMPTY_SELECT_VALUE,
                )
            ],
            min_values=1,
            max_values=1,
            disabled=True,
        )
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if not isinstance(view, TradeNegotiationView):
            await interaction.response.send_message(
                "This trade is no longer available.", ephemeral=True
            )
            return
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only this cultivator may adjust that offer.", ephemeral=True
            )
            return
        if not self.values or self.values[0] == _EMPTY_SELECT_VALUE:
            await interaction.response.send_message(
                "Select an item or currency first.", ephemeral=True
            )
            return
        entry = view.entry_mapping(self.owner_id).get(self.values[0])
        if entry is None:
            await view.refresh_for(self.owner_id)
            await view.edit(interaction)
            await interaction.followup.send(
                "That option is no longer available to trade.", ephemeral=True
            )
            return
        entry_type, key = entry
        await view.handle_entry_selection(interaction, self.owner_id, entry_type, key)


class TradeNegotiationQuantitySelect(discord.ui.Select["TradeNegotiationView"]):
    def __init__(self, *, owner_id: int) -> None:
        options = [
            discord.SelectOption(label="×1", value="1"),
            discord.SelectOption(label="×5", value="5"),
            discord.SelectOption(label="×10", value="10"),
            discord.SelectOption(label="×25", value="25"),
            discord.SelectOption(label="×50", value="50"),
            discord.SelectOption(
                label="All Available",
                value="all",
                description="Offer the maximum quantity you can trade",
            ),
            discord.SelectOption(
                label="Remove from Offer",
                value="remove",
                description="Remove the selected entry from your offer",
            ),
        ]
        super().__init__(
            placeholder="Choose a quantity to offer",
            options=options,
            min_values=1,
            max_values=1,
        )
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if not isinstance(view, TradeNegotiationView):
            await interaction.response.send_message(
                "This trade is no longer available.", ephemeral=True
            )
            return
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only this cultivator may adjust that offer.", ephemeral=True
            )
            return
        if not self.values:
            await interaction.response.send_message(
                "Select a quantity first.", ephemeral=True
            )
            return
        await view.apply_quantity(interaction, self.owner_id, self.values[0])


class TradeNegotiationClearButton(discord.ui.Button["TradeNegotiationView"]):
    def __init__(self, *, owner_id: int) -> None:
        super().__init__(
            label="Clear Offer",
            style=discord.ButtonStyle.secondary,
            emoji="🧹",
        )
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if not isinstance(view, TradeNegotiationView):
            await interaction.response.send_message(
                "This trade is no longer available.", ephemeral=True
            )
            return
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only this cultivator may adjust that offer.", ephemeral=True
            )
            return
        await view.clear_offer(interaction, self.owner_id)


class TradeNegotiationAcceptButton(discord.ui.Button["TradeNegotiationView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Ready to Trade",
            style=discord.ButtonStyle.success,
            emoji="🤝",
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if isinstance(view, TradeNegotiationView):
            await view.toggle_acceptance(interaction)


class TradeNegotiationCancelButton(discord.ui.Button["TradeNegotiationView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Cancel Trade",
            style=discord.ButtonStyle.danger,
            emoji="✋",
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if isinstance(view, TradeNegotiationView):
            await view.cancel_trade(interaction)


class TradeNegotiationView(SharedOwnedView):
    def __init__(
        self,
        *,
        thread_id: int,
        initiator: PlayerProgress,
        partner: PlayerProgress,
        items: Mapping[str, Item],
        currencies: Mapping[str, Currency],
        player_loaders: Mapping[int, Callable[[], Awaitable[Optional[PlayerProgress]]]],
        finalize_callback: Callable[
            [discord.Interaction, "TradeNegotiationView"], Awaitable[None]
        ],
        cancel_callback: Callable[
            [Optional[discord.Interaction], "TradeNegotiationView"], Awaitable[None]
        ],
        timeout: float = 900.0,
    ) -> None:
        super().__init__({initiator.user_id, partner.user_id}, timeout=timeout)
        self.thread_id = thread_id
        self.initiator_id = initiator.user_id
        self.partner_id = partner.user_id
        self.initiator_name = initiator.name
        self.partner_name = partner.name
        self.items = items
        self.currencies = currencies
        self.players: dict[int, PlayerProgress] = {
            initiator.user_id: initiator,
            partner.user_id: partner,
        }
        self._player_loaders = dict(player_loaders)
        self.offers: dict[int, TradeOfferDetails] = {
            initiator.user_id: TradeOfferDetails(),
            partner.user_id: TradeOfferDetails(),
        }
        self.pending_selection: dict[int, tuple[TradeEntryType, str]] = {}
        self.accepted: set[int] = set()
        self.status_message: str = "Discuss terms in this thread and adjust your offers."
        self._entry_maps: dict[int, dict[str, tuple[TradeEntryType, str]]] = {
            initiator.user_id: {},
            partner.user_id: {},
        }
        self._inventory_truncated: dict[int, bool] = {
            initiator.user_id: False,
            partner.user_id: False,
        }
        self._currency_truncated: dict[int, bool] = {
            initiator.user_id: False,
            partner.user_id: False,
        }
        self._finalize_callback = finalize_callback
        self._cancel_callback = cancel_callback
        self.message: Optional[discord.Message] = None
        self._finalizing = False

        self._entry_selects: dict[int, TradeNegotiationEntrySelect] = {
            initiator.user_id: TradeNegotiationEntrySelect(
                owner_id=initiator.user_id, owner_name=initiator.name
            ),
            partner.user_id: TradeNegotiationEntrySelect(
                owner_id=partner.user_id, owner_name=partner.name
            ),
        }
        self._quantity_selects: dict[int, TradeNegotiationQuantitySelect] = {
            initiator.user_id: TradeNegotiationQuantitySelect(owner_id=initiator.user_id),
            partner.user_id: TradeNegotiationQuantitySelect(owner_id=partner.user_id),
        }
        self._clear_buttons: dict[int, TradeNegotiationClearButton] = {
            initiator.user_id: TradeNegotiationClearButton(owner_id=initiator.user_id),
            partner.user_id: TradeNegotiationClearButton(owner_id=partner.user_id),
        }

        self._entry_selects[initiator.user_id].row = 0
        self._entry_selects[partner.user_id].row = 2
        self._quantity_selects[initiator.user_id].row = 1
        self._quantity_selects[partner.user_id].row = 3
        self._clear_buttons[initiator.user_id].row = 1
        self._clear_buttons[partner.user_id].row = 3

        self.add_item(self._entry_selects[initiator.user_id])
        self.add_item(self._quantity_selects[initiator.user_id])
        self.add_item(self._clear_buttons[initiator.user_id])
        self.add_item(self._entry_selects[partner.user_id])
        self.add_item(self._quantity_selects[partner.user_id])
        self.add_item(self._clear_buttons[partner.user_id])

        accept_button = TradeNegotiationAcceptButton()
        cancel_button = TradeNegotiationCancelButton()
        accept_button.row = 4
        cancel_button.row = 4
        self.add_item(accept_button)
        self.add_item(cancel_button)

        self.refresh_selects()

    def entry_mapping(
        self, owner_id: int
    ) -> dict[str, tuple[TradeEntryType, str]]:
        return self._entry_maps.get(owner_id, {})

    async def _load_player(self, owner_id: int) -> PlayerProgress | None:
        loader = self._player_loaders.get(owner_id)
        if loader is None:
            return self.players.get(owner_id)
        try:
            player = await loader()
        except Exception:
            player = None
        if player is not None:
            self.players[owner_id] = player
        return player

    def _offer_details(self, owner_id: int) -> TradeOfferDetails:
        return self.offers.setdefault(owner_id, TradeOfferDetails())

    def _player_name(self, owner_id: int) -> str:
        player = self.players.get(owner_id)
        if player is not None and player.name:
            return player.name
        if owner_id == self.initiator_id:
            return self.initiator_name
        if owner_id == self.partner_id:
            return self.partner_name
        return f"Cultivator {owner_id}"

    def _other_id(self, owner_id: int) -> int:
        return self.partner_id if owner_id == self.initiator_id else self.initiator_id

    def _set_select_options(
        self,
        select: discord.ui.Select[Any],
        options: list[discord.SelectOption],
        *,
        empty_label: str,
        enabled: bool,
    ) -> None:
        if options:
            select.options = options
        else:
            select.options = [
                discord.SelectOption(
                    label=empty_label[:100],
                    value=_EMPTY_SELECT_VALUE,
                )
            ]
        select.disabled = not enabled

    def refresh_selects(self) -> None:
        for owner_id, select in self._entry_selects.items():
            player = self.players.get(owner_id)
            if player is None:
                self._entry_maps[owner_id] = {}
                self._set_select_options(
                    select,
                    [],
                    empty_label="No trade options available",
                    enabled=False,
                )
                self._inventory_truncated[owner_id] = False
                self._currency_truncated[owner_id] = False
                continue
            options, mapping, inv_trunc, cur_trunc = self._build_entry_options(player)
            self._entry_maps[owner_id] = mapping
            self._inventory_truncated[owner_id] = inv_trunc
            self._currency_truncated[owner_id] = cur_trunc
            self._set_select_options(
                select,
                options,
                empty_label="No trade options available",
                enabled=bool(options),
            )

    async def refresh_for(self, owner_id: int) -> None:
        player = await self._load_player(owner_id)
        if player is not None:
            self._clamp_offer(owner_id)
        self.refresh_selects()

    def _build_entry_options(
        self, player: PlayerProgress
    ) -> tuple[
        list[discord.SelectOption],
        dict[str, tuple[TradeEntryType, str]],
        bool,
        bool,
    ]:
        options: list[discord.SelectOption] = []
        mapping: dict[str, tuple[TradeEntryType, str]] = {}
        inventory_overflow = False
        currency_overflow = False
        inventory_entries: list[tuple[str, discord.SelectOption]] = []
        for key, amount in player.inventory.items():
            try:
                count = max(0, int(amount))
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            label = f"{_trade_item_name(self.items, key)} (×{format_number(count)})"
            description = self.items.get(key).description if self.items.get(key) else ""
            if description:
                description = description.strip()
                description = description[:95] + "…" if len(description) > 95 else description
            option = discord.SelectOption(
                label=label[:100],
                value=key,
                description=description or None,
            )
            inventory_entries.append((_trade_item_name(self.items, key).lower(), option))
        inventory_entries.sort(key=lambda entry: entry[0])
        if len(inventory_entries) > _MAX_TRADE_OPTIONS:
            inventory_overflow = True
        for name, option in inventory_entries[:_MAX_TRADE_OPTIONS]:
            options.append(option)
            mapping[option.value] = ("item", option.value)

        currency_entries: list[tuple[str, discord.SelectOption]] = []
        for key, amount in player.currencies.items():
            try:
                balance = max(0, int(amount))
            except (TypeError, ValueError):
                continue
            if balance <= 0:
                continue
            label = f"{_trade_currency_name(self.currencies, key)} — {format_number(balance)}"
            option = discord.SelectOption(label=label[:100], value=key)
            currency_entries.append((_trade_currency_name(self.currencies, key).lower(), option))
        currency_entries.sort(key=lambda entry: entry[0])
        if len(currency_entries) > _MAX_TRADE_OPTIONS:
            currency_overflow = True
        for name, option in currency_entries[:_MAX_TRADE_OPTIONS]:
            options.append(option)
            mapping[option.value] = ("currency", option.value)

        return options, mapping, inventory_overflow, currency_overflow

    def _max_available(
        self, owner_id: int, entry_type: TradeEntryType, key: str
    ) -> int:
        player = self.players.get(owner_id)
        if player is None:
            return 0
        source = player.inventory if entry_type == "item" else player.currencies
        try:
            amount = int(source.get(key, 0))
        except (TypeError, ValueError):
            return 0
        return max(0, amount)

    def _clamp_offer(self, owner_id: int) -> None:
        details = self._offer_details(owner_id)
        player = self.players.get(owner_id)
        if player is None:
            details.items.clear()
            details.currencies.clear()
            return
        for key, amount in list(details.items.items()):
            maximum = self._max_available(owner_id, "item", key)
            if maximum <= 0:
                details.items.pop(key, None)
            else:
                details.items[key] = min(amount, maximum)
        for key, amount in list(details.currencies.items()):
            maximum = self._max_available(owner_id, "currency", key)
            if maximum <= 0:
                details.currencies.pop(key, None)
            else:
                details.currencies[key] = min(amount, maximum)

    async def handle_entry_selection(
        self,
        interaction: discord.Interaction,
        owner_id: int,
        entry_type: TradeEntryType,
        key: str,
    ) -> None:
        await self._load_player(owner_id)
        maximum = self._max_available(owner_id, entry_type, key)
        if maximum <= 0:
            details = self._offer_details(owner_id)
            if entry_type == "item":
                details.items.pop(key, None)
            else:
                details.currencies.pop(key, None)
            self.pending_selection.pop(owner_id, None)
            self.status_message = "That option is no longer available to trade."
            self.refresh_selects()
            await self.edit(interaction)
            return
        self.pending_selection[owner_id] = (entry_type, key)
        label = (
            _trade_item_name(self.items, key)
            if entry_type == "item"
            else _trade_currency_name(self.currencies, key)
        )
        self.status_message = (
            f"{self._player_name(owner_id)} selected {label}. Choose how much to offer."
        )
        await self.edit(interaction)

    async def apply_quantity(
        self, interaction: discord.Interaction, owner_id: int, raw_value: str
    ) -> None:
        pending = self.pending_selection.get(owner_id)
        if pending is None:
            await interaction.response.send_message(
                "Select an item or currency first.", ephemeral=True
            )
            return
        entry_type, key = pending
        await self._load_player(owner_id)
        maximum = self._max_available(owner_id, entry_type, key)
        details = self._offer_details(owner_id)
        target = details.items if entry_type == "item" else details.currencies
        if raw_value == "remove" or maximum <= 0:
            target.pop(key, None)
            self.status_message = (
                f"Removed {_trade_item_name(self.items, key) if entry_type == 'item' else _trade_currency_name(self.currencies, key)} from the offer."
            )
        else:
            if raw_value == "all":
                desired = maximum
            else:
                try:
                    desired = int(raw_value)
                except (TypeError, ValueError):
                    desired = 1
                desired = max(0, min(desired, maximum))
            if desired <= 0:
                target.pop(key, None)
                self.status_message = (
                    f"Removed {_trade_item_name(self.items, key) if entry_type == 'item' else _trade_currency_name(self.currencies, key)} from the offer."
                )
            else:
                target[key] = desired
                label = (
                    _trade_item_name(self.items, key)
                    if entry_type == "item"
                    else _trade_currency_name(self.currencies, key)
                )
                self.status_message = (
                    f"{self._player_name(owner_id)} will offer {format_number(desired)} {label}."
                )
        self.pending_selection.pop(owner_id, None)
        self.accepted.clear()
        self.refresh_selects()
        await self.edit(interaction)

    async def clear_offer(
        self, interaction: discord.Interaction, owner_id: int
    ) -> None:
        details = self._offer_details(owner_id)
        if not details.items and not details.currencies and owner_id not in self.pending_selection:
            await interaction.response.send_message(
                "There is nothing to clear from your offer.", ephemeral=True
            )
            return
        details.items.clear()
        details.currencies.clear()
        self.pending_selection.pop(owner_id, None)
        self.accepted.clear()
        self.status_message = f"Cleared {self._player_name(owner_id)}'s offer."
        self.refresh_selects()
        await self.edit(interaction)

    async def toggle_acceptance(self, interaction: discord.Interaction) -> None:
        owner_id = interaction.user.id
        if owner_id not in (self.initiator_id, self.partner_id):
            await interaction.response.send_message(
                "Only invited cultivators may confirm the trade.", ephemeral=True
            )
            return
        if owner_id in self.accepted:
            self.accepted.remove(owner_id)
            self.status_message = (
                f"{self._player_name(owner_id)} is no longer ready to finalize."
            )
            await self.edit(interaction)
            return
        self.accepted.add(owner_id)
        self.status_message = (
            f"{self._player_name(owner_id)} is ready to finalize the trade."
        )
        await self.edit(interaction)
        if len(self.accepted) == 2 and not self._finalizing:
            self._finalizing = True
            try:
                await self._finalize_callback(interaction, self)
            finally:
                self._finalizing = False

    async def cancel_trade(self, interaction: discord.Interaction) -> None:
        await self._cancel_callback(interaction, self)

    def build_embed(self) -> discord.Embed:
        description_lines = [
            "Use the controls below to add or remove items from your offer."
        ]
        if self.status_message:
            description_lines.append(f"**Status:** {self.status_message}")
        embed = discord.Embed(
            title="Trade Negotiation",
            description="\n\n".join(description_lines),
            colour=discord.Colour.gold(),
        )
        initiator_offer = _format_trade_section(
            items=self._offer_details(self.initiator_id).items,
            currencies=self._offer_details(self.initiator_id).currencies,
            item_lookup=self.items,
            currency_lookup=self.currencies,
            empty_message="Offering nothing.",
        )
        partner_offer = _format_trade_section(
            items=self._offer_details(self.partner_id).items,
            currencies=self._offer_details(self.partner_id).currencies,
            item_lookup=self.items,
            currency_lookup=self.currencies,
            empty_message="Offering nothing.",
        )
        embed.add_field(
            name=f"{self._player_name(self.initiator_id)} Offers",
            value=initiator_offer,
            inline=False,
        )
        embed.add_field(
            name=f"{self._player_name(self.partner_id)} Offers",
            value=partner_offer,
            inline=False,
        )
        status_lines = []
        for owner_id in (self.initiator_id, self.partner_id):
            ready = "✅ Ready" if owner_id in self.accepted else "⌛ Negotiating"
            status_lines.append(f"• {self._player_name(owner_id)} — {ready}")
        embed.add_field(
            name="Acceptance Status",
            value="\n".join(status_lines),
            inline=False,
        )
        notes: list[str] = []
        for owner_id in (self.initiator_id, self.partner_id):
            if self._inventory_truncated.get(owner_id) or self._currency_truncated.get(owner_id):
                notes.append(
                    f"{self._player_name(owner_id)} has more options than can be shown."
                )
        if self.pending_selection:
            owner_id, (entry_type, key) = next(iter(self.pending_selection.items()))
            label = (
                _trade_item_name(self.items, key)
                if entry_type == "item"
                else _trade_currency_name(self.currencies, key)
            )
            notes.append(
                f"{self._player_name(owner_id)}: choose a quantity for {label}."
            )
        if notes:
            embed.set_footer(text=" ".join(notes))
        else:
            embed.set_footer(
                text="Both cultivators must press Ready to finalize the trade."
            )
        return embed

    async def edit(self, interaction: discord.Interaction) -> None:
        embed = self.build_embed()
        try:
            if interaction.response.is_done():
                if self.message is not None:
                    await interaction.followup.edit_message(
                        self.message.id, embed=embed, view=self
                    )
            else:
                await interaction.response.edit_message(embed=embed, view=self)
        except discord.HTTPException:
            pass
        if self.message is None:
            message = interaction.message
            if message is not None:
                self.message = message

    def as_proposal(self) -> TradeProposal:
        return TradeProposal(
            initiator_id=self.initiator_id,
            initiator_name=self._player_name(self.initiator_id),
            partner_id=self.partner_id,
            partner_name=self._player_name(self.partner_id),
            offer=TradeOfferDetails(
                items=dict(self._offer_details(self.initiator_id).items),
                currencies=dict(self._offer_details(self.initiator_id).currencies),
            ),
            request=TradeOfferDetails(
                items=dict(self._offer_details(self.partner_id).items),
                currencies=dict(self._offer_details(self.partner_id).currencies),
            ),
        )

    def disable(self) -> None:
        for child in self.children:
            child.disabled = True

    def update_players(
        self,
        *,
        initiator: PlayerProgress | None = None,
        partner: PlayerProgress | None = None,
    ) -> None:
        if initiator is not None:
            self.players[self.initiator_id] = initiator
            self.initiator_name = initiator.name
        if partner is not None:
            self.players[self.partner_id] = partner
            self.partner_name = partner.name
        self._clamp_offer(self.initiator_id)
        self._clamp_offer(self.partner_id)
        self.refresh_selects()

    async def on_timeout(self) -> None:
        self.disable()
        if self.message is not None:
            embed = self.build_embed()
            embed.title = "Trade Negotiation Expired"
            embed.colour = discord.Colour.from_str("#f97316")
            embed.description = (
                "The trade negotiation expired due to inactivity."
            )
            try:
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass
        await self._cancel_callback(None, self)
        self.stop()
class CultivationActionView(OwnedView):
    """Presents quick cultivation-related actions as buttons."""

    def __init__(
        self,
        owner_id: int,
        *,
        on_cultivate_qi: SimpleCallback | None = None,
        on_cooperate: SimpleCallback | None = None,
        on_train_body: SimpleCallback | None = None,
        on_temper_soul: SimpleCallback | None = None,
        on_breakthrough: SimpleCallback | None = None,
        return_callback: SimpleCallback | None = None,
        timeout: float = 180.0,
    ) -> None:
        super().__init__(owner_id, timeout=timeout)
        if return_callback is not None:
            self.add_item(
                CallbackButton(
                    label=None,
                    emoji="🏠",
                    style=discord.ButtonStyle.secondary,
                    callback=return_callback,
                )
            )
        if on_cultivate_qi is not None:
            self.add_item(
                CallbackButton(
                    label="Cultivate Qi",
                    emoji="<:heaven_cultivation:1433142350590378086>",
                    style=discord.ButtonStyle.primary,
                    callback=on_cultivate_qi,
                )
            )
        if on_cooperate is not None:
            self.add_item(
                CallbackButton(
                    label="Cooperative Cultivation",
                    emoji="🤝",
                    style=discord.ButtonStyle.primary,
                    callback=on_cooperate,
                )
            )
        if on_train_body is not None:
            self.add_item(
                CallbackButton(
                    label="Train Your Body",
                    emoji="💪",
                    style=discord.ButtonStyle.success,
                    callback=on_train_body,
                )
            )
        if on_temper_soul is not None:
            self.add_item(
                CallbackButton(
                    label="Temper Your Soul",
                    emoji="🕯️",
                    style=discord.ButtonStyle.blurple,
                    callback=on_temper_soul,
                )
            )
        if on_breakthrough is not None:
            self.add_item(
                CallbackButton(
                    label="Attempt Breakthrough",
                    emoji="⚡",
                    style=discord.ButtonStyle.danger,
                    callback=on_breakthrough,
                )
            )


class LocationSelect(discord.ui.Select):
    def __init__(self, locations: list[tuple[str, str]]):
        options = [
            discord.SelectOption(label=name, value=key, emoji=WANDER_TRAVEL_EMOJI)
            for key, name in locations
        ]
        super().__init__(
            placeholder=f"{WANDER_TRAVEL_EMOJI_TEXT} Choose a destination channel",
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await interaction.response.defer()
        view = self.view
        if view is None or not hasattr(view, "on_location_selected"):
            await interaction.followup.send(
                "This travel menu is no longer available.", ephemeral=True
            )
            return
        handler = getattr(view, "on_location_selected")
        await handler(interaction, self.values[0])


class LocationSelectorView(discord.ui.View):
    def __init__(self, locations: list[tuple[str, str]], timeout: float = 60.0):
        super().__init__(timeout=timeout)
        self.select = LocationSelect(locations)
        self.add_item(self.select)
        self._callback: LocationCallback | None = None

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True

    async def on_location_selected(self, interaction: discord.Interaction, key: str) -> None:
        if self._callback:
            await self._callback(interaction, key)

    def set_callback(self, callback: LocationCallback) -> None:
        self._callback = callback


class TravelNavigatorView(OwnedView):
    def __init__(
        self,
        owner_id: int,
        *,
        state: "GameState",
        engine: "TravelEngine",
        player_id: int,
        status_builder: Callable[[PlayerProgress, TravelPath | None, Sequence[TravelEvent], str | None], discord.Embed],
        save_callback: Callable[[PlayerProgress], Awaitable[None]],
        return_callback: SimpleCallback | None = None,
        timeout: float = 180.0,
    ) -> None:
        super().__init__(owner_id, timeout=timeout)
        self.state = state
        self.engine = engine
        self.player_id = player_id
        self.status_builder = status_builder
        self.save_callback = save_callback
        self.message: Optional[discord.Message] = None
        self._return_callback = return_callback
        if return_callback is not None:
            return_button = CallbackButton(
                label=None,
                emoji="🏠",
                style=discord.ButtonStyle.secondary,
                callback=return_callback,
            )
            return_button.row = 4
            self.add_item(return_button)
        self.selected_target_id: int | None = None
        self.nearby_select = CallbackSelect(
            options=[],
            callback=self._on_select_nearby,
            placeholder="Select nearby cultivator",
            min_values=0,
            max_values=1,
        )
        self.nearby_select.row = 1
        self.attack_button = CallbackButton(
            label="Attack/Evade",
            emoji="⚔️",
            style=discord.ButtonStyle.danger,
            callback=self._attack_target,
        )
        self.attack_button.row = 4
        self.trade_button = CallbackButton(
            label="Trade",
            emoji="💱",
            style=discord.ButtonStyle.primary,
            callback=self._trade_target,
        )
        self.trade_button.row = 4
        self.trap_button = CallbackButton(
            label="Set Trap",
            emoji="🪤",
            style=discord.ButtonStyle.danger,
            callback=self._set_trap,
        )
        self.trap_button.row = 4
        self._refresh_nearby_components(self.player)

    @property
    def player(self) -> PlayerProgress | None:
        return self.state.players.get(self.player_id)

    async def push_update(
        self, interaction: discord.Interaction, player: PlayerProgress, embed: discord.Embed
    ) -> None:
        self._refresh_nearby_components(player)
        if interaction.response.is_done():
            if self.message:
                await interaction.followup.edit_message(self.message.id, embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)
        await self.save_callback(player)

    def _refresh_nearby_components(self, player: PlayerProgress | None) -> None:
        if not hasattr(self, "nearby_select"):
            return
        options: list[discord.SelectOption] = []
        if player and player.visible_players:
            for target_id, info in sorted(
                player.visible_players.items(), key=lambda item: (item[1].get("distance", 0), item[0])
            ):
                name = str(info.get("name")) or f"Cultivator {target_id}"
                distance = info.get("distance", "?")
                options.append(
                    discord.SelectOption(
                        label=name,
                        value=str(target_id),
                        description=f"{distance} tile{'s' if distance != 1 else ''} away",
                    )
                )
        if options:
            self.nearby_select.options = options
            self.nearby_select.disabled = False
            if self.nearby_select not in self.children:
                self.add_item(self.nearby_select)
            for button in (self.attack_button, self.trade_button, self.trap_button):
                if button not in self.children:
                    self.add_item(button)
            valid_values = {option.value for option in options}
            if self.selected_target_id is None or str(self.selected_target_id) not in valid_values:
                try:
                    self.selected_target_id = int(options[0].value)
                except ValueError:
                    self.selected_target_id = None
        else:
            if self.nearby_select in self.children:
                self.remove_item(self.nearby_select)
            for button in (self.attack_button, self.trade_button, self.trap_button):
                if button in self.children:
                    self.remove_item(button)
            self.selected_target_id = None
        disabled = self.selected_target_id is None
        for button in (self.attack_button, self.trade_button, self.trap_button):
            button.disabled = disabled

    async def _on_select_nearby(self, interaction: discord.Interaction, value: str) -> None:
        try:
            target_id = int(value)
        except ValueError:
            await interaction.response.send_message(
                "Unable to identify that cultivator.", ephemeral=True
            )
            return
        self.selected_target_id = target_id
        self._refresh_nearby_components(self.player)
        player = self.player
        name = None
        if player:
            info = player.visible_players.get(target_id)
            if info:
                name = info.get("name")
        label = name or f"Cultivator {target_id}"
        await interaction.response.send_message(
            f"Targeting {label}.", ephemeral=True
        )

    async def _attack_target(self, interaction: discord.Interaction) -> None:
        player = self.player
        if not player:
            await interaction.response.send_message(
                "Exploration session has ended.", ephemeral=True
            )
            return
        if self.selected_target_id is None:
            await interaction.response.send_message(
                "Select a cultivator to confront first.", ephemeral=True
            )
            return
        event = self.engine.handle_player_attack(player, self.selected_target_id)
        if event is None:
            await interaction.response.send_message(
                "No engagement occurred.", ephemeral=True
            )
            return
        embed = self.status_builder(
            player,
            None,
            [event],
            f"Engagement with {event.data.get('target_name', 'cultivator')}",
        )
        self._refresh_nearby_components(player)
        await self.push_update(interaction, player, embed)

    async def _trade_target(self, interaction: discord.Interaction) -> None:
        player = self.player
        if not player:
            await interaction.response.send_message(
                "Exploration session has ended.", ephemeral=True
            )
            return
        if self.selected_target_id is None:
            await interaction.response.send_message(
                "Select a cultivator to trade with first.", ephemeral=True
            )
            return
        event = self.engine.handle_player_trade(player, self.selected_target_id)
        if event is None:
            await interaction.response.send_message(
                "No trade could be initiated.", ephemeral=True
            )
            return
        embed = self.status_builder(
            player,
            None,
            [event],
            f"Trade offer to {event.data.get('target_name', 'cultivator')}",
        )
        self._refresh_nearby_components(player)
        await self.push_update(interaction, player, embed)

    async def _set_trap(self, interaction: discord.Interaction) -> None:
        player = self.player
        if not player:
            await interaction.response.send_message(
                "Exploration session has ended.", ephemeral=True
            )
            return
        if self.selected_target_id is None:
            await interaction.response.send_message(
                "Select a cultivator to target first.", ephemeral=True
            )
            return
        origin = player.tile_coordinate()
        if origin is None:
            await interaction.response.send_message(
                "Unknown starting tile. Travel first.", ephemeral=True
            )
            return
        action = SetTrapAction(
            actor_id=player.user_id,
            start=origin,
            trap_key="spirit-snare",
            potency=0.6,
            duration_seconds=900.0,
        )
        queue = self.engine.execute_action(action)
        events = self._drain_events(queue)
        embed = self.status_builder(
            player,
            None,
            events,
            "Set a spirit snare to prepare for nearby cultivators.",
        )
        self._refresh_nearby_components(player)
        await self.push_update(interaction, player, embed)

    def _drain_events(self, queue) -> list[TravelEvent]:
        events: list[TravelEvent] = []
        while queue:
            event = queue.pop()
            if event is None:
                break
            events.append(event)
        return events

    def _build_information_embed(self, player: PlayerProgress) -> discord.Embed:
        coordinate = player.tile_coordinate()
        coord_text = coordinate.to_key() if coordinate else "Unknown"
        self.state.ensure_world_map()
        world_map = self.state.world_map
        tile = world_map.tile_for_key(coord_text) if world_map and coordinate else None
        location = self.state.location_for_tile(coord_text) if coordinate else None
        if location is None and player.location:
            location = self.state.locations.get(player.location)
        if tile is None and world_map and coordinate:
            tile = world_map.tile_for_key(coord_text)
        safe = (location and location.is_safe) or (tile and tile.is_safe)
        location_name = location.name if location else "Uncharted Tile"
        if safe:
            location_name += " (Sanctuary)"
        tile_type = (
            "Point of Interest"
            if tile and tile.category is TileCategory.POINT_OF_INTEREST
            else "Normal"
        )

        embed = discord.Embed(
            title="Expedition Information",
            description="Decide the next expedition step.",
            color=discord.Color.teal(),
        )
        current_lines = [
            f"🧭 **Coordinate:** {coord_text}",
            f"📍 **Location:** {location_name}",
        ]
        if tile:
            current_lines.append(
                f"⛰️ **Terrain:** {tile_type} · Elev {int(tile.elevation)} m"
            )
            current_lines.append(f"🌌 **Qi Density:** {tile.environmental.qi_density:.2f}")
            current_lines.append(
                f"⚠️ **Hazard Level:** {tile.environmental.hazard_level:.2f}"
            )
        else:
            current_lines.extend(
                [
                    "⛰️ **Terrain:** Unknown",
                    "🌌 **Qi Density:** --",
                    "⚠️ **Hazard Level:** --",
                ]
            )
        embed.add_field(name="Current Tile", value="\n".join(current_lines), inline=False)

        intel_lines: list[str] = []
        if location and location.quests:
            quest_names = [
                self.state.quests.get(key).name
                for key in location.quests
                if key in self.state.quests
            ][:3]
            if quest_names:
                intel_lines.append("🟡 **Quests:** " + ", ".join(quest_names))
        if location and location.npcs:
            npc_names = [npc.name for npc in location.npcs[:3]]
            if npc_names:
                intel_lines.append("🟡 **NPCs:** " + ", ".join(npc_names))
        if not intel_lines:
            intel_lines.append("🟡 No intel recorded.")
        embed.add_field(name="Local Intel", value="\n".join(intel_lines), inline=False)

        energy_cap = self.state.ensure_player_energy(player)
        travel_lines = [
            f"🌀 **Movement:** {player.active_travel_mode.name.title()}",
            f"🔥 **Energy:** {player.energy:.1f}/{energy_cap:.1f}",
            f"🔔 **Noise Meter:** {player.travel_noise.value:.2f}",
        ]
        embed.add_field(name="Travel Readings", value="\n".join(travel_lines), inline=False)

        recent_events = list(player.travel_log[-5:])
        if recent_events:
            narrative = build_travel_narrative(recent_events)
            event_text = f"📜 {narrative}"
        else:
            event_text = "📜 No travel events recorded yet."
        embed.add_field(name="Recent Events", value=event_text, inline=False)

        if tile and tile.points_of_interest:
            poi_entries = [f"✨ {poi}" for poi in tile.points_of_interest[:5]]
            if len(tile.points_of_interest) > 5:
                poi_entries.append("…")
        elif location:
            poi_entries = [f"✨ {location.name}"]
        else:
            poi_entries = ["✨ None logged."]
        embed.add_field(
            name="Points of Interest",
            value="\n".join(poi_entries),
            inline=False,
        )

        active_season = world_map.active_season() if world_map else None
        season_line = "🍂 Season: Unknown"
        if active_season:
            season_line = f"🍂 Season: {active_season.name}"
        world_lines = [
            f"🕒 {self.state.world_time_summary()}",
            season_line,
        ]
        embed.add_field(name="World Conditions", value="\n".join(world_lines), inline=False)

        return embed

    def _build_records_embed(self, player: PlayerProgress) -> discord.Embed:
        state = self.state
        coordinate = player.tile_coordinate()
        tile = None
        location = None
        if coordinate:
            key = coordinate.to_key()
            if state.world_map is not None:
                tile = state.world_map.tile_for_key(key)
            location = state.location_for_tile(key)
        if location is None and player.location:
            location = state.locations.get(player.location)
            if location and coordinate is None and location.map_coordinate and state.world_map:
                try:
                    coordinate = TileCoordinate.from_key(location.map_coordinate)
                    tile = state.world_map.tile_for_key(coordinate.to_key())
                except ValueError:
                    coordinate = None

        embed = discord.Embed(
            title="Exploration Records",
            color=discord.Color.blurple(),
        )

        location_lines: list[str] = []
        if location:
            location_lines.append(f"• **Name:** {location.name}")
            if coordinate:
                location_lines.append(f"• **Coordinate:** {coordinate.to_key()}")
            if tile:
                location_lines.append(f"• **Terrain:** {tile.terrain.value.title()}")
            if tile and tile.is_safe and not location.is_safe:
                location_lines.append("• Local wards mark this tile as a sanctuary.")
            if location.points_of_interest:
                poi_preview = ", ".join(location.points_of_interest[:5])
                if len(location.points_of_interest) > 5:
                    poi_preview += " …"
                location_lines.append(f"• **Points of Interest:** {poi_preview}")
        elif tile:
            location_lines.append("• Surveyed tile with no anchored settlement.")
        else:
            location_lines.append("• No surveyed data available yet.")
        embed.add_field(
            name="Location Knowledge",
            value="\n".join(location_lines),
            inline=False,
        )

        fog = state.fog_of_war_for(player.user_id)
        exploration_lines = [
            f"• Discovered Tiles: {len(fog.discovered_tiles)}",
            f"• Surveyed Tiles: {len(fog.surveyed_tiles)}",
            f"• Rumor Leads: {len(fog.rumored_points)}",
            f"• Travel Mastery: Lv {player.travel_mastery.level} ({player.travel_mastery.experience} xp)",
            f"• Noise Meter: {player.travel_noise.value:.2f}",
        ]
        energy_cap = self.state.ensure_player_energy(player)
        exploration_lines.insert(
            0,
            f"• Energy: {player.energy:.1f}/{energy_cap:.1f}",
        )
        if player.travel_mastery.pending_rewards:
            exploration_lines.append(
                f"• Pending Rewards: {len(player.travel_mastery.pending_rewards)}"
            )
        if player.travel_journal:
            exploration_lines.append(
                f"• Journal Entries: {len(player.travel_journal)}"
            )
        embed.add_field(
            name="Exploration Status",
            value="\n".join(exploration_lines),
            inline=False,
        )

        if player.travel_log:
            recent = player.travel_log[-5:]
            log_lines = "\n".join(f"• {entry}" for entry in reversed(recent))
        else:
            log_lines = "• No travel history recorded yet."
        embed.add_field(name="Journey Log", value=log_lines, inline=False)

        if player.travel_journal:
            entries = list(player.travel_journal[-5:])
            journal_lines = [
                f"• [{entry.time_of_day.title()}] {entry.coordinate.to_key()} — {entry.summary}"
                for entry in reversed(entries)
            ]
        else:
            journal_lines = ["• No journal entries recorded yet."]
        embed.add_field(
            name="Travel Journal",
            value="\n".join(journal_lines),
            inline=False,
        )

        rumors = sorted(player.fog_of_war.rumored_points)
        if rumors:
            rumor_lines = "\n".join(f"• {rumor}" for rumor in rumors[:10])
            if len(rumors) > 10:
                rumor_lines += "\n• …"
        else:
            rumor_lines = "• No rumors gathered yet."
        embed.add_field(name="Rumors", value=rumor_lines, inline=False)

        return embed

    async def _travel_relative(self, interaction: discord.Interaction, dx: int, dy: int, dz: int = 0) -> None:
        player = self.player
        if not player:
            await interaction.response.send_message(
                "Exploration session has ended.", ephemeral=True
            )
            return
        start = player.tile_coordinate()
        if start is None:
            await interaction.response.send_message(
                "Unknown starting tile. Travel first.", ephemeral=True
            )
            return
        destination = TileCoordinate(start.x + dx, start.y + dy, start.z + dz)
        try:
            path, queue = self.engine.travel_to(player, destination)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        events = self._drain_events(queue)
        headline = f"Moved to {destination.to_key()}"
        embed = self.status_builder(player, path, events, headline)
        await self.push_update(interaction, player, embed)

    @discord.ui.button(label="West", style=discord.ButtonStyle.primary, emoji="⬅️", row=2)
    async def move_west(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._travel_relative(interaction, -1, 0)

    @discord.ui.button(label="North", style=discord.ButtonStyle.primary, emoji="⬆️", row=2)
    async def move_north(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._travel_relative(interaction, 0, 1)

    @discord.ui.button(label="East", style=discord.ButtonStyle.primary, emoji="➡️", row=2)
    async def move_east(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._travel_relative(interaction, 1, 0)

    @discord.ui.button(label="Descend", style=discord.ButtonStyle.secondary, emoji="🕳️", row=2)
    async def move_down(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._travel_relative(interaction, 0, 0, -1)

    @discord.ui.button(label="Ascend", style=discord.ButtonStyle.secondary, emoji="☁️", row=2)
    async def move_up(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._travel_relative(interaction, 0, 0, 1)

    @discord.ui.button(label="South", style=discord.ButtonStyle.primary, emoji="⬇️", row=3)
    async def move_south(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._travel_relative(interaction, 0, -1)

    @discord.ui.button(label="Camp", style=discord.ButtonStyle.secondary, emoji="🏕️", row=3)
    async def establish_camp(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        player = self.player
        if not player:
            await interaction.response.send_message(
                "Exploration session has ended.", ephemeral=True
            )
            return
        origin = player.tile_coordinate()
        if origin is None:
            await interaction.response.send_message(
                "Unknown starting tile. Travel first.", ephemeral=True
            )
            return
        action = CampAction(
            actor_id=player.user_id,
            start=origin,
            duration_seconds=300.0,
            warding_strength=0.4,
        )
        queue = self.engine.execute_action(action)
        events = self._drain_events(queue)
        embed = self.status_builder(player, None, events, "Established a temporary camp.")
        await self.push_update(interaction, player, embed)

    @discord.ui.button(label="Explore", style=discord.ButtonStyle.success, emoji="🍃", row=3)
    async def explore_tile(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        player = self.player
        if not player:
            await interaction.response.send_message(
                "Exploration session has ended.", ephemeral=True
            )
            return
        origin = player.tile_coordinate()
        if origin is None:
            await interaction.response.send_message(
                "Unknown starting tile. Travel first.", ephemeral=True
            )
            return
        action = ForageAction(
            actor_id=player.user_id,
            start=origin,
            yield_expectation=1.2,
            stealth_modifier=0.1,
        )
        queue = self.engine.execute_action(action)
        events = self._drain_events(queue)
        embed = self.status_builder(player, None, events, "Explored the area for loot.")
        await self.push_update(interaction, player, embed)

    @discord.ui.button(label="Information", style=discord.ButtonStyle.secondary, emoji="ℹ️", row=4)
    async def show_information(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        player = self.player
        if not player:
            await interaction.response.send_message(
                "Exploration session has ended.", ephemeral=True
            )
            return
        embed = self._build_information_embed(player)
        view = InformationMenu(self)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def send_records(self, interaction: discord.Interaction) -> None:
        player = self.player
        if not player:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "Exploration session has ended.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "Exploration session has ended.", ephemeral=True
                )
            return
        embed = self._build_records_embed(player)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class InformationMenu(discord.ui.View):
    def __init__(self, navigator: "TravelNavigatorView") -> None:
        super().__init__(timeout=90.0)
        self.navigator = navigator

    @discord.ui.button(label="Records", style=discord.ButtonStyle.primary, emoji="📚")
    async def open_records(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await self.navigator.send_records(interaction)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary, emoji="❌")
    async def close_panel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True

class WanderExpeditionView(OwnedView):
    def __init__(
        self,
        owner_id: int,
        *,
        wander_callback: SimpleCallback,
        return_callback: SimpleCallback | None = None,
        timeout: float = 60.0,
    ) -> None:
        super().__init__(owner_id, timeout=timeout)
        self._wander_callback = wander_callback
        self._return_callback = return_callback
        self.message: Optional[discord.Message] = None

        continue_button = CallbackButton(
            label="Continue Expedition",
            emoji=WANDER_TRAVEL_EMOJI,
            style=discord.ButtonStyle.primary,
            callback=self._handle_continue,
        )
        self.add_item(continue_button)

        if return_callback is not None:
            return_button = CallbackButton(
                label=None,
                emoji="🏠",
                style=discord.ButtonStyle.secondary,
                callback=return_callback,
            )
            return_button.row = 1
            self.add_item(return_button)

    async def _handle_continue(self, interaction: discord.Interaction) -> None:
        callback = self._wander_callback
        if callback is None:
            await interaction.response.send_message(
                "This expedition has concluded.", ephemeral=True
            )
            return
        await callback(interaction)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class EncounterDecisionView(discord.ui.View):
    def __init__(
        self,
        *,
        return_callback: SimpleCallback | None = None,
        timeout: float = 45.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self._fight_callback: CombatCallback | None = None
        self._escape_callback: CombatCallback | None = None
        self.message: Optional[discord.Message] = None
        self._return_callback = return_callback
        if return_callback is None:
            self.remove_item(self.return_to_menu)

    def set_callbacks(
        self,
        fight_callback: CombatCallback,
        escape_callback: CombatCallback,
    ) -> None:
        self._fight_callback = fight_callback
        self._escape_callback = escape_callback

    def disable_all_items(self) -> None:
        for child in self.children:
            child.disabled = True

    def finalize(self) -> None:
        self.disable_all_items()
        self._fight_callback = None
        self._escape_callback = None
        self.stop()

    async def on_timeout(self) -> None:
        self.disable_all_items()
        self._fight_callback = None
        self._escape_callback = None
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
        self.stop()

    @discord.ui.button(label=None, style=discord.ButtonStyle.secondary, emoji="🏠")
    async def return_to_menu(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:  # type: ignore[override]
        if self._return_callback is None:
            await interaction.response.send_message(
                "Resolve the encounter before leaving this menu.", ephemeral=True
            )
            return
        await self._return_callback(interaction)

    @discord.ui.button(label="Fight", style=discord.ButtonStyle.danger, emoji="⚔️")
    async def fight(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        if self._fight_callback is None:
            await interaction.response.send_message("This encounter is no longer available.", ephemeral=True)
            return
        await self._fight_callback(interaction, "fight")

    @discord.ui.button(label="Attempt to Escape", style=discord.ButtonStyle.secondary, emoji="🏃")
    async def escape(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        if self._escape_callback is None:
            await interaction.response.send_message("This encounter is no longer available.", ephemeral=True)
            return
        await self._escape_callback(interaction, "escape")


class CombatDecision(Enum):
    KEEP_FIGHTING = "keep_fighting"
    SURRENDER = "surrender"
    ESCAPE = "escape"


class CombatDecisionView(discord.ui.View):
    def __init__(
        self,
        allowed_user_ids: Sequence[int],
        *,
        escape_enabled: bool = True,
        timeout: float = 10.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.allowed_user_ids: set[int] = set(allowed_user_ids)
        self.message: Optional[discord.Message] = None
        loop = asyncio.get_running_loop()
        self._result: asyncio.Future[CombatDecision] = loop.create_future()
        self.escape_button.disabled = not escape_enabled

    def disable_all_items(self) -> None:
        for child in self.children:
            child.disabled = True

    async def on_timeout(self) -> None:
        if not self._result.done():
            self._result.set_result(CombatDecision.KEEP_FIGHTING)
        self.disable_all_items()
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
        self.stop()

    async def wait_for_result(self) -> CombatDecision:
        return await self._result

    def _authorized(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id in self.allowed_user_ids

    async def _handle_decision(
        self, interaction: discord.Interaction, decision: CombatDecision
    ) -> None:
        if not self._authorized(interaction):
            await interaction.response.send_message(
                "Only engaged cultivators may choose for this battle.",
                ephemeral=True,
            )
            return
        if not self._result.done():
            self._result.set_result(decision)
        self.disable_all_items()
        if interaction.response.is_done():
            await interaction.edit_original_response(view=self)
        else:
            await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Keep Fighting", style=discord.ButtonStyle.primary, emoji="⚔️")
    async def fight_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:  # type: ignore[override]
        await self._handle_decision(interaction, CombatDecision.KEEP_FIGHTING)

    @discord.ui.button(label="Surrender", style=discord.ButtonStyle.danger, emoji="🛎️")
    async def surrender_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:  # type: ignore[override]
        await self._handle_decision(interaction, CombatDecision.SURRENDER)

    @discord.ui.button(label="Try to Escape", style=discord.ButtonStyle.secondary, emoji="🏃")
    async def escape_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:  # type: ignore[override]
        if self.escape_button.disabled:
            await interaction.response.send_message(
                "Escape is not possible in this clash.", ephemeral=True
            )
            return
        await self._handle_decision(interaction, CombatDecision.ESCAPE)


class SafeZoneNPCButton(discord.ui.Button["SafeZoneActionView"]):
    def __init__(
        self,
        npc_index: int,
        *,
        label: str,
        style: discord.ButtonStyle,
        emoji: str | None,
    ) -> None:
        super().__init__(label=label, style=style, emoji=emoji)
        self.npc_index = npc_index

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if isinstance(view, SafeZoneActionView):
            await view.on_npc_selected(interaction, self.npc_index)
        else:
            await interaction.response.send_message(
                "This menu is no longer active.", ephemeral=True
            )


class SafeZoneNPCSelect(discord.ui.Select):
    def __init__(self, npcs: Sequence[LocationNPC]):
        options: list[discord.SelectOption] = []
        for index, npc in enumerate(npcs):
            label = (npc.name or "Unknown")[:95]
            description = npc.description.strip()
            if description:
                description = description[:95] + "…" if len(description) > 95 else description
            options.append(
                discord.SelectOption(
                    label=label or f"NPC #{index + 1}",
                    value=str(index),
                    description=description or None,
                )
            )
        super().__init__(
            placeholder="Choose someone to visit",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if not isinstance(view, SafeZoneActionView):
            await interaction.response.send_message(
                "This menu is no longer active.", ephemeral=True
            )
            return
        try:
            npc_index = int(self.values[0])
        except (TypeError, ValueError):
            await interaction.response.send_message(
                "That selection is unavailable.", ephemeral=True
            )
            return
        await view.on_npc_selected(interaction, npc_index)


class SafeZoneLeaveButton(discord.ui.Button["SafeZoneActionView"]):
    def __init__(self) -> None:
        super().__init__(label="Close", style=discord.ButtonStyle.secondary, emoji="🚪")

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if isinstance(view, SafeZoneActionView):
            view.disable_all_items()
            if view.message:
                try:
                    await view.message.edit(view=view)
                except discord.HTTPException:
                    pass
            if view._return_callback is not None:
                await view._return_callback(interaction)
            elif view._close_callback is not None:
                await view._close_callback(interaction)
            else:
                await interaction.response.send_message(
                    "You step away from the safe zone amenities.", ephemeral=True
                )
        else:
            await interaction.response.send_message(
                "This menu is no longer active.", ephemeral=True
            )


class SafeZoneActionView(discord.ui.View):
    def __init__(
        self,
        npcs: Sequence[LocationNPC],
        *,
        return_callback: SimpleCallback | None = None,
        timeout: float = 90.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.npcs = list(npcs)
        self.message: Optional[discord.Message] = None
        self._callback: Callable[[discord.Interaction, LocationNPC], Awaitable[None]] | None = None
        self._close_callback: Callable[[discord.Interaction], Awaitable[None]] | None = None
        self._return_callback = return_callback
        self._build_controls()

    def _build_controls(self) -> None:
        if not self.npcs:
            self.add_item(SafeZoneLeaveButton())
            return
        button_cap = min(len(self.npcs), 5)
        for index in range(button_cap):
            npc = self.npcs[index]
            label = npc.name[:80] if npc.name else f"NPC #{index + 1}"
            emoji: str | None = "💬"
            style = discord.ButtonStyle.primary
            if npc.npc_type is LocationNPCType.SHOP:
                emoji = "🛒"
                style = discord.ButtonStyle.success
            elif npc.npc_type is LocationNPCType.ENEMY:
                emoji = "⚔️"
                style = discord.ButtonStyle.danger
            self.add_item(
                SafeZoneNPCButton(
                    index,
                    label=label,
                    style=style,
                    emoji=emoji,
                )
            )
        if len(self.npcs) > button_cap:
            self.add_item(SafeZoneNPCSelect(self.npcs))
        self.add_item(SafeZoneLeaveButton())

    def set_callback(
        self,
        callback: Callable[[discord.Interaction, LocationNPC], Awaitable[None]],
    ) -> None:
        self._callback = callback

    def set_close_callback(
        self, callback: Callable[[discord.Interaction], Awaitable[None]]
    ) -> None:
        self._close_callback = callback

    async def on_npc_selected(
        self, interaction: discord.Interaction, index: int
    ) -> None:
        if self._callback is None:
            await interaction.response.send_message(
                "This menu is no longer active.", ephemeral=True
            )
            return
        if index < 0 or index >= len(self.npcs):
            await interaction.response.send_message(
                "That selection is unavailable.", ephemeral=True
            )
            return
        await self._callback(interaction, self.npcs[index])

    def disable_all_items(self) -> None:
        for child in self.children:
            child.disabled = True

    async def on_timeout(self) -> None:
        self.disable_all_items()
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass



class ProfilePageButton(discord.ui.Button["ProfileView"]):
    def __init__(
        self,
        page_key: str,
        label: str,
        emoji: str | None = None,
    ) -> None:
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            emoji=emoji,
        )
        self.page_key = page_key

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if isinstance(view, ProfileView):
            await view.on_page_button(interaction, self.page_key)


class ProfileReturnButton(discord.ui.Button["ProfileView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Return",
            style=discord.ButtonStyle.secondary,
            emoji="↩️",
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if isinstance(view, ProfileView):
            await view.exit_submenu(interaction)



class ProfileMainMenuButton(discord.ui.Button["ProfileView"]):
    def __init__(self) -> None:
        super().__init__(
            label=None,
            style=discord.ButtonStyle.secondary,
            emoji="🏠",
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if not isinstance(view, ProfileView) or view._main_menu_callback is None:
            await interaction.response.send_message(
                "This profile view is no longer linked to a menu.", ephemeral=True
            )
            return
        await view._main_menu_callback(interaction)


class ProfileQuickJumpSelect(discord.ui.Select["ProfileView"]):
    def __init__(
        self,
        options: Sequence[discord.SelectOption],
        *,
        placeholder: str = "Jump to page…",
    ) -> None:
        super().__init__(
            placeholder=placeholder,
            options=list(options),
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if not isinstance(view, ProfileView):
            await interaction.response.send_message(
                "This profile view is no longer active.", ephemeral=True
            )
            return
        await view.show_page(interaction, self.values[0])


class ProfileSubmenuSelect(discord.ui.Select["ProfileView"]):
    def __init__(
        self,
        submenu_key: str,
        options: Sequence[discord.SelectOption],
        *,
        placeholder: str,
    ) -> None:
        super().__init__(
            placeholder=placeholder,
            options=list(options),
            min_values=1,
            max_values=1,
        )
        self._submenu_key = submenu_key

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if not isinstance(view, ProfileView):
            await interaction.response.send_message(
                "This profile view is no longer active.", ephemeral=True
            )
            return
        await view.show_page(interaction, self.values[0])


class ProfileRefreshButton(discord.ui.Button["ProfileView"]):
    def __init__(self) -> None:
        super().__init__(
            label="Refresh Page",
            style=discord.ButtonStyle.success,
            emoji="🔄",
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = self.view
        if not isinstance(view, ProfileView):
            await interaction.response.send_message(
                "This profile view is no longer active.", ephemeral=True
            )
            return
        if interaction.user.id != view.owner_id:
            await interaction.response.send_message(
                "Only the profile owner can refresh these pages.",
                ephemeral=True,
            )
            return
        builder = view._page_builders.get(view.current_page)
        if builder is None:
            await interaction.response.send_message(
                "That section is currently unavailable.", ephemeral=True
            )
            return
        view.invalidate_page_cache(view.current_page)
        embed = view._render_page(view.current_page, builder, force_refresh=True)
        await interaction.response.edit_message(embed=embed, view=view)


class ProfileView(discord.ui.View):
    def __init__(
        self,
        owner_id: int,
        pages: dict[str, tuple[str, str | None, ProfilePageBuilder]],
        *,
        main_menu_callback: SimpleCallback | None = None,
        skill_tabs: dict[str, tuple[str, str | None, ProfilePageBuilder]] | None = None,
        inventory_tabs: dict[str, tuple[str, str | None, ProfilePageBuilder]] | None = None,
        submenus: dict[
            str, dict[str, tuple[str, str | None, ProfilePageBuilder]]
        ]
        | None = None,
        submenu_return_pages: dict[str, str] | None = None,
        initial_page: str | None = None,
        timeout: float = 120.0,
        page_item_factories: dict[
            str, Callable[["ProfileView"], Iterable[discord.ui.Item["ProfileView"]]]
        ]
        | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self._top_level_specs = dict(pages)
        self._main_menu_callback = main_menu_callback
        self._persistent_items: list[discord.ui.Item[ProfileView]] = []
        self._page_item_factories = dict(page_item_factories or {})
        self._active_page_items: list[discord.ui.Item[ProfileView]] = []
        self._embed_cache: dict[str, discord.Embed] = {}
        combined_submenus: dict[
            str, dict[str, tuple[str, str | None, ProfilePageBuilder]]
        ] = {}
        if skill_tabs:
            combined_submenus["skills"] = dict(skill_tabs)
        if inventory_tabs:
            combined_submenus["inventory"] = dict(inventory_tabs)
        if submenus:
            for key, mapping in submenus.items():
                if key in combined_submenus:
                    combined_submenus[key].update(mapping)
                else:
                    combined_submenus[key] = dict(mapping)
        self._submenu_specs = combined_submenus
        self._submenu_defaults: dict[str, str] = {
            key: next(iter(spec.keys()), "") for key, spec in self._submenu_specs.items()
        }
        self._page_builders: dict[str, ProfilePageBuilder] = {
            key: builder for key, (_, _, builder) in pages.items()
        }
        self._page_to_submenu: dict[str, str] = {}
        for submenu_key, entries in self._submenu_specs.items():
            for page_key, (_, _, builder) in entries.items():
                self._page_builders[page_key] = builder
                self._page_to_submenu[page_key] = submenu_key
        has_submenus = bool(self._submenu_specs)
        self._return_button = ProfileReturnButton() if has_submenus else None
        self._top_level_buttons: dict[str, ProfilePageButton] = {}
        self._submenu_buttons: dict[str, dict[str, ProfilePageButton]] = {
            key: {} for key in self._submenu_specs
        }
        self._mode: str = "top"
        self._active_submenu_key: str | None = None
        self._submenu_return_pages = dict(submenu_return_pages or {})
        default_page = (
            initial_page
            if initial_page in self._top_level_specs
            else next(iter(self._top_level_specs.keys()), "")
        )
        self._top_level_default = default_page
        self.current_page = default_page
        self._last_top_level_page = default_page
        self.message: Optional[discord.Message] = None

        if self._main_menu_callback is not None:
            self._persistent_items.insert(0, ProfileMainMenuButton())

        self._build_top_level_buttons()
        self._apply_page_items(self.current_page)
        self._update_button_styles()

    def _build_top_level_buttons(self) -> None:
        self.clear_items()
        self._top_level_buttons = {}
        self._active_submenu_key = None
        self._active_page_items = []
        for key in self._submenu_buttons:
            self._submenu_buttons[key] = {}
        self._add_persistent_items()
        for key, (label, emoji, _) in self._top_level_specs.items():
            button = ProfilePageButton(key, label, emoji)
            self._top_level_buttons[key] = button
            self.add_item(button)

    def _build_submenu_buttons(self, submenu_key: str) -> None:
        self.clear_items()
        self._top_level_buttons = {}
        self._active_page_items = []
        for key in self._submenu_buttons:
            self._submenu_buttons[key] = {}
        self._add_persistent_items()
        if self._return_button is not None:
            self.add_item(self._return_button)
        entries = self._submenu_specs.get(submenu_key, {})
        buttons: dict[str, ProfilePageButton] = {}
        for key, (label, emoji, _) in entries.items():
            button = ProfilePageButton(key, label, emoji)
            buttons[key] = button
            self.add_item(button)
        self._submenu_buttons[submenu_key] = buttons
        self._active_submenu_key = submenu_key

    def _add_persistent_items(self) -> None:
        for item in self._persistent_items:
            if item not in self.children:
                self.add_item(item)

    def _apply_page_items(self, page_key: str) -> None:
        for item in list(self._active_page_items):
            if item in self.children:
                self.remove_item(item)
        self._active_page_items = []
        factory = self._page_item_factories.get(page_key)
        if not factory:
            return
        items = list(factory(self))
        for item in items:
            if item not in self.children:
                self.add_item(item)
        self._active_page_items = items

    def _update_button_styles(self) -> None:
        if self._mode == "top":
            for key, button in self._top_level_buttons.items():
                button.style = (
                    discord.ButtonStyle.primary
                    if key == self.current_page
                    else discord.ButtonStyle.secondary
                )
        else:
            buttons = self._submenu_buttons.get(self._mode, {})
            for key, button in buttons.items():
                button.style = (
                    discord.ButtonStyle.primary
                    if key == self.current_page
                    else discord.ButtonStyle.secondary
                )

    def invalidate_page_cache(self, page_key: str | None = None) -> None:
        if page_key is None:
            self._embed_cache.clear()
            return
        self._embed_cache.pop(page_key, None)

    def _render_page(
        self,
        page_key: str,
        builder: ProfilePageBuilder,
        *,
        force_refresh: bool = False,
    ) -> discord.Embed:
        if not force_refresh:
            cached = self._embed_cache.get(page_key)
            if cached is not None:
                return cached.copy()
        embed = builder()
        self._embed_cache[page_key] = embed
        return embed.copy()

    def initial_embed(self) -> discord.Embed:
        builder = self._page_builders.get(self.current_page)
        if builder is None:
            raise RuntimeError("ProfileView has no initial page configured")
        return self._render_page(self.current_page, builder)

    async def on_page_button(self, interaction: discord.Interaction, page_key: str) -> None:
        await self.show_page(interaction, page_key)

    async def show_page(self, interaction: discord.Interaction, page_key: str) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the profile owner can view these pages.",
                ephemeral=True,
            )
            return
        if page_key in self._top_level_specs:
            self._last_top_level_page = page_key
        if page_key in self._submenu_specs:
            await self._open_submenu(interaction, page_key)
            return
        submenu_key = self._page_to_submenu.get(page_key)
        if submenu_key:
            if self._mode != submenu_key:
                self._mode = submenu_key
                self._build_submenu_buttons(submenu_key)
            builder = self._page_builders.get(page_key)
            if builder is None:
                await interaction.response.send_message(
                    "That page is currently unavailable.", ephemeral=True
                )
                return
            self.current_page = page_key
            self._apply_page_items(page_key)
            self._update_button_styles()
            embed = self._render_page(page_key, builder)
            await interaction.response.edit_message(embed=embed, view=self)
            return
        if self._mode != "top":
            self._mode = "top"
            self._build_top_level_buttons()
        builder = self._page_builders.get(page_key)
        if builder is None:
            await interaction.response.send_message(
                "That page is currently unavailable.", ephemeral=True
            )
            return
        self.current_page = page_key
        if page_key in self._top_level_specs:
            self._last_top_level_page = page_key
        self._apply_page_items(page_key)
        self._update_button_styles()
        embed = self._render_page(page_key, builder)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _open_submenu(
        self, interaction: discord.Interaction, submenu_key: str
    ) -> None:
        entries = self._submenu_specs.get(submenu_key)
        if not entries:
            await interaction.response.send_message(
                "That page is currently unavailable.", ephemeral=True
            )
            return
        default_page = self._submenu_defaults.get(submenu_key, "")
        if not default_page or default_page not in self._page_builders:
            await interaction.response.send_message(
                "That page is currently unavailable.", ephemeral=True
            )
            return
        self._mode = submenu_key
        self._build_submenu_buttons(submenu_key)
        self.current_page = default_page
        self._apply_page_items(default_page)
        self._update_button_styles()
        builder = self._page_builders.get(default_page)
        if builder is None:
            await interaction.response.send_message(
                "That page is currently unavailable.", ephemeral=True
            )
            return
        embed = self._render_page(default_page, builder)
        await interaction.response.edit_message(embed=embed, view=self)

    async def exit_submenu(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the profile owner can view these pages.",
                ephemeral=True,
            )
            return
        if self._mode == "top":
            await interaction.response.send_message(
                "There is nothing to return from.", ephemeral=True
            )
            return
        previous_mode = self._mode
        self._mode = "top"
        self._build_top_level_buttons()
        target_page = self._submenu_return_pages.get(previous_mode, self._last_top_level_page)
        if target_page not in self._top_level_specs:
            target_page = self._top_level_default
        self.current_page = target_page
        self._last_top_level_page = target_page
        self._apply_page_items(target_page)
        self._update_button_styles()
        builder = self._page_builders.get(self.current_page)
        if builder is None:
            await interaction.response.send_message(
                "That page is currently unavailable.", ephemeral=True
            )
            return
        embed = self._render_page(self.current_page, builder)
        await interaction.response.edit_message(embed=embed, view=self)

    async def refresh_current_page(self) -> None:
        self.invalidate_page_cache(self.current_page)
        if self.message is None:
            return
        builder = self._page_builders.get(self.current_page)
        if builder is None:
            return
        self._apply_page_items(self.current_page)
        self._update_button_styles()
        try:
            embed = self._render_page(
                self.current_page, builder, force_refresh=True
            )
            await self.message.edit(embed=embed, view=self)
        except discord.HTTPException:
            pass

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class CombatSkillSelect(discord.ui.Select):
    def __init__(
        self,
        owner_id: int,
        options: Iterable[discord.SelectOption],
        callback: CombatCallback,
    ) -> None:
        super().__init__(placeholder="Choose your attack", options=list(options))
        self._callback = callback
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the active combatant can choose an action.", ephemeral=True
            )
            return
        await interaction.response.defer()
        view = self.view
        if view is not None:
            for child in view.children:
                child.disabled = True
            try:
                await interaction.message.edit(view=view)
            except discord.HTTPException:
                pass
        await self._callback(interaction, self.values[0])
        if view is not None:
            view.stop()


class CombatActionView(discord.ui.View):
    def __init__(
        self,
        owner_id: int,
        options: Iterable[discord.SelectOption],
        callback: CombatCallback,
        *,
        timeout: float = 60.0,
        timeout_callback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self._select = CombatSkillSelect(owner_id, options, callback)
        self.add_item(self._select)
        self._timeout_callback = timeout_callback

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self._timeout_callback:
            await self._timeout_callback()


class DuelTurnPromptView(discord.ui.View):
    def __init__(
        self,
        actor_id: int,
        embed_factory: Callable[[], discord.Embed | None],
        view_factory: Callable[[], CombatActionView | None],
        *,
        initial_view: CombatActionView | None = None,
        prompt_text: str = "Select your action from the menu below.",
        timeout: float = 60.0,
        timeout_callback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self._actor_id = actor_id
        self._embed_factory = embed_factory
        self._view_factory = view_factory
        self._initial_view = initial_view
        self._prompt_text = prompt_text
        self._timeout_callback = timeout_callback

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
        if self._timeout_callback:
            await self._timeout_callback()

    @discord.ui.button(label="Take Your Turn", style=discord.ButtonStyle.primary)
    async def take_turn(  # type: ignore[override]
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.user.id != self._actor_id:
            await interaction.response.send_message(
                "Only the active combatant can open this prompt.", ephemeral=True
            )
            return
        view = self._initial_view or self._view_factory()
        self._initial_view = None
        if view is None:
            await interaction.response.send_message(
                "You currently have no actions available.", ephemeral=True
            )
            return
        embed = self._embed_factory()
        if embed is None:
            await interaction.response.send_message(
                content=self._prompt_text,
                view=view,
                ephemeral=True,
            )
            return
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
