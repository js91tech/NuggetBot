from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import config
from items import (
    CATEGORIES,
    ITEMS,
    ShopItem,
    armor_mitigation_percent,
    get_item,
    items_for_category,
)
from utils.helpers import fmt_amount, guild_only_message


def _item_line(item: ShopItem) -> str:
    if item.category == "weapon":
        crit = f", {int(item.crit_chance * 100)}% crit" if item.crit_chance > 0 else ""
        stat = f"{item.power} base damage (+1–5 roll){crit}"
    else:
        stat = f"{armor_mitigation_percent(item.power)}% mitigation, +{item.hp_bonus} HP"
    return f"`{item.id}` - **{item.name}** ({fmt_amount(item.price)}): {stat}"


class Shop(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def item_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        del interaction
        current_lower = current.lower()
        matches = [
            item
            for item in ITEMS.values()
            if current_lower in item.id.lower() or current_lower in item.name.lower()
        ][:25]
        return [
            app_commands.Choice(name=f"{item.name} ({item.id})", value=item.id) for item in matches
        ]

    async def buy_item_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        del interaction
        current_lower = current.lower()
        matches = [
            item
            for item in ITEMS.values()
            if item.price > 0
            and (current_lower in item.id.lower() or current_lower in item.name.lower())
        ][:25]
        return [
            app_commands.Choice(name=f"{item.name} ({item.id})", value=item.id) for item in matches
        ]

    async def sell_item_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        current_lower = current.lower()
        rows = await self.bot.db.get_inventory(interaction.user.id, interaction.guild_id)
        choices: list[app_commands.Choice[str]] = []
        for row in rows:
            item_id = str(row["item_id"])
            item = get_item(item_id)
            if item is None or item.price <= 0:
                continue
            if current_lower not in item.id.lower() and current_lower not in item.name.lower():
                continue
            qty = int(row["quantity"])
            choices.append(
                app_commands.Choice(name=f"{item.name} x{qty} ({item.id})", value=item.id),
            )
            if len(choices) >= 25:
                break
        return choices

    @app_commands.command(name="shop", description="Browse weapons and armor.")
    @app_commands.describe(category="Item category to view")
    @app_commands.choices(
        category=[
            app_commands.Choice(name="All", value="all"),
            app_commands.Choice(name="Weapons", value="weapon"),
            app_commands.Choice(name="Armor", value="armor"),
        ]
    )
    @app_commands.guild_only()
    async def shop(self, interaction: discord.Interaction, category: str = "all") -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if category not in CATEGORIES:
            await interaction.response.send_message("Choose all, weapon, or armor.", ephemeral=True)
            return

        items = items_for_category(category)
        title = "Nugget Shop" if category == "all" else f"Nugget Shop - {category.title()}"
        embed = discord.Embed(
            title=title,
            description="\n".join(_item_line(item) for item in items),
            color=discord.Color.orange(),
        )
        embed.set_footer(text="Use /buy item_id, then /equip item_id.")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="buy", description="Buy a weapon or armor piece.")
    @app_commands.describe(item="Item to buy")
    @app_commands.autocomplete(item=buy_item_autocomplete)
    @app_commands.guild_only()
    async def buy(self, interaction: discord.Interaction, item: str) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        shop_item = get_item(item.strip())
        if shop_item is None:
            await interaction.response.send_message(
                "Unknown item. Use autocomplete or `/shop`.", ephemeral=True
            )
            return
        if shop_item.price <= 0:
            await interaction.response.send_message(
                "That item is not sold in the shop (starter gear). Use `/shop` for prices.",
                ephemeral=True,
            )
            return

        bought = await self.bot.db.buy_item(
            interaction.user.id, interaction.guild_id, shop_item.id, shop_item.price
        )
        if not bought:
            await interaction.response.send_message(
                f"You need {fmt_amount(shop_item.price)} to buy {shop_item.name}.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"You bought **{shop_item.name}** for {fmt_amount(shop_item.price)}. Use `/equip {shop_item.id}`.",
            ephemeral=True,
        )

    @app_commands.command(name="inventory", description="View your owned and equipped gear.")
    @app_commands.describe(user="User to inspect. Defaults to you.")
    @app_commands.guild_only()
    async def inventory(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        target = user or interaction.user
        rows = await self.bot.db.get_inventory(target.id, interaction.guild_id)
        equipment = await self.bot.db.get_equipment(target.id, interaction.guild_id)
        if not rows:
            owned = "No gear yet. Use `/shop` to browse items."
        else:
            owned = "\n".join(
                self._inventory_line(str(row["item_id"]), int(row["quantity"]), equipment)
                for row in rows
            )

        embed = discord.Embed(
            title=f"{target.display_name}'s Gear",
            description=owned,
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="Equipped",
            value=(
                f"Weapon: {self._equipped_name(equipment.get('weapon'))}\n"
                f"Armor: {self._equipped_name(equipment.get('armor'))}"
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="equip", description="Equip an owned weapon or armor piece.")
    @app_commands.describe(item="Owned item to equip")
    @app_commands.autocomplete(item=item_autocomplete)
    @app_commands.guild_only()
    async def equip(self, interaction: discord.Interaction, item: str) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        shop_item = get_item(item.strip())
        if shop_item is None:
            await interaction.response.send_message(
                "Unknown item. Use autocomplete or `/inventory`.", ephemeral=True
            )
            return

        equipped = await self.bot.db.equip_item(
            interaction.user.id,
            interaction.guild_id,
            shop_item.category,
            shop_item.id,
        )
        if not equipped:
            await interaction.response.send_message(
                "You need to buy that item first.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"Equipped **{shop_item.name}** as your {shop_item.category}.",
            ephemeral=True,
        )

    @app_commands.command(
        name="sell", description="Sell shop gear from your inventory for half its shop price."
    )
    @app_commands.describe(item="Owned item to sell (one copy)")
    @app_commands.autocomplete(item=sell_item_autocomplete)
    @app_commands.guild_only()
    async def sell(self, interaction: discord.Interaction, item: str) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        shop_item = get_item(item.strip())
        if shop_item is None:
            await interaction.response.send_message(
                "Unknown item. Use autocomplete or `/inventory`.", ephemeral=True
            )
            return
        if shop_item.price <= 0:
            await interaction.response.send_message("You cannot sell starter gear.", ephemeral=True)
            return

        refund = max(1, int(shop_item.price // 2))
        sold = await self.bot.db.sell_one_item(
            interaction.user.id, interaction.guild_id, shop_item.id, refund
        )
        if not sold:
            await interaction.response.send_message(
                "You do not have that item to sell.", ephemeral=True
            )
            return

        equipment = await self.bot.db.get_equipment(interaction.user.id, interaction.guild_id)
        max_hp = float(config.PLAYER_BASE_HP)
        armor_id = equipment.get("armor")
        if armor_id:
            armor_item = get_item(armor_id)
            if armor_item is not None:
                max_hp += float(armor_item.hp_bonus)
        await self.bot.db.sync_combat_hp(interaction.user.id, interaction.guild_id, max_hp)

        await interaction.response.send_message(
            f"Sold **{shop_item.name}** for {fmt_amount(float(refund))}.",
            ephemeral=True,
        )

    @staticmethod
    def _inventory_line(item_id: str, quantity: int, equipment: dict[str, str]) -> str:
        item = get_item(item_id)
        if item is None:
            return f"`{item_id}` x{quantity}"
        equipped = " (equipped)" if equipment.get(item.category) == item.id else ""
        return f"**{item.name}** x{quantity}{equipped} - `{item.id}`"

    @staticmethod
    def _equipped_name(item_id: str | None) -> str:
        if item_id is None:
            return "None"
        item = get_item(item_id)
        return item.name if item is not None else item_id


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Shop(bot))
