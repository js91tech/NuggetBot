from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from items import get_item
from utils.enhancement import (
    display_level,
    enhance_attempt_cost,
    format_instance_label,
    repair_nugget_cost,
    roll_enhancement,
)
from utils.helpers import fmt_amount, guild_only_message


class EnhanceSelect(discord.ui.Select):
    def __init__(self, view: "EnhanceView") -> None:
        self._view = view
        options: list[discord.SelectOption] = []
        for row in view.instances[:25]:
            item = get_item(str(row["item_id"]))
            if item is None:
                continue
            level = int(row["enhancement_level"])
            broken = bool(int(row["is_broken"]))
            label = format_instance_label(
                item, int(row["instance_id"]), level, broken=broken,
            )[:100]
            options.append(
                discord.SelectOption(
                    label=label,
                    value=str(row["instance_id"]),
                    description=item.category.title(),
                    default=view.instance_id == int(row["instance_id"]),
                ),
            )
        super().__init__(
            placeholder="Choose gear to enhance…",
            min_values=1,
            max_values=1,
            options=options or [discord.SelectOption(label="No gear", value="0")],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "0":
            await interaction.response.send_message("No enhanceable gear.", ephemeral=True)
            return
        self._view.instance_id = int(self.values[0])
        await self._view.refresh(interaction)


class EnhanceView(discord.ui.View):
    def __init__(
        self,
        cog: "Enhancement",
        guild_id: int,
        user_id: int,
        instances: list,
        *,
        instance_id: int | None = None,
    ) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.instances = instances
        self.instance_id = instance_id
        if instances:
            self.add_item(EnhanceSelect(self))

    def _selected_row(self):
        if self.instance_id is None:
            return None
        for row in self.instances:
            if int(row["instance_id"]) == self.instance_id:
                return row
        return None

    def _refreshed(self) -> EnhanceView:
        return EnhanceView(
            self.cog,
            self.guild_id,
            self.user_id,
            self.instances,
            instance_id=self.instance_id,
        )

    async def refresh(self, interaction: discord.Interaction) -> None:
        view = self._refreshed()
        embed = await self.cog.build_enhance_embed(
            self.guild_id, self.user_id, view._selected_row(),
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This panel is not yours.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Enhance", style=discord.ButtonStyle.success, row=1)
    async def enhance_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        row = self._selected_row()
        if row is None:
            await interaction.response.send_message("Pick a gear instance first.", ephemeral=True)
            return
        if bool(int(row["is_broken"])):
            await interaction.response.send_message(
                "That gear is broken — use `/repair-gear` first.", ephemeral=True,
            )
            return
        level = int(row["enhancement_level"])
        cost = enhance_attempt_cost(level)
        if cost is None:
            await interaction.response.send_message("Already max enhancement.", ephemeral=True)
            return
        wallet = await self.cog.bot.db.get_balance(self.user_id, self.guild_id)
        if wallet < cost.nugget_cost:
            await interaction.response.send_message(
                f"Need **{fmt_amount(cost.nugget_cost)}** in your pocket.", ephemeral=True,
            )
            return
        scrap_have = await self.cog.bot.db.get_inventory_quantity(
            self.user_id, self.guild_id, cost.material_id,
        )
        if scrap_have < cost.material_qty:
            await interaction.response.send_message(
                f"Need **{cost.material_qty}×** `{cost.material_id}`.", ephemeral=True,
            )
            return
        for _ in range(cost.material_qty):
            if not await self.cog.bot.db.consume_inventory_item(
                self.user_id, self.guild_id, cost.material_id,
            ):
                await interaction.response.send_message("Material consumption failed.", ephemeral=True)
                return
        if not await self.cog.bot.db.debit_wallet(self.user_id, self.guild_id, cost.nugget_cost):
            for _ in range(cost.material_qty):
                await self.cog.bot.db.grant_item(
                    self.user_id, self.guild_id, cost.material_id,
                )
            await interaction.response.send_message("Could not debit nuggets.", ephemeral=True)
            return
        instance_id = int(row["instance_id"])
        result = roll_enhancement(level)
        await self.cog.bot.db.set_gear_instance_level(
            instance_id,
            self.guild_id,
            result.new_level,
            broken=result.broken,
        )
        await self.cog.bot.db.attach_gear_instance_to_equipped_slots(
            self.user_id, self.guild_id, instance_id,
        )
        self.instances = await self.cog.bot.db.list_gear_instances(self.user_id, self.guild_id)
        view = self._refreshed()
        panel = await self.cog.build_enhance_embed(
            self.guild_id, self.user_id, view._selected_row(),
        )
        embed = discord.Embed(
            title=panel.title,
            description=f"{result.message}\n\n{panel.description}",
            color=discord.Color.green() if result.success else discord.Color.red(),
        )
        embed.set_footer(text="Tap **Enhance** again to keep going.")
        await interaction.response.edit_message(embed=embed, view=view)


class RepairButton(discord.ui.Button):
    def __init__(self, cog: "Enhancement", guild_id: int, user_id: int, instance_id: int, label: str) -> None:
        super().__init__(label=label[:80], style=discord.ButtonStyle.primary)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.instance_id = instance_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your panel.", ephemeral=True)
            return
        row = await self.cog.bot.db.get_gear_instance(self.instance_id, self.guild_id)
        if row is None or int(row["user_id"]) != self.user_id:
            await interaction.response.send_message("Instance not found.", ephemeral=True)
            return
        cost = repair_nugget_cost(str(row["item_id"]))
        if not await self.cog.bot.db.debit_wallet(self.user_id, self.guild_id, cost):
            await interaction.response.send_message(
                f"Need **{fmt_amount(cost)}** in your pocket.", ephemeral=True,
            )
            return
        if not await self.cog.bot.db.repair_gear_instance(self.instance_id, self.guild_id):
            await interaction.response.send_message("Repair failed.", ephemeral=True)
            return
        item = get_item(str(row["item_id"]))
        item_name = item.name if item is not None else str(row["item_id"])
        await interaction.response.send_message(
            f"Repaired **{item_name}** for **{fmt_amount(cost)}**.",
            ephemeral=True,
        )


class Enhancement(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _default_instance_id(self, guild_id: int, user_id: int) -> int | None:
        records = await self.bot.db.get_equipment_records(user_id, guild_id)
        for slot in ("weapon", "off_hand", "armor", "ring", "amulet"):
            rec = records.get(slot)
            if not rec:
                continue
            inst = rec.get("gear_instance_id")
            if inst is not None:
                return int(inst)
        return None

    async def build_enhance_embed(self, guild_id: int, user_id: int, row) -> discord.Embed:
        if row is None:
            return discord.Embed(
                title="Gear enhancement",
                description="Select a gear instance to enhance.",
                color=discord.Color.gold(),
            )
        item = get_item(str(row["item_id"]))
        level = int(row["enhancement_level"])
        cost = enhance_attempt_cost(level)
        lines = [f"**{item.name if item else row['item_id']}** — **{display_level(level)}**"]
        if bool(int(row["is_broken"])):
            lines.append("Status: **BROKEN** — repair before enhancing.")
        elif cost is not None:
            mat = get_item(cost.material_id)
            mat_name = mat.name if mat else cost.material_id
            lines.append(
                f"Next: **{display_level(cost.target_level)}** · "
                f"**{cost.material_qty}×** {mat_name} · **{fmt_amount(cost.nugget_cost)}** · "
                f"**{int(cost.success_rate * 100)}%** success"
            )
        else:
            lines.append("**Max enhancement reached.**")
        return discord.Embed(
            title="Gear enhancement",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )

    @app_commands.command(name="enhance", description="Enhance gear (+1 to PENTA) with materials and nuggets.")
    @app_commands.guild_only()
    async def enhance(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        await self.bot.db.sync_gear_instances_from_inventory(
            interaction.user.id, interaction.guild_id,
        )
        await self.bot.db.ensure_equipment_gear_instance_links(
            interaction.user.id, interaction.guild_id,
        )
        gear_rows = await self.bot.db.list_gear_instances(interaction.user.id, interaction.guild_id)
        if not gear_rows:
            await interaction.response.send_message(
                "You have no enhanceable gear instances yet. Buy or earn weapons, armor, or accessories.",
                ephemeral=True,
            )
            return
        default_id = await self._default_instance_id(interaction.guild_id, interaction.user.id)
        selected = None
        if default_id is not None:
            for row in gear_rows:
                if int(row["instance_id"]) == default_id:
                    selected = row
                    break
        view = EnhanceView(
            self,
            interaction.guild_id,
            interaction.user.id,
            gear_rows,
            instance_id=default_id,
        )
        embed = await self.build_enhance_embed(interaction.guild_id, interaction.user.id, selected)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="repair-gear", description="Repair broken enhanced gear (10% of base item price).")
    @app_commands.guild_only()
    async def repair_gear(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        broken = await self.bot.db.list_broken_gear_instances(interaction.user.id, interaction.guild_id)
        if not broken:
            await interaction.response.send_message("No broken gear to repair.", ephemeral=True)
            return
        view = discord.ui.View(timeout=120.0)
        for row in broken[:5]:
            item = get_item(str(row["item_id"]))
            name = item.name if item else str(row["item_id"])
            cost = repair_nugget_cost(str(row["item_id"]))
            view.add_item(
                RepairButton(
                    self,
                    interaction.guild_id,
                    interaction.user.id,
                    int(row["instance_id"]),
                    f"{name} — {fmt_amount(cost)}",
                ),
            )
        await interaction.response.send_message(
            "Choose gear to repair:", view=view, ephemeral=True,
        )

    @app_commands.command(name="equip-instance", description="Equip a specific gear instance by id.")
    @app_commands.describe(instance_id="Gear instance id from /enhance")
    @app_commands.guild_only()
    async def equip_instance(self, interaction: discord.Interaction, instance_id: int) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        slot = await self.bot.db.equip_gear_instance(
            interaction.user.id, interaction.guild_id, instance_id,
        )
        if slot is None:
            await interaction.response.send_message("Could not equip that instance.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Equipped instance **#{instance_id}** to **{slot}**.", ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Enhancement(bot))
