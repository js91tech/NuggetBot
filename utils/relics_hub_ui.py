"""Relics hub — vault listing, equip-by-instance select, and unequip.

Mirrors ``cogs/relics.py`` (`/relics list|equip|unequip`) using the same
``bot.db`` relic methods, so the slash commands stay in sync.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from utils.goon_theme import brand_color, branded_embed, panel_title
from utils.helpers import guild_only_message
from utils.relics import relic_by_id

if TYPE_CHECKING:
    from discord.ext import commands

logger = logging.getLogger(__name__)


async def build_relics_embed(
    cog: commands.Cog,
    guild_id: int,
    user_id: int,
    display_name: str,
) -> tuple[discord.Embed, list, int | None]:
    rows = await cog.bot.db.list_relic_instances(user_id, guild_id)
    equipped = await cog.bot.db.get_equipped_relic_row(user_id, guild_id)
    eq_id = int(equipped["instance_id"]) if equipped else None

    embed = branded_embed(
        panel_title("Relic vault", member_name=display_name),
        color=brand_color(),
    )
    if not rows:
        embed.description = (
            "The vault is empty. Mythic bosses, vault clears, and expeditions can drop relics."
        )
        return embed, [], eq_id

    embed.description = f"**{len(rows)}** relic{'s' if len(rows) != 1 else ''} collected."
    for row in rows:
        defn = relic_by_id(str(row["relic_id"]))
        if defn is None:
            continue
        mark = " **(equipped)**" if eq_id == int(row["instance_id"]) else ""
        embed.add_field(
            name=f"{defn.emoji} {defn.name} (#{row['instance_id']}){mark}",
            value=f"_{defn.description}_",
            inline=False,
        )
    return embed, list(rows), eq_id


class EquipRelicSelect(discord.ui.Select):
    def __init__(self, rows: list, equipped_id: int | None) -> None:
        options = []
        for row in rows:
            defn = relic_by_id(str(row["relic_id"]))
            if defn is None:
                continue
            instance_id = int(row["instance_id"])
            options.append(
                discord.SelectOption(
                    label=f"{defn.name} (#{instance_id})",
                    value=str(instance_id),
                    description=defn.description[:100],
                    emoji=defn.emoji,
                    default=(instance_id == equipped_id),
                ),
            )
        if not options:
            options = [discord.SelectOption(label="No relics owned", value="_none")]
        super().__init__(placeholder="Equip a relic by instance id…", options=options, row=0, disabled=not rows)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: RelicsHubView = self.view  # type: ignore[assignment]
        if self.values[0] == "_none":
            await interaction.response.send_message("No relics to equip.", ephemeral=True)
            return
        instance_id = int(self.values[0])
        ok = await view.cog.bot.db.equip_relic_instance(view.user_id, view.guild_id, instance_id)
        if not ok:
            await interaction.response.send_message("Relic not found.", ephemeral=True)
            return
        await _refresh_hub(
            interaction,
            view.cog,
            view.guild_id,
            view.user_id,
            note=f"Equipped relic **#{instance_id}**.",
        )


class UnequipRelicButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Unequip", style=discord.ButtonStyle.danger, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: RelicsHubView = self.view  # type: ignore[assignment]
        removed = await view.cog.bot.db.unequip_relic(view.user_id, view.guild_id)
        note = "Relic unequipped." if removed else "No relic equipped."
        await _refresh_hub(interaction, view.cog, view.guild_id, view.user_id, note=note)


class RefreshButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Refresh", style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: RelicsHubView = self.view  # type: ignore[assignment]
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
    embed, rows, eq_id = await build_relics_embed(cog, guild_id, user_id, display_name)
    if note:
        embed.description = f"{note}\n\n{embed.description}"
    view = RelicsHubView(cog, guild_id, user_id, rows, eq_id)
    if interaction.response.is_done():
        await interaction.edit_original_response(embed=embed, view=view)
    else:
        await interaction.response.edit_message(embed=embed, view=view)


class RelicsHubView(discord.ui.View):
    def __init__(
        self,
        cog: commands.Cog,
        guild_id: int,
        user_id: int,
        rows: list,
        equipped_id: int | None,
    ) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.add_item(EquipRelicSelect(rows, equipped_id))
        self.add_item(UnequipRelicButton())
        self.add_item(RefreshButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your relic panel.", ephemeral=True)
            return False
        return True


async def send_relics_hub(cog: commands.Cog, interaction: discord.Interaction) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message(guild_only_message(), ephemeral=True)
        return
    guild_id = interaction.guild_id
    user_id = interaction.user.id
    try:
        embed, rows, eq_id = await build_relics_embed(
            cog, guild_id, user_id, interaction.user.display_name,
        )
        view = RelicsHubView(cog, guild_id, user_id, rows, eq_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    except Exception:
        logger.exception("Failed to open relics hub for user %s", user_id)
        if interaction.response.is_done():
            await interaction.followup.send(
                "Could not open the relic vault. Try again in a moment.", ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Could not open the relic vault. Try again in a moment.", ephemeral=True,
            )
