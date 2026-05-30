from __future__ import annotations

from typing import TYPE_CHECKING

import discord

import config
from items import get_item
from utils.helpers import fmt_amount

if TYPE_CHECKING:
    from cogs.loadout import Loadout


def fix_cost_for_item_id(item_id: str) -> float:
    item = get_item(item_id)
    if item is None:
        return 0.0
    base_id = item_id.removeprefix("boss_weak_") if item_id.startswith("boss_weak_") else item_id
    base = get_item(base_id)
    price = float(base.price if base is not None else item.price)
    return max(1.0, price * config.GEAR_FIX_COST_FRACTION)


async def build_fix_embed(cog: Loadout, guild_id: int, uid: int) -> discord.Embed:
    unstable = await cog.bot.db.list_unstable_slots(uid, guild_id)
    equipment = await cog.bot.db.get_equipment(uid, guild_id)
    if not unstable:
        return discord.Embed(
            title="Gear repair",
            description="All equipped gear is stable. Nothing to fix.",
            color=discord.Color.green(),
        )
    lines: list[str] = []
    for slot in sorted(unstable):
        item_id = equipment.get(slot)
        item = get_item(item_id) if item_id else None
        name = item.name if item is not None else slot
        cost = fix_cost_for_item_id(item_id) if item_id else 0.0
        lines.append(f"**{slot.title()}** — {name} · fix **{fmt_amount(cost)}**")
    return discord.Embed(
        title="Unstable gear",
        description=(
            "Unstable gear gives **no combat stats** until repaired.\n\n"
            + "\n".join(lines)
        ),
        color=discord.Color.orange(),
    )


class FixGearView(discord.ui.View):
    def __init__(self, cog: Loadout, guild_id: int, user_id: int, unstable: set[str]) -> None:
        super().__init__(timeout=120.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        row = 0
        for slot in sorted(unstable)[:5]:
            self.add_item(FixSlotButton(cog, guild_id, user_id, slot, row=row))
            row = min(row + 1, 4)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your repair panel.", ephemeral=True)
            return False
        return True


class FixSlotButton(discord.ui.Button):
    def __init__(
        self,
        cog: Loadout,
        guild_id: int,
        user_id: int,
        slot: str,
        *,
        row: int,
    ) -> None:
        super().__init__(label=f"Fix {slot}", style=discord.ButtonStyle.primary, row=row)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.slot = slot

    async def callback(self, interaction: discord.Interaction) -> None:
        err = await self.cog.bot.db.fix_unstable_slot(self.user_id, self.guild_id, self.slot)
        if err == "insufficient_funds":
            equipment = await self.cog.bot.db.get_equipment(self.user_id, self.guild_id)
            item_id = equipment.get(self.slot)
            cost = fix_cost_for_item_id(item_id) if item_id else 0.0
            await interaction.response.send_message(
                f"Need **{fmt_amount(cost)}** in your pocket to fix **{self.slot}**.",
                ephemeral=True,
            )
            return
        if err:
            await interaction.response.send_message("Could not repair that slot.", ephemeral=True)
            return
        await interaction.response.defer()
        unstable = await self.cog.bot.db.list_unstable_slots(self.user_id, self.guild_id)
        embed = await build_fix_embed(self.cog, self.guild_id, self.user_id)
        if unstable and isinstance(self.view, FixGearView):
            view = FixGearView(self.cog, self.guild_id, self.user_id, unstable)
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            if isinstance(self.view, FixGearView):
                for child in self.view.children:
                    child.disabled = True
            await interaction.edit_original_response(embed=embed, view=self.view)
        await interaction.followup.send(f"**{self.slot.title()}** repaired!", ephemeral=True)


async def send_fix_panel(interaction: discord.Interaction, cog: Loadout) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return
    uid = interaction.user.id
    unstable = await cog.bot.db.list_unstable_slots(uid, interaction.guild_id)
    embed = await build_fix_embed(cog, interaction.guild_id, uid)
    if not unstable:
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    view = FixGearView(cog, interaction.guild_id, uid, unstable)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
