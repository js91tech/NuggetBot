"""Character hub — class status, starter pick, evolution, and cross-links.

Pulls the same class/evolution logic as ``cogs/classes.py`` into one
persistent panel styled with the GoonBot brand kit.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

import config
from utils.classes import (
    STARTER_IDS,
    can_evolve,
    evolution_threshold,
    format_modifiers_summary,
    get_class,
    is_healer_class,
)
from utils.goon_theme import brand_color, branded_embed, panel_title
from utils.helpers import guild_only_message
from utils.mana import mana_bar

if TYPE_CHECKING:
    from discord.ext import commands

logger = logging.getLogger(__name__)


async def _profile(cog: commands.Cog, user_id: int, guild_id: int) -> tuple[str | None, int, set[str]]:
    await cog.bot.db.ensure_jester_class(user_id, guild_id)
    class_id = await cog.bot.db.get_class_id(user_id, guild_id)
    row = await cog.bot.db.get_user_character(user_id, guild_id)
    xp = int(row["class_xp"])
    roots = await cog.bot.db.get_master_roots(user_id, guild_id)
    return class_id, xp, roots


def _is_jester_bound(user_id: int) -> bool:
    return user_id == config.JESTER_EXCLUSIVE_USER_ID


async def build_character_embed(
    cog: commands.Cog,
    guild_id: int,
    user_id: int,
    display_name: str,
) -> tuple[discord.Embed, list, list]:
    """Returns (embed, evolve_options, roots) for building the view alongside it."""
    class_id, xp, roots = await _profile(cog, user_id, guild_id)
    cls = get_class(class_id)

    embed = branded_embed(
        panel_title("Character Suite", member_name=display_name),
        color=brand_color(),
    )

    if cls is None:
        embed.description = (
            "No class picked yet — every persona in GoonBot starts naked-stat and "
            "levels into something filthier. Pick a starter below.\n"
            f"**Starters:** {', '.join(s.title() for s in STARTER_IDS)}"
        )
        options: list = []
    else:
        threshold = evolution_threshold(cls.tier)
        options = can_evolve(class_id, xp, roots) if not _is_jester_bound(user_id) else []
        desc = cls.description
        if threshold is not None:
            desc += f"\nEvolve at **{threshold}** class XP (you have **{xp}**)."
        if options:
            desc += (
                f"\n**Ready to evolve:** {', '.join(o.name for o in options)} — "
                "use the Evolve button below."
            )
        embed.description = desc
        embed.add_field(
            name="Modifiers",
            value=format_modifiers_summary(cls.modifiers),
            inline=False,
        )
        snap = await cog.bot.db.get_mana_snapshot(user_id, guild_id)
        regen_hint = (
            "healer time regen"
            if is_healer_class(class_id)
            else f"{int(config.MANA_ON_DAMAGE_PCT * 100)}% dmg → mana"
        )
        embed.add_field(
            name="Mana",
            value=f"`{mana_bar(snap.current, snap.cap)}` {snap.current}/{snap.cap} ({regen_hint})",
            inline=False,
        )
        if roots:
            embed.add_field(name="Master roots", value=", ".join(sorted(roots)), inline=True)

    embed.add_field(
        name="Aspects",
        value="Combat aspects (crit, income, heist edges) live in `/aspects list` — equip up to 3.",
        inline=False,
    )
    embed.add_field(
        name="Avatars",
        value="Dress your victory pose and portrait with `/avatar action:list`.",
        inline=False,
    )
    return embed, options, roots


class StarterSelect(discord.ui.Select):
    def __init__(self) -> None:
        options = [
            discord.SelectOption(label=s.title(), value=s, description=f"Become a {s.title()}")
            for s in STARTER_IDS
        ]
        super().__init__(
            placeholder="Choose your starter class…",
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: CharacterHubView = self.view  # type: ignore[assignment]
        starter = self.values[0]
        ok, _code = await view.cog.bot.db.set_class_id(
            view.user_id, view.guild_id, starter,
        )
        if not ok:
            await interaction.response.send_message("Could not set class.", ephemeral=True)
            return
        cls = get_class(starter)
        await _refresh_hub(
            interaction,
            view.cog,
            view.guild_id,
            view.user_id,
            note=f"You are now a **{cls.emoji} {cls.name}**! {cls.description}" if cls else None,
        )


class EvolveButton(discord.ui.Button):
    def __init__(self, options: list) -> None:
        super().__init__(label="Evolve", style=discord.ButtonStyle.success, row=1)
        self._options = options

    async def callback(self, interaction: discord.Interaction) -> None:
        view: CharacterHubView = self.view  # type: ignore[assignment]
        if len(self._options) == 1:
            await _apply_evolution(interaction, view.cog, view.guild_id, view.user_id, self._options[0].class_id)
            return
        choice_view = EvolveChoiceView(view.cog, view.guild_id, view.user_id, self._options)
        embed = branded_embed(
            panel_title("Evolution — pick a path"),
            description="\n".join(
                f"`{o.class_id}` — **{o.name}** — {o.description}" for o in self._options
            ),
        )
        await interaction.response.edit_message(embed=embed, view=choice_view)


class EvolveChoiceSelect(discord.ui.Select):
    def __init__(self, options: list) -> None:
        select_options = [
            discord.SelectOption(label=o.name, value=o.class_id, description=o.description[:100])
            for o in options
        ]
        super().__init__(placeholder="Evolve into…", options=select_options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: EvolveChoiceView = self.view  # type: ignore[assignment]
        await _apply_evolution(interaction, view.cog, view.guild_id, view.user_id, self.values[0])


class EvolveChoiceView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int, options: list) -> None:
        super().__init__(timeout=120.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.add_item(EvolveChoiceSelect(options))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your panel.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await _refresh_hub(interaction, self.cog, self.guild_id, self.user_id)


async def _apply_evolution(
    interaction: discord.Interaction,
    cog: commands.Cog,
    guild_id: int,
    user_id: int,
    new_id: str,
) -> None:
    cls = get_class(new_id)
    ok, _ = await cog.bot.db.set_class_id(user_id, guild_id, new_id)
    if not ok or cls is None:
        await interaction.response.send_message("Evolution failed.", ephemeral=True)
        return
    if cls.tier == "master" and cls.starter_root:
        await cog.bot.db.record_master_root(user_id, guild_id, cls.starter_root)
    await _refresh_hub(
        interaction,
        cog,
        guild_id,
        user_id,
        note=f"Evolved into **{cls.emoji} {cls.name}**! {format_modifiers_summary(cls.modifiers)}",
    )


async def _refresh_hub(
    interaction: discord.Interaction,
    cog: commands.Cog,
    guild_id: int,
    user_id: int,
    *,
    note: str | None = None,
) -> None:
    member = interaction.guild.get_member(user_id) if interaction.guild else None
    display_name = member.display_name if member else str(user_id)
    embed, options, _roots = await build_character_embed(cog, guild_id, user_id, display_name)
    if note:
        embed.description = f"{note}\n\n{embed.description}"
    view = CharacterHubView(cog, guild_id, user_id, options)
    if interaction.response.is_done():
        await interaction.edit_original_response(embed=embed, view=view)
    else:
        await interaction.response.edit_message(embed=embed, view=view)


class CharacterHubView(discord.ui.View):
    def __init__(
        self,
        cog: commands.Cog,
        guild_id: int,
        user_id: int,
        evolve_options: list,
    ) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        jester_bound = _is_jester_bound(user_id)
        if not evolve_options and not jester_bound:
            self.add_item(StarterSelect())
        if evolve_options and not jester_bound:
            self.add_item(EvolveButton(evolve_options))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your character panel.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=2)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await _refresh_hub(interaction, self.cog, self.guild_id, self.user_id)


async def send_character_hub(cog: commands.Cog, interaction: discord.Interaction) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message(guild_only_message(), ephemeral=True)
        return
    guild_id = interaction.guild_id
    user_id = interaction.user.id
    try:
        embed, options, _roots = await build_character_embed(
            cog, guild_id, user_id, interaction.user.display_name,
        )
        view = CharacterHubView(cog, guild_id, user_id, options)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    except Exception:
        logger.exception("Failed to open character hub for user %s", user_id)
        if interaction.response.is_done():
            await interaction.followup.send(
                "Could not open the character hub. Try again in a moment.", ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Could not open the character hub. Try again in a moment.", ephemeral=True,
            )
