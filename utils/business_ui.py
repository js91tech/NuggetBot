"""Interactive UI for the Business Empire (panel, collect, tier-up, upgrades)."""
from __future__ import annotations

import contextlib
import io
from typing import TYPE_CHECKING

import discord

import config
from utils.business_art import render_business_image
from utils.businesses import (
    capacity_for_level,
    hourly_income,
    next_tier_def,
    security_rating,
    tier_def,
    upgrade_cost,
)
from utils.helpers import fmt_amount
from utils.quests import record_quest_event

if TYPE_CHECKING:
    from discord.ext import commands


def _bar(current: float, total: float, *, length: int = 12) -> str:
    if total <= 0:
        return "░" * length
    filled = int(round((min(current, total) / total) * length))
    filled = max(0, min(length, filled))
    return "█" * filled + "░" * (length - filled)


def _hourly_from_row(row: object) -> float:
    from utils.districts import district_income_mult

    return hourly_income(
        tier=int(row["tier"]),
        efficiency_level=int(row["efficiency"]),
        reputation_level=int(row["reputation"]),
        production_branch_level=int(row["branch_production"]),
        growth_branch_level=int(row["branch_growth"]),
        satisfaction=int(row["employee_satisfaction"]),
        business_prestige=int(row["business_prestige"]),
        district_mult=district_income_mult(row["district_id"]),
    )


def build_business_embed(member: discord.Member, row: object) -> tuple[discord.Embed, discord.File]:
    tier = int(row["tier"])
    defn = tier_def(tier)
    name = defn.name if defn else "Business"
    emoji = defn.emoji if defn else "🏪"
    hourly = _hourly_from_row(row)
    capacity = capacity_for_level(tier, int(row["capacity"]))
    stored = float(row["stored_income"])

    embed = discord.Embed(
        title=f"{emoji} {member.display_name}'s {name}",
        color=discord.Color.green(),
    )
    if defn is not None:
        embed.description = f"_{defn.blurb}_"

    embed.add_field(name="Tier", value=f"**{tier}** / 7", inline=True)
    embed.add_field(name="Income", value=f"{fmt_amount(hourly)}/hr", inline=True)
    prestige = int(row["business_prestige"])
    if prestige > 0:
        embed.add_field(name="Prestige", value=f"⭐ {prestige}", inline=True)

    from utils.districts import district_by_id

    district = district_by_id(row["district_id"])
    if district is not None:
        embed.add_field(
            name="District",
            value=f"{district.emoji} {district.name} · {district.label}",
            inline=False,
        )

    fill_pct = int((min(stored, capacity) / capacity) * 100) if capacity > 0 else 0
    embed.add_field(
        name="Stored revenue",
        value=(
            f"`{_bar(stored, capacity)}` {fill_pct}%\n"
            f"**{fmt_amount(stored)}** / {fmt_amount(capacity)}"
        ),
        inline=False,
    )

    sat = int(row["employee_satisfaction"])
    sat_icon = "😀" if sat >= 70 else "🙂" if sat >= 45 else "😟"
    embed.add_field(
        name="Attributes",
        value=(
            f"🛡️ Security **{int(row['security'])}**  ·  "
            f"📣 Reputation **{int(row['reputation'])}**\n"
            f"⚙️ Efficiency **{int(row['efficiency'])}**  ·  "
            f"📦 Capacity **{int(row['capacity'])}**\n"
            f"{sat_icon} Satisfaction **{sat}/100**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Upgrade branches",
        value=(
            f"🔒 Security **{int(row['branch_security'])}**/{config.BUSINESS_BRANCH_MAX}  ·  "
            f"📈 Growth **{int(row['branch_growth'])}**/{config.BUSINESS_BRANCH_MAX}  ·  "
            f"🏗️ Production **{int(row['branch_production'])}**/{config.BUSINESS_BRANCH_MAX}"
        ),
        inline=False,
    )

    rating = security_rating(
        security_level=int(row["security"]),
        branch_security_level=int(row["branch_security"]),
        tier=tier,
    )
    nxt = next_tier_def(tier)
    if nxt is not None:
        footer = f"Security rating {rating} · Next tier: {nxt.name} ({fmt_amount(nxt.purchase_cost)})"
    else:
        footer = f"Security rating {rating} · Max tier reached"
    embed.set_footer(text=footer)

    png = render_business_image(member.id, member.guild.id, str(row["tier_id"]))
    file = discord.File(io.BytesIO(png), filename="business.png")
    embed.set_image(url="attachment://business.png")
    return embed, file


async def build_business_payload(
    cog: commands.Cog,
    member: discord.Member,
    guild_id: int,
    user_id: int,
) -> tuple[discord.Embed, list[discord.File], BusinessPanelView] | None:
    row = await cog.bot.db.get_business(user_id, guild_id)
    if row is None:
        return None
    embed, file = build_business_embed(member, row)
    files = [file]
    from utils.districts import district_image_path

    district_path = district_image_path(row["district_id"])
    if district_path is not None:
        files.append(discord.File(str(district_path), filename="district.png"))
        embed.set_thumbnail(url="attachment://district.png")
    buffs = await cog.bot.db.list_active_business_buffs(user_id, guild_id)
    if buffs:
        from utils.business_competition import action_by_id

        lines = []
        for buff in buffs:
            mult = float(buff["multiplier"])
            action = action_by_id(str(buff["buff_type"]))
            label = action.name if action else str(buff["buff_type"])
            sign = "+" if mult >= 1.0 else "−"
            pct = int(round(abs(mult - 1.0) * 100))
            ends = int(float(buff["ends_at"]))
            lines.append(f"{sign}{pct}% {label} · ends <t:{ends}:R>")
        embed.add_field(name="Active effects", value="\n".join(lines), inline=False)
    view = BusinessPanelView(cog, guild_id, user_id)
    view.sync_state(row)
    return embed, files, view


async def _refresh_panel(
    interaction: discord.Interaction,
    cog: commands.Cog,
    guild_id: int,
    user_id: int,
    *,
    note: str | None = None,
) -> None:
    member = interaction.guild.get_member(user_id) if interaction.guild else None
    if member is None and isinstance(interaction.user, discord.Member):
        member = interaction.user
    if member is None:
        await interaction.followup.send("Done.", ephemeral=True)
        return
    payload = await build_business_payload(cog, member, guild_id, user_id)
    if payload is None:
        await interaction.followup.send("You no longer own a business.", ephemeral=True)
        return
    embed, files, view = payload
    if note:
        embed.description = (embed.description or "") + f"\n\n{note}"
    await interaction.edit_original_response(embed=embed, attachments=files, view=view)


class BusinessPanelView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    def sync_state(self, row: object) -> None:
        stored = float(row["stored_income"])
        self.collect_btn.disabled = stored <= 0
        self.tier_up_btn.disabled = next_tier_def(int(row["tier"])) is None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your business panel.", ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Collect", style=discord.ButtonStyle.success)
    async def collect_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await interaction.response.defer()
        amount, err = await self.cog.bot.db.collect_business_income(self.user_id, self.guild_id)
        if err == "empty":
            note = "Nothing to collect yet — let revenue build up."
        elif err:
            note = "Could not collect right now."
        else:
            await record_quest_event(
                self.cog.bot.db, self.guild_id, self.user_id, "business_collect",
            )
            note = f"💰 Collected **{fmt_amount(amount)}** to your pocket!"
        await _refresh_panel(interaction, self.cog, self.guild_id, self.user_id, note=note)

    @discord.ui.button(label="Upgrade", style=discord.ButtonStyle.primary)
    async def upgrade_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        row = await self.cog.bot.db.get_business(self.user_id, self.guild_id)
        if row is None:
            await interaction.response.send_message("You don't own a business.", ephemeral=True)
            return
        view = UpgradeBranchView(self.cog, self.guild_id, self.user_id)
        embed = build_upgrade_embed(row)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Tier up", style=discord.ButtonStyle.secondary)
    async def tier_up_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await interaction.response.defer()
        err, new_tier = await self.cog.bot.db.tier_up_business(self.user_id, self.guild_id)
        if err == "max_tier":
            note = "You already own the top-tier Corporation."
        elif err == "insufficient_funds":
            nxt = next_tier_def(new_tier)
            cost = nxt.purchase_cost if nxt else 0
            note = f"You need **{fmt_amount(cost)}** in your pocket to tier up."
        elif err:
            note = "Could not tier up right now."
        else:
            defn = tier_def(new_tier)
            note = f"🏗️ Upgraded to **{defn.name if defn else 'next tier'}**!"
        await _refresh_panel(interaction, self.cog, self.guild_id, self.user_id, note=note)
        # Explicit ephemeral confirmation so the result is never ambiguous.
        with contextlib.suppress(discord.HTTPException):
            await interaction.followup.send(note, ephemeral=True)

    @discord.ui.button(label="Districts", style=discord.ButtonStyle.secondary)
    async def districts_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        from utils.district_ui import DistrictMapView, build_district_payload

        # Defer first: building the district panel runs several DB queries (and may
        # open an image), which can exceed Discord's 3s ack window on a slow host.
        await interaction.response.defer(ephemeral=True, thinking=True)
        embed, files = await build_district_payload(self.cog, interaction.guild, self.user_id)
        view = DistrictMapView(self.cog, self.guild_id, self.user_id)
        await interaction.followup.send(embed=embed, view=view, files=files, ephemeral=True)

    @discord.ui.button(label="Compete", style=discord.ButtonStyle.danger, row=1)
    async def compete_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        from utils.business_action_ui import BusinessActionView, build_action_embed

        view = BusinessActionView(self.cog, self.guild_id, self.user_id)
        await interaction.response.send_message(
            embed=build_action_embed(), view=view, ephemeral=True,
        )

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await interaction.response.defer()
        await _refresh_panel(interaction, self.cog, self.guild_id, self.user_id)


def build_prestige_embed(row: object) -> discord.Embed:
    prestige = int(row["business_prestige"])
    next_bonus = int((prestige + 1) * config.BUSINESS_PRESTIGE_INCOME_BONUS_PER_LEVEL * 100)
    embed = discord.Embed(
        title="⭐ Business Prestige",
        description=(
            "Prestiging resets your **Corporation back to a Lemon Stand** and clears "
            "stored revenue, but grants a **permanent** business income bonus.\n\n"
            f"Current prestige: **{prestige}** / {config.BUSINESS_PRESTIGE_MAX_LEVEL}\n"
            f"After prestige: **+{next_bonus}%** total permanent business income"
        ),
        color=discord.Color.purple(),
    )
    if prestige + 1 >= config.BUSINESS_PRESTIGE_MAX_LEVEL:
        embed.add_field(
            name="Legendary",
            value="Reaching max prestige cements your legendary business empire status!",
            inline=False,
        )
    embed.set_footer(text="This cannot be undone. Press Confirm to prestige.")
    return embed


class PrestigeConfirmView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=60.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your panel.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm prestige", style=discord.ButtonStyle.danger)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        err, new_prestige = await self.cog.bot.db.prestige_business(self.user_id, self.guild_id)
        messages = {
            "no_business": "You don't own a business.",
            "not_max_tier": "You must reach the Corporation tier first.",
            "max_prestige": "You've already reached max business prestige.",
        }
        if err:
            await interaction.response.edit_message(
                content=messages.get(err, "Could not prestige."), embed=None, view=None,
            )
            return
        await record_quest_event(self.cog.bot.db, self.guild_id, self.user_id, "business_prestige")
        bonus = int(new_prestige * config.BUSINESS_PRESTIGE_INCOME_BONUS_PER_LEVEL * 100)
        await interaction.response.edit_message(
            content=(
                f"⭐ **Prestige {new_prestige}!** Your empire restarts as a Lemon Stand "
                f"with a permanent **+{bonus}%** business income bonus."
            ),
            embed=None,
            view=None,
        )


ATTRIBUTE_LABELS: dict[str, tuple[str, str]] = {
    "security": ("🛡️ Security", "security"),
    "reputation": ("📣 Reputation", "reputation"),
    "efficiency": ("⚙️ Efficiency", "efficiency"),
    "capacity": ("📦 Capacity", "capacity"),
}

BRANCH_LABELS: dict[str, tuple[str, str]] = {
    "branch_security": ("🔒 Security branch", "branch_security"),
    "branch_growth": ("📈 Growth branch", "branch_growth"),
    "branch_production": ("🏗️ Production branch", "branch_production"),
}


def build_upgrade_embed(row: object) -> discord.Embed:
    tier = int(row["tier"])
    embed = discord.Embed(
        title="Business upgrades",
        description="Spend nuggets to improve your business. Costs rise per level.",
        color=discord.Color.blurple(),
    )
    attr_lines = []
    for _key, (label, column) in ATTRIBUTE_LABELS.items():
        lvl = int(row[column])
        cost = upgrade_cost(tier, lvl)
        cap = config.BUSINESS_ATTRIBUTE_MAX
        suffix = "MAX" if lvl >= cap else f"{fmt_amount(cost)}"
        attr_lines.append(f"{label} — Lv **{lvl}**/{cap} · next {suffix}")
    embed.add_field(name="Attributes", value="\n".join(attr_lines), inline=False)

    branch_lines = []
    for _key, (label, column) in BRANCH_LABELS.items():
        lvl = int(row[column])
        cost = upgrade_cost(tier, lvl)
        cap = config.BUSINESS_BRANCH_MAX
        suffix = "MAX" if lvl >= cap else f"{fmt_amount(cost)}"
        branch_lines.append(f"{label} — Lv **{lvl}**/{cap} · next {suffix}")
    embed.add_field(name="Upgrade branches", value="\n".join(branch_lines), inline=False)
    return embed


class UpgradeButton(discord.ui.Button):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int, attribute: str, label: str, row: int) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.primary, row=row)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.attribute = attribute

    async def callback(self, interaction: discord.Interaction) -> None:
        cost, err = await self.cog.bot.db.upgrade_business_attribute(
            self.user_id, self.guild_id, self.attribute,
        )
        messages = {
            "no_business": "You don't own a business.",
            "max_level": "That upgrade is already maxed.",
            "insufficient_funds": f"You need **{fmt_amount(cost)}** in your pocket.",
            "invalid_attribute": "Unknown upgrade.",
        }
        if err:
            await interaction.response.send_message(messages.get(err, err), ephemeral=True)
            return
        await record_quest_event(
            self.cog.bot.db, self.guild_id, self.user_id, "business_upgrade",
        )
        row = await self.cog.bot.db.get_business(self.user_id, self.guild_id)
        view = UpgradeBranchView(self.cog, self.guild_id, self.user_id)
        embed = build_upgrade_embed(row) if row is not None else discord.Embed(title="Upgrades")
        nice = self.label.split(" ", 1)[-1] if self.label else self.attribute
        embed.description = f"✅ Upgraded **{nice}** for **{fmt_amount(cost)}**."
        await interaction.response.edit_message(embed=embed, view=view)


class UpgradeBranchView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        for attribute, (label, _column) in ATTRIBUTE_LABELS.items():
            self.add_item(UpgradeButton(cog, guild_id, user_id, attribute, label, row=0))
        for attribute, (label, _column) in BRANCH_LABELS.items():
            self.add_item(UpgradeButton(cog, guild_id, user_id, attribute, label, row=1))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your upgrade panel.", ephemeral=True,
            )
            return False
        return True
