"""Alchemy hub — recipe browser, scrap counter, and one-click crafting.

Mirrors the checks in ``cogs/alchemy.py`` (`/alchemy craft`) so the panel and
the slash command never drift apart.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from items import get_item
from utils.alchemy import RECIPE_MAP, RECIPES, AlchemyRecipe, recipe_available
from utils.goon_theme import brand_color, branded_embed, panel_title
from utils.helpers import fmt_amount, guild_only_message

if TYPE_CHECKING:
    from discord.ext import commands

logger = logging.getLogger(__name__)

SCRAP_ITEM_ID = "alchemy_scrap"


async def _material_qty(cog: commands.Cog, uid: int, guild_id: int, item_id: str) -> int:
    return await cog.bot.db.get_inventory_quantity(uid, guild_id, item_id)


async def _unlocked_blueprints(cog: commands.Cog, uid: int, guild_id: int) -> set[str]:
    return {
        str(r["blueprint_id"])
        for r in await cog.bot.db.list_blueprints(uid, guild_id)
    }


async def build_alchemy_embed(
    cog: commands.Cog,
    guild_id: int,
    user_id: int,
    display_name: str,
    *,
    selected: str | None = None,
) -> discord.Embed:
    scrap_qty = await _material_qty(cog, user_id, guild_id, SCRAP_ITEM_ID)
    blueprints = await _unlocked_blueprints(cog, user_id, guild_id)

    embed = branded_embed(
        panel_title("Alchemy Den", member_name=display_name),
        description=(
            "Cook raid potions, trap bombs, and other filthy little concoctions from "
            f"scrap. You have **{scrap_qty}** alchemy scrap."
        ),
        color=brand_color(),
    )
    for r in RECIPES:
        locked = not recipe_available(r, blueprints)
        status = "🔒" if locked else "✅"
        mark = " **← selected**" if r.recipe_id == selected else ""
        extra = []
        if r.essence_cost:
            extra.append(f"{r.essence_cost} essence")
        if r.resin_cost:
            extra.append(f"{r.resin_cost} resin")
        if r.waste_cost:
            extra.append(f"{r.waste_cost} waste")
        extra_txt = f" + {', '.join(extra)}" if extra else ""
        embed.add_field(
            name=f"{status} {r.name}{mark}",
            value=(
                f"{r.scrap_cost} scrap + {fmt_amount(r.nugget_cost)}{extra_txt} → `{r.output_item_id}`\n"
                f"_{r.description}_"
            ),
            inline=False,
        )
    return embed


async def _craft_recipe(
    cog: commands.Cog, uid: int, guild_id: int, r: AlchemyRecipe,
) -> tuple[str | None, str | None]:
    """Runs the exact same checks as ``/alchemy craft``. Returns (error, success message)."""
    blueprints = await _unlocked_blueprints(cog, uid, guild_id)
    if not recipe_available(r, blueprints):
        return "locked", None
    scrap_have = await _material_qty(cog, uid, guild_id, SCRAP_ITEM_ID)
    if scrap_have < r.scrap_cost:
        return "scrap", f"Need **{r.scrap_cost}** alchemy scrap (you have {scrap_have})."
    for mat, need, item_id in (
        ("essence", r.essence_cost, "dungeon_essence"),
        ("resin", r.resin_cost, "harvest_resin"),
        ("waste", r.waste_cost, "business_waste"),
    ):
        if need > 0:
            have = await _material_qty(cog, uid, guild_id, item_id)
            if have < need:
                return "material", f"Need **{need}** {mat} (you have {have})."
    if not await cog.bot.db.debit_wallet(uid, guild_id, r.nugget_cost):
        return "funds", f"Need **{fmt_amount(r.nugget_cost)}**."
    for _ in range(r.scrap_cost):
        if not await cog.bot.db.consume_inventory_item(uid, guild_id, SCRAP_ITEM_ID):
            await cog.bot.db.credit_wallet(uid, guild_id, r.nugget_cost)
            return "consume", "Craft failed — refunded."
    for _ in range(r.essence_cost):
        await cog.bot.db.consume_inventory_item(uid, guild_id, "dungeon_essence")
    for _ in range(r.resin_cost):
        await cog.bot.db.consume_inventory_item(uid, guild_id, "harvest_resin")
    for _ in range(r.waste_cost):
        await cog.bot.db.consume_inventory_item(uid, guild_id, "business_waste")
    await cog.bot.db.grant_item(uid, guild_id, r.output_item_id)
    from utils.expansion_events import record_expansion_event

    await record_expansion_event(cog.bot.db, guild_id, uid, "craft_done")
    out = get_item(r.output_item_id)
    return None, f"Crafted **{out.name if out else r.output_item_id}**!"


class RecipeSelect(discord.ui.Select):
    def __init__(self, selected: str | None) -> None:
        options = [
            discord.SelectOption(
                label=r.name,
                value=r.recipe_id,
                description=f"{r.scrap_cost} scrap + {fmt_amount(r.nugget_cost)}"[:100],
                default=(r.recipe_id == selected),
            )
            for r in RECIPES
        ]
        super().__init__(placeholder="Choose a recipe…", options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: AlchemyHubView = self.view  # type: ignore[assignment]
        view.selected_recipe_id = self.values[0]
        await _refresh_hub(interaction, view.cog, view.guild_id, view.user_id, view.selected_recipe_id)


class CraftButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Craft", style=discord.ButtonStyle.success, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: AlchemyHubView = self.view  # type: ignore[assignment]
        recipe_id = view.selected_recipe_id
        if not recipe_id or recipe_id not in RECIPE_MAP:
            await interaction.response.send_message("Pick a recipe from the dropdown first.", ephemeral=True)
            return
        await interaction.response.defer()
        r = RECIPE_MAP[recipe_id]
        err, message = await _craft_recipe(view.cog, view.user_id, view.guild_id, r)
        if err:
            messages = {
                "locked": "Blueprint not unlocked. Check `/codex`.",
            }
            note = messages.get(err, message or "Craft failed.")
            await _refresh_hub(interaction, view.cog, view.guild_id, view.user_id, recipe_id, note=note)
            return
        await _refresh_hub(interaction, view.cog, view.guild_id, view.user_id, recipe_id, note=message)


class RefreshButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Refresh", style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: AlchemyHubView = self.view  # type: ignore[assignment]
        await _refresh_hub(interaction, view.cog, view.guild_id, view.user_id, view.selected_recipe_id)


async def _refresh_hub(
    interaction: discord.Interaction,
    cog: commands.Cog,
    guild_id: int,
    user_id: int,
    selected_recipe_id: str | None,
    *,
    note: str | None = None,
) -> None:
    member = interaction.guild.get_member(user_id) if interaction.guild else None
    display_name = member.display_name if member else str(user_id)
    embed = await build_alchemy_embed(
        cog, guild_id, user_id, display_name, selected=selected_recipe_id,
    )
    if note:
        embed.description = f"{note}\n\n{embed.description}"
    view = AlchemyHubView(cog, guild_id, user_id, selected_recipe_id)
    if interaction.response.is_done():
        await interaction.edit_original_response(embed=embed, view=view)
    else:
        await interaction.response.edit_message(embed=embed, view=view)


class AlchemyHubView(discord.ui.View):
    def __init__(
        self,
        cog: commands.Cog,
        guild_id: int,
        user_id: int,
        selected_recipe_id: str | None = None,
    ) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.selected_recipe_id = selected_recipe_id
        self.add_item(RecipeSelect(selected_recipe_id))
        self.add_item(CraftButton())
        self.add_item(RefreshButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This is not your alchemy panel.", ephemeral=True)
            return False
        return True


async def send_alchemy_hub(cog: commands.Cog, interaction: discord.Interaction) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message(guild_only_message(), ephemeral=True)
        return
    guild_id = interaction.guild_id
    user_id = interaction.user.id
    try:
        embed = await build_alchemy_embed(cog, guild_id, user_id, interaction.user.display_name)
        view = AlchemyHubView(cog, guild_id, user_id)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    except Exception:
        logger.exception("Failed to open alchemy hub for user %s", user_id)
        if interaction.response.is_done():
            await interaction.followup.send(
                "Could not open the alchemy den. Try again in a moment.", ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Could not open the alchemy den. Try again in a moment.", ephemeral=True,
            )
