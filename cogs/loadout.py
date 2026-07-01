from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from items import ShopItem, get_item
from utils.fix_gear_ui import send_fix_panel
from utils.helpers import fmt_amount, guild_only_message
from utils.loadout import parse_loadout
from utils.loadout_presets import (
    best_accessory,
    best_weapon_and_gun,
    format_preset_slot,
)


def _best_armor(rows: list) -> ShopItem | None:
    best: ShopItem | None = None
    for row in rows:
        item = get_item(str(row["item_id"]))
        if item is None or item.category != "armor":
            continue
        if best is None or item.power > best.power:
            best = item
    return best


class Loadout(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="loadout", description="Save, apply, or list gear loadout presets.")
    @app_commands.describe(
        action="What to do",
        slot="Preset slot 1–3",
        name="Name for this preset (save only)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="List presets", value="list"),
            app_commands.Choice(name="Save current gear", value="save"),
            app_commands.Choice(name="Apply preset", value="apply"),
        ],
    )
    @app_commands.guild_only()
    async def loadout(
        self,
        interaction: discord.Interaction,
        action: str,
        slot: app_commands.Range[int, 1, 3] = 1,
        name: str = "Default",
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        guild_id = interaction.guild_id
        uid = interaction.user.id

        if action == "list":
            rows = await self.bot.db.list_loadout_presets(uid, guild_id)
            if not rows:
                await interaction.response.send_message(
                    "No presets saved. Use **Save current gear** on slot 1–3.",
                    ephemeral=True,
                )
                return
            lines = []
            for row in rows:
                ring_id = row["ring_id"] if "ring_id" in row.keys() else None
                amulet_id = row["amulet_id"] if "amulet_id" in row.keys() else None
                lines.append(
                    f"**Slot {int(row['slot'])}** — {row['name']}:\n"
                    f"  ⚔️ {format_preset_slot(row['weapon_id'])} · "
                    f"🔫 {format_preset_slot(row['off_hand_id'])} · "
                    f"🛡️ {format_preset_slot(row['armor_id'])}\n"
                    f"  💍 {format_preset_slot(ring_id)} · "
                    f"📿 {format_preset_slot(amulet_id)}"
                )
            await interaction.response.send_message("\n".join(lines), ephemeral=True)
            return

        if action == "save":
            equipment = await self.bot.db.get_equipment(uid, guild_id)
            parsed = parse_loadout(equipment)
            await self.bot.db.save_loadout_preset(
                uid,
                guild_id,
                int(slot),
                name,
                parsed.primary.id if parsed.primary else None,
                parsed.off_hand.id if parsed.off_hand else None,
                parsed.armor.id if parsed.armor else None,
                parsed.ring.id if parsed.ring else None,
                parsed.amulet.id if parsed.amulet else None,
            )
            await interaction.response.send_message(
                f"Saved preset **{name}** to slot **{slot}** (weapon, off-hand, armor, ring, amulet).",
                ephemeral=True,
            )
            return

        if action == "apply":
            row = await self.bot.db.get_loadout_preset(uid, guild_id, int(slot))
            if row is None:
                await interaction.response.send_message(
                    f"No preset in slot **{slot}**.", ephemeral=True,
                )
                return
            item_ids = [
                row["weapon_id"],
                row["off_hand_id"],
                row["armor_id"],
            ]
            if "ring_id" in row.keys():
                item_ids.append(row["ring_id"])
            if "amulet_id" in row.keys():
                item_ids.append(row["amulet_id"])
            for item_id in item_ids:
                if item_id:
                    slot_name = await self.bot.db.equip_gear_item(
                        uid, guild_id, str(item_id),
                    )
                    if slot_name is None:
                        await interaction.response.send_message(
                            f"You no longer own **{format_preset_slot(str(item_id))}** from that preset.",
                            ephemeral=True,
                        )
                        return
            await interaction.response.send_message(
                f"Applied loadout **{row['name']}** (slot {slot}).",
                ephemeral=True,
            )
            return

        await interaction.response.send_message("Unknown action.", ephemeral=True)

    @app_commands.command(
        name="equip-best",
        description="Equip your best weapon/gun, armor, ring, and amulet from inventory.",
    )
    @app_commands.guild_only()
    async def equip_best(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        rows = await self.bot.db.get_inventory(interaction.user.id, interaction.guild_id)
        weapon, gun = best_weapon_and_gun(rows)
        armor = _best_armor(rows)
        ring = best_accessory(rows, "ring")
        amulet = best_accessory(rows, "amulet")
        if not any((weapon, gun, armor, ring, amulet)):
            await interaction.response.send_message(
                "No weapons, armor, or accessories in inventory.", ephemeral=True,
            )
            return
        equipped: list[str] = []
        uid = interaction.user.id
        guild_id = interaction.guild_id
        if weapon and await self.bot.db.equip_gear_item(uid, guild_id, weapon.id):
            equipped.append(weapon.name)
        if gun and await self.bot.db.equip_gear_item(uid, guild_id, gun.id):
            equipped.append(gun.name)
        if armor and await self.bot.db.equip_gear_item(uid, guild_id, armor.id):
            equipped.append(armor.name)
        if ring and await self.bot.db.equip_gear_item(uid, guild_id, ring.id):
            equipped.append(ring.name)
        if amulet and await self.bot.db.equip_gear_item(uid, guild_id, amulet.id):
            equipped.append(amulet.name)
        await interaction.response.send_message(
            f"Equipped: **{', '.join(equipped)}**.", ephemeral=True,
        )

    @app_commands.command(
        name="sell-worn",
        description="Sell all battle-worn boss drops in your inventory.",
    )
    @app_commands.guild_only()
    async def sell_worn(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        sold, payout = await self.bot.db.sell_all_battle_worn(
            interaction.user.id, interaction.guild_id,
        )
        if sold <= 0:
            await interaction.response.send_message(
                "No battle-worn items to sell.", ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"Sold **{sold}** battle-worn item(s) for **{fmt_amount(payout)}**.",
            ephemeral=True,
        )

    @app_commands.command(
        name="fix",
        description="Repair unstable gear (80% of shop price) to restore stat bonuses.",
    )
    @app_commands.guild_only()
    async def fix(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        await send_fix_panel(interaction, self)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Loadout(bot))
