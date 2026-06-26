from __future__ import annotations

from typing import TYPE_CHECKING

import discord

import config
from utils.helpers import fmt_amount

if TYPE_CHECKING:
    from discord.ext import commands


def format_bank_expansion_roster(expansions: dict[int, int]) -> str:
    if not expansions:
        return "None"
    parts: list[str] = []
    for tier in sorted(expansions):
        spec = config.BANK_EXPANSION_TIERS.get(tier)
        if spec is None:
            continue
        qty = expansions[tier]
        parts.append(f"**{qty}× {spec['name']}** (T{tier})")
    return ", ".join(parts) if parts else "None"


def build_bank_expansion_embed(
    member: discord.Member,
    *,
    expansions: dict[int, int],
    capacity: float,
    wallet: float,
) -> discord.Embed:
    total = sum(expansions.values())
    embed = discord.Embed(
        title=f"{member.display_name}'s vault expansions",
        description=(
            f"Current capacity: **{fmt_amount(capacity)}** "
            f"(base **{fmt_amount(config.BANK_BASE_CAPACITY)}**)\n"
            f"Owned: {format_bank_expansion_roster(expansions)} · "
            f"**{total}** token(s) total"
        ),
        color=discord.Color.dark_green(),
    )
    for tier, spec in sorted(config.BANK_EXPANSION_TIERS.items()):
        qty = expansions.get(tier, 0)
        embed.add_field(
            name=f"T{tier} — {spec['name']}",
            value=(
                f"**{fmt_amount(float(spec['cost']))}** each · "
                f"+**{fmt_amount(float(spec['capacity']))}** cap · "
                f"Owned: **{qty}**"
            ),
            inline=False,
        )
    embed.set_footer(text=f"Pocket: {fmt_amount(wallet)} · Paid from wallet")
    return embed


class BankExpansionView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your vault expansion panel.", ephemeral=True,
            )
            return False
        return True

    async def _expand(self, interaction: discord.Interaction, tier: int) -> None:
        ok, reason = await self.cog.bot.db.expand_bank_capacity(
            self.user_id, self.guild_id, tier,
        )
        if reason == "invalid_tier":
            await interaction.response.send_message("Invalid expansion tier.", ephemeral=True)
            return
        if not ok:
            cost = float(config.BANK_EXPANSION_TIERS[tier]["cost"])
            await interaction.response.send_message(
                f"You need **{fmt_amount(cost)}** in your pocket.",
                ephemeral=True,
            )
            return
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message("Vault expanded.", ephemeral=True)
            return
        expansions = await self.cog.bot.db.get_bank_expansions(self.user_id, self.guild_id)
        capacity = await self.cog.bot.db.get_bank_capacity(self.user_id, self.guild_id)
        wallet = await self.cog.bot.db.get_balance(self.user_id, self.guild_id)
        name = str(config.BANK_EXPANSION_TIERS[tier]["name"])
        cap_gain = float(config.BANK_EXPANSION_TIERS[tier]["capacity"])
        embed = build_bank_expansion_embed(
            member, expansions=expansions, capacity=capacity, wallet=wallet,
        )
        await interaction.response.edit_message(
            content=(
                f"Purchased **1× {name}** (T{tier}) · "
                f"+**{fmt_amount(cap_gain)}** cap · "
                f"new capacity **{fmt_amount(capacity)}**."
            ),
            embed=embed,
            view=self,
        )

    @discord.ui.button(label="+1 Standard (T1)", style=discord.ButtonStyle.secondary, row=0)
    async def expand_t1(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._expand(interaction, 1)

    @discord.ui.button(label="+1 Reinforced (T2)", style=discord.ButtonStyle.primary, row=0)
    async def expand_t2(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._expand(interaction, 2)

    @discord.ui.button(label="+1 Fortified (T3)", style=discord.ButtonStyle.primary, row=1)
    async def expand_t3(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._expand(interaction, 3)

    @discord.ui.button(label="+1 Sovereign (T4)", style=discord.ButtonStyle.danger, row=1)
    async def expand_t4(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._expand(interaction, 4)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=2)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message("Refreshed.", ephemeral=True)
            return
        expansions = await self.cog.bot.db.get_bank_expansions(self.user_id, self.guild_id)
        capacity = await self.cog.bot.db.get_bank_capacity(self.user_id, self.guild_id)
        wallet = await self.cog.bot.db.get_balance(self.user_id, self.guild_id)
        embed = build_bank_expansion_embed(
            member, expansions=expansions, capacity=capacity, wallet=wallet,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=self)


async def send_bank_expansion_panel(interaction: discord.Interaction, cog: commands.Cog) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return
    member = interaction.user
    if not isinstance(member, discord.Member):
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return
    expansions = await cog.bot.db.get_bank_expansions(member.id, interaction.guild_id)
    capacity = await cog.bot.db.get_bank_capacity(member.id, interaction.guild_id)
    wallet = await cog.bot.db.get_balance(member.id, interaction.guild_id)
    embed = build_bank_expansion_embed(
        member, expansions=expansions, capacity=capacity, wallet=wallet,
    )
    view = BankExpansionView(cog, interaction.guild_id, member.id)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
