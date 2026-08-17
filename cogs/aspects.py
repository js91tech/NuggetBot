from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.aspects import (
    ASPECT_DEFINITIONS,
    format_aspect_effect,
    format_aspect_line,
    instance_from_row,
)
from utils.helpers import fmt_amount, guild_only_message


class Aspects(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    aspects_group = app_commands.Group(
        name="aspects",
        description="Collect, buy, equip, and fuse combat aspects.",
        guild_only=True,
    )

    async def _equipped_slot_map(self, user_id: int, guild_id: int) -> dict[int, int]:
        rows = await self.bot.db.list_equipped_aspect_slots(user_id, guild_id)
        return {int(row["instance_id"]): int(row["slot"]) for row in rows}

    async def aspect_instance_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        rows = await self.bot.db.list_aspect_instances(
            interaction.user.id,
            interaction.guild_id,
        )
        slot_map = await self._equipped_slot_map(
            interaction.user.id,
            interaction.guild_id,
        )
        current_lower = current.lower()
        choices: list[app_commands.Choice[str]] = []
        for row in rows:
            inst = instance_from_row(row)
            label = f"{inst.name} {inst.roll_pct:g}% (#{inst.instance_id})"
            if (
                current_lower
                and current_lower not in label.lower()
                and current_lower not in str(inst.instance_id)
            ):
                continue
            if inst.instance_id in slot_map:
                label += f" [slot {slot_map[inst.instance_id]}]"
            choices.append(
                app_commands.Choice(name=label[:100], value=str(inst.instance_id)),
            )
            if len(choices) >= 25:
                break
        return choices

    @aspects_group.command(
        name="list",
        description="View your collected aspects (Diablo-style combat modifiers).",
    )
    @app_commands.describe(user="Player to inspect. Defaults to you.")
    async def aspects_list(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        target = user or interaction.user
        rows = await self.bot.db.list_aspect_instances(target.id, interaction.guild_id)
        slot_map = await self._equipped_slot_map(target.id, interaction.guild_id)
        if not rows:
            await interaction.response.send_message(
                f"{target.display_name} has no aspects yet. "
                f"Boss kills can drop them, or buy one for **{fmt_amount(config.ASPECT_SHOP_PRICE)}** with `/aspects buy`.",
                ephemeral=True,
            )
            return
        lines = [
            format_aspect_line(
                instance_from_row(row),
                equip_slot=slot_map.get(int(row["instance_id"])),
            )
            for row in rows
        ]
        equipped_summary = ""
        if slot_map:
            equipped_summary = (
                f"\n\n**Equipped ({len(slot_map)}/{config.ASPECT_MAX_EQUIP_SLOTS}):** "
                + ", ".join(f"slot {s}" for s in sorted(slot_map.values()))
            )
        embed = discord.Embed(
            title=f"{target.display_name}'s Aspects",
            description="\n".join(lines[:15]) + equipped_summary,
            color=discord.Color.purple(),
        )
        if len(lines) > 15:
            embed.set_footer(text=f"+{len(lines) - 15} more · /aspects equip · /aspects unequip")
        else:
            embed.set_footer(
                text=(
                    f"Equip up to {config.ASPECT_MAX_EQUIP_SLOTS} at once: "
                    "/aspects equip [id] [slot 1-3]"
                ),
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @aspects_group.command(
        name="shop",
        description="Browse aspect types and shop pricing.",
    )
    async def aspect_shop(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        catalog = "\n".join(
            f"**{a.name}** — {a.description}" for a in ASPECT_DEFINITIONS
        )
        embed = discord.Embed(
            title="Aspect Shop",
            description=(
                f"Buy a random rolled aspect for **{fmt_amount(config.ASPECT_SHOP_PRICE)}** with `/aspects buy`.\n"
                "Shop rolls land between **4%** and **14%**. Boss drops scale with threat tier "
                "(harder bosses = higher rolls, up to **40%** on mythic-tier raids).\n"
                "Utility aspects affect duels/hr, work income (up to **3×**), energy regen, "
                "duel loot, daily/passive gold, and more.\n\n"
                f"{catalog}"
            ),
            color=discord.Color.dark_purple(),
        )
        embed.set_footer(
            text=f"Equip up to {config.ASPECT_MAX_EQUIP_SLOTS} aspects — bonuses stack",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @aspects_group.command(
        name="buy",
        description=f"Buy random aspect roll(s) for {config.ASPECT_SHOP_PRICE:,.0f} {config.CURRENCY_NAME} each.",
    )
    @app_commands.describe(quantity="How many aspects to buy (1–99)")
    async def buy_aspect(
        self,
        interaction: discord.Interaction,
        quantity: app_commands.Range[int, 1, 99] = 1,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        unit_price = config.ASPECT_SHOP_PRICE
        qty = int(quantity)
        total = unit_price * qty
        instance_ids = await self.bot.db.buy_aspect_from_shop(
            interaction.user.id,
            interaction.guild_id,
            unit_price,
            quantity=qty,
        )
        if instance_ids is None:
            await interaction.response.send_message(
                f"You need **{fmt_amount(total)}** to buy **{qty}×** aspect(s) "
                f"({fmt_amount(unit_price)} each).",
                ephemeral=True,
            )
            return
        lines: list[str] = []
        for iid in instance_ids[:8]:
            row = await self.bot.db.get_aspect_instance(
                interaction.user.id,
                interaction.guild_id,
                iid,
            )
            if row is None:
                continue
            inst = instance_from_row(row)
            lines.append(
                f"**{inst.name}** — {format_aspect_effect(inst)} (`aspect#{inst.instance_id}`)",
            )
        if len(instance_ids) > 8:
            lines.append(f"_…and {len(instance_ids) - 8} more_")
        body = "\n".join(lines) if lines else "_Rolls saved — check `/aspects list`_"
        await interaction.response.send_message(
            f"Bought **{qty}×** aspect(s) for **{fmt_amount(total)}**.\n{body}\n"
            f"Equip with `/aspects equip` (up to **{config.ASPECT_MAX_EQUIP_SLOTS}** slots).",
            ephemeral=True,
        )

    @aspects_group.command(
        name="equip",
        description=f"Equip an aspect (up to {config.ASPECT_MAX_EQUIP_SLOTS} slots).",
    )
    @app_commands.describe(
        instance_id="Aspect instance id from /aspects list",
        slot="Slot 1–3 (optional — uses first empty slot)",
    )
    @app_commands.autocomplete(instance_id=aspect_instance_autocomplete)
    @app_commands.choices(
        slot=[
            app_commands.Choice(name="Slot 1", value=1),
            app_commands.Choice(name="Slot 2", value=2),
            app_commands.Choice(name="Slot 3", value=3),
        ],
    )
    async def equip_aspect(
        self,
        interaction: discord.Interaction,
        instance_id: str,
        slot: int | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        raw = instance_id.strip().lower().removeprefix("aspect#")
        try:
            iid = int(raw)
        except ValueError:
            await interaction.response.send_message(
                "Use the numeric id from `/aspects list` (e.g. `42`).",
                ephemeral=True,
            )
            return
        ok, result = await self.bot.db.equip_aspect_instance(
            interaction.user.id,
            interaction.guild_id,
            iid,
            slot=slot,
        )
        if not ok:
            if result == "full":
                await interaction.response.send_message(
                    f"All **{config.ASPECT_MAX_EQUIP_SLOTS}** aspect slots are full. "
                    "Use `/aspects unequip` or pass a **slot** to replace one.",
                    ephemeral=True,
                )
                return
            if result == "invalid_slot":
                await interaction.response.send_message(
                    f"Slot must be **1**–**{config.ASPECT_MAX_EQUIP_SLOTS}**.",
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                "You do not own that aspect instance.",
                ephemeral=True,
            )
            return
        row = await self.bot.db.get_aspect_instance(
            interaction.user.id,
            interaction.guild_id,
            iid,
        )
        inst = instance_from_row(row)
        await interaction.response.send_message(
            f"Equipped **{inst.name}** in **slot {result}** — {format_aspect_effect(inst)}.",
            ephemeral=True,
        )

    @aspects_group.command(
        name="unequip",
        description="Remove an aspect from an equip slot.",
    )
    @app_commands.describe(slot="Slot 1–3 to clear")
    @app_commands.choices(
        slot=[
            app_commands.Choice(name="Slot 1", value=1),
            app_commands.Choice(name="Slot 2", value=2),
            app_commands.Choice(name="Slot 3", value=3),
        ],
    )
    async def unequip_aspect(self, interaction: discord.Interaction, slot: int) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        removed = await self.bot.db.unequip_aspect_slot(
            interaction.user.id,
            interaction.guild_id,
            slot,
        )
        if not removed:
            await interaction.response.send_message(
                f"Slot **{slot}** is already empty.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"Cleared aspect **slot {slot}**.",
            ephemeral=True,
        )

    @aspects_group.command(
        name="fuse",
        description="Sacrifice 3 unequipped aspects to forge one stronger roll.",
    )
    @app_commands.describe(
        aspect1="First aspect instance id",
        aspect2="Second aspect instance id",
        aspect3="Third aspect instance id",
    )
    @app_commands.autocomplete(aspect1=aspect_instance_autocomplete)
    @app_commands.autocomplete(aspect2=aspect_instance_autocomplete)
    @app_commands.autocomplete(aspect3=aspect_instance_autocomplete)
    async def fuse_aspects(
        self,
        interaction: discord.Interaction,
        aspect1: str,
        aspect2: str,
        aspect3: str,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        try:
            ids = [int(aspect1), int(aspect2), int(aspect3)]
        except ValueError:
            await interaction.response.send_message(
                "Pick three aspects from autocomplete.", ephemeral=True,
            )
            return
        if len(set(ids)) != 3:
            await interaction.response.send_message(
                "You must pick three **different** aspects.", ephemeral=True,
            )
            return
        new_id = await self.bot.db.fuse_aspect_instances(
            interaction.user.id, interaction.guild_id, ids,
        )
        if new_id is None:
            await interaction.response.send_message(
                "Fusion failed — aspects must be yours, unequipped, and valid.",
                ephemeral=True,
            )
            return
        row = await self.bot.db.get_aspect_instance(
            interaction.user.id, interaction.guild_id, new_id,
        )
        inst = instance_from_row(row)
        await interaction.response.send_message(
            f"Fusion complete! Created **{inst.name}** ({inst.roll_pct:g}%) — instance `#{new_id}`.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Aspects(bot))
