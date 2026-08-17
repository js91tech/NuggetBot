from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import guild_only_message
from utils.profile_hub_ui import send_profile_hub


class Profile(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="profile", description="Open your profile hub — vault, gear, jobs, and more.")
    @app_commands.describe(user="Player to inspect (defaults to you)")
    @app_commands.guild_only()
    async def profile(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        target = user or interaction.user
        await send_profile_hub(self, interaction, target)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Profile(bot))
