"""Public gear instance marketplace."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import config
from items import get_item
from utils.helpers import fmt_amount, guild_only_message, valid_amount


class GearMarket(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="gear-market", description="Browse, list, buy, or cancel enhanced gear listings.")
    @app_commands.describe(
        action="Market action",
        listing_id="Listing ID to buy or cancel",
        instance_id="Gear instance ID to list (from /loadout)",
        price="Asking price in nuggets",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Browse", value="browse"),
            app_commands.Choice(name="Sell", value="sell"),
            app_commands.Choice(name="Buy", value="buy"),
            app_commands.Choice(name="Cancel", value="cancel"),
            app_commands.Choice(name="My listings", value="mine"),
        ],
    )
    @app_commands.guild_only()
    async def gear_market(
        self,
        interaction: discord.Interaction,
        action: str,
        listing_id: int | None = None,
        instance_id: int | None = None,
        price: float | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        guild_id = interaction.guild_id
        uid = interaction.user.id

        if action == "browse":
            rows = await self.bot.db.list_gear_market(guild_id, limit=15)
            if not rows:
                await interaction.response.send_message(
                    "No gear listed right now.", ephemeral=True,
                )
                return
            lines: list[str] = []
            for row in rows:
                item = get_item(str(row["item_id"]))
                name = item.name if item else str(row["item_id"])
                lvl = int(row["enhancement_level"])
                broken = " (broken)" if int(row["is_broken"]) else ""
                lines.append(
                    f"**#{int(row['listing_id'])}** — {name} +{lvl}{broken} — "
                    f"**{fmt_amount(float(row['price']))}** · seller <@{int(row['seller_id'])}>",
                )
            embed = discord.Embed(
                title="⚔️ Gear market",
                description="\n".join(lines),
                color=discord.Color.dark_gold(),
            )
            embed.set_footer(text="Buy with /gear-market action:Buy listing_id:<id>")
            await interaction.response.send_message(embed=embed)
            return

        if action == "mine":
            rows = await self.bot.db.list_gear_market(guild_id, limit=50)
            mine = [r for r in rows if int(r["seller_id"]) == uid]
            if not mine:
                await interaction.response.send_message("You have no active listings.", ephemeral=True)
                return
            lines = []
            for row in mine:
                item = get_item(str(row["item_id"]))
                name = item.name if item else str(row["item_id"])
                lines.append(
                    f"**#{int(row['listing_id'])}** — {name} +{int(row['enhancement_level'])} — "
                    f"**{fmt_amount(float(row['price']))}**",
                )
            await interaction.response.send_message("\n".join(lines), ephemeral=True)
            return

        if action == "sell":
            if instance_id is None or price is None:
                await interaction.response.send_message(
                    "Provide **instance_id** and **price**.", ephemeral=True,
                )
                return
            if not valid_amount(price) or float(price) <= 0:
                await interaction.response.send_message("Invalid price.", ephemeral=True)
                return
            err = await self.bot.db.create_gear_listing(
                uid, guild_id, int(instance_id), float(price),
            )
            errors = {
                "invalid_price": "Invalid price.",
                "too_many_listings": f"Max **{config.GEAR_MARKET_MAX_LISTINGS}** listings.",
                "equipped": "Unequip that gear first.",
                "not_found": "Gear instance not found.",
                "already_listed": "Already listed.",
            }
            if err:
                await interaction.response.send_message(
                    errors.get(err, "Could not list."), ephemeral=True,
                )
                return
            await interaction.response.send_message(
                f"Listed instance **#{instance_id}** for **{fmt_amount(float(price))}**.",
                ephemeral=True,
            )
            return

        if action == "buy":
            if listing_id is None:
                await interaction.response.send_message("Provide **listing_id**.", ephemeral=True)
                return
            err = await self.bot.db.buy_gear_listing(uid, guild_id, int(listing_id))
            errors = {
                "not_found": "Listing not found.",
                "self_buy": "You can't buy your own listing.",
                "insufficient_funds": "Not enough nuggets.",
            }
            if err:
                await interaction.response.send_message(
                    errors.get(err, "Purchase failed."), ephemeral=True,
                )
                return
            await interaction.response.send_message("**Purchase complete!**", ephemeral=True)
            return

        if action == "cancel":
            if listing_id is None:
                await interaction.response.send_message("Provide **listing_id**.", ephemeral=True)
                return
            err = await self.bot.db.cancel_gear_listing(uid, guild_id, int(listing_id))
            if err:
                await interaction.response.send_message(
                    "Could not cancel listing.", ephemeral=True,
                )
                return
            await interaction.response.send_message("Listing removed.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GearMarket(bot))
