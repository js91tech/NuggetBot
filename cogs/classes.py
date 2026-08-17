from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.character_hub_ui import send_character_hub
from utils.classes import (
    CLASS_MAP,
    STARTER_IDS,
    can_evolve,
    evolution_threshold,
    format_modifiers_summary,
    get_class,
)
from utils.helpers import guild_only_message


class Classes(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    class_group = app_commands.Group(
        name="class",
        description="View, choose, and evolve your combat class.",
        guild_only=True,
    )

    async def _profile(self, user_id: int, guild_id: int) -> tuple[str | None, int, set[str]]:
        await self.bot.db.ensure_jester_class(user_id, guild_id)
        class_id = await self.bot.db.get_class_id(user_id, guild_id)
        row = await self.bot.db.get_user_character(user_id, guild_id)
        xp = int(row["class_xp"])
        roots = await self.bot.db.get_master_roots(user_id, guild_id)
        return class_id, xp, roots

    @class_group.command(name="view", description="View your class, XP, and modifiers.")
    @app_commands.describe(user="Player to inspect (defaults to you).")
    async def class_view(
        self, interaction: discord.Interaction, user: discord.Member | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if user is None:
            await send_character_hub(self, interaction)
            return
        target = user
        class_id, xp, roots = await self._profile(target.id, interaction.guild_id)
        cls = get_class(class_id)
        if cls is None:
            desc = (
                "No class yet. Pick a starter with `/class choose`.\n"
                f"Starters: **{', '.join(STARTER_IDS)}**"
            )
            embed = discord.Embed(
                title=f"{target.display_name}'s Class",
                description=desc,
                color=discord.Color.greyple(),
            )
        else:
            threshold = evolution_threshold(cls.tier)
            next_line = ""
            if threshold is not None:
                next_line = f"\nEvolve at **{threshold}** XP (you have **{xp}**)."
            options = can_evolve(class_id, xp, roots)
            if options:
                next_line += (
                    f"\n**Ready to evolve:** {', '.join(o.name for o in options)} "
                    "— use `/class evolve`."
                )
            embed = discord.Embed(
                title=f"{cls.emoji} {cls.name}",
                description=cls.description + next_line,
                color=discord.Color.gold(),
            )
            embed.add_field(
                name="Modifiers",
                value=format_modifiers_summary(cls.modifiers),
                inline=False,
            )
            snap = await self.bot.db.get_mana_snapshot(target.id, interaction.guild_id)
            from utils.classes import is_healer_class
            from utils.mana import mana_bar

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
        await interaction.response.send_message(embed=embed)

    @class_group.command(name="choose", description="Choose your starter class (one time).")
    @app_commands.describe(starter="Starter class")
    @app_commands.choices(
        starter=[app_commands.Choice(name=s.title(), value=s) for s in STARTER_IDS],
    )
    async def class_choose(self, interaction: discord.Interaction, starter: str) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if interaction.user.id == config.JESTER_EXCLUSIVE_USER_ID:
            await interaction.response.send_message(
                "You are bound to the **Jester** class.",
                ephemeral=True,
            )
            return
        existing = await self.bot.db.get_class_id(interaction.user.id, interaction.guild_id)
        if existing:
            await interaction.response.send_message(
                f"You already are **{get_class(existing).name}**. Evolution only goes forward.",
                ephemeral=True,
            )
            return
        ok, _code = await self.bot.db.set_class_id(
            interaction.user.id, interaction.guild_id, starter,
        )
        if not ok:
            await interaction.response.send_message("Could not set class.", ephemeral=True)
            return
        cls = get_class(starter)
        await interaction.response.send_message(
            f"You are now a **{cls.emoji} {cls.name}**! {cls.description}",
            ephemeral=True,
        )

    @class_group.command(name="evolve", description="Evolve when you have enough class XP.")
    async def class_evolve(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if interaction.user.id == config.JESTER_EXCLUSIVE_USER_ID:
            await interaction.response.send_message("The Jester does not evolve.", ephemeral=True)
            return
        class_id, xp, roots = await self._profile(interaction.user.id, interaction.guild_id)
        if not class_id:
            await interaction.response.send_message(
                "Choose a class first: `/class choose`.", ephemeral=True,
            )
            return
        options = can_evolve(class_id, xp, roots)
        if not options:
            current = get_class(class_id)
            threshold = evolution_threshold(current.tier) if current else None
            if threshold and xp < threshold:
                await interaction.response.send_message(
                    f"Need **{threshold}** class XP (you have **{xp}**). Fight duels and bosses!",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "No evolution available right now.",
                    ephemeral=True,
                )
            return
        if len(options) == 1:
            chosen = options[0]
        else:
            names = "\n".join(f"`{o.class_id}` — **{o.name}**" for o in options)
            await interaction.response.send_message(
                f"Pick one with `/class evolve-to`:\n{names}",
                ephemeral=True,
            )
            return
        await self._apply_evolution(interaction, chosen.class_id)

    @class_group.command(name="evolve-to", description="Evolve into a specific class.")
    @app_commands.describe(class_id="Class id from /class view")
    async def class_evolve_to(self, interaction: discord.Interaction, class_id: str) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        class_id = class_id.lower().strip()
        class_id_cur, xp, roots = await self._profile(interaction.user.id, interaction.guild_id)
        options = {o.class_id: o for o in can_evolve(class_id_cur, xp, roots)}
        if class_id not in options:
            await interaction.response.send_message(
                "That evolution is not available.", ephemeral=True,
            )
            return
        await self._apply_evolution(interaction, class_id)

    async def _apply_evolution(self, interaction: discord.Interaction, new_id: str) -> None:
        cls = get_class(new_id)
        ok, _ = await self.bot.db.set_class_id(interaction.user.id, interaction.guild_id, new_id)
        if not ok or cls is None:
            await interaction.response.send_message("Evolution failed.", ephemeral=True)
            return
        if cls.tier == "master" and cls.starter_root:
            await self.bot.db.record_master_root(
                interaction.user.id,
                interaction.guild_id,
                cls.starter_root,
            )
        await interaction.response.send_message(
            f"Evolved into **{cls.emoji} {cls.name}**!\n{format_modifiers_summary(cls.modifiers)}",
            ephemeral=True,
        )

    @class_group.command(name="tree", description="Browse the class evolution tree.")
    async def class_tree(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        lines = ["**Starters:** " + ", ".join(STARTER_IDS)]
        lines.append("**Hybrids:** warlord (vanguard+shade masters), archon (vanguard+mogul masters)")
        lines.append("**Special:** jester (exclusive)")
        for sid in STARTER_IDS:
            starter = CLASS_MAP[sid]
            branch_names = [CLASS_MAP[c].name for c in starter.children_ids]
            lines.append(f"**{starter.name}** → {', '.join(branch_names)}")
        embed = discord.Embed(
            title="Class tree",
            description="\n".join(lines[:20]),
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Use /class choose then /class evolve")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Classes(bot))
