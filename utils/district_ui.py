"""Interactive district map: relocate, claim deeds, buyouts, and influence."""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

import config
from utils.districts import (
    DISTRICT_MAP,
    deed_claim_cost,
    district_by_id,
    district_image_path,
    effective_district_mult,
    relocate_cost,
)
from utils.helpers import fmt_amount
from utils.quests import record_quest_event

if TYPE_CHECKING:
    from discord.ext import commands


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


async def build_district_embed(
    cog: commands.Cog,
    guild: discord.Guild,
    user_id: int,
) -> discord.Embed:
    row = await cog.bot.db.get_business(user_id, guild.id)
    current = str(row["district_id"]) if row is not None and row["district_id"] else None
    deeds = await cog.bot.db.list_district_deeds(guild.id)

    embed = discord.Embed(
        title="🗺️ Business Districts",
        description=(
            "Relocate for a placement bonus. **One player owns each district deed** "
            "(full bonus + **20% rent** from tenants). Tenants keep half the district "
            "bonus. Claim unowned deeds, or hostile-buyout an owned one."
        ),
        color=discord.Color.teal(),
    )
    for defn in DISTRICT_MAP.values():
        top = await cog.bot.db.list_district_influence(guild.id, defn.district_id, limit=3)
        top_lines = []
        for entity_type, entity_id, influence in top:
            if entity_type == "user":
                member = guild.get_member(int(entity_id)) if entity_id.isdigit() else None
                name = member.display_name if member else f"User {entity_id}"
            else:
                name = str(entity_id)
            top_lines.append(f"{name} ({int(influence)}%)")
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
                _, _, buyer_pays, err = await cog.bot.db.preview_district_buyout(
                    guild.id, defn.district_id, buyer_id=user_id,
                )
                if err is None:
                    owner_text += f" · buyout **{fmt_amount(buyer_pays)}**"
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
            value=(
                f"{defn.label} · owner mult x**{defn.income_mult:.2f}**{tenant_note}\n"
                f"Deed: {owner_text}\n"
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
                f"Your business: {loc_label} · your influence here {int(your_inf)}% · "
                f"Relocate fee {fmt_amount(relocate_cost(int(row['tier'])))}"
            ),
        )
    else:
        embed.set_footer(text="Create a business with /business create to relocate it.")
    return embed


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
            await interaction.response.send_message(messages.get(err, err), ephemeral=True)
            return
        await record_quest_event(
            self.cog.bot.db, self.guild_id, self.user_id, "business_relocate",
        )
        guild = interaction.guild
        view = DistrictMapView(self.cog, self.guild_id, self.user_id)
        embed, files = await build_district_payload(self.cog, guild, self.user_id)
        embed.description = (
            f"✅ Relocated to **{defn.emoji} {defn.name}** for **{fmt_amount(cost)}** "
            f"({defn.label})."
        )
        await interaction.response.edit_message(embed=embed, view=view, attachments=files)


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
            await interaction.response.send_message(messages.get(err, err), ephemeral=True)
            return
        guild = interaction.guild
        view = DistrictMapView(self.cog, self.guild_id, self.user_id)
        embed, files = await build_district_payload(self.cog, guild, self.user_id)
        embed.description = (
            f"📜 Claimed the **{defn.emoji} {defn.name}** deed for "
            f"**{fmt_amount(cost)}**. You collect **20% rent** from tenants."
        )
        await interaction.response.edit_message(embed=embed, view=view, attachments=files)


class BuyoutDeedSelect(discord.ui.Select):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        options = [
            discord.SelectOption(
                label=f"Buyout {defn.name}",
                value=defn.district_id,
                description="Hostile deed buyout (5 days + 15% burn)"[:100],
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
            await interaction.response.send_message(messages.get(err, err), ephemeral=True)
            return
        guild = interaction.guild
        view = DistrictMapView(self.cog, self.guild_id, self.user_id)
        embed, files = await build_district_payload(self.cog, guild, self.user_id)
        embed.description = (
            f"💥 Bought out **{defn.emoji} {defn.name}** for **{fmt_amount(paid)}** "
            f"(previous owner received **{fmt_amount(received)}**, "
            f"**{fmt_amount(burned)}** burned)."
        )
        await interaction.response.edit_message(embed=embed, view=view, attachments=files)


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
        try:
            amount = int(str(self.points.value).strip())
        except ValueError:
            await interaction.response.send_message("Enter a whole number.", ephemeral=True)
            return
        if amount <= 0:
            await interaction.response.send_message("Enter a positive amount.", ephemeral=True)
            return
        cost, new_inf, err = await self.cog.bot.db.expand_district_influence(
            self.user_id, self.guild_id, self.district_id, amount,
        )
        if err == "insufficient_funds":
            await interaction.response.send_message(
                f"You need **{fmt_amount(cost)}** for {amount} influence.", ephemeral=True,
            )
            return
        if err:
            await interaction.response.send_message("Could not expand influence.", ephemeral=True)
            return
        await record_quest_event(
            self.cog.bot.db, self.guild_id, self.user_id, "business_influence",
        )
        defn = district_by_id(self.district_id)
        guild = interaction.guild
        view = DistrictMapView(self.cog, self.guild_id, self.user_id)
        embed, files = await build_district_payload(self.cog, guild, self.user_id)
        territory_mult = await self.cog.bot.db.get_corporate_territory_mult(
            self.user_id, self.guild_id,
        )
        gained = amount * territory_mult
        gain_text = (
            f"📈 +{gained:.1f} influence"
            if territory_mult > 1.001
            else f"📈 +{int(gained)} influence"
        )
        embed.description = (
            f"{gain_text} in **{defn.name if defn else self.district_id}** "
            f"for **{fmt_amount(cost)}** — now **{int(new_inf)}%**."
        )
        await interaction.response.edit_message(embed=embed, view=view, attachments=files)


class InfluenceSelect(discord.ui.Select):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        options = [
            discord.SelectOption(
                label=defn.name,
                value=defn.district_id,
                description=f"+influence at {config.BUSINESS_DISTRICT_INFLUENCE_COST_PER_POINT:.0f}/pt"[:100],
                emoji=defn.emoji,
            )
            for defn in DISTRICT_MAP.values()
        ]
        super().__init__(
            placeholder="Invest influence in…",
            options=options,
            row=3,
        )
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            InfluenceModal(self.cog, self.guild_id, self.user_id, self.values[0]),
        )


class DistrictMapView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.add_item(RelocateSelect(cog, guild_id, user_id))
        self.add_item(ClaimDeedSelect(cog, guild_id, user_id))
        self.add_item(BuyoutDeedSelect(cog, guild_id, user_id))
        self.add_item(InfluenceSelect(cog, guild_id, user_id))

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
        embed, files = await build_district_payload(self.cog, interaction.guild, self.user_id)
        await interaction.response.edit_message(embed=embed, view=self, attachments=files)


async def send_district_panel(interaction: discord.Interaction, cog: commands.Cog) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return
    embed, files = await build_district_payload(cog, guild, interaction.user.id)
    view = DistrictMapView(cog, guild.id, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, files=files)
