from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import guild_only_message
from utils.relics_hub_ui import send_relics_hub


class Relics(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    relics_group = app_commands.Group(
        name="relics",
        description="View and equip raid relics.",
        guild_only=True,
    )

    @relics_group.command(name="list", description="View collected relics.")
    async def relics_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        await send_relics_hub(self, interaction)

    @relics_group.command(name="equip", description="Equip a relic by instance id.")
    @app_commands.describe(instance_id="Relic instance id from /relics list")
    async def relics_equip(self, interaction: discord.Interaction, instance_id: int) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        ok = await self.bot.db.equip_relic_instance(
            interaction.user.id, interaction.guild_id, instance_id,
        )
        if not ok:
            await interaction.response.send_message("Relic not found.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Equipped relic **#{instance_id}**.", ephemeral=True,
        )

    @relics_group.command(name="unequip", description="Unequip your relic.")
    async def relics_unequip(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if await self.bot.db.unequip_relic(interaction.user.id, interaction.guild_id):
            await interaction.response.send_message("Relic unequipped.", ephemeral=True)
        else:
            await interaction.response.send_message("No relic equipped.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Relics(bot))
