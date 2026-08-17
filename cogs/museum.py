from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import guild_only_message
from utils.museum_hub_ui import send_museum_hub


class Museum(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="museum", description="View your Goon Museum collection progress.")
    @app_commands.guild_only()
    async def museum(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        await send_museum_hub(self, interaction)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Museum(bot))
