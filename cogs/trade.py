"""Player trade commands."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from utils.bot_players import pvp_target_error
from utils.helpers import guild_only_message
from utils.trade_ui import open_trade_panel


class Trade(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="trade", description="Send a trade offer (nuggets, gear, stash drugs).")
    @app_commands.describe(user="Player to trade with")
    @app_commands.guild_only()
    async def trade(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        err = pvp_target_error(user, interaction.user.id)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        if await self.bot.db.is_restricted(interaction.user.id, interaction.guild_id):
            await interaction.response.send_message(
                "You cannot trade right now.", ephemeral=True,
            )
            return
        await open_trade_panel(self, interaction, user)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Trade(bot))
