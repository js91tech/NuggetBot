"""Gear hub — equipped loadout summary, inventory count, and shop shortcut."""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from items import get_item
from utils.goon_theme import FOOTER_BRAND, branded_embed, panel_title
from utils.helpers import guild_only_message

if TYPE_CHECKING:
    from discord.ext import commands

_SLOT_LABELS: tuple[tuple[str, str], ...] = (
    ("weapon", "Main hand"),
    ("off_hand", "Off-hand"),
    ("armor", "Armor"),
    ("ring", "Ring"),
    ("amulet", "Amulet"),
)


def _equipped_name(item_id: str | None) -> str:
    if not item_id:
        return "None"
    item = get_item(item_id)
    return item.name if item is not None else item_id


async def build_gear_hub_embed(
    cog: commands.Cog,
    member: discord.Member,
    guild_id: int,
    user_id: int,
) -> discord.Embed:
    equipment = await cog.bot.db.get_equipment(user_id, guild_id)
    inventory = await cog.bot.db.get_inventory(user_id, guild_id)
    loadout = await cog.bot.db.get_combat_loadout(user_id, guild_id)
    unstable = await cog.bot.db.list_unstable_slots(user_id, guild_id)

    embed = branded_embed(
        panel_title("Gear Hub", member_name=member.display_name),
        description="Equipped loadout and stash at a glance — swap gear from the shop.",
    )
    embed.set_thumbnail(url=member.display_avatar.url)

    lines = []
    for slot, label in _SLOT_LABELS:
        item_id = equipment.get(slot)
        note = " ⚠️ _unstable_" if slot in unstable else ""
        lines.append(f"**{label}:** {_equipped_name(item_id)}{note}")
    embed.add_field(name="Equipped", value="\n".join(lines), inline=False)

    total_stacks = len(inventory)
    total_qty = sum(int(row["quantity"]) for row in inventory)
    embed.add_field(
        name="Inventory",
        value=f"**{total_stacks}** item type(s) · **{total_qty}** piece(s) total",
        inline=True,
    )
    embed.add_field(
        name="Combat ready",
        value="**Yes**" if loadout.primary or loadout.armor else "_No weapon or armor equipped_",
        inline=True,
    )
    embed.set_footer(text=f"{FOOTER_BRAND} · /inventory for full stats · /stats for combat math")
    return embed


class GearHubView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your gear hub.", ephemeral=True,
            )
            return False
        return True

    async def _refresh(self, interaction: discord.Interaction) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message("Refreshed.", ephemeral=True)
            return
        embed = await build_gear_hub_embed(self.cog, member, self.guild_id, self.user_id)
        await interaction.response.edit_message(content=None, embed=embed, view=self)

    @discord.ui.button(label="Inventory / refresh", style=discord.ButtonStyle.secondary, row=0)
    async def inventory_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        await self._refresh(interaction)

    @discord.ui.button(label="Equipped summary", style=discord.ButtonStyle.primary, row=0)
    async def equipped_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        from utils.gear_sets import detect_set_bonus
        from utils.stats import compute_combat_stats, format_combat_stats_block

        loadout = await self.cog.bot.db.get_combat_loadout(self.user_id, self.guild_id)
        progress = await self.cog.bot.db.get_user_progress(self.user_id, self.guild_id)
        prestige = int(progress["prestige_level"])
        set_bonus = detect_set_bonus(loadout.primary, loadout.armor)
        summary = format_combat_stats_block(
            compute_combat_stats(
                loadout.primary,
                loadout.armor,
                off_hand=loadout.off_hand,
                prestige_level=prestige,
                set_bonus=set_bonus,
                accessory_bonuses=loadout.accessory_bonuses,
            ),
            set_bonus=set_bonus,
            prestige_level=prestige,
            off_hand=loadout.off_hand,
        )
        embed = branded_embed(panel_title("Equipped loadout"), description=summary)
        embed.set_footer(text=f"{FOOTER_BRAND} · /stats for the full breakdown")
        await interaction.response.edit_message(content=None, embed=embed, view=self)

    @discord.ui.button(label="Open Shop", style=discord.ButtonStyle.success, row=1)
    async def shop_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        from utils.shop_view import ShopView

        shop_cog = self.cog.bot.get_cog("Shop")
        if shop_cog is None:
            await interaction.response.send_message(
                "Shop is unavailable right now — try `/shop`.", ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        view = ShopView(shop_cog, self.guild_id, self.user_id)
        embed, files, view = await view.build_payload()
        await interaction.followup.send(embed=embed, files=files, view=view, ephemeral=True)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=1)
    async def refresh_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        await self._refresh(interaction)


async def send_gear_hub(cog: commands.Cog, interaction: discord.Interaction) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message(guild_only_message(), ephemeral=True)
        return
    member = interaction.user
    if not isinstance(member, discord.Member):
        await interaction.response.send_message(guild_only_message(), ephemeral=True)
        return
    embed = await build_gear_hub_embed(cog, member, interaction.guild_id, member.id)
    view = GearHubView(cog, interaction.guild_id, member.id)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
