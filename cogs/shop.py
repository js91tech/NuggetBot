from __future__ import annotations

import time

import discord
from discord import app_commands
from discord.ext import commands

import config
from items import (
    SHOP_CATEGORIES,
    ITEMS,
    ShopItem,
    get_item,
    items_for_category,
    sell_refund_for_item,
)
from utils.aspects import format_aspect_effect, instance_from_row
from utils.loadout import parse_loadout
from utils.gear_sets import detect_set_bonus
from utils.helpers import fmt_amount, guild_only_message
from utils.quests import record_quest_event
from utils.stats import compute_combat_stats, format_combat_stats_block, format_item_stats


def _item_line(item: ShopItem) -> str:
    refund = sell_refund_for_item(item)
    price_bits = [f"buy {fmt_amount(item.price)}"]
    if refund is not None:
        price_bits.append(f"sell {fmt_amount(refund)}")
    return f"`{item.id}` - **{item.name}** ({', '.join(price_bits)}): {format_item_stats(item)}"


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

    async def owned_equip_autocomplete(
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
            if item is None or item.category == "consumable":
                continue
            if current_lower not in item.id.lower() and current_lower not in item.name.lower():
                continue
            qty = int(row["quantity"])
            choices.append(
                app_commands.Choice(name=f"{item.name} x{qty} ({item.id})"[:100], value=item.id),
            )
            if len(choices) >= 25:
                break
        return choices

    async def buy_item_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        wallet = await self.bot.db.get_balance(interaction.user.id, interaction.guild_id)
        current_lower = current.lower()
        matches = [
            item
            for item in ITEMS.values()
            if item.shop_listed
            and item.price > 0
            and (current_lower in item.id.lower() or current_lower in item.name.lower())
        ][:25]
        choices: list[app_commands.Choice[str]] = []
        for item in matches:
            price_text = fmt_amount(item.price)
            if wallet >= item.price:
                afford = "✓"
            else:
                afford = f"need {fmt_amount(item.price - wallet)} more"
            label = f"{item.name} — {price_text} ({afford})"
            choices.append(app_commands.Choice(name=label[:100], value=item.id))
        return choices

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
            refund = sell_refund_for_item(item)
            refund_text = fmt_amount(refund) if refund is not None else "?"
            choices.append(
                app_commands.Choice(
                    name=f"{item.name} x{qty} → {refund_text}",
                    value=item.id,
                ),
            )
            if len(choices) >= 25:
                break
        return choices

    @app_commands.command(name="shop", description="Browse weapons, guns, and armor.")
    @app_commands.describe(category="Item category to view")
    @app_commands.choices(
        category=[
            app_commands.Choice(name="All", value="all"),
            app_commands.Choice(name="Weapons", value="weapon"),
            app_commands.Choice(name="Guns", value="gun"),
            app_commands.Choice(name="Armor", value="armor"),
            app_commands.Choice(name="Consumables", value="consumable"),
        ]
    )
    @app_commands.guild_only()
    async def shop(self, interaction: discord.Interaction, category: str = "all") -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if category not in SHOP_CATEGORIES:
            await interaction.response.send_message(
                "Choose all, weapon, gun, armor, or consumable.", ephemeral=True
            )
            return

        items = items_for_category(category)
        category_labels = {
            "all": "All",
            "weapon": "Weapons",
            "gun": "Guns",
            "armor": "Armor",
            "consumable": "Consumables",
        }
        title = (
            "Nugget Shop"
            if category == "all"
            else f"Nugget Shop — {category_labels.get(category, category.title())}"
        )
        embed = discord.Embed(
            title=title,
            description="\n".join(_item_line(item) for item in items),
            color=discord.Color.orange(),
        )
        embed.set_footer(
            text="Use /buy [item] [quantity] then /equip. Trap bombs: /buy trap_bomb. Aspects: /buy-aspect."
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="buy", description="Buy shop gear or consumables (e.g. trap bombs).")
    @app_commands.describe(
        item="Item to buy",
        quantity="How many to buy (1–99)",
        confirm="Required true for purchases totaling 50k+ nuggets",
    )
    @app_commands.autocomplete(item=buy_item_autocomplete)
    @app_commands.guild_only()
    async def buy(
        self,
        interaction: discord.Interaction,
        item: str,
        quantity: app_commands.Range[int, 1, 99] = 1,
        confirm: bool = False,
    ) -> None:
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

        qty = int(quantity)
        total = shop_item.price * qty
        balance = await self.bot.db.get_balance(interaction.user.id, interaction.guild_id)
        large_purchase_threshold = 50_000.0
        if total >= large_purchase_threshold and not confirm:
            await interaction.response.send_message(
                f"**{qty}×** **{shop_item.name}** costs **{fmt_amount(total)}** "
                f"(you have **{fmt_amount(balance)}**). "
                "Run the command again with `confirm:true` to purchase.",
                ephemeral=True,
            )
            return
        bought = await self.bot.db.buy_item(
            interaction.user.id,
            interaction.guild_id,
            shop_item.id,
            shop_item.price,
            quantity=qty,
        )
        if not bought:
            await interaction.response.send_message(
                f"You need **{fmt_amount(total)}** to buy **{qty}×** {shop_item.name} "
                f"({fmt_amount(shop_item.price)} each).",
                ephemeral=True,
            )
            return

        if shop_item.category == "consumable":
            equip_hint = "They stack in your inventory automatically."
        else:
            equip_hint = f"Use `/equip {shop_item.id}` for each piece you want to wear."
        await interaction.response.send_message(
            f"You bought **{qty}×** **{shop_item.name}** for **{fmt_amount(total)}** "
            f"({fmt_amount(shop_item.price)} each). {equip_hint}",
            ephemeral=True,
        )
        for _ in range(qty):
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
        loadout = parse_loadout(equipment)

        max_hp = float(
            config.PLAYER_BASE_HP + (loadout.armor.hp_bonus if loadout.armor is not None else 0)
        )
        await self.bot.db.sync_combat_hp(target.id, guild_id, max_hp)
        combat = await self.bot.db.get_combat_state(target.id, guild_id)
        current_hp = combat[0] if combat is not None else max_hp

        progress = await self.bot.db.get_user_progress(target.id, guild_id)
        prestige = int(progress["prestige_level"])
        set_bonus = detect_set_bonus(loadout.primary, loadout.armor)
        from utils.classes import format_modifiers_summary, get_class

        class_id = await self.bot.db.get_class_id(target.id, guild_id)
        cls = get_class(class_id)
        combat_stats = compute_combat_stats(
            loadout.primary,
            loadout.armor,
            off_hand=loadout.off_hand,
            current_hp=current_hp,
            prestige_level=prestige,
            set_bonus=set_bonus,
        )
        class_blurb = ""
        if cls is not None:
            class_blurb = f"\n**{cls.emoji} {cls.name}** — {format_modifiers_summary(cls.modifiers)}"
        aspect_rows = await self.bot.db.list_equipped_aspect_rows(target.id, guild_id)
        slot_rows = await self.bot.db.list_equipped_aspect_slots(target.id, guild_id)
        slot_by_inst = {int(r["instance_id"]): int(r["slot"]) for r in slot_rows}
        aspect_blurb = ""
        if aspect_rows:
            parts = []
            for row in aspect_rows:
                inst = instance_from_row(row)
                s = slot_by_inst.get(inst.instance_id, "?")
                parts.append(f"**{inst.name}** (slot {s}): {format_aspect_effect(inst)}")
            aspect_blurb = "\n**Aspects** — " + " · ".join(parts)
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
                off_hand=loadout.off_hand,
            )
            + class_blurb
            + aspect_blurb,
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
                f"Main: **{loadout.primary.name if loadout.primary else 'None'}**\n"
                f"Off-hand: **{loadout.off_hand.name if loadout.off_hand else 'None'}**\n"
                f"Armor: **{loadout.armor.name if loadout.armor else 'None'}**"
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

        loadout = parse_loadout(equipment)
        progress = await self.bot.db.get_user_progress(target.id, interaction.guild_id)
        set_bonus = detect_set_bonus(loadout.primary, loadout.armor)
        summary = format_combat_stats_block(
            compute_combat_stats(
                loadout.primary,
                loadout.armor,
                off_hand=loadout.off_hand,
                prestige_level=int(progress["prestige_level"]),
                set_bonus=set_bonus,
            ),
            set_bonus=set_bonus,
            prestige_level=int(progress["prestige_level"]),
            off_hand=loadout.off_hand,
        )

        embed = discord.Embed(
            title=f"{target.display_name}'s Gear",
            description=owned,
            color=discord.Color.blue(),
        )
        embed.add_field(name="Equipped loadout", value=summary, inline=False)
        embed.set_footer(text="/stats for wallet, raid damage, and HP bar · /equip to change gear")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="equip",
        description="Equip gear. Swords go main hand; guns fill off-hand when you have a blade.",
    )
    @app_commands.describe(item="Owned item to equip")
    @app_commands.autocomplete(item=owned_equip_autocomplete)
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

        slot = await self.bot.db.equip_gear_item(
            interaction.user.id,
            interaction.guild_id,
            shop_item.id,
        )
        if slot is None:
            await interaction.response.send_message(
                "You need to buy that item first.", ephemeral=True
            )
            return
        if shop_item.category == "consumable":
            await interaction.response.send_message(
                f"**{shop_item.name}** stays in your inventory — no equip slot. "
                "Trap bombs auto-trigger when duelists attack you.",
                ephemeral=True,
            )
            return

        slot_labels = {
            "weapon": "main hand",
            "off_hand": "off-hand",
            "armor": "armor",
        }
        extra = ""
        if shop_item.category == "gun" and slot == "weapon":
            extra = " Equip a **sword** in main hand first to dual-wield with off-hand bonuses."
        elif shop_item.category == "gun" and slot == "off_hand":
            extra = " Dual-wield active with your main-hand blade."
        await interaction.response.send_message(
            f"Equipped **{shop_item.name}** ({slot_labels.get(slot, slot)}).{extra}",
            ephemeral=True,
        )

    @app_commands.command(name="unequip", description="Clear a gear slot.")
    @app_commands.describe(
        slot="Which slot to clear",
    )
    @app_commands.choices(
        slot=[
            app_commands.Choice(name="Main hand (weapon)", value="weapon"),
            app_commands.Choice(name="Off-hand", value="off_hand"),
            app_commands.Choice(name="Armor", value="armor"),
        ],
    )
    @app_commands.guild_only()
    async def unequip(self, interaction: discord.Interaction, slot: str) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        removed = await self.bot.db.unequip_gear_slot(
            interaction.user.id,
            interaction.guild_id,
            slot,
        )
        if not removed:
            labels = {
                "weapon": "main hand",
                "off_hand": "off-hand",
                "armor": "armor",
            }
            await interaction.response.send_message(
                f"Nothing equipped in **{labels.get(slot, slot)}**.",
                ephemeral=True,
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
        labels = {
            "weapon": "Main hand",
            "off_hand": "Off-hand",
            "armor": "Armor",
        }
        await interaction.response.send_message(
            f"Cleared **{labels.get(slot, slot)}**.",
            ephemeral=True,
        )

    @app_commands.command(
        name="sell", description="Sell shop gear from your inventory for half its shop price."
    )
    @app_commands.describe(
        item="Owned item to sell",
        quantity="How many to sell (1–99, ignored if sell_all is true)",
        sell_all="Sell every copy you own",
    )
    @app_commands.autocomplete(item=sell_item_autocomplete)
    @app_commands.guild_only()
    async def sell(
        self,
        interaction: discord.Interaction,
        item: str,
        quantity: app_commands.Range[int, 1, 99] = 1,
        sell_all: bool = False,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        shop_item = get_item(item.strip())
        if shop_item is None:
            await interaction.response.send_message(
                "Unknown item. Use autocomplete or `/inventory`.", ephemeral=True
            )
            return
        refund_amount = sell_refund_for_item(shop_item)
        if refund_amount is None:
            await interaction.response.send_message("You cannot sell starter gear.", ephemeral=True)
            return

        rows = await self.bot.db.get_inventory(interaction.user.id, interaction.guild_id)
        owned_qty = 0
        for row in rows:
            if str(row["item_id"]) == shop_item.id:
                owned_qty = int(row["quantity"])
                break
        if owned_qty <= 0:
            await interaction.response.send_message(
                "You do not have that item to sell.", ephemeral=True
            )
            return

        qty = owned_qty if sell_all else int(quantity)
        sold = await self.bot.db.sell_one_item(
            interaction.user.id,
            interaction.guild_id,
            shop_item.id,
            refund_amount,
            quantity=qty,
        )
        if sold <= 0:
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

        payout = refund_amount * sold
        remaining = owned_qty - sold
        extra = ""
        if remaining > 0:
            extra = (
                f"\n**{remaining}** left in inventory "
                f"({fmt_amount(refund_amount)} each)."
            )
        await interaction.response.send_message(
            f"Sold **{sold}×** **{shop_item.name}** for **{fmt_amount(payout)}** "
            f"({fmt_amount(refund_amount)} each).{extra}",
            ephemeral=True,
        )

    @staticmethod
    def _inventory_line(item_id: str, quantity: int, equipment: dict[str, str]) -> str:
        item = get_item(item_id)
        if item is None:
            return f"`{item_id}` x{quantity}"
        if equipment.get("weapon") == item.id:
            equipped = " (main hand)"
        elif equipment.get("off_hand") == item.id:
            equipped = " (off-hand)"
        elif equipment.get("armor") == item.id:
            equipped = " (armor)"
        else:
            equipped = ""
        refund = sell_refund_for_item(item)
        sell_note = f" · sell {fmt_amount(refund)}/ea" if refund is not None else ""
        return (
            f"**{item.name}** x{quantity}{equipped}\n"
            f"└ {format_item_stats(item)}{sell_note} · `{item.id}`"
        )

    @staticmethod
    def _equipped_name(item_id: str | None) -> str:
        if item_id is None:
            return "None"
        item = get_item(item_id)
        return item.name if item is not None else item_id


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Shop(bot))
