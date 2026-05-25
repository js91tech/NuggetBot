from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import guild_only_message
from utils.player_status import build_status_fields


class Status(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="status",
        description="Wallet, daily, energy, restrictions, virus, and duel limits.",
    )
    @app_commands.describe(
        user="Player to inspect. Defaults to you.",
        public="Show in the channel instead of privately",
    )
    @app_commands.guild_only()
    async def status(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
        public: bool = False,
    ) -> None:
        if interaction.guild_id is None or interaction.guild is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        target = user or interaction.user
        fields = await build_status_fields(
            self.bot.db,
            user_id=target.id,
            guild_id=interaction.guild_id,
            guild=interaction.guild,
        )
        embed = discord.Embed(
            title=f"{target.display_name}'s status",
            color=discord.Color.blurple(),
        )
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
        embed.set_footer(text="/quests · /boss · /stats · /balance")

        ephemeral = not (public and target.id == interaction.user.id)
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Status(bot))
