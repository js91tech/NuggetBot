from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.classes import get_class, is_healer_class
from utils.combat_engine import max_hp_from_armor
from utils.helpers import fmt_amount, guild_only_message
from utils.loadout import parse_loadout
from utils.mana import mana_bar
from utils.skills import (
    format_skills_list,
    get_skill,
    skill_available,
    skills_for_class,
    spell_buff_from_skill,
)
from utils.spell_effects import combat_state_from_spell


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

    async def build_cast_select_options(
        self,
        user_id: int,
        guild_id: int,
    ) -> list[discord.SelectOption]:
        class_id = await self.bot.db.get_class_id(user_id, guild_id)
        if not class_id:
            return []
        options: list[discord.SelectOption] = []
        for skill_def in skills_for_class(class_id):
            if not skill_available(skill_def, class_id):
                continue
            options.append(
                discord.SelectOption(
                    label=skill_def.name[:100],
                    value=skill_def.skill_id,
                    description=f"{skill_def.mana_cost} mana · {skill_def.description[:50]}",
                    emoji=skill_def.emoji,
                ),
            )
            if len(options) >= 25:
                break
        return options

    async def execute_cast_skill(
        self,
        user_id: int,
        guild_id: int,
        skill: str,
    ) -> tuple[str | None, str | None]:
        if await self.bot.db.is_downed(user_id, guild_id):
            return "You cannot cast while downed. Use **Heal** on the boss panel first.", None
        if await self.bot.db.is_restricted(user_id, guild_id):
            return "You cannot cast while arrested.", None

        class_id = await self.bot.db.get_class_id(user_id, guild_id)
        if not class_id:
            return "Choose a class with `/class-choose` first.", None

        skill_def = get_skill(skill)
        if skill_def is None or not skill_available(skill_def, class_id):
            return "Unknown or locked skill. Use `/skills` for valid ids.", None

        ok, err = await self.bot.db.spend_mana(user_id, guild_id, skill_def.mana_cost)
        if not ok:
            snap = await self.bot.db.get_mana_snapshot(user_id, guild_id)
            return (
                f"Not enough mana. Need **{skill_def.mana_cost}**, "
                f"you have **{snap.current}/{snap.cap}**.",
                None,
            )

        state = combat_state_from_spell(spell_buff_from_skill(skill_def))
        extra_lines: list[str] = []

        if state.heal_self_fraction > 0:
            from utils.classes import get_modifiers

            equipment = await self.bot.db.get_equipment(user_id, guild_id)
            loadout = parse_loadout(equipment)
            max_hp = float(
                max_hp_from_armor(loadout.armor, class_modifiers=get_modifiers(class_id))
            )
            heal = max(1, int(max_hp * state.heal_self_fraction))
            await self.bot.db.heal_player(
                user_id,
                guild_id,
                float(heal),
                max_hp,
            )
            extra_lines.append(f"Restored **{heal}** HP.")

        if state.heal_ally_fraction > 0:
            await self.bot.db.set_pending_spell(user_id, guild_id, skill_def.skill_id)
            extra_lines.append(
                f"**{skill_def.name}** ready — your next `/heal` pays "
                f"**+{int(state.heal_ally_fraction * 100)}%** bonus reward."
            )

        if state.income_bonus > 0:
            await self.bot.db.credit_wallet(user_id, guild_id, state.income_bonus)
            extra_lines.append(f"Gained **{fmt_amount(state.income_bonus)}** nuggets.")

        if state.heist_bonus > 0:
            await self.bot.db.add_heist_spell_bonus(user_id, guild_id, state.heist_bonus)
            extra_lines.append(
                f"Next heist gains **+{int(state.heist_bonus * 100)}%** success chance."
            )

        if (
            (state.damage_mult > 1.0 or state.fortify_mult < 1.0 or state.extra_crit > 0)
            and state.heal_ally_fraction <= 0
            and state.heal_self_fraction <= 0
        ):
            await self.bot.db.set_pending_spell(user_id, guild_id, skill_def.skill_id)
            extra_lines.append(
                f"**{skill_def.name}** charged — your next **Attack** or `/duel` within "
                f"{config.PENDING_SPELL_SECONDS}s."
            )

        snap = await self.bot.db.get_mana_snapshot(user_id, guild_id)
        desc = (
            f"{skill_def.emoji} **{skill_def.name}** cast (−{skill_def.mana_cost} mana).\n"
            f"Mana: **{snap.current}/{snap.cap}**"
        )
        if extra_lines:
            desc += "\n" + "\n".join(extra_lines)
        return None, desc

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

    @app_commands.command(name="skills", description="List skills for your class.")
    @app_commands.guild_only()
    async def skills(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        class_id = await self.bot.db.get_class_id(interaction.user.id, interaction.guild_id)
        cls = get_class(class_id)
        if cls is None:
            await interaction.response.send_message(
                "Choose a class with `/class-choose` first.",
                ephemeral=True,
            )
            return
        embed = discord.Embed(
            title=f"{cls.emoji} {cls.name} — Skills",
            description=format_skills_list(class_id),
            color=discord.Color.purple(),
        )
        embed.set_footer(text="Use /cast or the boss panel **Cast** button · Buffs last until your next attack")
        await interaction.response.send_message(embed=embed, ephemeral=True)

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

    @app_commands.command(name="cast", description="Cast a class skill (costs mana).")
    @app_commands.describe(skill="Skill to cast")
    @app_commands.autocomplete(skill=skill_autocomplete)
    @app_commands.guild_only()
    async def cast(self, interaction: discord.Interaction, skill: str) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        error, message = await self.execute_cast_skill(
            interaction.user.id,
            interaction.guild_id,
            skill,
        )
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        await interaction.response.send_message(message or "Cast.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Spells(bot))
