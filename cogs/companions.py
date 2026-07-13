from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from utils.companions import COMPANION_DEFINITIONS, companion_by_id
from utils.helpers import guild_only_message


class Companions(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="companion", description="View and manage your henchling companion.")
    @app_commands.describe(action="List, equip, or unequip", companion_id="Companion id")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Status", value="status"),
            app_commands.Choice(name="Equip", value="equip"),
            app_commands.Choice(name="Unequip", value="unequip"),
        ],
    )
    @app_commands.guild_only()
    async def companion(
        self,
        interaction: discord.Interaction,
        action: str,
        companion_id: str | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        uid = interaction.user.id
        gid = interaction.guild_id

        if action == "status":
            owned = await self.bot.db.list_companions(uid, gid)
            equipped = await self.bot.db.get_equipped_companion_id(uid, gid)
            if not owned:
                await interaction.response.send_message(
                    "No henchlings yet. Raid adds and vault clears can drop them.",
                    ephemeral=True,
                )
                return
            lines = []
            for row in owned:
                cid = str(row["companion_id"])
                defn = companion_by_id(cid)
                if defn is None:
                    continue
                mark = " **(active)**" if cid == equipped else ""
                lines.append(f"{defn.emoji} **{defn.name}**{mark} — _{defn.description}_")
            await interaction.response.send_message(
                f"**Companion Dex** ({len(owned)}/{len(COMPANION_DEFINITIONS)})\n\n" + "\n".join(lines),
                ephemeral=True,
            )
            return

        if action == "equip":
            if not companion_id:
                await interaction.response.send_message("Provide a companion id.", ephemeral=True)
                return
            if not await self.bot.db.equip_companion(uid, gid, companion_id):
                await interaction.response.send_message("Companion not owned.", ephemeral=True)
                return
            defn = companion_by_id(companion_id)
            await interaction.response.send_message(
                f"Equipped **{defn.name if defn else companion_id}**.", ephemeral=True,
            )
            return

        if action == "unequip":
            if await self.bot.db.unequip_companion(uid, gid):
                await interaction.response.send_message("Companion unequipped.", ephemeral=True)
            else:
                await interaction.response.send_message("No companion active.", ephemeral=True)
            return

        await interaction.response.send_message("Unknown action.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Companions(bot))
