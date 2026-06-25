"""Business drug supply chain UI (tier 5+ auto-funded lab grows)."""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

import config
from utils.drugs import DRUGS, drug_by_id
from utils.helpers import fmt_amount

if TYPE_CHECKING:
    from discord.ext import commands


async def _lab_slot_summary(cog: commands.Cog, user_id: int, guild_id: int) -> tuple[int, int]:
    from utils.drug_ui import user_lab_slot_count

    grows = await cog.bot.db.list_drug_grows(user_id, guild_id)
    max_slots = await user_lab_slot_count(cog, user_id, guild_id)
    return len(grows), max_slots


def build_supply_chain_embed(
    row: object,
    *,
    used_slots: int,
    max_slots: int,
) -> discord.Embed:
    tier = int(row["tier"])
    stored = float(row["stored_income"])
    current_id = row["supply_chain_drug_id"]
    current_id = str(current_id) if current_id else None

    embed = discord.Embed(
        title="🔗 Drug Supply Chain",
        color=discord.Color.dark_teal(),
    )
    if tier < config.DRUG_SUPPLY_CHAIN_TIER_MIN:
        embed.description = (
            f"Unlocks at business tier **{config.DRUG_SUPPLY_CHAIN_TIER_MIN}** "
            f"(you are tier **{tier}**). Keep tiering up to auto-fund your grow lab "
            "from stored business revenue."
        )
        return embed

    embed.description = (
        "When a lab slot is free, stored business revenue auto-buys seeds for your "
        "chosen strain. Supply-chain grows take "
        f"**{int((config.DRUG_SUPPLY_CHAIN_GROW_SLOWDOWN - 1) * 100)}%** longer than manual plants."
    )
    if current_id:
        defn = drug_by_id(current_id)
        if defn is not None:
            grow_mins = int(defn.grow_seconds * config.DRUG_SUPPLY_CHAIN_GROW_SLOWDOWN) // 60
            embed.add_field(
                name="Active strain",
                value=(
                    f"{defn.emoji} **{defn.name}** · seed **{fmt_amount(defn.seed_cost)}** · "
                    f"~{grow_mins}m grow"
                ),
                inline=False,
            )
        else:
            embed.add_field(name="Active strain", value=f"Unknown (`{current_id}`)", inline=False)
    else:
        embed.add_field(name="Active strain", value="_Disabled_", inline=False)

    embed.add_field(
        name="Lab slots",
        value=f"**{used_slots}/{max_slots}** in use",
        inline=True,
    )
    embed.add_field(
        name="Stored revenue",
        value=f"**{fmt_amount(stored)}** available for seeds",
        inline=True,
    )
    embed.set_footer(text="Pick a strain below · Disable turns auto-funding off")
    return embed


class SupplyChainDrugSelect(discord.ui.Select):
    def __init__(self, view: "SupplyChainView", *, current_id: str | None, unlocked: bool) -> None:
        self._view = view
        options: list[discord.SelectOption] = []
        for defn in DRUGS[:25]:
            options.append(
                discord.SelectOption(
                    label=defn.name,
                    value=defn.drug_id,
                    description=(
                        f"Seed {fmt_amount(defn.seed_cost)} · {defn.grow_seconds // 60}m grow"
                    )[:100],
                    emoji=defn.emoji,
                    default=current_id == defn.drug_id,
                ),
            )
        super().__init__(
            placeholder="Choose strain to auto-fund…",
            min_values=1,
            max_values=1,
            options=options,
            disabled=not unlocked,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        drug_id = self.values[0]
        err = await self._view.cog.bot.db.set_supply_chain_drug(
            self._view.user_id, self._view.guild_id, drug_id,
        )
        if err:
            msgs = {
                "no_business": "You don't own a business.",
                "tier_too_low": (
                    f"Supply chain unlocks at tier **{config.DRUG_SUPPLY_CHAIN_TIER_MIN}**."
                ),
                "invalid_drug": "Unknown strain.",
            }
            await interaction.response.send_message(msgs.get(err, "Could not update."), ephemeral=True)
            return
        defn = drug_by_id(drug_id)
        embed, view = await build_supply_chain_panel(
            self._view.cog, self._view.guild_id, self._view.user_id,
        )
        name = defn.name if defn else drug_id
        embed.description = (
            f"🔗 Supply chain set to **{name}**. Stored revenue will auto-buy seeds "
            f"when lab slots are free.\n\n{embed.description or ''}"
        ).strip()
        await interaction.response.edit_message(embed=embed, view=view)


class SupplyChainView(discord.ui.View):
    def __init__(
        self,
        cog: commands.Cog,
        guild_id: int,
        user_id: int,
        *,
        row: object,
        unlocked: bool,
    ) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        current_id = str(row["supply_chain_drug_id"]) if row["supply_chain_drug_id"] else None
        self.add_item(SupplyChainDrugSelect(self, current_id=current_id, unlocked=unlocked))
        self._unlocked = unlocked

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your panel.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Disable", style=discord.ButtonStyle.danger, row=1)
    async def disable_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        if not self._unlocked:
            await interaction.response.send_message(
                f"Supply chain unlocks at tier **{config.DRUG_SUPPLY_CHAIN_TIER_MIN}**.",
                ephemeral=True,
            )
            return
        err = await self.cog.bot.db.set_supply_chain_drug(self.user_id, self.guild_id, None)
        if err:
            await interaction.response.send_message("Could not disable supply chain.", ephemeral=True)
            return
        embed, view = await build_supply_chain_panel(self.cog, self.guild_id, self.user_id)
        embed.description = "Supply chain **disabled**.\n\n" + (embed.description or "")
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=1)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        embed, view = await build_supply_chain_panel(self.cog, self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)


async def build_supply_chain_panel(
    cog: commands.Cog, guild_id: int, user_id: int,
) -> tuple[discord.Embed, SupplyChainView]:
    row = await cog.bot.db.get_business(user_id, guild_id)
    assert row is not None
    used, max_slots = await _lab_slot_summary(cog, user_id, guild_id)
    unlocked = int(row["tier"]) >= config.DRUG_SUPPLY_CHAIN_TIER_MIN
    embed = build_supply_chain_embed(row, used_slots=used, max_slots=max_slots)
    view = SupplyChainView(cog, guild_id, user_id, row=row, unlocked=unlocked)
    return embed, view


async def send_supply_chain_panel(interaction: discord.Interaction, cog: commands.Cog) -> None:
    guild_id = interaction.guild_id
    assert guild_id is not None
    row = await cog.bot.db.get_business(interaction.user.id, guild_id)
    if row is None:
        await interaction.response.send_message(
            "You don't own a business. Use **/business create**.", ephemeral=True,
        )
        return
    embed, view = await build_supply_chain_panel(cog, guild_id, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
