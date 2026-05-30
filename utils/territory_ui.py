from __future__ import annotations

import time
from typing import TYPE_CHECKING

import discord

import config
from utils.helpers import fmt_amount
from utils.quests import record_quest_event
from utils.territories import (
    TERRITORY_MAP,
    guard_cost_per_unit,
    perks_from_held,
    siege_success_chance,
    territory_by_id,
)

if TYPE_CHECKING:
    from cogs.territories import Territories


async def build_territory_map_embed(
    cog: Territories,
    guild: discord.Guild,
    uid: int,
) -> discord.Embed:
    guild_id = guild.id
    crew = await cog.bot.db.get_crew_membership(uid, guild_id)
    rows = await cog.bot.db.list_territory_rows(guild_id)
    now = time.time()
    lines: list[str] = []
    for row in rows:
        tid = str(row["territory_id"])
        defn = TERRITORY_MAP.get(tid)
        if defn is None:
            continue
        owner = row["owner_crew_name"]
        guards = int(row["guards"])
        owner_text = f"**{owner}**" if owner else "_Neutral_"
        siege = row["siege_ends_at"]
        extra = ""
        if siege is not None and float(siege) > now:
            attacker = row["siege_attacker_crew"]
            left = int((float(siege) - now) // 60) + 1
            members = await cog.bot.db.count_crew_members(guild_id, str(attacker))
            chance = siege_success_chance(members, guards, defn)
            extra = f" · ⚔️ **{attacker}** ({left}m, ~{int(chance * 100)}%)"
        perk = f" · _{defn.perk_label}_"
        lines.append(
            f"**{defn.name}** — {owner_text} · "
            f"{fmt_amount(defn.income_per_hour)}/hr · "
            f"Guards {guards}/{defn.max_guards}{extra}{perk}",
        )

    embed = discord.Embed(
        title="Territory map",
        description="\n".join(lines) if lines else "_No zones configured_",
        color=discord.Color.dark_green(),
    )
    if crew:
        held = await cog.bot.db.list_crew_held_territories(guild_id, crew)
        income_total = sum(
            TERRITORY_MAP[t].income_per_hour for t, _ in held if t in TERRITORY_MAP
        )
        perk_lines = perks_from_held({t for t, _ in held}).summary_lines()
        embed.add_field(
            name=f"Crew {crew} zones",
            value="\n".join(perk_lines) if perk_lines else "_None held_",
            inline=False,
        )
        embed.set_footer(
            text=(
                f"Holds {len(held)}/{config.TERRITORY_MAX_HELD_PER_CREW} · "
                f"≈{fmt_amount(income_total)}/hr → treasury · Hire guards below"
            ),
        )
    else:
        embed.set_footer(text="Join a crew to attack, claim, or hire guards.")
    return embed


class HeldZoneSelect(discord.ui.Select):
    def __init__(
        self,
        cog: Territories,
        guild_id: int,
        user_id: int,
        held: list[tuple[str, int]],
    ) -> None:
        options: list[discord.SelectOption] = []
        for tid, guards in held:
            defn = territory_by_id(tid)
            if defn is None:
                continue
            unit = guard_cost_per_unit(defn)
            options.append(
                discord.SelectOption(
                    label=f"{defn.name} ({guards}/{defn.max_guards})"[:100],
                    value=tid,
                    description=f"{fmt_amount(unit)}/guard · {fmt_amount(defn.income_per_hour)}/hr"[:100],
                ),
            )
        super().__init__(
            placeholder="Pick a zone to hire guards…",
            min_values=1,
            max_values=1,
            options=options[:25],
            row=0,
        )
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if isinstance(self.view, TerritoryMapView):
            self.view.selected_zone = self.values[0]
        defn = territory_by_id(self.values[0])
        label = defn.name if defn else self.values[0]
        await interaction.response.send_message(
            f"Selected **{label}** — use the guard buttons below.",
            ephemeral=True,
        )


class TerritoryMapView(discord.ui.View):
    def __init__(self, cog: Territories, guild_id: int, user_id: int, *, held: list[tuple[str, int]]) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.selected_zone: str | None = None
        if held:
            self.add_item(HeldZoneSelect(cog, guild_id, user_id, held))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Open your own map with `/territory`.", ephemeral=True,
            )
            return False
        return True

    def _zone(self) -> str | None:
        return self.selected_zone

    async def _buy(
        self,
        interaction: discord.Interaction,
        count: int,
        pay_from: str,
    ) -> None:
        zone = self._zone()
        if zone is None:
            await interaction.response.send_message(
                "Select a zone from the dropdown first.", ephemeral=True,
            )
            return
        defn = territory_by_id(zone)
        if defn is None:
            await interaction.response.send_message("Unknown zone.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        err = await self.cog.bot.db.buy_territory_guards(
            self.user_id,
            self.guild_id,
            zone,
            count,
            pay_from=pay_from,
        )
        if err:
            unit = guard_cost_per_unit(defn)
            msgs = {
                "not_in_crew": "Join a crew first.",
                "not_owner": "Only the holding crew can buy guards here.",
                "guard_cap": f"Max **{defn.max_guards}** guards at {defn.name}.",
                "insufficient_funds": (
                    f"Need **{fmt_amount(unit * count)}** in your wallet."
                ),
                "insufficient_treasury": (
                    f"Crew treasury needs **{fmt_amount(unit * count)}**."
                ),
            }
            await interaction.followup.send(msgs.get(err, err), ephemeral=True)
            return
        await record_quest_event(
            self.cog.bot.db, self.guild_id, self.user_id, "territory_guards", amount=count,
        )
        if interaction.guild is not None:
            embed = await build_territory_map_embed(self.cog, interaction.guild, self.user_id)
            await interaction.edit_original_response(embed=embed, view=self)
        src = "crew treasury" if pay_from == "treasury" else "your wallet"
        row = await self.cog.bot.db.get_territory_row(self.guild_id, zone)
        guards = int(row["guards"]) if row is not None else count
        await interaction.followup.send(
            f"Hired **{count}** guard(s) at **{defn.name}** ({guards}/{defn.max_guards}) from {src}.",
            ephemeral=True,
        )

    @discord.ui.button(label="+1 Wallet", style=discord.ButtonStyle.primary, row=1)
    async def one_wallet(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._buy(interaction, 1, "wallet")

    @discord.ui.button(label="+1 Treasury", style=discord.ButtonStyle.success, row=1)
    async def one_treasury(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._buy(interaction, 1, "treasury")

    @discord.ui.button(label="+5 Wallet", style=discord.ButtonStyle.secondary, row=1)
    async def five_wallet(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._buy(interaction, 5, "wallet")

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=2)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        if interaction.guild is None:
            await interaction.response.send_message("Guild only.", ephemeral=True)
            return
        embed = await build_territory_map_embed(self.cog, interaction.guild, self.user_id)
        await interaction.response.edit_message(embed=embed, view=self)


async def send_territory_map_panel(
    interaction: discord.Interaction,
    cog: Territories,
) -> None:
    if interaction.guild_id is None or interaction.guild is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return

    uid = interaction.user.id
    crew = await cog.bot.db.get_crew_membership(uid, interaction.guild_id)
    held: list[tuple[str, int]] = []
    if crew:
        held = await cog.bot.db.list_crew_held_territories(interaction.guild_id, crew)

    embed = await build_territory_map_embed(cog, interaction.guild, uid)
    view = TerritoryMapView(cog, interaction.guild_id, uid, held=held)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
