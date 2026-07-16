"""Interactive district map: deeds, war board, and influence ops."""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import discord

import config
from utils.districts import (
    DISTRICT_MAP,
    deed_claim_cost,
    district_by_id,
    district_image_path,
    effective_district_mult,
    format_influence_race_line,
    relocate_cost,
)
from utils.helpers import fmt_amount, resolve_main_channel
from utils.quests import record_quest_event

if TYPE_CHECKING:
    from discord.ext import commands


def _format_eta(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _clip_field(value: str, limit: int = 1024) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


async def build_district_payload(
    cog: commands.Cog,
    guild: discord.Guild,
    user_id: int,
) -> tuple[discord.Embed, list[discord.File]]:
    embed = await build_district_embed(cog, guild, user_id)
    files: list[discord.File] = []
    row = await cog.bot.db.get_business(user_id, guild.id)
    current = str(row["district_id"]) if row is not None and row["district_id"] else None
    image_path = district_image_path(current)
    if image_path is not None:
        files.append(discord.File(str(image_path), filename="district.png"))
        embed.set_image(url="attachment://district.png")
    return embed, files


async def _district_field_data(
    cog: commands.Cog,
    guild: discord.Guild,
    district_id: str,
) -> tuple[Any, Any, float, list[tuple[str, float]]]:
    """Parallel-friendly fetch for one district's war/influence panel bits."""
    top, control, suppress_until, crew_standings = await asyncio.gather(
        cog.bot.db.list_district_influence(guild.id, district_id, limit=3),
        cog.bot.db.get_district_war_control(guild.id, district_id),
        cog.bot.db.get_district_war_suppress_until(guild.id, district_id),
        cog.bot.db.list_district_crew_influence(guild.id, district_id, limit=3),
    )
    return top, control, suppress_until, crew_standings


async def build_district_embed(
    cog: commands.Cog,
    guild: discord.Guild,
    user_id: int,
) -> discord.Embed:
    row, deeds, my_crew = await asyncio.gather(
        cog.bot.db.get_business(user_id, guild.id),
        cog.bot.db.list_district_deeds(guild.id),
        cog.bot.db.get_crew_membership(user_id, guild.id),
    )
    current = str(row["district_id"]) if row is not None and row["district_id"] else None
    now = time.time()

    embed = discord.Embed(
        title="🗺️ Business Districts",
        description=(
            "Relocate for a placement bonus. **One player owns each district deed** "
            "(full bonus + **20% rent** from tenants). Fight for **crew war control** "
            "with influence: Contest, Undermine, Fortify, or (deed owners) Suppress.\n"
            f"Buyout burn is **{int(config.DISTRICT_BUYOUT_INFLUENCE_DISCOUNT * 100)}%** "
            f"cheaper at **{int(config.DISTRICT_BUYOUT_INFLUENCE_DISCOUNT_THRESHOLD)}+** "
            "influence in that district."
        ),
        color=discord.Color.teal(),
    )

    district_defs = list(DISTRICT_MAP.values())
    snapshots = await asyncio.gather(
        *[_district_field_data(cog, guild, defn.district_id) for defn in district_defs]
    )

    for defn, (top, control, suppress_until, crew_standings) in zip(
        district_defs, snapshots, strict=True,
    ):
        top_lines = []
        for entity_type, entity_id, influence in top:
            if entity_type == "user":
                member = guild.get_member(int(entity_id)) if entity_id.isdigit() else None
                name = member.display_name if member else f"User {entity_id}"
            else:
                name = str(entity_id)
            top_lines.append(f"{name} ({int(influence)})")
        here = "  ← **you are here**" if current == defn.district_id else ""
        influence_text = ", ".join(top_lines) if top_lines else "_no influence yet_"

        owner_id = deeds.get(defn.district_id)
        if owner_id is None:
            owner_text = f"_unowned_ — claim **{fmt_amount(deed_claim_cost(defn.district_id))}**"
        else:
            member = guild.get_member(owner_id)
            owner_name = member.display_name if member else f"User {owner_id}"
            yours = " **(you)**" if owner_id == user_id else ""
            owner_text = f"**{owner_name}**{yours}"
            if owner_id != user_id:
                # Avoid heavy buyout preview on panel open (was causing interaction timeouts).
                owner_text += " · hostile buyout available"

        your_crew_score = 0.0
        if my_crew:
            for crew_name, score in crew_standings:
                if crew_name.lower() == my_crew.lower():
                    your_crew_score = score
                    break
        leader_name = crew_standings[0][0] if crew_standings else "—"
        leader_score = crew_standings[0][1] if crew_standings else 0.0
        race_line = format_influence_race_line(your_crew_score, leader_score, leader_name)

        if suppress_until > now:
            war_line = f"🛑 War bonus **suppressed** ({_format_eta(suppress_until - now)} left)"
        elif control is not None:
            war_line = (
                f"⚔️ Controlled by **{control['crew_name']}** "
                f"(+{int(config.DISTRICT_WAR_CONTROL_BONUS * 100)}% · "
                f"{_format_eta(float(control['bonus_ends_at']) - now)} left)"
            )
        else:
            war_line = "⚔️ No active war control"

        is_owner = owner_id == user_id if owner_id is not None else True
        your_mult = effective_district_mult(
            defn.district_id,
            is_owner=is_owner if owner_id is not None else True,
        )
        tenant_note = ""
        if owner_id is not None and owner_id != user_id:
            tenant_note = (
                f" · tenant mult x**{your_mult:.2f}** "
                f"({int(config.DISTRICT_TENANT_RENT_RATE * 100)}% rent)"
            )
        embed.add_field(
            name=f"{defn.emoji} {defn.name}{here}",
            value=_clip_field(
                f"{defn.label} · owner mult x**{defn.income_mult:.2f}**{tenant_note}\n"
                f"Deed: {owner_text}\n"
                f"{war_line}\n"
                f"Crew race: {race_line}\n"
                f"Top influence: {influence_text}"
            ),
            inline=False,
        )
    if row is not None:
        your_inf = (
            await cog.bot.db.get_user_district_influence(user_id, guild.id, current)
            if current
            else 0.0
        )
        loc = district_by_id(current)
        loc_label = f"{loc.emoji} {loc.name}" if loc else "_unassigned (no bonus)_"
        embed.set_footer(
            text=(
                f"Your business: {loc_label} · your influence here {int(your_inf)} · "
                f"Relocate fee {fmt_amount(relocate_cost(int(row['tier'])))}"
            ),
        )
    else:
        embed.set_footer(text="Create a business with /business create to relocate it.")
    return embed


async def _announce_district_event(
    cog: commands.Cog,
    guild: discord.Guild | None,
    content: str,
) -> None:
    if guild is None:
        return
    channel = await resolve_main_channel(guild, cog.bot.db)
    if channel is None:
        return
    try:
        await channel.send(
            content=content,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception:
        return


async def _ensure_deferred(interaction: discord.Interaction) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer()


async def _refresh_map(
    interaction: discord.Interaction,
    cog: commands.Cog,
    user_id: int,
    *,
    description: str | None = None,
) -> None:
    await _ensure_deferred(interaction)
    guild = interaction.guild
    if guild is None:
        await interaction.followup.send("Guild only.", ephemeral=True)
        return
    view = DistrictMapView(cog, guild.id, user_id)
    embed, files = await build_district_payload(cog, guild, user_id)
    if description:
        embed.description = description
    await interaction.edit_original_response(embed=embed, view=view, attachments=files)


class RelocateSelect(discord.ui.Select):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        options = [
            discord.SelectOption(
                label=defn.name,
                value=defn.district_id,
                description=f"{defn.label} (income x{defn.income_mult:.2f})"[:100],
                emoji=defn.emoji,
            )
            for defn in DISTRICT_MAP.values()
        ]
        super().__init__(
            placeholder="Relocate business to…",
            options=options,
            row=0,
        )
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await _ensure_deferred(interaction)
        district_id = self.values[0]
        cost, err = await self.cog.bot.db.relocate_business(
            self.user_id, self.guild_id, district_id,
        )
        defn = district_by_id(district_id)
        messages = {
            "no_business": "You don't own a business. Use **/business create**.",
            "invalid_district": "Unknown district.",
            "already_here": f"Your business is already in {defn.name if defn else 'that district'}.",
            "cooldown": "You relocated recently — try again later.",
            "insufficient_funds": f"You need **{fmt_amount(cost)}** to relocate.",
        }
        if err:
            await interaction.followup.send(messages.get(err, err), ephemeral=True)
            return
        await record_quest_event(
            self.cog.bot.db, self.guild_id, self.user_id, "business_relocate",
        )
        defn = district_by_id(district_id)
        await _refresh_map(
            interaction,
            self.cog,
            self.user_id,
            description=(
                f"✅ Relocated to **{defn.emoji} {defn.name}** for **{fmt_amount(cost)}** "
                f"({defn.label})."
            ),
        )


class ClaimDeedSelect(discord.ui.Select):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        options = [
            discord.SelectOption(
                label=f"Claim {defn.name}",
                value=defn.district_id,
                description=f"Cost {fmt_amount(deed_claim_cost(defn.district_id))}"[:100],
                emoji=defn.emoji,
            )
            for defn in DISTRICT_MAP.values()
        ]
        super().__init__(
            placeholder="Claim unowned district deed…",
            options=options,
            row=1,
        )
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await _ensure_deferred(interaction)
        district_id = self.values[0]
        cost, err = await self.cog.bot.db.claim_district_deed(
            self.user_id, self.guild_id, district_id,
        )
        defn = district_by_id(district_id)
        messages = {
            "no_business": "You don't own a business. Use **/business create**.",
            "invalid_district": "Unknown district.",
            "already_owned": "That district already has a deed owner — use Buyout.",
            "insufficient_funds": f"You need **{fmt_amount(cost)}** to claim this deed.",
        }
        if err:
            await interaction.followup.send(messages.get(err, err), ephemeral=True)
            return
        await _refresh_map(
            interaction,
            self.cog,
            self.user_id,
            description=(
                f"📜 Claimed the **{defn.emoji} {defn.name}** deed for "
                f"**{fmt_amount(cost)}**. You collect **20% rent** from tenants."
            ),
        )


class BuyoutDeedSelect(discord.ui.Select):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        options = [
            discord.SelectOption(
                label=f"Buyout {defn.name}",
                value=defn.district_id,
                description="Hostile deed buyout (influence can cut burn)"[:100],
                emoji=defn.emoji,
            )
            for defn in DISTRICT_MAP.values()
        ]
        super().__init__(
            placeholder="Hostile buyout district deed…",
            options=options,
            row=2,
        )
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await _ensure_deferred(interaction)
        district_id = self.values[0]
        paid, received, burned, err = await self.cog.bot.db.buyout_district_deed(
            self.user_id, self.guild_id, district_id,
        )
        defn = district_by_id(district_id)
        messages = {
            "no_business": "You don't own a business. Use **/business create**.",
            "invalid_district": "Unknown district.",
            "unowned": "That district is unowned — use Claim instead.",
            "already_owner": "You already own this deed.",
            "insufficient_funds": f"You need **{fmt_amount(paid)}** for this buyout.",
        }
        if err:
            await interaction.followup.send(messages.get(err, err), ephemeral=True)
            return
        await _refresh_map(
            interaction,
            self.cog,
            self.user_id,
            description=(
                f"💥 Bought out **{defn.emoji} {defn.name}** for **{fmt_amount(paid)}** "
                f"(previous owner received **{fmt_amount(received)}**, "
                f"**{fmt_amount(burned)}** burned)."
            ),
        )


class InfluenceModal(discord.ui.Modal, title="Expand influence"):
    points = discord.ui.TextInput(
        label="Influence points to buy",
        placeholder="e.g. 10",
        required=True,
        max_length=5,
    )

    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int, district_id: str) -> None:
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.district_id = district_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _ensure_deferred(interaction)
        try:
            amount = int(str(self.points.value).strip())
        except ValueError:
            await interaction.followup.send("Enter a whole number.", ephemeral=True)
            return
        if amount <= 0:
            await interaction.followup.send("Enter a positive amount.", ephemeral=True)
            return
        cost, new_inf, err = await self.cog.bot.db.expand_district_influence(
            self.user_id, self.guild_id, self.district_id, amount,
        )
        if err == "insufficient_funds":
            await interaction.followup.send(
                f"You need **{fmt_amount(cost)}** for {amount} influence.", ephemeral=True,
            )
            return
        if err:
            await interaction.followup.send("Could not expand influence.", ephemeral=True)
            return
        await record_quest_event(
            self.cog.bot.db, self.guild_id, self.user_id, "business_influence",
        )
        defn = district_by_id(self.district_id)
        territory_mult = await self.cog.bot.db.get_corporate_territory_mult(
            self.user_id, self.guild_id,
        )
        gained = amount * territory_mult
        gain_text = (
            f"📈 +{gained:.1f} influence"
            if territory_mult > 1.001
            else f"📈 +{int(gained)} influence"
        )
        await _refresh_map(
            interaction,
            self.cog,
            self.user_id,
            description=(
                f"{gain_text} in **{defn.name if defn else self.district_id}** "
                f"for **{fmt_amount(cost)}** — now **{int(new_inf)}**."
            ),
        )


class InfluenceOpsSelect(discord.ui.Select):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        options = [
            discord.SelectOption(
                label=defn.name,
                value=defn.district_id,
                description="Invest · Contest · Undermine · Fortify"[:100],
                emoji=defn.emoji,
            )
            for defn in DISTRICT_MAP.values()
        ]
        super().__init__(
            placeholder="Open influence ops for…",
            options=options,
            row=3,
        )
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        district_id = self.values[0]
        defn = district_by_id(district_id)
        view = DistrictInfluenceOpsView(
            self.cog, self.guild_id, self.user_id, district_id,
        )
        embed = discord.Embed(
            title=f"🗺️ Influence Ops — {defn.emoji} {defn.name}" if defn else "Influence Ops",
            description=(
                f"**Invest** — buy influence ({config.BUSINESS_DISTRICT_INFLUENCE_COST_PER_POINT:.0f}/pt)\n"
                f"**Contest** — spend **{int(config.DISTRICT_WAR_CONTEST_COST)}** influence to seize "
                f"war control for **{_format_eta(config.DISTRICT_WAR_CONTEST_DURATION_SECONDS)}**\n"
                f"**Undermine** — strip **{config.DISTRICT_UNDERMINE_DEFAULT_POINTS}** pts from the "
                f"local leader ({fmt_amount(config.DISTRICT_UNDERMINE_COST_PER_POINT)}/pt)\n"
                f"**Fortify** — temporary bonus influence "
                f"({fmt_amount(config.DISTRICT_FORTIFY_COST_PER_POINT)}/pt, "
                f"{_format_eta(config.DISTRICT_FORTIFY_DURATION_SECONDS)})\n"
                f"**Suppress** — deed owners spend **{int(config.DISTRICT_OWNER_SUPPRESS_COST)}** "
                f"influence to kill the war bonus for "
                f"**{_format_eta(config.DISTRICT_OWNER_SUPPRESS_DURATION_SECONDS)}**"
            ),
            color=discord.Color.dark_teal(),
        )
        await interaction.response.edit_message(embed=embed, view=view, attachments=[])


class DistrictInfluenceOpsView(discord.ui.View):
    def __init__(
        self,
        cog: commands.Cog,
        guild_id: int,
        user_id: int,
        district_id: str,
    ) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.district_id = district_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your district panel.", ephemeral=True,
            )
            return False
        return True

    def _ops_error(self, result: dict[str, object]) -> str:
        err = str(result.get("error") or "failed")
        if err == "cooldown":
            retry = float(result.get("retry_after") or 0)
            return f"On cooldown — try again in **{_format_eta(retry)}**."
        if err == "insufficient_funds":
            cost = float(result.get("cost") or 0)
            return f"You need **{fmt_amount(cost)}**."
        if err == "insufficient_influence":
            have = float(result.get("have") or 0)
            need = float(result.get("need") or 0)
            return f"Need **{int(need)}** influence (you have **{int(have)}**)."
        messages = {
            "no_crew": "Join a crew first to contest war control.",
            "no_target": "No rival influence to undermine here.",
            "not_deed_owner": "Only the deed owner can suppress the war bonus.",
            "invalid_district": "Unknown district.",
        }
        return messages.get(err, err)

    @discord.ui.button(label="Invest", style=discord.ButtonStyle.primary, row=0)
    async def invest_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await interaction.response.send_modal(
            InfluenceModal(self.cog, self.guild_id, self.user_id, self.district_id),
        )

    @discord.ui.button(label="Contest", style=discord.ButtonStyle.danger, row=0)
    async def contest_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await _ensure_deferred(interaction)
        result = await self.cog.bot.db.contest_district_war(
            self.user_id, self.guild_id, self.district_id,
        )
        if result.get("error"):
            await interaction.followup.send(self._ops_error(result), ephemeral=True)
            return
        defn = district_by_id(self.district_id)
        crew = str(result.get("crew_name"))
        ends_at = float(result.get("ends_at") or 0)
        spent = float(result.get("influence_spent") or 0)
        await record_quest_event(
            self.cog.bot.db, self.guild_id, self.user_id, "business_influence",
        )
        await _announce_district_event(
            self.cog,
            interaction.guild,
            (
                f"⚔️ **{crew}** contested **{defn.emoji if defn else ''} "
                f"{defn.name if defn else self.district_id}** and seized war control "
                f"for {_format_eta(ends_at - time.time())}!"
            ),
        )
        await _refresh_map(
            interaction,
            self.cog,
            self.user_id,
            description=(
                f"⚔️ Contested **{defn.name if defn else self.district_id}** for "
                f"**{int(spent)}** influence — **{crew}** holds control "
                f"({_format_eta(ends_at - time.time())})."
            ),
        )

    @discord.ui.button(label="Undermine", style=discord.ButtonStyle.secondary, row=0)
    async def undermine_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        await _ensure_deferred(interaction)
        result = await self.cog.bot.db.undermine_district_influence(
            self.user_id, self.guild_id, self.district_id,
        )
        if result.get("error"):
            await interaction.followup.send(self._ops_error(result), ephemeral=True)
            return
        defn = district_by_id(self.district_id)
        target_id = int(result["target_id"])
        member = interaction.guild.get_member(target_id) if interaction.guild else None
        target_name = member.display_name if member else f"User {target_id}"
        removed = float(result.get("removed") or 0)
        cost = float(result.get("cost") or 0)
        await record_quest_event(
            self.cog.bot.db, self.guild_id, self.user_id, "business_influence",
        )
        await _announce_district_event(
            self.cog,
            interaction.guild,
            (
                f"🗡️ Influence undermined in **{defn.emoji if defn else ''} "
                f"{defn.name if defn else self.district_id}** — **{target_name}** lost "
                f"**{int(removed)}** pts."
            ),
        )
        await _refresh_map(
            interaction,
            self.cog,
            self.user_id,
            description=(
                f"🗡️ Undermined **{target_name}** for **{fmt_amount(cost)}** "
                f"(−**{int(removed)}** influence)."
            ),
        )

    @discord.ui.button(label="Fortify", style=discord.ButtonStyle.success, row=1)
    async def fortify_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await _ensure_deferred(interaction)
        result = await self.cog.bot.db.fortify_district_influence(
            self.user_id, self.guild_id, self.district_id,
        )
        if result.get("error"):
            await interaction.followup.send(self._ops_error(result), ephemeral=True)
            return
        defn = district_by_id(self.district_id)
        points = float(result.get("points") or 0)
        cost = float(result.get("cost") or 0)
        ends = float(result.get("expires_at") or 0)
        await record_quest_event(
            self.cog.bot.db, self.guild_id, self.user_id, "business_influence",
        )
        await _refresh_map(
            interaction,
            self.cog,
            self.user_id,
            description=(
                f"🛡️ Fortified **{defn.name if defn else self.district_id}** to "
                f"**{int(points)}** temp influence for **{fmt_amount(cost)}** "
                f"({_format_eta(ends - time.time())})."
            ),
        )

    @discord.ui.button(label="Suppress", style=discord.ButtonStyle.secondary, row=1)
    async def suppress_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        await _ensure_deferred(interaction)
        result = await self.cog.bot.db.suppress_district_war(
            self.user_id, self.guild_id, self.district_id,
        )
        if result.get("error"):
            await interaction.followup.send(self._ops_error(result), ephemeral=True)
            return
        defn = district_by_id(self.district_id)
        until = float(result.get("suppressed_until") or 0)
        spent = float(result.get("influence_spent") or 0)
        await _announce_district_event(
            self.cog,
            interaction.guild,
            (
                f"🛑 Deed owner suppressed war bonus in **{defn.emoji if defn else ''} "
                f"{defn.name if defn else self.district_id}** "
                f"for {_format_eta(until - time.time())}."
            ),
        )
        await _refresh_map(
            interaction,
            self.cog,
            self.user_id,
            description=(
                f"🛑 Suppressed war bonus in **{defn.name if defn else self.district_id}** "
                f"for **{int(spent)}** influence ({_format_eta(until - time.time())})."
            ),
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await _refresh_map(interaction, self.cog, self.user_id)


class DistrictMapView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.add_item(RelocateSelect(cog, guild_id, user_id))
        self.add_item(ClaimDeedSelect(cog, guild_id, user_id))
        self.add_item(BuyoutDeedSelect(cog, guild_id, user_id))
        self.add_item(InfluenceOpsSelect(cog, guild_id, user_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your district panel.", ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=4)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await _refresh_map(interaction, self.cog, self.user_id)


async def send_district_panel(interaction: discord.Interaction, cog: commands.Cog) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return
    await interaction.response.defer()
    embed, files = await build_district_payload(cog, guild, interaction.user.id)
    view = DistrictMapView(cog, guild.id, interaction.user.id)
    await interaction.followup.send(embed=embed, view=view, files=files)
