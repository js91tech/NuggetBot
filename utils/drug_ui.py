"""Interactive drug trade: grow lab, inventory, street sales, and player market."""
from __future__ import annotations

import io
import time
from typing import TYPE_CHECKING

import discord

import config
from utils.drug_art import render_lab_image
from utils.drug_encounter import (
    CopEncounterResult,
    build_cop_encounter_embed,
    execute_cop_fight,
    execute_cop_flee,
)
from utils.drugs import DRUGS, drug_by_id, format_consume_message, format_street_sale_bonus
from utils.helpers import fmt_amount
from utils.quests import record_quest_event

if TYPE_CHECKING:
    from discord.ext import commands


async def player_max_hp(cog: commands.Cog, user_id: int, guild_id: int) -> float:
    from utils.combat_engine import max_hp_from_armor
    from utils.classes import get_modifiers
    from utils.stats import combat_bonuses_from_attributes

    loadout = await cog.bot.db.get_combat_loadout(user_id, guild_id)
    class_id = await cog.bot.db.get_class_id(user_id, guild_id)
    attrs = await cog.bot.db.get_character_attributes(user_id, guild_id)
    attr_bonuses = combat_bonuses_from_attributes(attrs)
    return float(
        max_hp_from_armor(
            loadout.armor,
            class_modifiers=get_modifiers(class_id),
            attr_hp_bonus=attr_bonuses.hp_bonus,
            accessory_bonuses=loadout.accessory_bonuses,
        ),
    )


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


async def consume_stash_product(
    cog: commands.Cog, guild_id: int, user_id: int, drug_id: str,
) -> dict[str, object]:
    max_hp = await player_max_hp(cog, user_id, guild_id)
    return await cog.bot.db.consume_drug(user_id, guild_id, drug_id, max_hp=max_hp)


async def build_lab_embed(
    cog: commands.Cog, guild_id: int, user_id: int,
) -> tuple[discord.Embed, discord.File]:
    grows = await cog.bot.db.list_drug_grows(user_id, guild_id)
    inventory = await cog.bot.db.get_drug_inventory(user_id, guild_id)
    pending_buff = await cog.bot.db.peek_pending_drug_buff(user_id, guild_id)
    sale_breakdown = await cog.bot.db.get_street_sale_breakdown(user_id, guild_id)
    sale_mult = float(sale_breakdown["multiplier"])
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
            if ready_at <= now:
                lines.append(f"{emoji} **{name}** — ✅ ready to harvest")
                ready_count += 1
            else:
                lines.append(f"{emoji} **{name}** — ready <t:{int(ready_at)}:R>")
        footer = f"{len(grows)}/{config.DRUG_LAB_SLOTS} slots used"
        if ready_count:
            footer += f" · {ready_count} ready"
        if sale_mult > 1.001:
            footer += (
                f" · Street ×{sale_mult:.2f} (rep {sale_breakdown['reputation_level']}, "
                f"influence {int(float(sale_breakdown['influence_pct']))}%)"
            )
        embed.add_field(name="Grow slots", value="\n".join(lines), inline=False)
        embed.set_footer(text=footer)
    else:
        embed.add_field(
            name="Grow slots",
            value=f"_Empty — plant a strain below ({config.DRUG_LAB_SLOTS} slots)._",
            inline=False,
        )
        if sale_mult > 1.001:
            embed.set_footer(
                text=(
                    f"Street prices ×{sale_mult:.2f} "
                    f"(rep {sale_breakdown['reputation_level']}, "
                    f"influence {int(float(sale_breakdown['influence_pct']))}%)"
                ),
            )

    if inventory:
        inv_lines = []
        for drug_id, qty in inventory.items():
            defn = drug_by_id(drug_id)
            name = defn.name if defn else drug_id
            emoji = defn.emoji if defn else "📦"
            price = defn.street_price * sale_mult if defn else 0
            effect = f" · _{defn.effect_summary}_" if defn else ""
            inv_lines.append(f"{emoji} **{name}** ×{qty} · ~{fmt_amount(price)}/unit{effect}")
        embed.add_field(name="Stash", value="\n".join(inv_lines), inline=False)

    if pending_buff:
        buff_parts = []
        if float(pending_buff["boss_mult"]) > 1.0:
            pct = int((float(pending_buff["boss_mult"]) - 1.0) * 100)
            buff_parts.append(f"**/attack** +{pct}%")
        if float(pending_buff["duel_mult"]) > 1.0:
            pct = int((float(pending_buff["duel_mult"]) - 1.0) * 100)
            buff_parts.append(f"**/duel** +{pct}%")
        embed.add_field(
            name="Active high",
            value=(
                f"**{pending_buff['name']}** — {' · '.join(buff_parts)} "
                f"· expires <t:{int(float(pending_buff['expires']))}:R>"
            ),
            inline=False,
        )

    png = render_lab_image()
    file = discord.File(io.BytesIO(png), filename="lab.png")
    embed.set_image(url="attachment://lab.png")
    return embed, file


class PlantSelect(discord.ui.Select):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        options = [
            discord.SelectOption(
                label=defn.name,
                value=defn.drug_id,
                description=f"{defn.category.title()} · seed {fmt_amount(defn.seed_cost)} · {defn.grow_seconds // 60}m"[:100],
                emoji=defn.emoji,
            )
            for defn in DRUGS
        ]
        super().__init__(placeholder="Plant a strain…", options=options, row=0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

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
        await record_quest_event(self.cog.bot.db, self.guild_id, self.user_id, "drug_plant")
        defn = drug_by_id(self.values[0])
        view = await DrugLabView.build(self.cog, self.guild_id, self.user_id)
        embed, file = await build_lab_embed(self.cog, self.guild_id, self.user_id)
        embed.description = f"🌱 Planted **{defn.name if defn else 'a strain'}** for **{fmt_amount(cost)}**."
        await interaction.response.edit_message(embed=embed, attachments=[file], view=view)


class UseSelect(discord.ui.Select):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int, options: list[discord.SelectOption]) -> None:
        super().__init__(
            placeholder="Use product (consume for effects)…",
            options=options or [discord.SelectOption(label="Empty stash", value="_none")],
            disabled=not options,
            row=2,
        )
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "_none":
            await interaction.response.send_message("Nothing to use.", ephemeral=True)
            return
        result = await consume_stash_product(self.cog, self.guild_id, self.user_id, self.values[0])
        if result.get("error"):
            messages = {
                "invalid_drug": "Unknown product.",
                "insufficient_product": "You don't have any of that left.",
            }
            await interaction.response.send_message(
                messages.get(str(result["error"]), "Could not use."), ephemeral=True,
            )
            return
        await record_quest_event(self.cog.bot.db, self.guild_id, self.user_id, "drug_use")
        view = await DrugLabView.build(self.cog, self.guild_id, self.user_id)
        embed, file = await build_lab_embed(self.cog, self.guild_id, self.user_id)
        embed.description = format_consume_message(result)
        await interaction.response.edit_message(embed=embed, attachments=[file], view=view)


class SellSelect(discord.ui.Select):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int, options: list[discord.SelectOption]) -> None:
        super().__init__(
            placeholder="Sell product on the street…",
            options=options or [discord.SelectOption(label="Empty stash", value="_none")],
            disabled=not options,
            row=1,
        )
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            StreetSellModal(self.cog, self.guild_id, self.user_id, self.values[0]),
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
        max_hp = await player_max_hp(self.cog, self.user_id, self.guild_id)
        result = await self.cog.bot.db.sell_drugs_street(
            self.user_id, self.guild_id, self.drug_id, qty, player_max_hp=max_hp,
        )
        if result.get("error"):
            messages = {
                "invalid_drug": "Unknown strain.",
                "invalid_amount": "Enter a valid quantity.",
                "insufficient_product": "You don't have that much product.",
                "cop_encounter_active": "You are already in a police bust — finish it first.",
            }
            await interaction.response.send_message(
                messages.get(str(result["error"]), "Could not sell."), ephemeral=True,
            )
            return
        if result.get("encounter"):
            encounter = await self.cog.bot.db.get_drug_cop_encounter(self.user_id, self.guild_id)
            if encounter is None:
                await interaction.response.send_message("Bust failed to start.", ephemeral=True)
                return
            embed, file = await build_cop_encounter_embed(encounter)
            view = StreetCopEncounterView(self.cog, self.guild_id, self.user_id)
            await interaction.response.edit_message(
                embed=embed,
                attachments=[file] if file is not None else [],
                view=view,
            )
            return
        await record_quest_event(self.cog.bot.db, self.guild_id, self.user_id, "drug_sell")
        defn = drug_by_id(self.drug_id)
        name = defn.name if defn else self.drug_id
        view = await DrugLabView.build(self.cog, self.guild_id, self.user_id)
        embed, file = await build_lab_embed(self.cog, self.guild_id, self.user_id)
        if result.get("raided"):
            embed.description = (
                f"🚨 **Raided!** Lost **{int(result['lost'])} {name}** in a bust. No payout."
            )
        else:
            bonus = format_street_sale_bonus(
                float(result.get("sale_multiplier") or 1.0),
                reputation_level=int(result.get("reputation_level") or 0),
                influence_pct=float(result.get("influence_pct") or 0),
            )
            embed.description = (
                f"💵 Sold **{int(result['quantity'])} {name}** on the street for "
                f"**{fmt_amount(float(result['total']))}**{bonus}."
            )
        await interaction.response.edit_message(embed=embed, attachments=[file], view=view)


class StreetCopEncounterView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=300.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your bust.", ephemeral=True)
            return False
        return True

    async def _apply_result(self, interaction: discord.Interaction, result: CopEncounterResult) -> None:
        if result.error and result.finished:
            await interaction.response.send_message(result.error, ephemeral=True)
            return
        attachments = [result.file] if result.file is not None else []
        if result.finished:
            self.stop()
            if result.record_sale_quest:
                await record_quest_event(self.cog.bot.db, self.guild_id, self.user_id, "drug_sell")
            if result.lab_description:
                lab_embed, lab_file = await build_lab_embed(self.cog, self.guild_id, self.user_id)
                lab_embed.description = result.lab_description
                lab_view = await DrugLabView.build(self.cog, self.guild_id, self.user_id)
                await interaction.response.edit_message(
                    embed=lab_embed,
                    attachments=[lab_file],
                    view=lab_view,
                )
                return
        await interaction.response.edit_message(
            embed=result.embed,
            attachments=attachments,
            view=None if result.finished else self,
        )

    @discord.ui.button(label="⚔️ Fight", style=discord.ButtonStyle.danger, row=0)
    async def fight_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        result = await execute_cop_fight(self.cog, self.guild_id, self.user_id)
        await self._apply_result(interaction, result)

    @discord.ui.button(label="🏃 Flee", style=discord.ButtonStyle.secondary, row=0)
    async def flee_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        result = await execute_cop_flee(self.cog, self.guild_id, self.user_id)
        await self._apply_result(interaction, result)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, row=0)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        encounter = await self.cog.bot.db.get_drug_cop_encounter(self.user_id, self.guild_id)
        if encounter is None:
            lab_embed, lab_file = await build_lab_embed(self.cog, self.guild_id, self.user_id)
            lab_view = await DrugLabView.build(self.cog, self.guild_id, self.user_id)
            await interaction.response.edit_message(
                embed=lab_embed, attachments=[lab_file], view=lab_view,
            )
            return
        embed, file = await build_cop_encounter_embed(encounter)
        await interaction.response.edit_message(
            embed=embed,
            attachments=[file] if file is not None else [],
            view=self,
        )


class DrugLabView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    @classmethod
    async def build(cls, cog: commands.Cog, guild_id: int, user_id: int) -> DrugLabView:
        view = cls(cog, guild_id, user_id)
        view.add_item(PlantSelect(cog, guild_id, user_id))
        inventory = await cog.bot.db.get_drug_inventory(user_id, guild_id)
        options = stash_select_options(inventory)
        view.add_item(SellSelect(cog, guild_id, user_id, options))
        view.add_item(UseSelect(cog, guild_id, user_id, options))
        return view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your lab.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="🌾 Harvest", style=discord.ButtonStyle.success, row=3)
    async def harvest_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        harvested = await self.cog.bot.db.harvest_drugs(self.user_id, self.guild_id)
        view = await DrugLabView.build(self.cog, self.guild_id, self.user_id)
        embed, file = await build_lab_embed(self.cog, self.guild_id, self.user_id)
        if harvested:
            await record_quest_event(self.cog.bot.db, self.guild_id, self.user_id, "drug_harvest")
            parts = []
            for drug_id, qty in harvested.items():
                defn = drug_by_id(drug_id)
                parts.append(f"{defn.emoji if defn else ''} {qty} {defn.name if defn else drug_id}")
            embed.description = "🌾 Harvested " + ", ".join(parts) + "!"
        else:
            embed.description = "Nothing ready to harvest yet."
        await interaction.response.edit_message(embed=embed, attachments=[file], view=view)

    @discord.ui.button(label="🏪 Market", style=discord.ButtonStyle.primary, row=3)
    async def market_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        embed = await build_drug_market_embed(self.cog, interaction.guild, self.user_id)
        view = await DrugMarketView.build(self.cog, self.guild_id, self.user_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=3)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        view = await DrugLabView.build(self.cog, self.guild_id, self.user_id)
        embed, file = await build_lab_embed(self.cog, self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, attachments=[file], view=view)


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
    embed.set_footer(text=f"Market tax {int(config.DRUG_MARKET_TAX * 100)}% on sales")
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
        "stimulant": "⚡ Stimulants",
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
    sale_breakdown = await cog.bot.db.get_street_sale_breakdown(user_id, guild_id)
    sale_mult = float(sale_breakdown["multiplier"])
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
            price_note = f" · ~{fmt_amount(defn.street_price * sale_mult)}/unit street" if defn else ""
            effect = f" — _{defn.effect_summary}_" if defn else ""
            lines.append(f"{emoji} **{name}** ×{qty}{price_note}{effect}")
        embed.add_field(name="Inventory", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Inventory", value="_Empty — harvest from /drugs lab._", inline=False)
    if pending_buff:
        buff_parts = []
        if float(pending_buff["boss_mult"]) > 1.0:
            pct = int((float(pending_buff["boss_mult"]) - 1.0) * 100)
            buff_parts.append(f"**/attack** +{pct}%")
        if float(pending_buff["duel_mult"]) > 1.0:
            pct = int((float(pending_buff["duel_mult"]) - 1.0) * 100)
            buff_parts.append(f"**/duel** +{pct}%")
        embed.add_field(
            name="Active high",
            value=(
                f"**{pending_buff['name']}** — {' · '.join(buff_parts)} "
                f"· expires <t:{int(float(pending_buff['expires']))}:R>"
            ),
            inline=False,
        )
    if sale_mult > 1.001:
        embed.set_footer(
            text=(
                f"Street prices ×{sale_mult:.2f} "
                f"(rep {sale_breakdown['reputation_level']}, "
                f"influence {int(float(sale_breakdown['influence_pct']))}%)"
            ),
        )
    return embed


async def send_drug_lab_panel(interaction: discord.Interaction, cog: commands.Cog) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return
    embed, file = await build_lab_embed(cog, interaction.guild_id, interaction.user.id)
    view = await DrugLabView.build(cog, interaction.guild_id, interaction.user.id)
    await interaction.response.send_message(embed=embed, file=file, view=view)


async def send_drug_market_panel(interaction: discord.Interaction, cog: commands.Cog) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return
    embed = await build_drug_market_embed(cog, interaction.guild, interaction.user.id)
    view = await DrugMarketView.build(cog, interaction.guild.id, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view)
