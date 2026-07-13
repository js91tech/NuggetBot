from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import guild_only_message
from utils.museum import CATEGORY_TOTALS, MUSEUM_BONUS_TIERS, museum_bonuses_for_pct, museum_completion_pct


class Museum(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="museum", description="View your Nugget Museum collection progress.")
    @app_commands.guild_only()
    async def museum(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        counts = await self.bot.db.get_museum_counts(interaction.user.id, interaction.guild_id)
        pct = museum_completion_pct(counts)
        income, damage, title = museum_bonuses_for_pct(pct)
        lines = []
        for cat, cap in CATEGORY_TOTALS.items():
            have = min(int(counts.get(cat, 0)), cap)
            lines.append(f"**{cat.title()}** — {have}/{cap}")
        tier_lines = [f"• {t.label} ({t.pct_required:.0f}%)" for t in MUSEUM_BONUS_TIERS]
        await interaction.response.send_message(
            f"**Nugget Museum** — {pct:.1f}% complete · Title: **{title}**\n"
            f"Bonuses: **{(income - 1) * 100:.1f}%** income · **{(damage - 1) * 100:.1f}%** damage\n\n"
            + "\n".join(lines)
            + "\n\n**Tiers**\n" + "\n".join(tier_lines),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Museum(bot))
