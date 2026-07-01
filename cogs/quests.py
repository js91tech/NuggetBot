from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import guild_only_message
from utils.quest_ui import build_quest_embeds
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
    quest_by_id,
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

        embeds, view = await build_quest_embeds(
            self.bot, interaction.guild_id, interaction.user.id,
        )
        await interaction.response.send_message(
            embeds=embeds, view=view, ephemeral=True,
        )

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
        quest = quest_by_id(str(pending[0]["quest_id"]))
        hint = lines[0]
        if quest is not None:
            from utils.quest_ui import QUEST_SHORTCUT_HINTS

            extra = QUEST_SHORTCUT_HINTS.get(quest.event)
            if extra:
                hint += f"\n\n{extra}"
        await interaction.response.send_message(hint, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Quests(bot))
