"""Employee management UI for business satisfaction."""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

import config
from utils.helpers import fmt_amount
from utils.quests import record_quest_event

if TYPE_CHECKING:
    from discord.ext import commands


def build_manage_embed(row: object, *, effective_hourly: float) -> discord.Embed:
    sat = int(row["employee_satisfaction"])
    sat_icon = "😀" if sat >= 70 else "🙂" if sat >= 45 else "😟"
    wage_cost = round(effective_hourly * config.BUSINESS_MANAGE_WAGE_COST_FRACTION, 2)
    tier = int(row["tier"])
    event_cost = config.BUSINESS_MANAGE_EVENT_BASE_COST + tier * config.BUSINESS_MANAGE_EVENT_COST_PER_TIER
    embed = discord.Embed(
        title="👥 Employee Management",
        description=(
            f"{sat_icon} Satisfaction **{sat}/100** — affects income by up to "
            f"±{int(config.BUSINESS_SATISFACTION_SWING * 100)}%.\n"
            "Satisfaction drifts toward neutral over time without care."
        ),
        color=discord.Color.teal(),
    )
    embed.add_field(
        name="Raise wages",
        value=f"Cost **{fmt_amount(wage_cost)}** · +{config.BUSINESS_MANAGE_WAGE_SAT_GAIN} satisfaction",
        inline=False,
    )
    embed.add_field(
        name="Team event",
        value=(
            f"Cost **{fmt_amount(event_cost)}** · +{config.BUSINESS_MANAGE_EVENT_SAT_GAIN} satisfaction "
            f"(12h cooldown)"
        ),
        inline=False,
    )
    return embed


class ManageView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=120.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your panel.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Raise wages", style=discord.ButtonStyle.primary)
    async def wages_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        result = await self.cog.bot.db.manage_business_wages(self.user_id, self.guild_id)
        if result.get("error"):
            err = str(result["error"])
            if err == "insufficient_funds":
                msg = f"You need **{fmt_amount(float(result.get('cost', 0)))}**."
            else:
                msg = "Could not raise wages."
            await interaction.response.send_message(msg, ephemeral=True)
            return
        await record_quest_event(
            self.cog.bot.db, self.guild_id, self.user_id, "business_manage",
        )
        await interaction.response.send_message(
            f"💵 Wages raised! Satisfaction is now **{result['satisfaction']}/100**.",
            ephemeral=True,
        )

    @discord.ui.button(label="Team event", style=discord.ButtonStyle.secondary)
    async def event_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        result = await self.cog.bot.db.manage_business_event(self.user_id, self.guild_id)
        if result.get("error"):
            err = str(result["error"])
            if err == "cooldown":
                secs = int(float(result.get("retry_after", 0)))
                msg = f"Team event on cooldown — try again in **{secs // 3600}h {(secs % 3600) // 60}m**."
            elif err == "insufficient_funds":
                msg = f"You need **{fmt_amount(float(result.get('cost', 0)))}**."
            else:
                msg = "Could not host event."
            await interaction.response.send_message(msg, ephemeral=True)
            return
        await record_quest_event(
            self.cog.bot.db, self.guild_id, self.user_id, "business_manage",
        )
        await interaction.response.send_message(
            f"🎉 Team event hosted! Satisfaction is now **{result['satisfaction']}/100**.",
            ephemeral=True,
        )


async def send_manage_panel(interaction: discord.Interaction, cog: commands.Cog) -> None:
    user_id = interaction.user.id
    guild_id = interaction.guild_id
    assert guild_id is not None
    row = await cog.bot.db.get_business(user_id, guild_id)
    if row is None:
        await interaction.response.send_message(
            "You don't own a business. Use **/business create**.", ephemeral=True,
        )
        return
    breakdown = await cog.bot.db.get_business_income_breakdown(user_id, guild_id, row)
    hourly = breakdown.effective_hourly if breakdown else 0.0
    embed = build_manage_embed(row, effective_hourly=hourly)
    view = ManageView(cog, guild_id, user_id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
