from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from items import get_item
from utils.alchemy import RECIPE_MAP, RECIPES, recipe_available
from utils.alchemy_hub_ui import send_alchemy_hub
from utils.helpers import fmt_amount, guild_only_message


class Alchemy(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _material_qty(self, uid: int, guild_id: int, item_id: str) -> int:
        return await self.bot.db.get_inventory_quantity(uid, guild_id, item_id)

    @app_commands.command(name="alchemy", description="Craft consumables from alchemy scrap.")
    @app_commands.describe(
        action="List recipes or craft one",
        recipe="Recipe id from list",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="List recipes", value="list"),
            app_commands.Choice(name="Craft", value="craft"),
        ],
        recipe=[app_commands.Choice(name=r.name, value=r.recipe_id) for r in RECIPES],
    )
    @app_commands.guild_only()
    async def alchemy(
        self,
        interaction: discord.Interaction,
        action: str,
        recipe: str | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        guild_id = interaction.guild_id
        uid = interaction.user.id

        if action == "list":
            await send_alchemy_hub(self, interaction)
            return

        if action == "craft":
            if not recipe or recipe not in RECIPE_MAP:
                await interaction.response.send_message("Pick a recipe.", ephemeral=True)
                return
            r = RECIPE_MAP[recipe]
            blueprints = {
                str(row["blueprint_id"])
                for row in await self.bot.db.list_blueprints(uid, guild_id)
            }
            if not recipe_available(r, blueprints):
                await interaction.response.send_message(
                    "Blueprint not unlocked. Check `/codex`.", ephemeral=True,
                )
                return
            scrap_have = await self._material_qty(uid, guild_id, "alchemy_scrap")
            if scrap_have < r.scrap_cost:
                await interaction.response.send_message(
                    f"Need **{r.scrap_cost}** alchemy scrap (you have {scrap_have}).",
                    ephemeral=True,
                )
                return
            for mat, need, item_id in (
                ("essence", r.essence_cost, "dungeon_essence"),
                ("resin", r.resin_cost, "harvest_resin"),
                ("waste", r.waste_cost, "business_waste"),
            ):
                if need > 0:
                    have = await self._material_qty(uid, guild_id, item_id)
                    if have < need:
                        await interaction.response.send_message(
                            f"Need **{need}** {mat} (you have {have}).", ephemeral=True,
                        )
                        return
            if not await self.bot.db.debit_wallet(uid, guild_id, r.nugget_cost):
                await interaction.response.send_message(
                    f"Need **{fmt_amount(r.nugget_cost)}**.", ephemeral=True,
                )
                return
            for _ in range(r.scrap_cost):
                if not await self.bot.db.consume_inventory_item(uid, guild_id, "alchemy_scrap"):
                    await self.bot.db.credit_wallet(uid, guild_id, r.nugget_cost)
                    await interaction.response.send_message("Craft failed — refunded.", ephemeral=True)
                    return
            for _ in range(r.essence_cost):
                await self.bot.db.consume_inventory_item(uid, guild_id, "dungeon_essence")
            for _ in range(r.resin_cost):
                await self.bot.db.consume_inventory_item(uid, guild_id, "harvest_resin")
            for _ in range(r.waste_cost):
                await self.bot.db.consume_inventory_item(uid, guild_id, "business_waste")
            await self.bot.db.grant_item(uid, guild_id, r.output_item_id)
            from utils.expansion_events import record_expansion_event

            await record_expansion_event(self.bot.db, guild_id, uid, "craft_done")
            out = get_item(r.output_item_id)
            await interaction.response.send_message(
                f"Crafted **{out.name if out else r.output_item_id}**!",
                ephemeral=True,
            )
            return

        await interaction.response.send_message("Unknown action.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Alchemy(bot))
