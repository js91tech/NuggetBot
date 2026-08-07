from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import discord

import config
from utils.achievements import evaluate_unlocks, format_unlock_message
from utils.helpers import fmt_amount, send_error
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

logger = logging.getLogger(__name__)


def zone_select_options_from_rows(rows: list[Any]) -> list[discord.SelectOption]:
    options: list[discord.SelectOption] = []
    for row in rows:
        tid = str(row["territory_id"])
        defn = territory_by_id(tid)
        if defn is None:
            continue
        owner = row["owner_crew_name"]
        owner_text = str(owner) if owner else "Neutral"
        guards = int(row["guards"])
        options.append(
            discord.SelectOption(
                label=defn.name,
                value=tid,
                description=(
                    f"{owner_text} · {fmt_amount(defn.income_per_hour)}/hr · "
                    f"{guards}/{defn.max_guards} guards"
                )[:100],
            ),
        )
    return options[:25]


async def build_territory_map_embed(
    cog: Territories,
    guild: discord.Guild,
    uid: int,
    *,
    selected_zone: str | None = None,
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
        prefix = "👉 " if selected_zone == tid else ""
        lines.append(
            f"{prefix}**{defn.name}** — {owner_text} · "
            f"{fmt_amount(defn.income_per_hour)}/hr · "
            f"Guards {guards}/{defn.max_guards}{extra}{perk}",
        )

    embed = discord.Embed(
        title="Territory map",
        description="\n".join(lines) if lines else "_No zones configured_",
        color=discord.Color.dark_green(),
    )
    footer_bits: list[str] = []
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
        footer_bits.append(
            f"Holds {len(held)}/{config.TERRITORY_MAX_HELD_PER_CREW} · "
            f"≈{fmt_amount(income_total)}/hr → treasury",
        )
    else:
        embed.set_footer(text="Join a crew to attack, claim, or hire guards.")
        return embed

    if selected_zone:
        defn = territory_by_id(selected_zone)
        if defn is not None:
            footer_bits.append(f"Selected: **{defn.name}**")
    footer_bits.append("Pick a zone below, then attack or hire guards")
    embed.set_footer(text=" · ".join(footer_bits))
    return embed


class TerritoryZoneSelect(discord.ui.Select):
    def __init__(
        self,
        cog: Territories,
        guild_id: int,
        user_id: int,
        options: list[discord.SelectOption],
    ) -> None:
        super().__init__(
            placeholder="Pick a zone…",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, TerritoryMapView):
            return
        view.selected_zone = self.values[0]
        if interaction.guild is None:
            await interaction.response.send_message("Guild only.", ephemeral=True)
            return
        await interaction.response.defer()
        embed = await build_territory_map_embed(
            self.cog,
            interaction.guild,
            self.user_id,
            selected_zone=view.selected_zone,
        )
        await interaction.edit_original_response(embed=embed, view=view)


class TerritoryMapView(discord.ui.View):
    def __init__(
        self,
        cog: Territories,
        guild_id: int,
        user_id: int,
        *,
        territory_rows: list[Any],
        in_crew: bool,
    ) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.in_crew = in_crew
        self.selected_zone: str | None = None
        options = zone_select_options_from_rows(territory_rows)
        if options:
            self.add_item(TerritoryZoneSelect(cog, guild_id, user_id, options))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Open your own map with `/territory`.", ephemeral=True,
            )
            return False
        return True

    def _zone(self) -> str | None:
        return self.selected_zone

    async def _require_zone(self, interaction: discord.Interaction) -> str | None:
        zone = self._zone()
        if zone is None:
            await interaction.response.send_message(
                "Pick a zone from the dropdown first.", ephemeral=True,
            )
            return None
        if territory_by_id(zone) is None:
            await interaction.response.send_message("Unknown zone.", ephemeral=True)
            return None
        return zone

    async def _refresh_panel(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        crew = await self.cog.bot.db.get_crew_membership(self.user_id, self.guild_id)
        rows = await self.cog.bot.db.list_territory_rows(self.guild_id)
        new_view = TerritoryMapView(
            self.cog,
            self.guild_id,
            self.user_id,
            territory_rows=rows,
            in_crew=bool(crew),
        )
        new_view.selected_zone = self.selected_zone
        embed = await build_territory_map_embed(
            self.cog,
            interaction.guild,
            self.user_id,
            selected_zone=self.selected_zone,
        )
        await interaction.edit_original_response(embed=embed, view=new_view)

    async def _buy(
        self,
        interaction: discord.Interaction,
        count: int,
        pay_from: str,
    ) -> None:
        if not self.in_crew:
            await interaction.response.send_message(
                "Join a crew first (`/crew panel`).", ephemeral=True,
            )
            return
        zone = await self._require_zone(interaction)
        if zone is None:
            return
        defn = territory_by_id(zone)
        assert defn is not None
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
        await self._refresh_panel(interaction)
        src = "crew treasury" if pay_from == "treasury" else "your wallet"
        row = await self.cog.bot.db.get_territory_row(self.guild_id, zone)
        guards = int(row["guards"]) if row is not None else count
        await interaction.followup.send(
            f"Hired **{count}** guard(s) at **{defn.name}** ({guards}/{defn.max_guards}) from {src}.",
            ephemeral=True,
        )

    @discord.ui.button(label="⚔️ Attack / claim", style=discord.ButtonStyle.danger, row=1)
    async def attack_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        if not self.in_crew:
            await interaction.response.send_message(
                "Join a crew first (`/crew panel`).", ephemeral=True,
            )
            return
        zone = await self._require_zone(interaction)
        if zone is None:
            return
        defn = territory_by_id(zone)
        assert defn is not None
        await interaction.response.defer(ephemeral=True)
        try:
            err = await self.cog.bot.db.start_territory_siege(
                self.user_id, self.guild_id, defn.territory_id,
            )
            msgs = {
                "not_in_crew": "Join a crew first (`/crew panel` → Join crew).",
                "crew_too_small": (
                    f"Need at least {config.TERRITORY_MIN_CREW_MEMBERS_TO_ATTACK} "
                    "crew members to attack."
                ),
                "own_territory": "Your crew already holds this zone.",
                "already_under_siege": "This zone is already under siege.",
                "siege_cooldown": "This zone was attacked recently. Try again later.",
                "max_territories": (
                    f"Your crew already holds {config.TERRITORY_MAX_HELD_PER_CREW} zones."
                ),
                "invalid_territory": "Unknown territory.",
            }
            if err == "claimed_neutral":
                crew = await self.cog.bot.db.get_crew_membership(self.user_id, self.guild_id)
                await record_quest_event(
                    self.cog.bot.db, self.guild_id, self.user_id, "territory_claim",
                )
                unlocked = await evaluate_unlocks(self.cog.bot.db, self.guild_id, self.user_id)
                extra = format_unlock_message(unlocked)
                await self._refresh_panel(interaction)
                await interaction.followup.send(
                    f"**{defn.name}** is unclaimed — crew **{crew}** now holds it!"
                    + (f"\n{extra}" if extra else ""),
                    ephemeral=True,
                )
                return
            if err:
                await interaction.followup.send(msgs.get(err, err), ephemeral=True)
                return
            row = await self.cog.bot.db.get_territory_row(self.guild_id, defn.territory_id)
            if row and interaction.guild is not None:
                await self.cog._announce_siege_start(
                    interaction.guild, self.guild_id, row, defn,
                )
            mins = int(config.TERRITORY_SIEGE_DURATION_SECONDS // 60)
            await self._refresh_panel(interaction)
            await interaction.followup.send(
                f"Siege started on **{defn.name}**! Resolves in **{mins}** minutes.",
                ephemeral=True,
            )
        except Exception:
            logger.exception("territory panel attack failed")
            await send_error(interaction, "Something went wrong starting the siege.")

    @discord.ui.button(label="🚪 Abandon", style=discord.ButtonStyle.secondary, row=1)
    async def abandon_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        if not self.in_crew:
            await interaction.response.send_message(
                "Join a crew first (`/crew panel`).", ephemeral=True,
            )
            return
        zone = await self._require_zone(interaction)
        if zone is None:
            return
        defn = territory_by_id(zone)
        assert defn is not None
        await interaction.response.defer(ephemeral=True)
        try:
            err = await self.cog.bot.db.abandon_territory(
                self.user_id, self.guild_id, defn.territory_id,
            )
            msgs = {
                "not_in_crew": "Join a crew first.",
                "not_owner": "Your crew does not hold this zone.",
                "invalid_territory": "Unknown territory.",
            }
            if err:
                await interaction.followup.send(msgs.get(err, err), ephemeral=True)
                return
            await self._refresh_panel(interaction)
            await interaction.followup.send(
                f"Your crew abandoned **{defn.name}**. It is now neutral.",
                ephemeral=True,
            )
        except Exception:
            logger.exception("territory panel abandon failed")
            await send_error(interaction, "Something went wrong.")

    @discord.ui.button(label="+1 Wallet", style=discord.ButtonStyle.primary, row=2)
    async def one_wallet(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._buy(interaction, 1, "wallet")

    @discord.ui.button(label="+1 Treasury", style=discord.ButtonStyle.success, row=2)
    async def one_treasury(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._buy(interaction, 1, "treasury")

    @discord.ui.button(label="+5 Wallet", style=discord.ButtonStyle.secondary, row=2)
    async def five_wallet(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._buy(interaction, 5, "wallet")

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=3)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        if interaction.guild is None:
            await interaction.response.send_message("Guild only.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._refresh_panel(interaction)


async def send_territory_map_panel(
    interaction: discord.Interaction,
    cog: Territories,
) -> None:
    if interaction.guild_id is None or interaction.guild is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    uid = interaction.user.id
    guild_id = interaction.guild_id
    crew = await cog.bot.db.get_crew_membership(uid, guild_id)
    rows = await cog.bot.db.list_territory_rows(guild_id)
    embed = await build_territory_map_embed(cog, interaction.guild, uid)
    view = TerritoryMapView(
        cog,
        guild_id,
        uid,
        territory_rows=rows,
        in_crew=bool(crew),
    )
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
