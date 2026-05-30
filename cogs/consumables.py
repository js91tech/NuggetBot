from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import config
from items import GIFTABLE_ITEM_IDS, get_item
from utils.bot_players import pvp_target_error
from utils.helpers import fmt_amount, guild_only_message
from utils.jail import execute_jail_key


class Consumables(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def use_item_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        from items import CONSUMABLE_USE_IDS

        rows = await self.bot.db.get_inventory(interaction.user.id, interaction.guild_id)
        needle = current.lower()
        choices: list[app_commands.Choice[str]] = []
        for row in rows:
            item_id = str(row["item_id"])
            if item_id not in CONSUMABLE_USE_IDS:
                continue
            item = get_item(item_id)
            if item is None:
                continue
            if needle and needle not in item_id and needle not in item.name.lower():
                continue
            choices.append(
                app_commands.Choice(
                    name=f"{item.name} x{int(row['quantity'])}",
                    value=item_id,
                ),
            )
            if len(choices) >= 25:
                break
        return choices

    async def gift_item_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        rows = await self.bot.db.get_inventory(interaction.user.id, interaction.guild_id)
        needle = current.lower()
        choices: list[app_commands.Choice[str]] = []
        for row in rows:
            item_id = str(row["item_id"])
            if item_id not in GIFTABLE_ITEM_IDS:
                continue
            item = get_item(item_id)
            if item is None:
                continue
            if needle and needle not in item_id and needle not in item.name.lower():
                continue
            choices.append(
                app_commands.Choice(
                    name=f"{item.name} x{int(row['quantity'])}",
                    value=item_id,
                ),
            )
            if len(choices) >= 25:
                break
        return choices

    @app_commands.command(name="use", description="Use a consumable from your inventory.")
    @app_commands.describe(
        item="Consumable to use",
        target="Arrested player (Jail Key only — omit to use on yourself)",
    )
    @app_commands.autocomplete(item=use_item_autocomplete)
    @app_commands.guild_only()
    async def use_item(
        self,
        interaction: discord.Interaction,
        item: str,
        target: discord.Member | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        from items import CONSUMABLE_USE_IDS

        item_id = item.strip()
        shop_item = get_item(item_id)
        if shop_item is None or item_id not in CONSUMABLE_USE_IDS:
            await interaction.response.send_message(
                "That item cannot be used with /use.", ephemeral=True,
            )
            return
        guild_id = interaction.guild_id
        uid = interaction.user.id
        qty = await self.bot.db.get_inventory_quantity(uid, guild_id, item_id)
        if qty <= 0:
            await interaction.response.send_message(
                "You do not have that item.", ephemeral=True,
            )
            return

        if target is not None and item_id != "jail_key":
            await interaction.response.send_message(
                "Only **Jail Key** accepts a target player.", ephemeral=True,
            )
            return

        if item_id == "energy_drink":
            if not await self.bot.db.consume_inventory_item(uid, guild_id, item_id):
                await interaction.response.send_message(
                    "Could not consume item.", ephemeral=True,
                )
                return
            new_energy = await self.bot.db.add_energy(uid, guild_id, 15)
            await interaction.response.send_message(
                f"**Energy Drink** — energy restored to **{new_energy}**.",
                ephemeral=True,
            )
            return

        if item_id == "jail_key":
            if target is not None and target.bot and not config.ALLOW_BOT_PLAYERS:
                await interaction.response.send_message(
                    "Bots cannot be freed with a Jail Key.", ephemeral=True,
                )
                return
            release_target = target if target is not None else interaction.user
            if not isinstance(release_target, discord.Member):
                await interaction.response.send_message("Members only.", ephemeral=True)
                return
            result = await execute_jail_key(
                self.bot.db,
                uid,
                release_target.id,
                guild_id,
            )
            if not result.ok:
                await interaction.response.send_message(result.error or "Use failed.", ephemeral=True)
                return
            if release_target.id == uid:
                await interaction.response.send_message(result.message, ephemeral=True)
            else:
                await interaction.response.send_message(
                    f"{result.message} ({release_target.display_name})",
                    ephemeral=True,
                )
            return

        if not await self.bot.db.consume_inventory_item(uid, guild_id, item_id):
            await interaction.response.send_message(
                "Could not consume item.", ephemeral=True,
            )
            return
        await self.bot.db.set_pending_consumable(uid, guild_id, item_id)
        hint = {
            "raid_potion": "Next **/attack** deals +20% boss damage.",
            "duel_scroll": "Your next **/duel** deals +15% strike damage.",
        }.get(item_id, "Buff active.")
        await interaction.response.send_message(
            f"Used **{shop_item.name}**. {hint} (5 min window)",
            ephemeral=True,
        )

    @app_commands.command(
        name="gift",
        description="Gift chia seeds (or other giftable items) from your inventory.",
    )
    @app_commands.describe(
        user="Player to receive the gift",
        item="Item to gift (buy Chia Seeds from /shop first)",
        quantity="How many to send (1–99)",
    )
    @app_commands.autocomplete(item=gift_item_autocomplete)
    @app_commands.guild_only()
    async def gift(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        item: str,
        quantity: app_commands.Range[int, 1, 99] = 1,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        gift_err = pvp_target_error(user, interaction.user.id)
        if gift_err:
            await interaction.response.send_message(gift_err, ephemeral=True)
            return
        item_id = item.strip()
        shop_item = get_item(item_id)
        if shop_item is None or item_id not in GIFTABLE_ITEM_IDS:
            await interaction.response.send_message(
                "That item cannot be gifted. Buy **Chia Seeds** from `/shop` consumables.",
                ephemeral=True,
            )
            return
        guild_id = interaction.guild_id
        sender_id = interaction.user.id
        qty = int(quantity)
        err = await self.bot.db.gift_inventory_item(
            sender_id, user.id, guild_id, item_id, qty,
        )
        if err == "insufficient_items":
            await interaction.response.send_message(
                f"You need **{qty}×** **{shop_item.name}** in your inventory "
                f"(buy with `/buy chia_seeds`).",
                ephemeral=True,
            )
            return
        if err == "self_gift":
            await interaction.response.send_message(
                "Gift them to someone else!", ephemeral=True,
            )
            return
        if err:
            await interaction.response.send_message(
                "Could not complete the gift.", ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"{interaction.user.mention} gifted **{qty}×** **{shop_item.name}** "
            f"to {user.mention}! 🌱",
            allowed_mentions=discord.AllowedMentions(users=True),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Consumables(bot))
