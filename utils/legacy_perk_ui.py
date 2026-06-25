"""UI for choosing business legacy perks at prestige 10+."""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from utils.legacy_perks import LEGACY_PERKS, legacy_perk_by_id

if TYPE_CHECKING:
    from discord.ext import commands


def build_legacy_pick_embed(owned: set[str]) -> discord.Embed:
    embed = discord.Embed(
        title="⭐ Choose a Legacy Perk",
        description=(
            "Your empire has reached legendary status. Pick a **permanent** legacy perk "
            "(one per legendary reset)."
        ),
        color=discord.Color.purple(),
    )
    for perk in LEGACY_PERKS:
        status = "✅ Owned" if perk.perk_id in owned else perk.description
        embed.add_field(
            name=f"{perk.emoji} {perk.name}",
            value=status,
            inline=False,
        )
    return embed


class LegacyPerkView(discord.ui.View):
    def __init__(
        self,
        cog: commands.Cog,
        guild_id: int,
        user_id: int,
        owned: set[str],
    ) -> None:
        super().__init__(timeout=120.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        for perk in LEGACY_PERKS:
            if perk.perk_id not in owned:
                self.add_item(LegacyPickButton(cog, guild_id, user_id, perk.perk_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your panel.", ephemeral=True)
            return False
        return True


class LegacyPickButton(discord.ui.Button):
    def __init__(
        self,
        cog: commands.Cog,
        guild_id: int,
        user_id: int,
        perk_id: str,
    ) -> None:
        perk = legacy_perk_by_id(perk_id)
        super().__init__(
            label=perk.name if perk else perk_id,
            emoji=perk.emoji if perk else None,
            style=discord.ButtonStyle.success,
        )
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.perk_id = perk_id

    async def callback(self, interaction: discord.Interaction) -> None:
        err = await self.cog.bot.db.grant_legacy_perk(
            self.user_id, self.guild_id, self.perk_id,
        )
        if err:
            await interaction.response.send_message(
                "Could not grant that perk.", ephemeral=True,
            )
            return
        perk = legacy_perk_by_id(self.perk_id)
        await interaction.response.edit_message(
            content=f"⭐ Legacy perk unlocked: **{perk.name if perk else self.perk_id}** — {perk.description if perk else ''}",
            embed=None,
            view=None,
        )
