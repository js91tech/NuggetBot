from __future__ import annotations

import time

import discord
from discord import app_commands
from discord.ext import commands

import config
from items import (
    CATEGORIES,
    ITEMS,
    ShopItem,
    get_item,
    items_for_category,
)
from utils.gear_sets import detect_set_bonus
from utils.helpers import fmt_amount, guild_only_message
from utils.quests import record_quest_event
from utils.stats import compute_combat_stats, format_combat_stats_block, format_item_stats


def _item_line(item: ShopItem) -> str:
    return f"`{item.id}` - **{item.name}** ({fmt_amount(item.price)}): {format_item_stats(item)}"


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
            if item.shop_listed
            and item.price > 0
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
        embed.set_footer(text="Use /buy item_id, then /equip item_id. /stats for your combat sheet.")
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
        if not shop_item.shop_listed:
            await interaction.response.send_message(
                "That item is not sold in the shop.",
                ephemeral=True,
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
        await record_quest_event(
            self.bot.db,
            interaction.guild_id,
            interaction.user.id,
            "shop_buy",
        )

    @app_commands.command(
        name="stats",
        description="View combat stats for yourself or another player.",
    )
    @app_commands.describe(
        user="Player to inspect. Defaults to you.",
        public="Show your stats in the channel instead of privately",
    )
    @app_commands.guild_only()
    async def stats(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
        public: bool = False,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        target = user or interaction.user
        guild_id = interaction.guild_id
        equipment = await self.bot.db.get_equipment(target.id, guild_id)
        weapon = get_item(equipment.get("weapon")) if equipment.get("weapon") else None
        armor = get_item(equipment.get("armor")) if equipment.get("armor") else None

        max_hp = float(config.PLAYER_BASE_HP + (armor.hp_bonus if armor is not None else 0))
        await self.bot.db.sync_combat_hp(target.id, guild_id, max_hp)
        combat = await self.bot.db.get_combat_state(target.id, guild_id)
        current_hp = combat[0] if combat is not None else max_hp

        progress = await self.bot.db.get_user_progress(target.id, guild_id)
        prestige = int(progress["prestige_level"])
        set_bonus = detect_set_bonus(weapon, armor)
        combat_stats = compute_combat_stats(
            weapon,
            armor,
            current_hp=current_hp,
            prestige_level=prestige,
            set_bonus=set_bonus,
        )
        user_row = await self.bot.db.get_user(target.id, guild_id)
        wallet = float(user_row["wallet"])
        total_earned = float(user_row["total_earned"])
        raid_damage = await self.bot.db.get_boss_damage(target.id, guild_id)

        embed = discord.Embed(
            title=f"{target.display_name}'s Stats",
            description=format_combat_stats_block(
                combat_stats,
                set_bonus=set_bonus,
                prestige_level=prestige,
            ),
            color=discord.Color.gold(),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(
            name="Economy",
            value=(
                f"Wallet: **{fmt_amount(wallet)}**\n"
                f"Lifetime earned: **{fmt_amount(total_earned)}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Gear",
            value=(
                f"Weapon: **{weapon.name if weapon else 'None'}**\n"
                f"Armor: **{armor.name if armor else 'None'}**"
            ),
            inline=True,
        )
        if raid_damage > 0:
            embed.add_field(
                name="Current raid",
                value=f"**{fmt_amount(raid_damage)}** damage dealt this boss",
                inline=False,
            )

        status_parts: list[str] = []
        now = time.time()
        if float(user_row["downed_until"]) > now:
            status_parts.append("Downed (cannot attack)")
        if float(user_row["arrested_until"]) > now:
            status_parts.append("Arrested")
        if status_parts:
            embed.add_field(name="Status", value=" · ".join(status_parts), inline=False)

        achievements = await self.bot.db.list_achievements(target.id, guild_id)
        embed.add_field(
            name="Achievements",
            value=f"**{len(achievements)}** unlocked · Prestige **{prestige}**",
            inline=False,
        )
        embed.set_footer(text="Use /inventory to see all owned items with per-item stats.")
        ephemeral = not (public and target.id == interaction.user.id)
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

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

        weapon = get_item(equipment.get("weapon")) if equipment.get("weapon") else None
        armor = get_item(equipment.get("armor")) if equipment.get("armor") else None
        progress = await self.bot.db.get_user_progress(target.id, interaction.guild_id)
        set_bonus = detect_set_bonus(weapon, armor)
        summary = format_combat_stats_block(
            compute_combat_stats(
                weapon,
                armor,
                prestige_level=int(progress["prestige_level"]),
                set_bonus=set_bonus,
            ),
            set_bonus=set_bonus,
            prestige_level=int(progress["prestige_level"]),
        )

        embed = discord.Embed(
            title=f"{target.display_name}'s Gear",
            description=owned,
            color=discord.Color.blue(),
        )
        embed.add_field(name="Equipped loadout", value=summary, inline=False)
        embed.set_footer(text="/stats for wallet, raid damage, and HP bar · /equip to change gear")
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
        return (
            f"**{item.name}** x{quantity}{equipped}\n"
            f"└ {format_item_stats(item)} · `{item.id}`"
        )

    @staticmethod
    def _equipped_name(item_id: str | None) -> str:
        if item_id is None:
            return "None"
        item = get_item(item_id)
        return item.name if item is not None else item_id


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Shop(bot))
