"""UI for post-mega empire acquisitions."""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from utils.empire_acquisitions import ACQUISITIONS, acquisition_by_id
from utils.helpers import fmt_amount

if TYPE_CHECKING:
    from discord.ext import commands


def build_acquisitions_embed(
    owned: set[str],
    *,
    megas_complete: bool,
) -> discord.Embed:
    embed = discord.Embed(
        title="🏛️ Empire Acquisitions",
        description=(
            "After completing all mega projects, acquire sub-empires for unique passive perks."
        ),
        color=discord.Color.dark_gold(),
    )
    if not megas_complete:
        embed.description += "\n\n⚠️ Complete all **mega projects** first to unlock acquisitions."
    for acq in ACQUISITIONS:
        status = "✅ Owned" if acq.acquisition_id in owned else fmt_amount(acq.cost)
        embed.add_field(
            name=f"{acq.emoji} {acq.name}",
            value=f"{acq.perk_label}\nCost: **{status}**",
            inline=False,
        )
    return embed


class AcquisitionView(discord.ui.View):
    def __init__(
        self,
        cog: commands.Cog,
        guild_id: int,
        user_id: int,
        *,
        owned: set[str],
        megas_complete: bool,
    ) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        if megas_complete:
            for acq in ACQUISITIONS:
                if acq.acquisition_id not in owned:
                    self.add_item(AcquisitionBuyButton(cog, guild_id, user_id, acq.acquisition_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your panel.", ephemeral=True)
            return False
        return True


class AcquisitionBuyButton(discord.ui.Button):
    def __init__(
        self,
        cog: commands.Cog,
        guild_id: int,
        user_id: int,
        acquisition_id: str,
    ) -> None:
        acq = acquisition_by_id(acquisition_id)
        label = acq.name[:80] if acq else acquisition_id
        super().__init__(label=f"Buy {label}", style=discord.ButtonStyle.primary)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.acquisition_id = acquisition_id

    async def callback(self, interaction: discord.Interaction) -> None:
        result = await self.cog.bot.db.purchase_empire_acquisition(
            self.user_id, self.guild_id, self.acquisition_id,
        )
        if result.get("error"):
            err = str(result["error"])
            if err == "insufficient_funds":
                msg = f"You need **{fmt_amount(float(result.get('cost', 0)))}**."
            elif err == "megas_incomplete":
                msg = "Complete all mega projects first."
            elif err == "already_owned":
                msg = "You already own this acquisition."
            else:
                msg = "Could not purchase."
            await interaction.response.send_message(msg, ephemeral=True)
            return
        acq = acquisition_by_id(self.acquisition_id)
        owned = await self.cog.bot.db.list_empire_acquisitions(self.user_id, self.guild_id)
        embed = build_acquisitions_embed(owned, megas_complete=True)
        view = AcquisitionView(
            self.cog, self.guild_id, self.user_id, owned=owned, megas_complete=True,
        )
        name = acq.name if acq else self.acquisition_id
        embed.description = f"✅ Acquired **{name}**!"
        await interaction.response.edit_message(embed=embed, view=view)


async def send_acquisition_panel(interaction: discord.Interaction, cog: commands.Cog) -> None:
    from utils.mega_projects import MEGA_PROJECTS

    user_id = interaction.user.id
    guild_id = interaction.guild_id
    assert guild_id is not None
    owned = await cog.bot.db.list_empire_acquisitions(user_id, guild_id)
    megas = await cog.bot.db.list_user_mega_projects(user_id, guild_id)
    megas_complete = all(
        pid in megas and megas[pid].get("completed_at")
        for pid in (p.project_id for p in MEGA_PROJECTS)
    )
    embed = build_acquisitions_embed(owned, megas_complete=megas_complete)
    view = AcquisitionView(
        cog, guild_id, user_id, owned=owned, megas_complete=megas_complete,
    )
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
