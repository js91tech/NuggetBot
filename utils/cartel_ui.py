"""Crew cartel drug lab UI."""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

import config
from utils.drugs import DRUGS, drug_by_id
from utils.helpers import fmt_amount
from utils.quests import record_quest_event

if TYPE_CHECKING:
    from discord.ext import commands


def build_cartel_embed(
    crew_name: str,
    stash: dict[str, int],
    *,
    grow_count: int,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"🕶️ {crew_name} Cartel Lab",
        description=(
            f"Shared crew lab with **{config.CARTEL_LAB_SLOTS}** slots. "
            f"Treasury-funded grows · street sells split "
            f"{int(config.CARTEL_STREET_SELL_CREW_SHARE * 100)}% crew / "
            f"{int(config.CARTEL_STREET_SELL_PLAYER_SHARE * 100)}% you."
        ),
        color=discord.Color.dark_purple(),
    )
    embed.add_field(name="Lab slots", value=f"{grow_count}/{config.CARTEL_LAB_SLOTS} in use", inline=True)
    if stash:
        lines = []
        for drug_id, qty in stash.items():
            defn = drug_by_id(drug_id)
            name = defn.name if defn else drug_id
            lines.append(f"{defn.emoji if defn else '📦'} **{name}** ×{qty}")
        embed.add_field(name="Cartel stash", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Cartel stash", value="_Empty_", inline=False)
    return embed


class CartelSellModal(discord.ui.Modal, title="Cartel street sell"):
    quantity = discord.ui.TextInput(label="Quantity to sell", placeholder="e.g. 10", required=True, max_length=8)

    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int, crew_name: str, drug_id: str) -> None:
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.crew_name = crew_name
        self.drug_id = drug_id
        defn = drug_by_id(drug_id)
        if defn is not None:
            self.title = f"Sell {defn.name}"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            qty = int(str(self.quantity.value).replace(",", "").strip())
        except ValueError:
            await interaction.response.send_message("Enter a whole number.", ephemeral=True)
            return
        if qty <= 0:
            await interaction.response.send_message("Enter a positive amount.", ephemeral=True)
            return
        result = await self.cog.bot.db.cartel_street_sell(
            self.user_id, self.guild_id, self.crew_name, self.drug_id, qty,
        )
        if result.get("error"):
            msgs = {
                "not_in_crew": "You are not in this crew.",
                "insufficient_product": "Not enough in the cartel stash.",
                "invalid_amount": "Invalid quantity.",
            }
            await interaction.response.send_message(
                msgs.get(str(result["error"]), "Could not sell."), ephemeral=True,
            )
            return
        await record_quest_event(self.cog.bot.db, self.guild_id, self.user_id, "drug_sell")
        stash = await self.cog.bot.db.get_cartel_stash(self.guild_id, self.crew_name)
        cursor = await self.cog.bot.db.conn.execute(
            "SELECT COUNT(*) AS c FROM crew_cartel_grows WHERE guild_id = ? AND crew_name = ?",
            (self.guild_id, self.crew_name),
        )
        grow_count = int((await cursor.fetchone())["c"])
        embed = build_cartel_embed(self.crew_name, stash, grow_count=grow_count)
        view = CartelView(self.cog, self.guild_id, self.user_id, self.crew_name, stash)
        embed.description = (
            f"💵 Sold **{qty}** units — you got **{fmt_amount(float(result['player_share']))}**, "
            f"crew treasury +**{fmt_amount(float(result['crew_share']))}**."
        )
        await interaction.response.edit_message(embed=embed, view=view)


class CartelSellSelect(discord.ui.Select):
    def __init__(self, view: "CartelView", stash: dict[str, int]) -> None:
        options = [
            discord.SelectOption(
                label=f"{drug_by_id(d).name if drug_by_id(d) else d} (×{q})",
                value=d,
                emoji=drug_by_id(d).emoji if drug_by_id(d) else None,
            )
            for d, q in stash.items()
            if drug_by_id(d) is not None or d
        ][:25]
        super().__init__(
            placeholder="Sell from cartel stash…",
            options=options or [discord.SelectOption(label="Empty stash", value="_none")],
            disabled=not options,
            row=1,
        )
        self._view = view

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "_none":
            await interaction.response.send_message("Cartel stash is empty.", ephemeral=True)
            return
        await interaction.response.send_modal(
            CartelSellModal(
                self._view.cog, self._view.guild_id, self._view.user_id,
                self._view.crew_name, self.values[0],
            ),
        )


class CartelView(discord.ui.View):
    def __init__(
        self,
        cog: commands.Cog,
        guild_id: int,
        user_id: int,
        crew_name: str,
        stash: dict[str, int] | None = None,
    ) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.crew_name = crew_name
        self._stash = stash or {}
        options = [
            discord.SelectOption(label=d.name, value=d.drug_id, emoji=d.emoji)
            for d in DRUGS[:25]
        ]
        if options:
            self.add_item(CartelPlantSelect(self, options))
        self.add_item(CartelSellSelect(self, self._stash))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your panel.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Harvest cartel", style=discord.ButtonStyle.success, row=2)
    async def harvest_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        harvested = await self.cog.bot.db.harvest_cartel(self.guild_id, self.crew_name)
        stash = await self.cog.bot.db.get_cartel_stash(self.guild_id, self.crew_name)
        cursor = await self.cog.bot.db.conn.execute(
            "SELECT COUNT(*) AS c FROM crew_cartel_grows WHERE guild_id = ? AND crew_name = ?",
            (self.guild_id, self.crew_name),
        )
        grow_count = int((await cursor.fetchone())["c"])
        view = CartelView(self.cog, self.guild_id, self.user_id, self.crew_name, stash)
        embed = build_cartel_embed(self.crew_name, stash, grow_count=grow_count)
        if not harvested:
            embed.description = "Nothing ready to harvest yet."
        else:
            parts = [f"**{drug_by_id(d).name if drug_by_id(d) else d}** +{q}" for d, q in harvested.items()]
            embed.description = f"🌾 Harvested: {', '.join(parts)}"
        await interaction.response.edit_message(embed=embed, view=view)


class CartelPlantSelect(discord.ui.Select):
    def __init__(self, view: CartelView, options: list[discord.SelectOption]) -> None:
        super().__init__(placeholder="Plant strain (treasury-funded)", options=options, row=0)
        self._view = view

    async def callback(self, interaction: discord.Interaction) -> None:
        cost, err = await self._view.cog.bot.db.plant_cartel_drug(
            self._view.user_id, self._view.guild_id, self._view.crew_name, self.values[0],
        )
        if err:
            msgs = {
                "insufficient_treasury": f"Crew treasury needs **{fmt_amount(cost)}**.",
                "no_slots": "All cartel lab slots are busy.",
                "not_in_crew": "You are not in this crew.",
            }
            await interaction.response.send_message(msgs.get(err, "Could not plant."), ephemeral=True)
            return
        defn = drug_by_id(self.values[0])
        await interaction.response.send_message(
            f"🌱 Planted **{defn.name if defn else self.values[0]}** ({fmt_amount(cost)} from treasury).",
            ephemeral=True,
        )


async def send_cartel_panel(interaction: discord.Interaction, cog: commands.Cog) -> None:
    user_id = interaction.user.id
    guild_id = interaction.guild_id
    assert guild_id is not None
    crew = await cog.bot.db.get_crew_membership(user_id, guild_id)
    if crew is None:
        await interaction.response.send_message("Join a crew first.", ephemeral=True)
        return
    stash = await cog.bot.db.get_cartel_stash(guild_id, crew)
    cursor = await cog.bot.db.conn.execute(
        "SELECT COUNT(*) AS c FROM crew_cartel_grows WHERE guild_id = ? AND crew_name = ?",
        (guild_id, crew),
    )
    grow_count = int((await cursor.fetchone())["c"])
    embed = build_cartel_embed(crew, stash, grow_count=grow_count)
    view = CartelView(cog, guild_id, user_id, crew, stash)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
