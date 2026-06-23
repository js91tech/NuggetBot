"""UI for personal mega projects (endgame funding goals)."""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from utils.helpers import fmt_amount
from utils.mega_projects import MEGA_PROJECTS, mega_project_by_id
from utils.quests import record_quest_event

if TYPE_CHECKING:
    from discord.ext import commands


def _bar(funded: float, target: float, *, length: int = 12) -> str:
    if target <= 0:
        return "░" * length
    filled = int(round((min(funded, target) / target) * length))
    filled = max(0, min(length, filled))
    return "█" * filled + "░" * (length - filled)


async def build_mega_embed(cog: commands.Cog, guild_id: int, user_id: int) -> discord.Embed:
    progress = await cog.bot.db.list_user_mega_projects(user_id, guild_id)
    embed = discord.Embed(
        title="🌐 Mega Projects",
        description=(
            "Colossal personal endgame goals. Completing one grants a **permanent** "
            "business income bonus."
        ),
        color=discord.Color.dark_purple(),
    )
    for defn in MEGA_PROJECTS:
        state = progress.get(defn.project_id, {})
        funded = float(state.get("funded_amount", 0.0))
        completed = state.get("completed_at") is not None
        status = "✅ **Complete**" if completed else f"{int((funded / defn.cost) * 100)}%"
        embed.add_field(
            name=f"{defn.emoji} {defn.name} — {status}",
            value=(
                f"`{_bar(funded, defn.cost)}`\n"
                f"{fmt_amount(funded)} / {fmt_amount(defn.cost)}\n"
                f"_{defn.reward_label}_"
            ),
            inline=False,
        )
    return embed


class MegaContributeModal(discord.ui.Modal, title="Fund mega project"):
    amount = discord.ui.TextInput(label="Amount to contribute", placeholder="e.g. 1000000", required=True, max_length=20)

    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int, project_id: str) -> None:
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.project_id = project_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            value = float(str(self.amount.value).replace(",", "").strip())
        except ValueError:
            await interaction.response.send_message("Enter a valid number.", ephemeral=True)
            return
        if value <= 0:
            await interaction.response.send_message("Enter a positive amount.", ephemeral=True)
            return
        result = await self.cog.bot.db.contribute_to_mega_project(
            self.user_id, self.guild_id, self.project_id, value,
        )
        if result.get("error"):
            messages = {
                "invalid_project": "Unknown project.",
                "already_complete": "That project is already complete.",
                "insufficient_funds": f"You need **{fmt_amount(float(result.get('needed', 0)))}**.",
            }
            await interaction.response.send_message(
                messages.get(str(result["error"]), "Could not contribute."), ephemeral=True,
            )
            return
        await record_quest_event(self.cog.bot.db, self.guild_id, self.user_id, "mega_project")
        defn = mega_project_by_id(self.project_id)
        view = MegaProjectView(self.cog, self.guild_id, self.user_id)
        embed = await build_mega_embed(self.cog, self.guild_id, self.user_id)
        if result.get("completed"):
            bonus = int(float(result["income_bonus"]) * 100)
            embed.description = (
                f"🎉 **{defn.name if defn else 'Project'}** complete! Permanent "
                f"**+{bonus}%** business income unlocked."
            )
        else:
            embed.description = (
                f"📈 Contributed **{fmt_amount(float(result['contribution']))}** to "
                f"**{defn.name if defn else 'the project'}**."
            )
        await interaction.response.edit_message(embed=embed, view=view)


class MegaSelect(discord.ui.Select):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        options = [
            discord.SelectOption(
                label=defn.name,
                value=defn.project_id,
                description=f"Goal {fmt_amount(defn.cost)}"[:100],
                emoji=defn.emoji,
            )
            for defn in MEGA_PROJECTS
        ]
        super().__init__(placeholder="Fund a mega project…", options=options)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            MegaContributeModal(self.cog, self.guild_id, self.user_id, self.values[0]),
        )


class MegaProjectView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.user_id = user_id
        self.add_item(MegaSelect(cog, guild_id, user_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your panel.", ephemeral=True)
            return False
        return True


async def send_mega_project_panel(interaction: discord.Interaction, cog: commands.Cog) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return
    if not interaction.response.is_done():
        await interaction.response.defer()
    embed = await build_mega_embed(cog, interaction.guild_id, interaction.user.id)
    view = MegaProjectView(cog, interaction.guild_id, interaction.user.id)
    await interaction.followup.send(embed=embed, view=view)
