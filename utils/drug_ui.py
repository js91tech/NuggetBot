"""Interactive drug trade: grow lab, inventory, street sales, and player market."""
from __future__ import annotations

import io
import logging
import time
from typing import TYPE_CHECKING

import discord

import config
from utils.drug_art import render_lab_image
from utils.fertilizer import FERTILIZERS, fertilizer_by_id
from utils.drugs import (
    DRUGS,
    DRUG_CATEGORY_LABELS,
    drug_by_id,
    drugs_by_category,
    drugs_for_category,
    format_consume_message,
)
from utils.helpers import clip_embed_field, fmt_amount
from utils.quests import record_quest_event

if TYPE_CHECKING:
    from discord.ext import commands

logger = logging.getLogger(__name__)


async def player_max_hp(cog: commands.Cog, user_id: int, guild_id: int) -> float:
    from utils.player_combat import player_max_hp as _player_max_hp

    return await _player_max_hp(cog, user_id, guild_id)


async def _apply_lab_panel(
    interaction: discord.Interaction,
    cog: commands.Cog,
    guild_id: int,
    user_id: int,
    *,
    description: str | None = None,
    plant_category: str | None = None,
) -> None:
    """Refresh the lab embed and controls (re-attach banner so attachment:// URL stays valid)."""
    view = await DrugLabView.build(
        cog, guild_id, user_id, plant_category=plant_category,
    )
    embed, banner = await build_lab_embed(cog, guild_id, user_id)
    if description is not None:
        embed.description = description
    edit_kwargs: dict[str, object] = {"embed": embed, "view": view, "attachments": [banner]}
    if interaction.response.is_done():
        await interaction.edit_original_response(**edit_kwargs)  # type: ignore[arg-type]
    else:
        await interaction.response.edit_message(**edit_kwargs)  # type: ignore[arg-type]


def stash_select_options(inventory: dict[str, int]) -> list[discord.SelectOption]:
    options: list[discord.SelectOption] = []
    for drug_id, qty in inventory.items():
        defn = drug_by_id(drug_id)
        desc = defn.effect_summary[:100] if defn else None
        options.append(
            discord.SelectOption(
                label=f"{defn.name if defn else drug_id} (×{qty})",
                value=drug_id,
                description=desc,
                emoji=defn.emoji if defn else None,
            ),
        )
    return options


def stash_action_options(inventory: dict[str, int]) -> list[discord.SelectOption]:
    options: list[discord.SelectOption] = []
    for drug_id, qty in inventory.items():
        defn = drug_by_id(drug_id)
        name = defn.name if defn else drug_id
        emoji = defn.emoji if defn else None
        options.append(
            discord.SelectOption(
                label=f"Sell {name} (×{qty})"[:100],
                value=f"sell:{drug_id}",
                description="Street sale",
                emoji=emoji,
            ),
        )
        options.append(
            discord.SelectOption(
                label=f"Use {name} (×{qty})"[:100],
                value=f"use:{drug_id}",
                description=(defn.effect_summary[:100] if defn else None),
                emoji=emoji,
            ),
        )
    return options[:25]


async def consume_stash_product(
    cog: commands.Cog, guild_id: int, user_id: int, drug_id: str,
) -> dict[str, object]:
    max_hp = await player_max_hp(cog, user_id, guild_id)
    return await cog.bot.db.consume_drug(user_id, guild_id, drug_id, max_hp=max_hp)


def _active_drug_buff_lines(pending_buff: dict[str, object]) -> list[str]:
    buff_parts: list[str] = []
    if float(pending_buff["boss_mult"]) > 1.0:
        pct = int((float(pending_buff["boss_mult"]) - 1.0) * 100)
        buff_parts.append(f"**/attack** +{pct}%")
    if float(pending_buff["duel_mult"]) > 1.0:
        pct = int((float(pending_buff["duel_mult"]) - 1.0) * 100)
        buff_parts.append(f"**/duel** +{pct}%")
    if pending_buff.get("cc_immunity"):
        risk = int(float(pending_buff.get("attack_hp_risk_chance") or 0) * 100)
        hp_loss = int(float(pending_buff.get("attack_hp_risk_pct") or 0) * 100)
        buff_parts.append(f"CC immune · {risk}%/-{hp_loss}% HP per hit")
    return buff_parts


async def user_lab_slot_count(cog: commands.Cog, user_id: int, guild_id: int) -> int:
    from utils.dealer_ranks import dealer_rank, lab_slot_count
    from utils.legacy_perks import extra_lab_slots_from_perks

    stats = await cog.bot.db.get_drug_stats(user_id, guild_id)
    rank = dealer_rank(
        units_sold=stats["units_sold"],
        units_harvested=stats["units_harvested"],
    )
    legacy = await cog.bot.db.list_legacy_perks(user_id, guild_id)
    return lab_slot_count(rank=rank, legacy_extra=extra_lab_slots_from_perks(legacy))


async def build_lab_embed(
    cog: commands.Cog, guild_id: int, user_id: int,
) -> tuple[discord.Embed, discord.File]:
    grows = await cog.bot.db.list_drug_grows(user_id, guild_id)
    inventory = await cog.bot.db.get_drug_inventory(user_id, guild_id)
    pending_buff = await cog.bot.db.peek_pending_drug_buff(user_id, guild_id)
    stats = await cog.bot.db.get_drug_stats(user_id, guild_id)
    from utils.dealer_ranks import dealer_rank, dealer_reputation, next_rank_threshold, rank_title

    rank = dealer_rank(
        units_sold=stats["units_sold"],
        units_harvested=stats["units_harvested"],
    )
    max_slots = await user_lab_slot_count(cog, user_id, guild_id)
    now = time.time()

    embed = discord.Embed(
        title="🧪 Grow Lab",
        description=(
            "Plant strains and cook product, wait for it to mature, then harvest and sell on the "
            "street or to other players — or **use** it for effects. Watch out for raids!"
        ),
        color=discord.Color.dark_green(),
    )

    if grows:
        lines = []
        ready_count = 0
        for g in grows:
            defn = drug_by_id(str(g["drug_id"]))
            name = defn.name if defn else str(g["drug_id"])
            emoji = defn.emoji if defn else "🌱"
            ready_at = float(g["ready_at"])
            yield_mult = float(g.get("yield_mult") or 1.0)
            fert_note = f" · **{yield_mult:g}×** yield" if yield_mult > 1.0 else ""
            if ready_at <= now:
                lines.append(f"{emoji} **{name}** — ✅ ready to harvest{fert_note}")
                ready_count += 1
            else:
                lines.append(f"{emoji} **{name}** — ready <t:{int(ready_at)}:R>{fert_note}")
        footer = f"{len(grows)}/{max_slots} slots used"
        if ready_count:
            footer += f" · {ready_count} ready"
        embed.add_field(name="Grow slots", value=clip_embed_field("\n".join(lines)), inline=False)
    else:
        embed.add_field(
            name="Grow slots",
            value=f"_Empty — plant a strain below ({max_slots} slots)._",
            inline=False,
        )
        footer = ""

    if inventory:
        inv_lines = []
        for drug_id, qty in inventory.items():
            defn = drug_by_id(drug_id)
            name = defn.name if defn else drug_id
            emoji = defn.emoji if defn else "📦"
            price = defn.street_price if defn else 0
            effect = f" · _{defn.effect_summary}_" if defn else ""
            inv_lines.append(f"{emoji} **{name}** ×{qty} · ~{fmt_amount(price)}/unit{effect}")
        embed.add_field(name="Stash", value=clip_embed_field("\n".join(inv_lines)), inline=False)

    if pending_buff:
        buff_parts = _active_drug_buff_lines(pending_buff)
        embed.add_field(
            name="Active high",
            value=clip_embed_field(
                f"**{pending_buff['name']}** — {' · '.join(buff_parts)} "
                f"· expires <t:{int(float(pending_buff['expires']))}:R>",
            ),
            inline=False,
        )

    png = render_lab_image()
    file = discord.File(io.BytesIO(png), filename="lab.png")
    embed.set_image(url="attachment://lab.png")
    next_thr = next_rank_threshold(rank)
    rep = dealer_reputation(
        units_sold=stats["units_sold"],
        units_harvested=stats["units_harvested"],
    )
    rank_line = f"Dealer rank **{rank}** ({rank_title(rank)}) · **{rep:,}** rep"
    if next_thr is not None:
        rank_line += f" · {max(0, next_thr - rep):,} to next rank"
    embed.set_footer(text=f"{footer} · {rank_line}" if footer else rank_line)
    return embed, file


class PlantCategorySelect(discord.ui.Select):
    def __init__(self, view: "DrugLabView", *, selected: str) -> None:
        self._view = view
        options = [
            discord.SelectOption(
                label=DRUG_CATEGORY_LABELS.get(category, category.title()),
                value=category,
                default=category == selected,
            )
            for category in drugs_by_category()
        ]
        super().__init__(
            placeholder="Product category…",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self._view.plant_category = self.values[0]
        await interaction.response.defer()
        try:
            await _apply_lab_panel(
                interaction,
                self._view.cog,
                self._view.guild_id,
                self._view.user_id,
                plant_category=self.values[0],
            )
        except Exception:
            logger.exception(
                "lab category switch failed user=%s guild=%s",
                self._view.user_id,
                self._view.guild_id,
            )
            await interaction.followup.send(
                "Could not refresh the lab — try again.", ephemeral=True,
            )


class PlantSelect(discord.ui.Select):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int, *, category: str) -> None:
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        options = [
            discord.SelectOption(
                label=defn.name,
                value=defn.drug_id,
                description=(
                    f"{DRUG_CATEGORY_LABELS.get(defn.category, defn.category.title())} · "
                    f"seed {fmt_amount(defn.seed_cost)} · {defn.grow_seconds // 60}m"
                )[:100],
                emoji=defn.emoji,
            )
            for defn in drugs_for_category(category)
        ]
        super().__init__(
            placeholder=f"Plant {DRUG_CATEGORY_LABELS.get(category, category)}…",
            min_values=1,
            max_values=1,
            options=options,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        cost, err = await self.cog.bot.db.plant_drug(self.user_id, self.guild_id, self.values[0])
        messages = {
            "invalid_drug": "Unknown strain.",
            "no_slots": f"All {config.DRUG_LAB_SLOTS} lab slots are busy. Harvest first.",
            "insufficient_funds": f"Seeds cost **{fmt_amount(cost)}**.",
        }
        if err:
            await interaction.response.send_message(messages.get(err, err), ephemeral=True)
            return
        await interaction.response.defer()
        try:
            await record_quest_event(self.cog.bot.db, self.guild_id, self.user_id, "drug_plant")
            defn = drug_by_id(self.values[0])
            desc = f"🌱 Planted **{defn.name if defn else 'a strain'}** for **{fmt_amount(cost)}**."
            await _apply_lab_panel(
                interaction, self.cog, self.guild_id, self.user_id, description=desc,
            )
        except Exception:
            logger.exception(
                "lab plant UI refresh failed user=%s guild=%s",
                self.user_id,
                self.guild_id,
            )
            await interaction.followup.send(
                "Planted, but the panel could not refresh — reopen `/drugs lab`.",
                ephemeral=True,
            )


class FertilizeSelect(discord.ui.Select):
    def __init__(
        self,
        cog: commands.Cog,
        guild_id: int,
        user_id: int,
        grows: list[dict[str, object]],
        fert_counts: dict[str, int],
    ) -> None:
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        options: list[discord.SelectOption] = []
        for g in grows:
            if float(g.get("yield_mult") or 1.0) > 1.0:
                continue
            defn = drug_by_id(str(g["drug_id"]))
            grow_name = defn.name if defn else str(g["drug_id"])
            grow_id = int(g["grow_id"])
            for fert in FERTILIZERS:
                if fert_counts.get(fert.item_id, 0) <= 0:
                    continue
                options.append(
                    discord.SelectOption(
                        label=f"{fert.name} → {grow_name}"[:100],
                        value=f"{grow_id}:{fert.item_id}",
                        description=(
                            f"{fert.yield_mult:g}× yield · "
                            f"{int((1 - fert.grow_time_mult) * 100)}% faster"
                        )[:100],
                        emoji=fert.emoji,
                    ),
                )
        super().__init__(
            placeholder="Apply fertilizer to a crop…",
            min_values=1,
            max_values=1,
            options=options[:25] or [
                discord.SelectOption(label="No crops need fertilizer", value="_none"),
            ],
            disabled=not options,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "_none":
            await interaction.response.send_message(
                "No crops need fertilizer right now.", ephemeral=True,
            )
            return
        grow_id_str, fert_id = self.values[0].split(":", 1)
        err = await self.cog.bot.db.apply_fertilizer_to_grow(
            self.user_id, self.guild_id, int(grow_id_str), fert_id,
        )
        messages = {
            "invalid_fertilizer": "Unknown fertilizer.",
            "no_fertilizer": "You do not have that fertilizer — buy from `/shop`.",
            "invalid_grow": "That crop is gone.",
            "already_fertilized": "That crop already has fertilizer applied.",
        }
        if err:
            await interaction.response.send_message(messages.get(err, err), ephemeral=True)
            return
        await interaction.response.defer()
        try:
            fert = fertilizer_by_id(fert_id)
            name = fert.name if fert else "Fertilizer"
            desc = (
                f"{fert.emoji if fert else '🧪'} Applied **{name}** — "
                "faster grow and bigger harvest!"
            )
            await _apply_lab_panel(
                interaction, self.cog, self.guild_id, self.user_id, description=desc,
            )
        except Exception:
            logger.exception(
                "lab fertilize UI refresh failed user=%s guild=%s",
                self.user_id,
                self.guild_id,
            )
            await interaction.followup.send(
                "Fertilizer applied, but the panel could not refresh.", ephemeral=True,
            )


class StashActionSelect(discord.ui.Select):
    def __init__(
        self,
        cog: commands.Cog,
        guild_id: int,
        user_id: int,
        inventory: dict[str, int],
    ) -> None:
        options = stash_action_options(inventory)
        super().__init__(
            placeholder="Stash: sell or use product…",
            options=options or [discord.SelectOption(label="Empty stash", value="_none")],
            disabled=not options,
            row=3,
        )
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "_none":
            await interaction.response.send_message("Nothing in your stash.", ephemeral=True)
            return
        action, drug_id = self.values[0].split(":", 1)
        if action == "sell":
            await interaction.response.send_modal(
                StreetSellModal(self.cog, self.guild_id, self.user_id, drug_id),
            )
            return
        if action != "use":
            await interaction.response.send_message("Unknown stash action.", ephemeral=True)
            return
        await interaction.response.defer()
        result = await consume_stash_product(self.cog, self.guild_id, self.user_id, drug_id)
        if result.get("error"):
            messages = {
                "invalid_drug": "Unknown product.",
                "insufficient_product": "You don't have any of that left.",
            }
            await interaction.followup.send(
                messages.get(str(result["error"]), "Could not use."), ephemeral=True,
            )
            return
        try:
            await record_quest_event(self.cog.bot.db, self.guild_id, self.user_id, "drug_use")
            await _apply_lab_panel(
                interaction,
                self.cog,
                self.guild_id,
                self.user_id,
                description=format_consume_message(result),
            )
        except Exception:
            logger.exception(
                "lab stash use UI refresh failed user=%s guild=%s",
                self.user_id,
                self.guild_id,
            )
            await interaction.followup.send(
                "Product used, but the panel could not refresh.", ephemeral=True,
            )


class StreetSellModal(discord.ui.Modal, title="Sell on the street"):
    quantity = discord.ui.TextInput(label="Quantity to sell", placeholder="e.g. 5", required=True, max_length=8)

    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int, drug_id: str) -> None:
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.drug_id = drug_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            qty = int(str(self.quantity.value).replace(",", "").strip())
        except ValueError:
            await interaction.response.send_message("Enter a whole number.", ephemeral=True)
            return
        if qty <= 0:
            await interaction.response.send_message("Enter a positive amount.", ephemeral=True)
            return
        result = await self.cog.bot.db.sell_drugs_street(self.user_id, self.guild_id, self.drug_id, qty)
        if result.get("error"):
            messages = {
                "invalid_drug": "Unknown strain.",
                "invalid_amount": "Enter a valid quantity.",
                "insufficient_product": "You don't have that much product.",
            }
            await interaction.response.send_message(
                messages.get(str(result["error"]), "Could not sell."), ephemeral=True,
            )
            return
        await interaction.response.defer()
        try:
            await record_quest_event(self.cog.bot.db, self.guild_id, self.user_id, "drug_sell")
            from utils.achievements import evaluate_unlocks, format_unlock_message

            unlocked = await evaluate_unlocks(
                self.cog.bot.db, self.guild_id, self.user_id,
            )
            ach = format_unlock_message(unlocked)
            defn = drug_by_id(self.drug_id)
            name = defn.name if defn else self.drug_id
            if result.get("raided"):
                desc = (
                    f"🚨 **Raided!** Lost **{int(result['lost'])} {name}** in a bust. No payout."
                )
            else:
                desc = (
                    f"💵 Sold **{int(result['quantity'])} {name}** on the street for "
                    f"**{fmt_amount(float(result['total']))}**."
                )
            if ach:
                desc += f"\n\n{ach}"
            await _apply_lab_panel(
                interaction, self.cog, self.guild_id, self.user_id, description=desc,
            )
        except Exception:
            logger.exception(
                "lab street sell UI refresh failed user=%s guild=%s",
                self.user_id,
                self.guild_id,
            )
            await interaction.followup.send(
                "Sale processed, but the panel could not refresh.", ephemeral=True,
            )


class DrugLabView(discord.ui.View):
    def __init__(
        self,
        cog: commands.Cog,
        guild_id: int,
        user_id: int,
        *,
        plant_category: str = "cannabis",
    ) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.plant_category = plant_category

    @classmethod
    async def build(
        cls,
        cog: commands.Cog,
        guild_id: int,
        user_id: int,
        *,
        plant_category: str | None = None,
    ) -> "DrugLabView":
        categories = list(drugs_by_category())
        category = plant_category if plant_category in categories else categories[0]
        view = cls(cog, guild_id, user_id, plant_category=category)
        view.add_item(PlantCategorySelect(view, selected=category))
        view.add_item(PlantSelect(cog, guild_id, user_id, category=category))
        grows = await cog.bot.db.list_drug_grows(user_id, guild_id)
        fert_counts: dict[str, int] = {}
        for fert in FERTILIZERS:
            fert_counts[fert.item_id] = await cog.bot.db.get_inventory_quantity(
                user_id, guild_id, fert.item_id,
            )
        view.add_item(FertilizeSelect(cog, guild_id, user_id, grows, fert_counts))
        inventory = await cog.bot.db.get_drug_inventory(user_id, guild_id)
        view.add_item(StashActionSelect(cog, guild_id, user_id, inventory))
        return view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your lab.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="🌾 Harvest", style=discord.ButtonStyle.success, row=4)
    async def harvest_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await interaction.response.defer()
        try:
            harvested = await self.cog.bot.db.harvest_drugs(self.user_id, self.guild_id)
            if harvested:
                await record_quest_event(
                    self.cog.bot.db, self.guild_id, self.user_id, "drug_harvest",
                )
                from utils.achievements import ACHIEVEMENTS, evaluate_unlocks, format_unlock_message

                ach_msg = ""
                if await self.cog.bot.db.unlock_achievement(
                    self.user_id, self.guild_id, "first_harvest",
                ):
                    ach_msg = format_unlock_message([ACHIEVEMENTS["first_harvest"]])
                unlocked = await evaluate_unlocks(
                    self.cog.bot.db, self.guild_id, self.user_id,
                )
                extra = format_unlock_message(unlocked)
                if extra:
                    ach_msg = f"{ach_msg}\n{extra}" if ach_msg else extra
                parts = []
                for drug_id, qty in harvested.items():
                    defn = drug_by_id(drug_id)
                    parts.append(
                        f"{defn.emoji if defn else ''} {qty} {defn.name if defn else drug_id}",
                    )
                desc = "🌾 Harvested " + ", ".join(parts) + "!"
                if ach_msg:
                    desc += f"\n\n{ach_msg}"
            else:
                desc = "Nothing ready to harvest yet."
            await _apply_lab_panel(
                interaction, self.cog, self.guild_id, self.user_id, description=desc,
            )
        except Exception:
            logger.exception(
                "lab harvest failed user=%s guild=%s", self.user_id, self.guild_id,
            )
            await interaction.followup.send(
                "Harvest failed — try again in a moment.", ephemeral=True,
            )

    @discord.ui.button(label="🏪 Market", style=discord.ButtonStyle.primary, row=4)
    async def market_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        embed = await build_drug_market_embed(self.cog, interaction.guild, self.user_id)
        view = await DrugMarketView.build(self.cog, self.guild_id, self.user_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=4)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await interaction.response.defer()
        try:
            await _apply_lab_panel(interaction, self.cog, self.guild_id, self.user_id)
        except Exception:
            logger.exception(
                "lab refresh failed user=%s guild=%s", self.user_id, self.guild_id,
            )
            await interaction.followup.send(
                "Could not refresh the lab — try again.", ephemeral=True,
            )


async def build_drug_market_embed(
    cog: commands.Cog, guild: discord.Guild, user_id: int,
) -> discord.Embed:
    listings = await cog.bot.db.list_drug_market(guild.id)
    embed = discord.Embed(
        title="🏪 Black Market",
        description="Buy product from other players or list your own stash for sale.",
        color=discord.Color.dark_teal(),
    )
    if listings:
        lines = []
        for listing in listings:
            defn = drug_by_id(str(listing["drug_id"]))
            name = defn.name if defn else str(listing["drug_id"])
            emoji = defn.emoji if defn else "📦"
            seller = guild.get_member(int(listing["seller_id"]))
            seller_name = seller.display_name if seller else f"User {listing['seller_id']}"
            lines.append(
                f"`#{int(listing['listing_id'])}` {emoji} **{name}** ×{int(listing['quantity'])} "
                f"@ {fmt_amount(float(listing['price_per_unit']))}/unit — {seller_name}",
            )
        embed.add_field(name="Listings", value="\n".join(lines[:15]), inline=False)
    else:
        embed.add_field(name="Listings", value="_No listings yet._", inline=False)
    my_listings = await cog.bot.db.list_user_drug_listings(user_id, guild.id)
    if my_listings:
        mine = []
        for listing in my_listings:
            defn = drug_by_id(str(listing["drug_id"]))
            name = defn.name if defn else str(listing["drug_id"])
            emoji = defn.emoji if defn else "📦"
            mine.append(
                f"`#{int(listing['listing_id'])}` {emoji} **{name}** ×{int(listing['quantity'])} "
                f"@ {fmt_amount(float(listing['price_per_unit']))}/unit",
            )
        embed.add_field(
            name="Your listings",
            value="\n".join(mine[:10]) + ("\n_Use the dropdown below to unlist._" if mine else ""),
            inline=False,
        )
    embed.set_footer(text=f"Market tax {int(config.DRUG_MARKET_TAX * 100)}% on sales · unlist returns product to stash")
    return embed


class ListProductSelect(discord.ui.Select):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int, options: list[discord.SelectOption]) -> None:
        super().__init__(
            placeholder="Choose product to list…",
            options=options or [discord.SelectOption(label="Empty stash", value="_none")],
            disabled=not options,
        )
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "_none":
            await interaction.response.send_message("Nothing in your stash to list.", ephemeral=True)
            return
        await interaction.response.send_modal(
            ListProductPriceModal(self.cog, self.guild_id, self.user_id, self.values[0]),
        )


class ListProductPriceModal(discord.ui.Modal, title="List product for sale"):
    quantity = discord.ui.TextInput(label="Quantity", placeholder="e.g. 5", required=True, max_length=8)
    price = discord.ui.TextInput(label="Price per unit", placeholder="e.g. 150", required=True, max_length=12)

    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int, drug_id: str) -> None:
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.drug_id = drug_id
        defn = drug_by_id(drug_id)
        if defn is not None:
            self.title = f"List {defn.name}"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            qty = int(str(self.quantity.value).replace(",", "").strip())
            price = float(str(self.price.value).replace(",", "").strip())
        except ValueError:
            await interaction.response.send_message("Enter valid numbers.", ephemeral=True)
            return
        err = await self.cog.bot.db.create_drug_listing(
            self.user_id, self.guild_id, self.drug_id, qty, price,
        )
        messages = {
            "invalid_drug": "Unknown product.",
            "invalid_amount": "Enter a valid quantity and price.",
            "insufficient_product": "You don't have that much product to list.",
        }
        if err:
            await interaction.response.send_message(messages.get(err, err), ephemeral=True)
            return
        await record_quest_event(self.cog.bot.db, self.guild_id, self.user_id, "drug_list")
        embed = await build_drug_market_embed(self.cog, interaction.guild, self.user_id)
        view = await DrugMarketView.build(self.cog, self.guild_id, self.user_id)
        defn = drug_by_id(self.drug_id)
        embed.description = (
            f"📋 Listed **{qty} {defn.name if defn else self.drug_id}** at "
            f"**{fmt_amount(price)}**/unit."
        )
        await interaction.response.edit_message(embed=embed, view=view)


class BuyListingModal(discord.ui.Modal, title="Buy from listing"):
    listing = discord.ui.TextInput(label="Listing # (id)", placeholder="e.g. 12", required=True, max_length=10)
    quantity = discord.ui.TextInput(label="Quantity", placeholder="e.g. 3", required=True, max_length=8)

    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            listing_id = int(str(self.listing.value).strip().lstrip("#"))
            qty = int(str(self.quantity.value).replace(",", "").strip())
        except ValueError:
            await interaction.response.send_message("Enter valid numbers.", ephemeral=True)
            return
        result = await self.cog.bot.db.buy_drug_listing(self.user_id, self.guild_id, listing_id, qty)
        if result.get("error"):
            messages = {
                "invalid_amount": "Enter a valid quantity.",
                "not_found": "That listing no longer exists.",
                "own_listing": "You can't buy your own listing.",
                "not_enough_listed": "The listing doesn't have that many units.",
                "insufficient_funds": f"You need **{fmt_amount(float(result.get('total', 0)))}**.",
            }
            await interaction.response.send_message(
                messages.get(str(result["error"]), "Could not buy."), ephemeral=True,
            )
            return
        await record_quest_event(self.cog.bot.db, self.guild_id, self.user_id, "drug_buy")
        defn = drug_by_id(str(result["drug_id"]))
        embed = await build_drug_market_embed(self.cog, interaction.guild, self.user_id)
        view = await DrugMarketView.build(self.cog, self.guild_id, self.user_id)
        embed.description = (
            f"🛒 Bought **{int(result['quantity'])} {defn.name if defn else result['drug_id']}** "
            f"for **{fmt_amount(float(result['total']))}**."
        )
        await interaction.response.edit_message(embed=embed, view=view)


class UnlistListingSelect(discord.ui.Select):
    def __init__(
        self,
        cog: commands.Cog,
        guild_id: int,
        user_id: int,
        listings: list[dict[str, object]],
    ) -> None:
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        if listings:
            options = []
            for listing in listings[:25]:
                defn = drug_by_id(str(listing["drug_id"]))
                name = defn.name if defn else str(listing["drug_id"])
                emoji = defn.emoji if defn else "📦"
                listing_id = int(listing["listing_id"])
                qty = int(listing["quantity"])
                price = fmt_amount(float(listing["price_per_unit"]))
                options.append(
                    discord.SelectOption(
                        label=f"#{listing_id} {name} ×{qty}"[:100],
                        value=str(listing_id),
                        description=f"{emoji} @ {price}/unit — returns to stash"[:100],
                    ),
                )
            disabled = False
        else:
            options = [discord.SelectOption(label="No active listings", value="_none")]
            disabled = True
        super().__init__(
            placeholder="Unlist your product…",
            options=options,
            disabled=disabled,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "_none":
            await interaction.response.send_message("You have no listings to remove.", ephemeral=True)
            return
        listing_id = int(self.values[0])
        err = await self.cog.bot.db.cancel_drug_listing(self.user_id, self.guild_id, listing_id)
        messages = {
            "not_found": "That listing no longer exists.",
            "not_owner": "You can only unlist your own product.",
        }
        if err:
            await interaction.response.send_message(messages.get(err, "Could not unlist."), ephemeral=True)
            return
        embed = await build_drug_market_embed(self.cog, interaction.guild, self.user_id)
        view = await DrugMarketView.build(self.cog, self.guild_id, self.user_id)
        embed.description = f"📤 Unlisted listing **#{listing_id}** — product returned to your stash."
        await interaction.response.edit_message(embed=embed, view=view)


class DrugMarketView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    @classmethod
    async def build(cls, cog: commands.Cog, guild_id: int, user_id: int) -> DrugMarketView:
        view = cls(cog, guild_id, user_id)
        inventory = await cog.bot.db.get_drug_inventory(user_id, guild_id)
        view.add_item(ListProductSelect(cog, guild_id, user_id, stash_select_options(inventory)))
        my_listings = await cog.bot.db.list_user_drug_listings(user_id, guild_id)
        view.add_item(UnlistListingSelect(cog, guild_id, user_id, my_listings))
        return view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your market panel.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="🛒 Buy", style=discord.ButtonStyle.success, row=1)
    async def buy_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await interaction.response.send_modal(BuyListingModal(self.cog, self.guild_id, self.user_id))

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=1)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        embed = await build_drug_market_embed(self.cog, interaction.guild, self.user_id)
        view = await DrugMarketView.build(self.cog, self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)


async def build_drug_catalog_embed() -> discord.Embed:
    from utils.drugs import drugs_by_category

    embed = discord.Embed(
        title="🧪 Drug Catalog",
        description="All growable products, street prices, and consume effects.",
        color=discord.Color.purple(),
    )
    category_labels = {
        "cannabis": "🌿 Cannabis (THC strains)",
        "stimulant": "⚡ Stimulants (incl. addies)",
        "codeine": "💊 Codeine",
        "lean": "🍇 Lean (Hi-Tech, Wockhardt, Tris, etc.)",
        "opioid": "💉 Opioids",
        "psychedelic": "🌈 Psychedelics",
    }
    for category, items in drugs_by_category().items():
        lines = []
        for defn in items:
            grow_mins = defn.grow_seconds // 60
            lines.append(
                f"{defn.emoji} **{defn.name}** (`{defn.drug_id}`)\n"
                f"Seed {fmt_amount(defn.seed_cost)} · {grow_mins}m grow · "
                f"~{fmt_amount(defn.street_price)}/unit\n"
                f"_{defn.effect_summary}_",
            )
        embed.add_field(
            name=category_labels.get(category, category.title()),
            value="\n\n".join(lines),
            inline=False,
        )
    embed.set_footer(text="Use /drugs lab to grow · consume from stash for effects")
    return embed


async def build_stash_embed(
    cog: commands.Cog, guild_id: int, user_id: int,
) -> discord.Embed:
    inventory = await cog.bot.db.get_drug_inventory(user_id, guild_id)
    pending_buff = await cog.bot.db.peek_pending_drug_buff(user_id, guild_id)
    embed = discord.Embed(
        title="📦 Your Stash",
        description="_Product ready to sell, trade, or use._",
        color=discord.Color.dark_green(),
    )
    if inventory:
        lines = []
        for drug_id, qty in inventory.items():
            defn = drug_by_id(drug_id)
            name = defn.name if defn else drug_id
            emoji = defn.emoji if defn else "📦"
            effect = f" — _{defn.effect_summary}_" if defn else ""
            lines.append(f"{emoji} **{name}** ×{qty}{effect}")
        embed.add_field(name="Inventory", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Inventory", value="_Empty — harvest from /drugs lab._", inline=False)
    if pending_buff:
        buff_parts = _active_drug_buff_lines(pending_buff)
        embed.add_field(
            name="Active high",
            value=clip_embed_field(
                f"**{pending_buff['name']}** — {' · '.join(buff_parts)} "
                f"· expires <t:{int(float(pending_buff['expires']))}:R>",
            ),
            inline=False,
        )
    return embed


async def send_drug_lab_panel(interaction: discord.Interaction, cog: commands.Cog) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        embed, file = await build_lab_embed(cog, interaction.guild_id, interaction.user.id)
        view = await DrugLabView.build(cog, interaction.guild_id, interaction.user.id)
    except Exception:
        logger.exception(
            "drug lab panel build failed user=%s guild=%s",
            interaction.user.id,
            interaction.guild_id,
        )
        await interaction.followup.send(
            "Could not open the lab — try again in a moment.", ephemeral=True,
        )
        return
    try:
        await interaction.followup.send(embed=embed, file=file, view=view)
    except discord.HTTPException:
        logger.exception(
            "drug lab panel send failed user=%s guild=%s",
            interaction.user.id,
            interaction.guild_id,
        )
        try:
            embed.set_image(url=None)
            await interaction.followup.send(embed=embed, view=view)
        except Exception:
            logger.exception(
                "drug lab panel fallback send failed user=%s guild=%s",
                interaction.user.id,
                interaction.guild_id,
            )
            await interaction.followup.send(
                "Could not open the lab — try again in a moment.", ephemeral=True,
            )


async def send_drug_market_panel(interaction: discord.Interaction, cog: commands.Cog) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return
    embed = await build_drug_market_embed(cog, interaction.guild, interaction.user.id)
    view = await DrugMarketView.build(cog, interaction.guild.id, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view)
