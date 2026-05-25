from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import guild_only_message
from utils.quests import (
    ONBOARDING_QUESTS,
    TRACK_DAILY,
    TRACK_ONBOARDING,
    ensure_daily_quests,
    ensure_onboarding_quests,
    format_quest_lines,
    is_veteran,
)
from utils.quests_display import next_quest_line


class Quests(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="quests",
        description="View onboarding steps or daily goals and claim progress.",
    )
    @app_commands.guild_only()
    async def quests(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        progress = await self.bot.db.get_user_progress(interaction.user.id, interaction.guild_id)
        veteran = is_veteran(progress)
        if veteran:
            await ensure_daily_quests(self.bot.db, interaction.guild_id, interaction.user.id)
            daily_rows = await self.bot.db.list_user_quests(
                interaction.guild_id, interaction.user.id, TRACK_DAILY
            )
            embed = discord.Embed(
                title="Daily goals",
                description="\n".join(format_quest_lines(daily_rows, track=TRACK_DAILY)),
                color=discord.Color.blue(),
            )
            embed.set_footer(text="Resets at UTC midnight · Rewards pay on completion")
            hint = next_quest_line(daily_rows, track=TRACK_DAILY)
            if hint:
                embed.add_field(name="Focus next", value=hint, inline=False)
        else:
            await ensure_onboarding_quests(
                self.bot.db, interaction.guild_id, interaction.user.id
            )
            onboard_rows = await self.bot.db.list_user_quests(
                interaction.guild_id, interaction.user.id, TRACK_ONBOARDING
            )
            done = await self.bot.db.count_completed_quests(
                interaction.guild_id, interaction.user.id, TRACK_ONBOARDING
            )
            embed = discord.Embed(
                title="New raider onboarding",
                description="\n".join(format_quest_lines(onboard_rows, track=TRACK_ONBOARDING)),
                color=discord.Color.green(),
            )
            embed.add_field(
                name="Progress",
                value=f"{done}/{len(ONBOARDING_QUESTS)} complete",
                inline=False,
            )
            if done >= len(ONBOARDING_QUESTS):
                embed.add_field(
                    name="Next up",
                    value="You unlocked **daily goals** — check back tomorrow or after UTC midnight.",
                    inline=False,
                )
            else:
                hint = next_quest_line(onboard_rows, track=TRACK_ONBOARDING)
                if hint:
                    embed.add_field(name="Focus next", value=hint, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="quest-hint",
        description="Get a nudge on your current quest objective.",
    )
    @app_commands.guild_only()
    async def quest_hint(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        progress = await self.bot.db.get_user_progress(interaction.user.id, interaction.guild_id)
        if is_veteran(progress):
            await ensure_daily_quests(self.bot.db, interaction.guild_id, interaction.user.id)
            rows = await self.bot.db.list_user_quests(
                interaction.guild_id, interaction.user.id, TRACK_DAILY
            )
        else:
            await ensure_onboarding_quests(
                self.bot.db, interaction.guild_id, interaction.user.id
            )
            rows = await self.bot.db.list_user_quests(
                interaction.guild_id, interaction.user.id, TRACK_ONBOARDING
            )

        pending = [row for row in rows if row["completed_at"] is None]
        if not pending:
            await interaction.response.send_message(
                "All current quests are complete. Nice work!",
                ephemeral=True,
            )
            return

        lines = format_quest_lines(pending[:1], track=TRACK_DAILY)
        await interaction.response.send_message(lines[0], ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Quests(bot))
