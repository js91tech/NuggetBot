from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import guild_only_message
from utils.quests import (
    EMPIRE_QUESTS,
    ONBOARDING_QUESTS,
    TRACK_DAILY,
    TRACK_EMPIRE,
    TRACK_ONBOARDING,
    ensure_daily_quests,
    ensure_empire_quests,
    ensure_onboarding_quests,
    format_quest_lines,
    is_veteran,
)


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

        await ensure_onboarding_quests(self.bot.db, interaction.guild_id, interaction.user.id)
        onboard_done = await self.bot.db.count_completed_quests(
            interaction.guild_id, interaction.user.id, TRACK_ONBOARDING,
        ) >= len(ONBOARDING_QUESTS)

        embeds: list[discord.Embed] = []
        if not onboard_done:
            onboard_rows = await self.bot.db.list_user_quests(
                interaction.guild_id, interaction.user.id, TRACK_ONBOARDING,
            )
            done = await self.bot.db.count_completed_quests(
                interaction.guild_id, interaction.user.id, TRACK_ONBOARDING,
            )
            embed = discord.Embed(
                title="New raider onboarding",
                description="\n".join(format_quest_lines(onboard_rows, track=TRACK_ONBOARDING)),
                color=discord.Color.green(),
            )
            embed.add_field(name="Progress", value=f"{done}/{len(ONBOARDING_QUESTS)} complete", inline=False)
            embeds.append(embed)
        else:
            await ensure_empire_quests(self.bot.db, interaction.guild_id, interaction.user.id)
            empire_done = await self.bot.db.count_completed_quests(
                interaction.guild_id, interaction.user.id, TRACK_EMPIRE,
            ) >= len(EMPIRE_QUESTS)
            if not empire_done:
                empire_rows = await self.bot.db.list_user_quests(
                    interaction.guild_id, interaction.user.id, TRACK_EMPIRE,
                )
                done = await self.bot.db.count_completed_quests(
                    interaction.guild_id, interaction.user.id, TRACK_EMPIRE,
                )
                embed = discord.Embed(
                    title="Empire tutorial",
                    description="\n".join(format_quest_lines(empire_rows, track=TRACK_EMPIRE)),
                    color=discord.Color.dark_green(),
                )
                embed.add_field(name="Progress", value=f"{done}/{len(EMPIRE_QUESTS)} complete", inline=False)
                embeds.append(embed)

        progress = await self.bot.db.get_user_progress(interaction.user.id, interaction.guild_id)
        if is_veteran(progress) or onboard_done:
            await ensure_daily_quests(self.bot.db, interaction.guild_id, interaction.user.id)
            daily_rows = await self.bot.db.list_user_quests(
                interaction.guild_id, interaction.user.id, TRACK_DAILY,
            )
            daily_embed = discord.Embed(
                title="Daily goals",
                description="\n".join(format_quest_lines(daily_rows, track=TRACK_DAILY)),
                color=discord.Color.blue(),
            )
            daily_embed.set_footer(text="Resets at UTC midnight · Rewards pay on completion")
            embeds.append(daily_embed)

        if not embeds:
            embeds.append(discord.Embed(title="Quests", description="No active quests.", color=discord.Color.greyple()))

        await interaction.response.send_message(embeds=embeds, ephemeral=True)

    @app_commands.command(
        name="quest-hint",
        description="Get a nudge on your current quest objective.",
    )
    @app_commands.guild_only()
    async def quest_hint(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        await ensure_onboarding_quests(self.bot.db, interaction.guild_id, interaction.user.id)
        onboard_done = await self.bot.db.count_completed_quests(
            interaction.guild_id, interaction.user.id, TRACK_ONBOARDING,
        ) >= len(ONBOARDING_QUESTS)
        if not onboard_done:
            rows = await self.bot.db.list_user_quests(
                interaction.guild_id, interaction.user.id, TRACK_ONBOARDING,
            )
            track = TRACK_ONBOARDING
        else:
            await ensure_empire_quests(self.bot.db, interaction.guild_id, interaction.user.id)
            empire_done = await self.bot.db.count_completed_quests(
                interaction.guild_id, interaction.user.id, TRACK_EMPIRE,
            ) >= len(EMPIRE_QUESTS)
            if not empire_done:
                rows = await self.bot.db.list_user_quests(
                    interaction.guild_id, interaction.user.id, TRACK_EMPIRE,
                )
                track = TRACK_EMPIRE
            else:
                await ensure_daily_quests(self.bot.db, interaction.guild_id, interaction.user.id)
                rows = await self.bot.db.list_user_quests(
                    interaction.guild_id, interaction.user.id, TRACK_DAILY,
                )
                track = TRACK_DAILY

        pending = [row for row in rows if row["completed_at"] is None]
        if not pending:
            await interaction.response.send_message(
                "All current quests are complete. Nice work!",
                ephemeral=True,
            )
            return

        lines = format_quest_lines(pending[:1], track=track)
        await interaction.response.send_message(lines[0], ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Quests(bot))
