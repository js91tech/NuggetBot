"""Drug trade — grow product in a lab and deal it on the street or black market."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils.drug_ui import (
    build_drug_catalog_embed,
    build_stash_embed,
    consume_stash_product,
    format_consume_message,
    send_drug_lab_panel,
    send_drug_market_panel,
)
from utils.drugs import drug_by_id
from utils.helpers import fmt_amount, guild_only_message
from utils.quests import record_quest_event

logger = logging.getLogger(__name__)


class Drugs(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    drugs_group = app_commands.Group(
        name="drugs",
        description="Grow, harvest, use, and deal contraband for high-risk profit.",
        guild_only=True,
    )

    async def drug_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        inventory = await self.bot.db.get_drug_inventory(interaction.user.id, interaction.guild_id)
        needle = current.lower()
        choices: list[app_commands.Choice[str]] = []
        for drug_id, qty in inventory.items():
            defn = drug_by_id(drug_id)
            if defn is None:
                continue
            if needle and needle not in drug_id and needle not in defn.name.lower():
                continue
            choices.append(
                app_commands.Choice(
                    name=f"{defn.name} x{qty}",
                    value=drug_id,
                ),
            )
            if len(choices) >= 25:
                break
        return choices

    async def listing_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        listings = await self.bot.db.list_user_drug_listings(
            interaction.user.id, interaction.guild_id,
        )
        needle = current.lower().lstrip("#")
        choices: list[app_commands.Choice[str]] = []
        for listing in listings:
            listing_id = int(listing["listing_id"])
            defn = drug_by_id(str(listing["drug_id"]))
            name = defn.name if defn is not None else str(listing["drug_id"])
            if needle and needle not in str(listing_id) and needle not in name.lower():
                continue
            choices.append(
                app_commands.Choice(
                    name=f"#{listing_id} {name} ×{int(listing['quantity'])}"[:100],
                    value=str(listing_id),
                ),
            )
            if len(choices) >= 25:
                break
        return choices

    @drugs_group.command(name="lab", description="Open your grow lab: plant, harvest, sell, and use.")
    async def lab(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        await send_drug_lab_panel(interaction, self)

    @drugs_group.command(name="market", description="Browse and trade on the black market.")
    async def market(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        await send_drug_market_panel(interaction, self)

    @drugs_group.command(name="catalog", description="Browse all strains, prices, and consume effects.")
    async def catalog(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        embed = await build_drug_catalog_embed()
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @drugs_group.command(name="stash", description="View your product stash and active drug buffs.")
    async def stash(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        embed = await build_stash_embed(self, interaction.guild_id, interaction.user.id)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @drugs_group.command(name="use", description="Consume product from your stash for its effects.")
    @app_commands.describe(product="Product in your stash to use")
    @app_commands.autocomplete(product=drug_autocomplete)
    async def use(self, interaction: discord.Interaction, product: str) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        drug_id = product.strip().lower()
        if drug_by_id(drug_id) is None:
            await interaction.response.send_message("Unknown product.", ephemeral=True)
            return
        qty = (await self.bot.db.get_drug_inventory(interaction.user.id, interaction.guild_id)).get(drug_id, 0)
        if qty <= 0:
            await interaction.response.send_message("You don't have that product in your stash.", ephemeral=True)
            return
        result = await consume_stash_product(self, interaction.guild_id, interaction.user.id, drug_id)
        if result.get("error"):
            await interaction.response.send_message("Could not use that product.", ephemeral=True)
            return
        await record_quest_event(self.bot.db, interaction.guild_id, interaction.user.id, "drug_use")
        await interaction.response.send_message(format_consume_message(result), ephemeral=True)

    @drugs_group.command(
        name="unlist",
        description="Remove one of your black-market listings and return product to stash.",
    )
    @app_commands.describe(listing="Your listing # to remove")
    @app_commands.autocomplete(listing=listing_autocomplete)
    async def unlist(self, interaction: discord.Interaction, listing: str) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        try:
            listing_id = int(listing.strip().lstrip("#"))
        except ValueError:
            await interaction.response.send_message("Enter a valid listing #.", ephemeral=True)
            return
        err = await self.bot.db.cancel_drug_listing(
            interaction.user.id, interaction.guild_id, listing_id,
        )
        messages = {
            "not_found": "That listing no longer exists.",
            "not_owner": "You can only unlist your own product.",
        }
        if err:
            await interaction.response.send_message(messages.get(err, "Could not unlist."), ephemeral=True)
            return
        await interaction.response.send_message(
            f"📤 Unlisted **#{listing_id}** — product returned to your stash.",
            ephemeral=True,
        )


    @drugs_group.command(name="wholesale", description="Sell bulk to an NPC buyer (Dealer Rank 7+, no raid risk).")
    @app_commands.describe(product="Product in your stash", quantity="Units to sell")
    @app_commands.autocomplete(product=drug_autocomplete)
    async def wholesale(
        self,
        interaction: discord.Interaction,
        product: str,
        quantity: int,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        result = await self.bot.db.sell_drugs_wholesale(
            interaction.user.id, interaction.guild_id, product.strip().lower(), quantity,
        )
        if result.get("error") == "rank_locked":
            await interaction.response.send_message(
                f"Wholesale unlocks at dealer rank **{config.DEALER_RANK_WHOLESALE_UNLOCK}**.",
                ephemeral=True,
            )
            return
        if result.get("error"):
            await interaction.response.send_message("Could not complete wholesale sale.", ephemeral=True)
            return
        await record_quest_event(self.bot.db, interaction.guild_id, interaction.user.id, "drug_sell")
        await interaction.response.send_message(
            f"📦 Wholesale deal: **{quantity}** units for **{fmt_amount(float(result['total']))}** "
            f"(fixed {fmt_amount(float(result['unit_price']))}/unit, no raid risk).",
            ephemeral=True,
        )

    @drugs_group.command(name="rank", description="View your dealer rank and unlock progress.")
    async def rank(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        from utils.dealer_ranks import (
            can_list_on_market,
            can_wholesale,
            dealer_rank,
            next_rank_threshold,
            rank_title,
        )

        stats = await self.bot.db.get_drug_stats(interaction.user.id, interaction.guild_id)
        rank = dealer_rank(stats["units_sold"])
        embed = discord.Embed(
            title=f"Dealer Rank {rank} — {rank_title(rank)}",
            description=f"Lifetime units sold: **{stats['units_sold']:,}**",
            color=discord.Color.dark_green(),
        )
        unlocks = [
            "Street selling",
            f"Black market (rank {config.DEALER_RANK_MARKET_UNLOCK})",
            f"Extra lab slot (rank {config.DEALER_RANK_EXTRA_LAB_SLOT})",
            f"Wholesale NPC buyer (rank {config.DEALER_RANK_WHOLESALE_UNLOCK})",
            f"Cartel title (rank {config.DEALER_RANK_CARTEL_TITLE})",
        ]
        embed.add_field(name="Unlock track", value="\n".join(f"{'✅' if i == 0 or (i == 1 and can_list_on_market(rank)) or (i == 2 and rank >= config.DEALER_RANK_EXTRA_LAB_SLOT) or (i == 3 and can_wholesale(rank)) or (i == 4 and rank >= config.DEALER_RANK_CARTEL_TITLE) else '⬜'} {u}" for i, u in enumerate(unlocks)), inline=False)
        nxt = next_rank_threshold(rank)
        if nxt is not None:
            embed.set_footer(text=f"{nxt - stats['units_sold']} units to rank {rank + 1}")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Drugs(bot))
