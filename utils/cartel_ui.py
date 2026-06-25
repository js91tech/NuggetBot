"""Crew cartel drug lab UI."""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

import config
from utils.drugs import DRUGS, drug_by_id
from utils.helpers import fmt_amount

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


async def cartel_grow_count(db, guild_id: int, crew_name: str) -> int:
    cursor = await db.conn.execute(
        "SELECT COUNT(*) AS c FROM crew_cartel_grows WHERE guild_id = ? AND crew_name = ?",
        (guild_id, crew_name),
    )
    row = await cursor.fetchone()
    return int(row["c"]) if row is not None else 0


async def build_cartel_panel(
    cog: commands.Cog, guild_id: int, user_id: int, crew_name: str,
) -> tuple[discord.Embed, CartelView]:
    stash = await cog.bot.db.get_cartel_stash(guild_id, crew_name)
    grow_count = await cartel_grow_count(cog.bot.db, guild_id, crew_name)
    embed = build_cartel_embed(crew_name, stash, grow_count=grow_count)
    view = CartelView(cog, guild_id, user_id, crew_name)
    return embed, view


class CartelView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int, crew_name: str) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.crew_name = crew_name
        options = [
            discord.SelectOption(label=d.name, value=d.drug_id, emoji=d.emoji)
            for d in DRUGS[:25]
        ]
        if options:
            self.add_item(CartelPlantSelect(self, options))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your panel.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="🌾 Harvest cartel", style=discord.ButtonStyle.success, row=1)
    async def harvest_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        harvested = await self.cog.bot.db.harvest_cartel(self.guild_id, self.crew_name)
        embed, view = await build_cartel_panel(
            self.cog, self.guild_id, self.user_id, self.crew_name,
        )
        if not harvested:
            embed.description = "Nothing ready to harvest yet."
            await interaction.response.edit_message(embed=embed, view=view)
            return
        parts = [
            f"{drug_by_id(d).emoji if drug_by_id(d) else '📦'} **{drug_by_id(d).name if drug_by_id(d) else d}** +{q}"
            for d, q in harvested.items()
        ]
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
        embed, view = await build_cartel_panel(
            self._view.cog, self._view.guild_id, self._view.user_id, self._view.crew_name,
        )
        embed.description = (
            f"🌱 Planted **{defn.name if defn else self.values[0]}** "
            f"({fmt_amount(cost)} from treasury)."
        )
        await interaction.response.edit_message(embed=embed, view=view)


async def send_cartel_panel(interaction: discord.Interaction, cog: commands.Cog) -> None:
    user_id = interaction.user.id
    guild_id = interaction.guild_id
    assert guild_id is not None
    crew = await cog.bot.db.get_crew_membership(user_id, guild_id)
    if crew is None:
        await interaction.response.send_message("Join a crew first.", ephemeral=True)
        return
    embed, view = await build_cartel_panel(cog, guild_id, user_id, crew)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
