from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from utils.classes import get_class
from utils.mana import mana_bar
from utils.skills import format_skills_list, skills_for_class
from utils.spell_actions import execute_cast_skill

if TYPE_CHECKING:
    from cogs.spells import Spells


async def build_spellbook_embed(
    cog: Spells,
    guild_id: int,
    user_id: int,
) -> tuple[discord.Embed | None, str | None]:
    class_id = await cog.bot.db.get_class_id(user_id, guild_id)
    cls = get_class(class_id)
    if cls is None:
        return None, "Choose a class with `/class-choose` first."

    snap = await cog.bot.db.get_mana_snapshot(user_id, guild_id)
    pending = await cog.bot.db.get_pending_spell_id(user_id, guild_id)
    pending_line = f"\nReady spell: **{pending}**" if pending else ""
    embed = discord.Embed(
        title=f"{cls.emoji} {cls.name} — Spellbook",
        description=format_skills_list(class_id),
        color=discord.Color.purple(),
    )
    embed.add_field(
        name="Mana",
        value=f"`{mana_bar(snap.current, snap.cap)}` **{snap.current}/{snap.cap}**{pending_line}",
        inline=False,
    )
    embed.set_footer(text="Pick a skill below · Buffs apply on next attack or heal")
    return embed, None


class SpellbookView(discord.ui.View):
    def __init__(
        self,
        cog: Spells,
        guild_id: int,
        user_id: int,
        *,
        skill_options: list[discord.SelectOption],
    ) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        if skill_options:
            select = discord.ui.Select(
                placeholder="Cast a skill…",
                options=skill_options,
                row=0,
            )

            async def on_cast(interaction: discord.Interaction) -> None:
                skill_id = select.values[0]
                result = await execute_cast_skill(
                    self.cog.bot.db,
                    self.user_id,
                    self.guild_id,
                    skill_id,
                )
                if not result.ok:
                    await interaction.response.send_message(
                        result.error or "Cast failed.",
                        ephemeral=True,
                    )
                    return
                embed, err = await build_spellbook_embed(self.cog, self.guild_id, self.user_id)
                if err or embed is None:
                    await interaction.response.send_message(result.message, ephemeral=True)
                    return
                view = await build_spellbook_view(self.cog, self.guild_id, self.user_id)
                await interaction.response.edit_message(embed=embed, view=view)
                await interaction.followup.send(result.message, ephemeral=True)

            select.callback = on_cast
            self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your spellbook.", ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        embed, err = await build_spellbook_embed(self.cog, self.guild_id, self.user_id)
        if err or embed is None:
            await interaction.response.send_message(err or "No class.", ephemeral=True)
            return
        view = await build_spellbook_view(self.cog, self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)


async def build_spellbook_view(
    cog: Spells,
    guild_id: int,
    user_id: int,
) -> SpellbookView:
    class_id = await cog.bot.db.get_class_id(user_id, guild_id)
    skills = skills_for_class(class_id)
    options: list[discord.SelectOption] = []
    for skill in sorted(skills, key=lambda s: s.mana_cost)[:25]:
        options.append(
            discord.SelectOption(
                label=f"{skill.name} ({skill.mana_cost} mana)",
                value=skill.skill_id,
                emoji=skill.emoji,
                description=skill.description[:100],
            )
        )
    return SpellbookView(cog, guild_id, user_id, skill_options=options)


async def send_spellbook_panel(interaction: discord.Interaction, cog: Spells) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return
    embed, err = await build_spellbook_embed(
        cog,
        interaction.guild_id,
        interaction.user.id,
    )
    if err or embed is None:
        await interaction.response.send_message(err or "No class.", ephemeral=True)
        return
    view = await build_spellbook_view(cog, interaction.guild_id, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
