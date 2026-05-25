from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import guild_only_message


class Help(commands.Cog):
    SECTIONS: tuple[tuple[str, str], ...] = (
        (
            "Economy",
            "`/daily` `/balance` `/pay` `/leaderboard` `/status`",
        ),
        (
            "Shop & gear",
            "`/shop` `/buy` `/sell` `/inventory` `/equip` `/unequip` `/stats`",
        ),
        (
            "Jobs & classes",
            "`/jobs` `/work` `/energy` `/class` `/class-choose` `/class-evolve` `/mana` `/skills` `/cast`",
        ),
        (
            "Boss raids",
            "`/boss` `/attack` `/heal` `/raid-leaderboard`",
        ),
        (
            "Crime & virus",
            "`/heist` `/arrest` `/bounty` `/bounties` `/hack` `/transfer` `/virus`",
        ),
        (
            "PvP & casino",
            "`/duel` `/coinflip` `/coinflip-duel` `/blackjack`",
        ),
        (
            "Progression",
            "`/quests` `/quest-hint` `/craft` `/achievements` `/hall-of-fame` `/aspects`",
        ),
        (
            "Fun",
            "`/trivia`",
        ),
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="Command cheat sheet by category.")
    @app_commands.guild_only()
    async def help_cmd(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        embed = discord.Embed(
            title="NuggetBot commands",
            description="Use slash commands in this server. Admins also have `/config` and dashboard tools.",
            color=discord.Color.gold(),
        )
        for name, commands_text in self.SECTIONS:
            embed.add_field(name=name, value=commands_text, inline=False)
        embed.set_footer(text="Tip: /status shows timers · /stats shows combat sheet")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
