from __future__ import annotations

import time
from typing import TYPE_CHECKING

import discord

import config
from utils.helpers import fmt_amount
from utils.jail import (
    bail_cost_for_tier,
    execute_bail,
    execute_jail_key,
    format_jail_time_remaining,
)

if TYPE_CHECKING:
    from cogs.heist import Heist


def arrest_tier_label(tier: str | None) -> str:
    normalized = (tier or "").strip().lower()
    if normalized == "1":
        return "Bank heist tier 1"
    if normalized == "2":
        return "Bank heist tier 2"
    if normalized == "3":
        return "Bank heist tier 3"
    return "Wallet heist"


async def build_jail_embed(
    cog: Heist,
    guild_id: int,
    member: discord.Member,
) -> discord.Embed:
    user_row = await cog.bot.db.get_user(member.id, guild_id)
    now = time.time()
    arrested_until = float(user_row["arrested_until"])
    wallet = await cog.bot.db.get_balance(member.id, guild_id)
    keys = await cog.bot.db.get_inventory_quantity(member.id, guild_id, "jail_key")

    embed = discord.Embed(
        title="Jail",
        color=discord.Color.dark_grey(),
    )
    embed.add_field(
        name="Bail rates",
        value=(
            f"Wallet heist — **{fmt_amount(config.BAIL_WALLET_HEIST)}**\n"
            f"Bank tier 1 — **{fmt_amount(config.BAIL_BANK_TIER_1)}**\n"
            f"Bank tier 2 — **{fmt_amount(config.BAIL_BANK_TIER_2)}**\n"
            f"Bank tier 3 — **{fmt_amount(config.BAIL_BANK_TIER_3)}**"
        ),
        inline=False,
    )
    embed.add_field(name="Your pocket", value=fmt_amount(wallet), inline=True)
    embed.add_field(name="Jail Keys", value=f"**{keys}**", inline=True)

    if arrested_until > now:
        tier = await cog.bot.db.get_arrest_tier(member.id, guild_id)
        remaining = arrested_until - now
        cost = bail_cost_for_tier(tier)
        embed.description = (
            f"**{member.display_name}** is in jail.\n"
            f"**{arrest_tier_label(tier)}** · **{format_jail_time_remaining(remaining)}** left"
        )
        embed.add_field(name="Your bail", value=fmt_amount(cost), inline=True)
        embed.color = discord.Color.red()
    else:
        embed.description = (
            f"**{member.display_name}** is not in jail.\n"
            "Use the buttons below to post bail or use a **Jail Key** on an arrested ally."
        )

    embed.set_footer(text="Jail Key shop price: 75k · Does not clear downed status")
    return embed


class JailView(discord.ui.View):
    def __init__(self, cog: Heist, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

        bail_ally = discord.ui.UserSelect(
            placeholder="Pay bail for arrested ally…",
            row=1,
            min_values=1,
            max_values=1,
        )
        bail_ally.callback = self._bail_ally_callback
        self.add_item(bail_ally)

        key_ally = discord.ui.UserSelect(
            placeholder="Use Jail Key on arrested ally…",
            row=2,
            min_values=1,
            max_values=1,
        )
        key_ally.callback = self._key_ally_callback
        self.add_item(key_ally)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your jail panel.", ephemeral=True,
            )
            return False
        return True

    async def _refresh(self, interaction: discord.Interaction) -> None:
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if member is None or interaction.guild is None:
            await interaction.response.send_message("Members only.", ephemeral=True)
            return
        embed = await build_jail_embed(self.cog, self.guild_id, member)
        view = JailView(self.cog, self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _resolve_member(
        self,
        interaction: discord.Interaction,
        raw_id: str,
    ) -> discord.Member | None:
        if interaction.guild is None:
            await interaction.response.send_message("Guild only.", ephemeral=True)
            return None
        target = interaction.guild.get_member(int(raw_id))
        if target is None:
            await interaction.response.send_message("That user left the server.", ephemeral=True)
            return None
        if target.bot and not config.ALLOW_BOT_PLAYERS:
            await interaction.response.send_message("Bots cannot be bailed out.", ephemeral=True)
            return None
        return target

    async def _bail_ally_callback(self, interaction: discord.Interaction) -> None:
        raw_id = interaction.data["values"][0]  # type: ignore[index]
        target = await self._resolve_member(interaction, raw_id)
        if target is None:
            return
        result = await execute_bail(
            self.cog.bot.db,
            interaction.user.id,
            target.id,
            self.guild_id,
        )
        if not result.ok:
            await interaction.response.send_message(result.error or "Bail failed.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"{result.message}\nReleased **{target.display_name}**.",
            ephemeral=True,
        )

    async def _key_ally_callback(self, interaction: discord.Interaction) -> None:
        raw_id = interaction.data["values"][0]  # type: ignore[index]
        target = await self._resolve_member(interaction, raw_id)
        if target is None:
            return
        result = await execute_jail_key(
            self.cog.bot.db,
            interaction.user.id,
            target.id,
            self.guild_id,
        )
        if not result.ok:
            await interaction.response.send_message(result.error or "Use failed.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"{result.message}\n**{target.display_name}** is free.",
            ephemeral=True,
        )

    @discord.ui.button(label="Post my bail", style=discord.ButtonStyle.success, row=0)
    async def bail_self(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        result = await execute_bail(
            self.cog.bot.db,
            interaction.user.id,
            interaction.user.id,
            self.guild_id,
        )
        if not result.ok:
            await interaction.response.send_message(result.error or "Bail failed.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(result.message, ephemeral=True)
            return
        embed = await build_jail_embed(self.cog, self.guild_id, interaction.user)
        view = JailView(self.cog, self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)
        await interaction.followup.send(result.message, ephemeral=True)

    @discord.ui.button(label="Use Jail Key", style=discord.ButtonStyle.primary, row=0)
    async def key_self(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        result = await execute_jail_key(
            self.cog.bot.db,
            interaction.user.id,
            interaction.user.id,
            self.guild_id,
        )
        if not result.ok:
            await interaction.response.send_message(result.error or "Use failed.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(result.message, ephemeral=True)
            return
        embed = await build_jail_embed(self.cog, self.guild_id, interaction.user)
        view = JailView(self.cog, self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)
        await interaction.followup.send(result.message, ephemeral=True)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=0)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._refresh(interaction)


async def send_jail_panel(interaction: discord.Interaction, cog: Heist) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("Members only.", ephemeral=True)
        return

    embed = await build_jail_embed(cog, interaction.guild_id, interaction.user)
    view = JailView(cog, interaction.guild_id, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
