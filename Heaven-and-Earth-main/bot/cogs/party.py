from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ..game import create_party, join_party, leave_party
from ..models.players import PlayerProgress
from .base import HeavenCog


class PartyActionView(discord.ui.View):
    def __init__(
        self,
        cog: "PartyCog",
        guild_id: int,
        user_id: int,
        party_id: str | None,
        *,
        timeout: float = 90.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.party_id = party_id
        self.message: Optional[discord.Message] = None
        self._update_button_states()

    def set_party(self, party_id: str | None) -> None:
        self.party_id = party_id
        self._update_button_states()

    def _has_party(self) -> bool:
        return bool(self.party_id)

    def _update_button_states(self) -> None:
        has_party = self._has_party()
        self.invite.disabled = not has_party
        self.info.disabled = not has_party
        self.leave.disabled = not has_party

    async def _ensure_authorized(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Only the party member who opened this panel can use these controls.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Invite Code", style=discord.ButtonStyle.secondary, emoji="🪪")
    async def invite(  # type: ignore[override]
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await self._ensure_authorized(interaction):
            return
        if not self._has_party():
            await interaction.response.send_message(
                "You are not currently in a party.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Your current party invite code is `{self.party_id}`.",
            ephemeral=True,
        )

    @discord.ui.button(label="Refresh Info", style=discord.ButtonStyle.primary, emoji="📜")
    async def info(  # type: ignore[override]
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await self._ensure_authorized(interaction):
            return
        await self.cog.refresh_party_view(interaction, self)

    @discord.ui.button(label="Leave Party", style=discord.ButtonStyle.danger, emoji="🚪")
    async def leave(  # type: ignore[override]
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not await self._ensure_authorized(interaction):
            return
        await self.cog.leave_party_from_view(interaction, self)


class PartyCog(HeavenCog):
    party_group = app_commands.Group(name="party", description="Manage parties")

    def __init__(self, bot: commands.Bot):
        super().__init__(bot)

    async def _fetch_player(self, guild_id: int, user_id: int) -> PlayerProgress | None:
        data = await self.store.get_player(guild_id, user_id)
        if not data:
            return None
        player = PlayerProgress(**data)
        self.state.register_player(player)
        return player

    async def _save_player(self, guild_id: int, player: PlayerProgress) -> None:
        await self.store.upsert_player(guild_id, asdict(player))

    def _party_members_text(self, guild: discord.Guild, party) -> str:
        members: list[str] = []
        for member_id in party.member_ids:
            member = guild.get_member(member_id)
            mention = member.mention if member else f"<@{member_id}>"
            prefix = "[Leader]" if member_id == party.leader_id else "•"
            members.append(f"{prefix} {mention}")
        return "\n".join(members) if members else "No members enlisted."

    def _party_embed(
        self,
        guild: discord.Guild,
        party,
        *,
        title: str,
        description: str,
        colour: discord.Colour | None = None,
    ) -> discord.Embed:
        embed = discord.Embed(
            title=title,
            description=description,
            colour=colour or discord.Colour.blurple(),
        )
        embed.add_field(name="Invite Code", value=f"`{party.party_id}`", inline=False)
        leader_member = guild.get_member(party.leader_id)
        leader_label = (
            leader_member.mention if leader_member else f"<@{party.leader_id}>"
        )
        embed.add_field(name="Leader", value=leader_label, inline=False)
        embed.add_field(
            name="Members",
            value=self._party_members_text(guild, party),
            inline=False,
        )
        embed.set_footer(
            text=f"Party size: {len(party.member_ids)} cultivator(s)"
        )
        return embed

    def _make_party_view(
        self, guild_id: int, user_id: int, party_id: str | None
    ) -> PartyActionView:
        return PartyActionView(self, guild_id, user_id, party_id)

    async def refresh_party_view(
        self, interaction: discord.Interaction, view: PartyActionView
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This server is no longer available.", ephemeral=True
            )
            return
        party_id = view.party_id
        if not party_id or party_id not in self.state.parties:
            view.set_party(None)
            await interaction.response.send_message(
                "Your party could not be found.", ephemeral=True
            )
            if view.message:
                try:
                    await view.message.edit(view=view)
                except discord.HTTPException:
                    pass
            return
        party = self.state.parties[party_id]
        embed = self._party_embed(
            guild,
            party,
            title="Party Roster",
            description="Current members gathered under your banner.",
        )
        await interaction.response.edit_message(embed=embed, view=view)
        if interaction.message is not None:
            view.message = interaction.message

    async def leave_party_from_view(
        self, interaction: discord.Interaction, view: PartyActionView
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This server is no longer available.", ephemeral=True
            )
            return
        player = await self._fetch_player(guild.id, view.user_id)
        if not player or not player.party_id:
            view.set_party(None)
            await interaction.response.send_message(
                "You are not currently in a party.", ephemeral=True
            )
            if view.message:
                try:
                    await view.message.edit(view=view)
                except discord.HTTPException:
                    pass
            return
        party = self.state.parties.get(player.party_id)
        if party:
            leave_party(party, player.user_id)
            if not party.member_ids:
                self.state.parties.pop(party.party_id, None)
        player.party_id = None
        await self._save_player(guild.id, player)
        view.set_party(None)
        embed = discord.Embed(
            title="Party Departure",
            description="You step away from your companions.",
            colour=discord.Colour.orange(),
        )
        await interaction.response.edit_message(embed=embed, view=view)
        if interaction.message is not None:
            view.message = interaction.message

    @party_group.command(name="create", description="Create a new party")
    @app_commands.guild_only()
    async def party_create(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        await self.ensure_guild_loaded(guild.id)
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            await interaction.response.send_message("Register first with /register", ephemeral=True)
            return
        if player.party_id:
            await interaction.response.send_message("You are already in a party.", ephemeral=True)
            return
        party_id = uuid.uuid4().hex[:8]
        party = create_party(party_id, interaction.user.id)
        self.state.parties[party_id] = party
        player.party_id = party_id
        await self._save_player(guild.id, player)
        embed = self._party_embed(
            guild,
            party,
            title="Party Founded",
            description=(
                "A new expedition begins. Share the invite code with trusted allies."
            ),
        )
        view = self._make_party_view(guild.id, interaction.user.id, party_id)
        await interaction.response.send_message(embed=embed, view=view)
        try:
            view.message = await interaction.original_response()
        except discord.HTTPException:
            view.message = None

    @party_group.command(name="join", description="Join an existing party")
    @app_commands.describe(party_id="Identifier of the party to join")
    @app_commands.guild_only()
    async def party_join(self, interaction: discord.Interaction, party_id: str) -> None:
        guild = interaction.guild
        assert guild is not None
        await self.ensure_guild_loaded(guild.id)
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            await interaction.response.send_message("Register first with /register", ephemeral=True)
            return
        if player.party_id:
            await interaction.response.send_message("You are already in a party.", ephemeral=True)
            return
        party = self.state.parties.get(party_id)
        if not party:
            await interaction.response.send_message("No party with that ID exists.", ephemeral=True)
            return
        join_party(party, interaction.user.id)
        player.party_id = party_id
        await self._save_player(guild.id, player)
        embed = self._party_embed(
            guild,
            party,
            title="Joined Party",
            description=(
                f"{interaction.user.mention} joins the formation. "
                f"{len(party.member_ids)} cultivator(s) stand united."
            ),
        )
        view = self._make_party_view(guild.id, interaction.user.id, party_id)
        await interaction.response.send_message(embed=embed, view=view)
        try:
            view.message = await interaction.original_response()
        except discord.HTTPException:
            view.message = None

    @party_group.command(name="leave", description="Leave your current party")
    @app_commands.guild_only()
    async def party_leave(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        await self.ensure_guild_loaded(guild.id)
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player or not player.party_id:
            await interaction.response.send_message("You are not in a party.", ephemeral=True)
            return
        party = self.state.parties.get(player.party_id)
        party_snapshot = None
        if party:
            leave_party(party, interaction.user.id)
            party_snapshot = party
            if not party.member_ids:
                self.state.parties.pop(party.party_id, None)
        player.party_id = None
        await self._save_player(guild.id, player)
        if party_snapshot and party_snapshot.member_ids:
            embed = self._party_embed(
                guild,
                party_snapshot,
                title="Party Departure",
                description="You take your leave. Remaining companions press onward without you.",
                colour=discord.Colour.orange(),
            )
        else:
            embed = discord.Embed(
                title="Party Departure",
                description="You take your leave. Without its members, the party dissolves.",
                colour=discord.Colour.orange(),
            )
        view = self._make_party_view(guild.id, interaction.user.id, None)
        await interaction.response.send_message(embed=embed, view=view)
        try:
            view.message = await interaction.original_response()
        except discord.HTTPException:
            view.message = None

    @party_group.command(name="info", description="View your party information")
    @app_commands.guild_only()
    async def party_info(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        await self.ensure_guild_loaded(guild.id)
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player or not player.party_id:
            await interaction.response.send_message("You are not in a party.", ephemeral=True)
            return
        party = self.state.parties.get(player.party_id)
        if not party:
            await interaction.response.send_message("Your party no longer exists.", ephemeral=True)
            return
        embed = self._party_embed(
            guild,
            party,
            title="Party Ledger",
            description="Here is the current party formation.",
        )
        view = self._make_party_view(guild.id, interaction.user.id, party.party_id)
        await interaction.response.send_message(embed=embed, view=view)
        try:
            view.message = await interaction.original_response()
        except discord.HTTPException:
            view.message = None


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PartyCog(bot))
