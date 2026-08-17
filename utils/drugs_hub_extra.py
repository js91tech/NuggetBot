"""Drugs Extra Hub — quick tabs for stash, catalog, dealer rank, and crossbreeding.

The main grow-lab view (``utils/drug_ui.py``) already fills all five Discord
component rows, so this ships as its own tabbed panel rather than bolting more
buttons onto the lab. Wired from ``/drugs stash``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

import config
from utils.drug_ui import build_drug_catalog_embed, build_stash_embed
from utils.goon_theme import FOOTER_BRAND, branded_embed, panel_title
from utils.helpers import guild_only_message

if TYPE_CHECKING:
    from discord.ext import commands


async def build_dealer_rank_embed(cog: commands.Cog, guild_id: int, user_id: int) -> discord.Embed:
    from utils.dealer_ranks import (
        can_list_on_market,
        can_wholesale,
        dealer_rank,
        dealer_reputation,
        next_rank_threshold,
        rank_title,
    )

    stats = await cog.bot.db.get_drug_stats(user_id, guild_id)
    rank = dealer_rank(units_sold=stats["units_sold"], units_harvested=stats["units_harvested"])
    rep = dealer_reputation(units_sold=stats["units_sold"], units_harvested=stats["units_harvested"])
    title = rank_title(rank)

    embed = branded_embed(
        panel_title(f"Dealer Rank {rank} — {title}"),
        description=(
            f"Dealer reputation: **{rep:,}**\n"
            f"Cultivated: **{stats['units_harvested']:,}** · Sold: **{stats['units_sold']:,}**"
        ),
    )
    if rank >= config.DEALER_RANK_CARTEL_TITLE:
        embed.add_field(
            name="Active title",
            value=f"**{title}** — shown on `/profile`",
            inline=False,
        )
    unlocks = [
        "Street selling",
        f"Black market (rank {config.DEALER_RANK_MARKET_UNLOCK})",
        f"Extra lab slot (rank {config.DEALER_RANK_EXTRA_LAB_SLOT})",
        f"Wholesale NPC buyer (rank {config.DEALER_RANK_WHOLESALE_UNLOCK})",
        f"Cartel title (rank {config.DEALER_RANK_CARTEL_TITLE})",
    ]
    unlocked_flags = [
        True,
        can_list_on_market(rank),
        rank >= config.DEALER_RANK_EXTRA_LAB_SLOT,
        can_wholesale(rank),
        rank >= config.DEALER_RANK_CARTEL_TITLE,
    ]
    embed.add_field(
        name="Unlock track",
        value="\n".join(
            f"{'✅' if unlocked else '⬜'} {label}"
            for label, unlocked in zip(unlocks, unlocked_flags, strict=True)
        ),
        inline=False,
    )
    next_thr = next_rank_threshold(rank)
    footer = f"{FOOTER_BRAND} · use /drugs lab and /drugs wholesale to climb"
    if next_thr is not None:
        footer = f"{max(0, next_thr - rep):,} rep to rank {rank + 1} · {footer}"
    embed.set_footer(text=footer)
    return embed


async def build_crossbreed_embed(cog: commands.Cog, guild_id: int, user_id: int) -> discord.Embed:
    from utils.drugs import drug_by_id
    from utils.phenotypes import PHENOTYPE_DEFINITIONS, drug_family, phenotype_buff_description

    inventory = await cog.bot.db.get_drug_inventory(user_id, guild_id)
    active_id = await cog.bot.db.get_active_phenotype_id(user_id, guild_id)

    embed = branded_embed(
        panel_title("Crossbreed Lab"),
        description=(
            "Burn 1 unit each of two different strains for an **~8%** chance at a rare "
            "phenotype (buff persists until you discover another). Run "
            "`/drugs crossbreed strain_a:<x> strain_b:<y>` to try it."
        ),
    )
    if inventory:
        lines = []
        for drug_id, qty in inventory.items():
            defn = drug_by_id(drug_id)
            name = defn.name if defn else drug_id
            emoji = defn.emoji if defn else "📦"
            family = drug_family(drug_id)
            lines.append(f"{emoji} **{name}** ×{qty} — _{family}_")
        embed.add_field(name="Your strains", value="\n".join(lines), inline=False)
    else:
        embed.add_field(
            name="Your strains",
            value="_Empty — harvest from `/drugs lab` first._",
            inline=False,
        )
    if active_id:
        pheno = PHENOTYPE_DEFINITIONS.get(active_id)
        if pheno is not None:
            embed.add_field(
                name="Active phenotype",
                value=(
                    f"{pheno.emoji} **{pheno.name}** — "
                    f"{phenotype_buff_description(pheno.buff_effect)}"
                ),
                inline=False,
            )
    embed.set_footer(text=f"{FOOTER_BRAND} · needs 1 unit of each strain")
    return embed


async def _build_tab_embed(
    cog: commands.Cog, guild_id: int, user_id: int, tab: str,
) -> discord.Embed:
    if tab == "catalog":
        return await build_drug_catalog_embed()
    if tab == "rank":
        return await build_dealer_rank_embed(cog, guild_id, user_id)
    if tab == "crossbreed":
        return await build_crossbreed_embed(cog, guild_id, user_id)
    return await build_stash_embed(cog, guild_id, user_id)


class DrugsExtraHubView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Open your own stash with `/drugs stash`.", ephemeral=True,
            )
            return False
        return True

    async def _switch(self, interaction: discord.Interaction, tab: str) -> None:
        embed = await _build_tab_embed(self.cog, self.guild_id, self.user_id, tab)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="📦 Stash", style=discord.ButtonStyle.success, row=0)
    async def stash_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._switch(interaction, "stash")

    @discord.ui.button(label="📖 Catalog", style=discord.ButtonStyle.primary, row=0)
    async def catalog_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._switch(interaction, "catalog")

    @discord.ui.button(label="🏅 Rank", style=discord.ButtonStyle.secondary, row=0)
    async def rank_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._switch(interaction, "rank")

    @discord.ui.button(label="🧬 Crossbreed", style=discord.ButtonStyle.secondary, row=0)
    async def crossbreed_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._switch(interaction, "crossbreed")


async def send_drugs_extra_hub(interaction: discord.Interaction, cog: commands.Cog) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message(guild_only_message(), ephemeral=True)
        return
    embed = await build_stash_embed(cog, interaction.guild_id, interaction.user.id)
    view = DrugsExtraHubView(cog, interaction.guild_id, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
