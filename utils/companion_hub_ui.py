"""Companion hub — roster embed, equip select, and unequip button.

Mirrors ``cogs/companions.py`` (`/companion status|equip|unequip`) using the
same ``bot.db`` companion methods.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from utils.companions import COMPANION_DEFINITIONS, companion_by_id
from utils.goon_theme import brand_color, branded_embed, panel_title
from utils.helpers import guild_only_message

if TYPE_CHECKING:
    from discord.ext import commands

logger = logging.getLogger(__name__)


async def build_companion_embed(
    cog: commands.Cog,
    guild_id: int,
    user_id: int,
    display_name: str,
) -> tuple[discord.Embed, list, str | None]:
    owned = await cog.bot.db.list_companions(user_id, guild_id)
    equipped = await cog.bot.db.get_equipped_companion_id(user_id, guild_id)

    embed = branded_embed(
        panel_title("Companion roster", member_name=display_name),
        color=brand_color(),
    )
    if not owned:
        embed.description = (
            "No henchlings warming your bed yet. Raid adds and vault clears can drop them."
        )
        return embed, [], equipped

    embed.description = f"**{len(owned)}/{len(COMPANION_DEFINITIONS)}** henchlings collected."
    for row in owned:
        cid = str(row["companion_id"])
        defn = companion_by_id(cid)
        if defn is None:
            continue
        mark = " **(active)**" if cid == equipped else ""
        embed.add_field(
            name=f"{defn.emoji} {defn.name}{mark}",
            value=f"_{defn.description}_",
            inline=False,
        )
    return embed, list(owned), equipped


class EquipCompanionSelect(discord.ui.Select):
    def __init__(self, owned: list, equipped: str | None) -> None:
        options = []
        for row in owned:
            cid = str(row["companion_id"])
            defn = companion_by_id(cid)
            if defn is None:
                continue
            options.append(
                discord.SelectOption(
                    label=defn.name,
                    value=cid,
                    description=defn.description[:100],
                    emoji=defn.emoji,
                    default=(cid == equipped),
                ),
            )
        if not options:
            options = [discord.SelectOption(label="No companions owned", value="_none")]
        super().__init__(placeholder="Equip a companion…", options=options, row=0, disabled=not owned)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: CompanionHubView = self.view  # type: ignore[assignment]
        if self.values[0] == "_none":
            await interaction.response.send_message("No companions to equip.", ephemeral=True)
            return
        companion_id = self.values[0]
        ok = await view.cog.bot.db.equip_companion(view.user_id, view.guild_id, companion_id)
        if not ok:
            await interaction.response.send_message("Companion not owned.", ephemeral=True)
            return
        defn = companion_by_id(companion_id)
        await _refresh_hub(
            interaction,
            view.cog,
            view.guild_id,
            view.user_id,
            note=f"Equipped **{defn.name if defn else companion_id}**.",
        )


class UnequipButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Unequip", style=discord.ButtonStyle.danger, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: CompanionHubView = self.view  # type: ignore[assignment]
        removed = await view.cog.bot.db.unequip_companion(view.user_id, view.guild_id)
        note = "Companion unequipped." if removed else "No companion active."
        await _refresh_hub(interaction, view.cog, view.guild_id, view.user_id, note=note)


class RefreshButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Refresh", style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: CompanionHubView = self.view  # type: ignore[assignment]
        await _refresh_hub(interaction, view.cog, view.guild_id, view.user_id)


async def _refresh_hub(
    interaction: discord.Interaction,
    cog: commands.Cog,
    guild_id: int,
    user_id: int,
    *,
    note: str | None = None,
) -> None:
    member = interaction.guild.get_member(user_id) if interaction.guild else None
    display_name = member.display_name if member else str(user_id)
    embed, owned, equipped = await build_companion_embed(cog, guild_id, user_id, display_name)
    if note:
        embed.description = f"{note}\n\n{embed.description}"
    view = CompanionHubView(cog, guild_id, user_id, owned, equipped)
    if interaction.response.is_done():
        await interaction.edit_original_response(embed=embed, view=view)
    else:
        await interaction.response.edit_message(embed=embed, view=view)


class CompanionHubView(discord.ui.View):
    def __init__(
        self,
        cog: commands.Cog,
        guild_id: int,
        user_id: int,
        owned: list,
        equipped: str | None,
    ) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.add_item(EquipCompanionSelect(owned, equipped))
        self.add_item(UnequipButton())
        self.add_item(RefreshButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your companion panel.", ephemeral=True)
            return False
        return True


async def send_companion_hub(cog: commands.Cog, interaction: discord.Interaction) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message(guild_only_message(), ephemeral=True)
        return
    guild_id = interaction.guild_id
    user_id = interaction.user.id
    try:
        embed, owned, equipped = await build_companion_embed(
            cog, guild_id, user_id, interaction.user.display_name,
        )
        view = CompanionHubView(cog, guild_id, user_id, owned, equipped)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    except Exception:
        logger.exception("Failed to open companion hub for user %s", user_id)
        if interaction.response.is_done():
            await interaction.followup.send(
                "Could not open the companion roster. Try again in a moment.", ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Could not open the companion roster. Try again in a moment.", ephemeral=True,
            )
