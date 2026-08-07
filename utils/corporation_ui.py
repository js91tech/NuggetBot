"""UI for corporate (crew) upgrades, projects, and war standings."""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from utils.corporations import (
    CORPORATE_PROJECTS,
    CORPORATE_UPGRADES,
    project_by_id,
    upgrade_by_id,
    upgrade_cost,
)
from utils.helpers import fmt_amount
from utils.quests import record_quest_event

if TYPE_CHECKING:
    from discord.ext import commands


async def build_corporate_upgrade_embed(
    cog: commands.Cog, guild_id: int, crew_name: str,
) -> discord.Embed:
    levels = await cog.bot.db.get_corporate_upgrades(guild_id, crew_name)
    stats = await cog.bot.db.get_crew_stats(guild_id, crew_name)
    treasury = float(stats["treasury"]) if stats else 0.0
    embed = discord.Embed(
        title=f"🏢 {crew_name} — Corporate Upgrades",
        description=f"Funded from the corporate vault (**{fmt_amount(treasury)}**).",
        color=discord.Color.blurple(),
    )
    for defn in CORPORATE_UPGRADES:
        level = levels.get(defn.upgrade_id, 0)
        cost = upgrade_cost(level)
        embed.add_field(
            name=f"{defn.emoji} {defn.name} — Lv {level}",
            value=f"{defn.description}\nNext level: **{fmt_amount(cost)}**",
            inline=False,
        )
    return embed


class CorporateUpgradeButton(discord.ui.Button):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int, upgrade_id: str, label: str) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.upgrade_id = upgrade_id

    async def callback(self, interaction: discord.Interaction) -> None:
        cost, err = await self.cog.bot.db.buy_corporate_upgrade(
            self.user_id, self.guild_id, self.upgrade_id,
        )
        messages = {
            "not_in_crew": "Join a corporation (crew) first.",
            "invalid_upgrade": "Unknown upgrade.",
            "max_level": "That division is already maxed.",
            "insufficient_treasury": f"The corporate vault needs **{fmt_amount(cost)}**.",
        }
        if err:
            await interaction.response.send_message(messages.get(err, err), ephemeral=True)
            return
        await record_quest_event(self.cog.bot.db, self.guild_id, self.user_id, "corp_upgrade")
        crew = await self.cog.bot.db.get_crew_membership(self.user_id, self.guild_id)
        defn = upgrade_by_id(self.upgrade_id)
        view = CorporateUpgradeView(self.cog, self.guild_id, self.user_id)
        embed = await build_corporate_upgrade_embed(self.cog, self.guild_id, crew)
        extra = ""
        if self.upgrade_id == "income":
            extra = " Member business income (see **/business info**) is now higher."
        elif self.upgrade_id == "defense":
            extra = " Member security rating vs attacks is now higher."
        elif self.upgrade_id == "territory":
            extra = " District influence purchases and Market Expansion now grant more points."
        embed.description = (
            f"✅ Upgraded **{defn.name if defn else self.upgrade_id}** for "
            f"**{fmt_amount(cost)}** from the vault.{extra}"
        ) + "\n" + (embed.description or "")
        await interaction.response.edit_message(embed=embed, view=view)


class CorporateUpgradeView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.user_id = user_id
        for defn in CORPORATE_UPGRADES:
            self.add_item(
                CorporateUpgradeButton(
                    cog, guild_id, user_id, defn.upgrade_id, f"{defn.emoji} {defn.name}",
                ),
            )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your panel.", ephemeral=True)
            return False
        return True


def _progress_bar(funded: float, target: float, *, length: int = 12) -> str:
    if target <= 0:
        return "░" * length
    filled = int(round((min(funded, target) / target) * length))
    filled = max(0, min(length, filled))
    return "█" * filled + "░" * (length - filled)


async def build_project_embed(
    cog: commands.Cog, guild_id: int, crew_name: str,
) -> discord.Embed:
    progress = await cog.bot.db.list_corporate_projects(guild_id, crew_name)
    embed = discord.Embed(
        title=f"🏗️ {crew_name} — Corporate Projects",
        description="Large multi-member goals. Contribute nuggets to complete them for a treasury windfall.",
        color=discord.Color.gold(),
    )
    for defn in CORPORATE_PROJECTS:
        state = progress.get(defn.project_id, {})
        funded = float(state.get("funded_amount", 0.0))
        completed = state.get("completed_at") is not None
        status = "✅ **Completed**" if completed else f"{int((funded / defn.target_amount) * 100)}%"
        embed.add_field(
            name=f"{defn.emoji} {defn.name} — {status}",
            value=(
                f"`{_progress_bar(funded, defn.target_amount)}`\n"
                f"{fmt_amount(funded)} / {fmt_amount(defn.target_amount)} · "
                f"Reward {fmt_amount(defn.reward_treasury)}"
            ),
            inline=False,
        )
    return embed


class ContributeModal(discord.ui.Modal, title="Fund project"):
    amount = discord.ui.TextInput(label="Amount to contribute", placeholder="e.g. 10000", required=True, max_length=16)

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
        result = await self.cog.bot.db.contribute_to_corporate_project(
            self.user_id, self.guild_id, self.project_id, value,
        )
        if result.get("error"):
            messages = {
                "not_in_crew": "Join a corporation first.",
                "invalid_project": "Unknown project.",
                "already_complete": "That project is already complete.",
                "insufficient_funds": f"You need **{fmt_amount(float(result.get('needed', 0)))}**.",
            }
            await interaction.response.send_message(
                messages.get(str(result["error"]), "Could not contribute."), ephemeral=True,
            )
            return
        await record_quest_event(self.cog.bot.db, self.guild_id, self.user_id, "corp_project")
        crew = result["crew"]
        defn = project_by_id(self.project_id)
        view = CorporateProjectView(self.cog, self.guild_id, self.user_id)
        embed = await build_project_embed(self.cog, self.guild_id, str(crew))
        if result.get("completed"):
            embed.description = (
                f"🎉 **{defn.name if defn else 'Project'}** completed! "
                f"The corporate vault gains **{fmt_amount(float(result['reward']))}**."
            )
        else:
            embed.description = (
                f"📈 Contributed **{fmt_amount(float(result['contribution']))}** to "
                f"**{defn.name if defn else 'the project'}**."
            )
        await interaction.response.edit_message(embed=embed, view=view)


class ProjectSelect(discord.ui.Select):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        options = [
            discord.SelectOption(
                label=defn.name,
                value=defn.project_id,
                description=f"Goal {fmt_amount(defn.target_amount)}"[:100],
                emoji=defn.emoji,
            )
            for defn in CORPORATE_PROJECTS
        ]
        super().__init__(placeholder="Fund a project…", options=options)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            ContributeModal(self.cog, self.guild_id, self.user_id, self.values[0]),
        )


class CorporateProjectView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.user_id = user_id
        self.add_item(ProjectSelect(cog, guild_id, user_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your panel.", ephemeral=True)
            return False
        return True


async def build_war_standings_embed(
    cog: commands.Cog, guild: discord.Guild,
) -> discord.Embed:
    standings = await cog.bot.db.get_corporate_war_standings(guild.id, limit=10)
    embed = discord.Embed(
        title="⚔️ Corporate War — Live Standings",
        description="Weekly score = corporate vault + territory holdings. Top corp earns a treasury bonus.",
        color=discord.Color.dark_gold(),
    )
    if not standings:
        embed.add_field(name="No corporations yet", value="Found a crew with `/crew panel`.", inline=False)
        return embed
    lines = [
        f"**{i}. {name}** — {fmt_amount(score)}"
        for i, (name, score) in enumerate(standings, 1)
    ]
    embed.add_field(name="Ranking", value="\n".join(lines), inline=False)
    return embed
