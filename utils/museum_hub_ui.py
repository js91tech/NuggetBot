"""Museum hub — trophy wall progress with a one-button refresh.

Mirrors ``cogs/museum.py`` (`/museum`) so the panel and slash command share
the same completion math.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from utils.goon_theme import brand_color, branded_embed, panel_title
from utils.helpers import guild_only_message
from utils.museum import (
    CATEGORY_TOTALS,
    MUSEUM_BONUS_TIERS,
    museum_bonuses_for_pct,
    museum_completion_pct,
)

if TYPE_CHECKING:
    from discord.ext import commands

logger = logging.getLogger(__name__)


async def build_museum_embed(
    cog: commands.Cog,
    guild_id: int,
    user_id: int,
    display_name: str,
) -> discord.Embed:
    counts = await cog.bot.db.get_museum_counts(user_id, guild_id)
    pct = museum_completion_pct(counts)
    income, damage, title = museum_bonuses_for_pct(pct)

    embed = branded_embed(
        panel_title("Trophy Room", member_name=display_name),
        description=(
            f"**{pct:.1f}%** complete · Curator title: **{title}**\n"
            f"Bonuses: **{(income - 1) * 100:.1f}%** income · **{(damage - 1) * 100:.1f}%** damage"
        ),
        color=brand_color(),
    )
    lines = []
    for cat, cap in CATEGORY_TOTALS.items():
        have = min(int(counts.get(cat, 0)), cap)
        lines.append(f"**{cat.title()}** — {have}/{cap}")
    embed.add_field(name="Collection", value="\n".join(lines), inline=False)
    tier_lines = [f"• {t.label} ({t.pct_required:.0f}%)" for t in MUSEUM_BONUS_TIERS]
    embed.add_field(name="Tiers", value="\n".join(tier_lines), inline=False)
    return embed


class RefreshButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Refresh", style=discord.ButtonStyle.secondary, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: MuseumHubView = self.view  # type: ignore[assignment]
        member = interaction.guild.get_member(view.user_id) if interaction.guild else None
        display_name = member.display_name if member else str(view.user_id)
        embed = await build_museum_embed(view.cog, view.guild_id, view.user_id, display_name)
        await interaction.response.edit_message(embed=embed, view=view)


class MuseumHubView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.add_item(RefreshButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your museum panel.", ephemeral=True)
            return False
        return True


async def send_museum_hub(cog: commands.Cog, interaction: discord.Interaction) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message(guild_only_message(), ephemeral=True)
        return
    guild_id = interaction.guild_id
    user_id = interaction.user.id
    try:
        embed = await build_museum_embed(cog, guild_id, user_id, interaction.user.display_name)
        view = MuseumHubView(cog, guild_id, user_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    except Exception:
        logger.exception("Failed to open museum hub for user %s", user_id)
        if interaction.response.is_done():
            await interaction.followup.send(
                "Could not open the trophy room. Try again in a moment.", ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Could not open the trophy room. Try again in a moment.", ephemeral=True,
            )
