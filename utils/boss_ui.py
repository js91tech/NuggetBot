from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from utils.helpers import fmt_amount
from utils.summoner_penalty import boss_summoner_id, summoner_penalty_summary

if TYPE_CHECKING:
    from cogs.boss import Boss


@dataclass
class BossAttackResult:
    embed: discord.Embed | None = None
    defeated: bool = False
    error: str | None = None


class BossFightView(discord.ui.View):
    """Interactive boss raid panel — attack, refresh, and raid leaderboard."""

    def __init__(self, cog: Boss, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=300.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Open your own fight panel with `/boss`.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="⚔️ Attack", style=discord.ButtonStyle.danger, row=0)
    async def attack_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        if interaction.guild is None:
            await interaction.response.send_message("Guild only.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Members only.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        result = await self.cog.execute_boss_attack(
            interaction.user,
            interaction.guild,
            interaction=interaction,
        )
        if result.error:
            await interaction.followup.send(result.error, ephemeral=True)
            return
        if result.defeated:
            for item in self.children:
                item.disabled = True
            if result.embed is not None:
                await interaction.edit_original_response(embed=result.embed, view=self)
            return
        if result.embed is None:
            await interaction.followup.send("Attack failed.", ephemeral=True)
            return
        await interaction.edit_original_response(embed=result.embed, view=self)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=0)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        embed, err = await self.cog.build_boss_fight_embed(self.guild_id, member=member)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Raid LB", style=discord.ButtonStyle.secondary, row=0)
    async def leaderboard_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        rows = await self.cog.bot.db.list_boss_damage(self.guild_id)
        if not rows:
            await interaction.response.send_message("Nobody has attacked yet.", ephemeral=True)
            return
        lines = []
        guild = interaction.guild
        for index, row in enumerate(rows[:10], start=1):
            uid = int(row["user_id"])
            member = guild.get_member(uid) if guild else None
            name = member.display_name if member else f"User {uid}"
            lines.append(f"**{index}.** {name} — **{fmt_amount(float(row['damage']))}** dmg")
        embed = discord.Embed(
            title="Raid damage leaderboard",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(text="Rewards scale with damage share when the boss falls")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def send_boss_fight_panel(
    interaction: discord.Interaction,
    cog: Boss,
) -> None:
    """Shared handler for /boss fight UI."""
    if interaction.guild_id is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return

    boss_row = await cog.bot.db.apply_boss_passive_decay(interaction.guild_id)
    if boss_row is None:
        await interaction.response.send_message("No boss is active right now.", ephemeral=True)
        return

    if float(boss_row["hp"]) <= 0 and interaction.guild is not None:
        await cog._complete_boss_defeat(
            interaction.guild,
            interaction=interaction,
            killer_user_id=None,
        )
        return

    embed, err = await cog.build_boss_fight_embed(
        interaction.guild_id,
        boss_row=boss_row,
        member=interaction.user if isinstance(interaction.user, discord.Member) else None,
    )
    if err or embed is None:
        await interaction.response.send_message(err or "No boss active.", ephemeral=True)
        return

    view = BossFightView(cog, interaction.guild_id, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
