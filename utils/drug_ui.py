"""Interactive drug trade: grow lab, inventory, street sales, and player market."""
from __future__ import annotations

import io
import time
from typing import TYPE_CHECKING

import discord

import config
from utils.drug_art import render_lab_image
from utils.drugs import DRUGS, drug_by_id
from utils.helpers import fmt_amount
from utils.quests import record_quest_event

if TYPE_CHECKING:
    from discord.ext import commands


async def build_lab_embed(
    cog: commands.Cog, guild_id: int, user_id: int,
) -> tuple[discord.Embed, discord.File]:
    grows = await cog.bot.db.list_drug_grows(user_id, guild_id)
    inventory = await cog.bot.db.get_drug_inventory(user_id, guild_id)
    now = time.time()

    embed = discord.Embed(
        title="🧪 Grow Lab",
        description=(
            "Plant strains, wait for them to mature, then harvest and sell on the "
            "street or to other players. Watch out for raids!"
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
        embed.add_field(name="Grow slots", value="\n".join(lines), inline=False)
        embed.set_footer(text=footer)
    else:
        embed.add_field(
            name="Grow slots",
            value=f"_Empty — plant a strain below ({config.DRUG_LAB_SLOTS} slots)._",
            inline=False,
        )

    if inventory:
        inv_lines = []
        for drug_id, qty in inventory.items():
            defn = drug_by_id(drug_id)
            name = defn.name if defn else drug_id
            emoji = defn.emoji if defn else "📦"
            price = defn.street_price if defn else 0
            inv_lines.append(f"{emoji} **{name}** ×{qty} · ~{fmt_amount(price)}/unit")
        embed.add_field(name="Stash", value="\n".join(inv_lines), inline=False)

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
                description=f"Seed {fmt_amount(defn.seed_cost)} · {defn.grow_seconds // 60}m grow"[:100],
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
            embed.description = (
                f"💵 Sold **{int(result['quantity'])} {name}** on the street for "
                f"**{fmt_amount(float(result['total']))}**."
            )
        await interaction.response.edit_message(embed=embed, attachments=[file], view=view)


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
        options = []
        for drug_id, qty in inventory.items():
            defn = drug_by_id(drug_id)
            options.append(
                discord.SelectOption(
                    label=f"{defn.name if defn else drug_id} (×{qty})",
                    value=drug_id,
                    emoji=defn.emoji if defn else None,
                ),
            )
        view.add_item(SellSelect(cog, guild_id, user_id, options))
        return view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your lab.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="🌾 Harvest", style=discord.ButtonStyle.success, row=2)
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

    @discord.ui.button(label="🏪 Market", style=discord.ButtonStyle.primary, row=2)
    async def market_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        embed = await build_drug_market_embed(self.cog, interaction.guild, self.user_id)
        view = await DrugMarketView.build(self.cog, self.guild_id, self.user_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=2)
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


class ListProductModal(discord.ui.Modal, title="List product for sale"):
    drug = discord.ui.TextInput(label="Strain id", placeholder="greenleaf / bluecrystal / whitedust / goldenpoppy", required=True, max_length=20)
    quantity = discord.ui.TextInput(label="Quantity", placeholder="e.g. 5", required=True, max_length=8)
    price = discord.ui.TextInput(label="Price per unit", placeholder="e.g. 150", required=True, max_length=12)

    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        drug_id = str(self.drug.value).strip().lower()
        try:
            qty = int(str(self.quantity.value).replace(",", "").strip())
            price = float(str(self.price.value).replace(",", "").strip())
        except ValueError:
            await interaction.response.send_message("Enter valid numbers.", ephemeral=True)
            return
        err = await self.cog.bot.db.create_drug_listing(self.user_id, self.guild_id, drug_id, qty, price)
        messages = {
            "invalid_drug": "Unknown strain id.",
            "invalid_amount": "Enter a valid quantity and price.",
            "insufficient_product": "You don't have that much product to list.",
        }
        if err:
            await interaction.response.send_message(messages.get(err, err), ephemeral=True)
            return
        await record_quest_event(self.cog.bot.db, self.guild_id, self.user_id, "drug_list")
        embed = await build_drug_market_embed(self.cog, interaction.guild, self.user_id)
        view = await DrugMarketView.build(self.cog, self.guild_id, self.user_id)
        defn = drug_by_id(drug_id)
        embed.description = (
            f"📋 Listed **{qty} {defn.name if defn else drug_id}** at "
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
        return cls(cog, guild_id, user_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your market panel.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="📋 List product", style=discord.ButtonStyle.primary)
    async def list_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await interaction.response.send_modal(ListProductModal(self.cog, self.guild_id, self.user_id))

    @discord.ui.button(label="🛒 Buy", style=discord.ButtonStyle.success)
    async def buy_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await interaction.response.send_modal(BuyListingModal(self.cog, self.guild_id, self.user_id))

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        embed = await build_drug_market_embed(self.cog, interaction.guild, self.user_id)
        await interaction.response.edit_message(embed=embed, view=self)


async def send_drug_lab_panel(interaction: discord.Interaction, cog: commands.Cog) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return
    if not interaction.response.is_done():
        await interaction.response.defer()
    embed, file = await build_lab_embed(cog, interaction.guild_id, interaction.user.id)
    view = await DrugLabView.build(cog, interaction.guild_id, interaction.user.id)
    await interaction.followup.send(embed=embed, file=file, view=view)


async def send_drug_market_panel(interaction: discord.Interaction, cog: commands.Cog) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return
    if not interaction.response.is_done():
        await interaction.response.defer()
    embed = await build_drug_market_embed(cog, interaction.guild, interaction.user.id)
    view = await DrugMarketView.build(cog, interaction.guild.id, interaction.user.id)
    await interaction.followup.send(embed=embed, view=view)
