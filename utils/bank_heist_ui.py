from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING

import discord

import config
from utils.bot_players import pvp_target_error
from utils.helpers import fmt_amount

if TYPE_CHECKING:
    from cogs.heist import Heist


def tier_summary(tier: int) -> str:
    spec = config.BANK_HEIST_TIERS[tier]
    success = int(round(float(spec["success"]) * 100))
    loot = int(round(float(spec["loot_fraction"]) * 100))
    jail_m = int(float(spec["jail_seconds"]) // 60)
    extra = ""
    if tier == 3:
        unstable = int(round(float(spec.get("unstable_chance", 0)) * 100))
        extra = f" · Fail: **{unstable}%** gear unstable"
    return f"**{success}%** success · Steal **{loot}%** of bank · Fail jail **{jail_m}m**{extra}"


def build_bank_heist_embed(
    target: discord.Member,
    *,
    target_bank: float,
    cooldown_left: float = 0.0,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"Bank heist — {target.display_name}",
        description=(
            f"Target vault: **{fmt_amount(target_bank)}** in bank\n\n"
            f"**Tier 1** — {tier_summary(1)}\n"
            f"**Tier 2** — {tier_summary(2)}\n"
            f"**Tier 3** — {tier_summary(3)}"
        ),
        color=discord.Color.dark_red(),
    )
    if cooldown_left > 0:
        embed.set_footer(text=f"Cooldown: {int(cooldown_left // 60) + 1}m remaining")
    else:
        embed.set_footer(text="Pick a tier · Pocket heists use /heist · Bank is the target here")
    return embed


class BankHeistView(discord.ui.View):
    def __init__(
        self,
        cog: Heist,
        guild_id: int,
        user_id: int,
        target_id: int,
    ) -> None:
        super().__init__(timeout=120.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.target_id = target_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your heist panel.", ephemeral=True,
            )
            return False
        return True

    async def _run_tier(self, interaction: discord.Interaction, tier: int) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Guild only.", ephemeral=True)
            return
        target = interaction.guild.get_member(self.target_id)
        if target is None:
            await interaction.response.send_message("Target left the server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        result = await self.cog.execute_bank_heist(
            interaction.user,
            target,
            interaction.guild,
            tier=tier,
        )
        if result.error:
            await interaction.followup.send(result.error, ephemeral=True)
            return
        for child in self.children:
            child.disabled = True
        if result.embed is not None:
            await interaction.edit_original_response(embed=result.embed, view=self)
        if result.message:
            await interaction.followup.send(result.message, ephemeral=True)

    @discord.ui.button(label="Tier 1", style=discord.ButtonStyle.secondary, row=0)
    async def tier1(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._run_tier(interaction, 1)

    @discord.ui.button(label="Tier 2", style=discord.ButtonStyle.primary, row=0)
    async def tier2(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._run_tier(interaction, 2)

    @discord.ui.button(label="Tier 3", style=discord.ButtonStyle.danger, row=0)
    async def tier3(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._run_tier(interaction, 3)


async def send_bank_heist_panel(
    interaction: discord.Interaction,
    cog: Heist,
    target: discord.Member,
) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return
    target_err = pvp_target_error(target, interaction.user.id)
    if target_err:
        await interaction.response.send_message(target_err, ephemeral=True)
        return
    if await cog.bot.db.is_restricted(interaction.user.id, interaction.guild_id):
        await interaction.response.send_message(
            "You cannot run a bank heist right now.", ephemeral=True,
        )
        return

    target_bank = await cog.bot.db.get_bank(target.id, interaction.guild_id)
    if target_bank <= 0:
        await interaction.response.send_message(
            f"{target.display_name} has nothing in their bank.", ephemeral=True,
        )
        return

    thief_row = await cog.bot.db.get_user(interaction.user.id, interaction.guild_id)
    cooldown_left = (
        float(thief_row["last_bank_heist"]) + config.BANK_HEIST_COOLDOWN_SECONDS - time.time()
    )
    if cooldown_left > 0:
        await interaction.response.send_message(
            f"Bank heist cooldown — try again in **{int(cooldown_left // 60) + 1}** minutes.",
            ephemeral=True,
        )
        return

    embed = build_bank_heist_embed(target, target_bank=target_bank)
    view = BankHeistView(cog, interaction.guild_id, interaction.user.id, target.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
