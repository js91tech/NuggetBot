from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.classes import is_healer_class
from utils.helpers import guild_only_message
from utils.mana import mana_bar
from utils.skills import skills_for_class
from utils.spell_actions import execute_cast_skill
from utils.spellbook_ui import send_spellbook_panel


class Spells(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _mana_regen_hint(self, class_id: str | None) -> str:
        if is_healer_class(class_id):
            return (
                f"Healer regen: **+{config.MANA_HEALER_REGEN_PER_TICK}** mana / "
                f"{config.MANA_HEALER_REGEN_INTERVAL_SECONDS}s · "
                f"**{int(config.MANA_HEALER_ON_DAMAGE_PCT * 100)}%** of damage dealt"
            )
        return (
            f"Regen: **+{config.MANA_REGEN_PER_TICK}** mana / {config.MANA_REGEN_INTERVAL_SECONDS}s · "
            f"**{int(config.MANA_ON_DAMAGE_PCT * 100)}%** of damage dealt (high damage = more casts)"
        )

    @app_commands.command(name="mana", description="View your mana pool and regen rules.")
    @app_commands.guild_only()
    async def mana(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        class_id = await self.bot.db.get_class_id(interaction.user.id, interaction.guild_id)
        snap = await self.bot.db.get_mana_snapshot(interaction.user.id, interaction.guild_id)
        pending = await self.bot.db.get_pending_spell_id(interaction.user.id, interaction.guild_id)
        pending_line = f"\nReady spell: `{pending}`" if pending else ""
        embed = discord.Embed(
            title="Mana",
            description=(
                f"`{mana_bar(snap.current, snap.cap)}` **{snap.current}/{snap.cap}**\n"
                f"{self._mana_regen_hint(class_id)}"
                f"{pending_line}"
            ),
            color=discord.Color.blue(),
        )
        footer = (
            f"+{snap.regen_per_tick} mana in ~{snap.seconds_until_tick}s"
            if snap.seconds_until_tick > 0
            else None
        )
        mana_mult = await self.bot.db.summoner_mana_regen_multiplier(
            interaction.user.id,
            interaction.guild_id,
        )
        if mana_mult < 1.0:
            pct = int(round(mana_mult * 100))
            curse = f"Summoner curse: mana regen at **{pct}%** while your boss is up."
            footer = f"{curse} · {footer}" if footer else curse
        if footer:
            embed.set_footer(text=footer)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="skills", description="Open your spellbook panel.")
    @app_commands.guild_only()
    async def skills(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        await send_spellbook_panel(interaction, self)

    async def skill_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        class_id = await self.bot.db.get_class_id(interaction.user.id, interaction.guild_id)
        if not class_id:
            return []
        needle = current.lower().strip()
        choices: list[app_commands.Choice[str]] = []
        for skill_def in skills_for_class(class_id):
            haystack = f"{skill_def.skill_id} {skill_def.name}".lower()
            if needle and needle not in haystack:
                continue
            choices.append(
                app_commands.Choice(
                    name=f"{skill_def.emoji} {skill_def.name} — {skill_def.mana_cost} mana",
                    value=skill_def.skill_id,
                )
            )
        return choices[:25]

    @app_commands.command(name="cast", description="Open spellbook or cast a skill by name.")
    @app_commands.describe(skill="Skill id (optional — opens panel if omitted)")
    @app_commands.autocomplete(skill=skill_autocomplete)
    @app_commands.guild_only()
    async def cast(self, interaction: discord.Interaction, skill: str | None = None) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if not skill:
            await send_spellbook_panel(interaction, self)
            return

        result = await execute_cast_skill(
            self.bot.db,
            interaction.user.id,
            interaction.guild_id,
            skill,
        )
        if not result.ok:
            await interaction.response.send_message(result.error or "Cast failed.", ephemeral=True)
            return
        await interaction.response.send_message(result.message, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Spells(bot))
