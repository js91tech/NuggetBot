from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from utils.blueprints import BLUEPRINT_DEFINITIONS, blueprint_by_id
from utils.helpers import guild_only_message


class Codex(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="codex", description="View unlocked blueprints and crafting unlocks.")
    @app_commands.guild_only()
    async def codex(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        unlocked = {
            str(r["blueprint_id"])
            for r in await self.bot.db.list_blueprints(interaction.user.id, interaction.guild_id)
        }
        lines = []
        for bp in BLUEPRINT_DEFINITIONS.values():
            mark = "✅" if bp.blueprint_id in unlocked else "🔒"
            lines.append(
                f"{mark} **{bp.name}** ({bp.category})\n"
                f"_{bp.description}_ · Hint: {bp.unlock_hint}"
            )
        await interaction.response.send_message(
            f"**Codex** — {len(unlocked)}/{len(BLUEPRINT_DEFINITIONS)} unlocked\n\n"
            + "\n\n".join(lines),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Codex(bot))
