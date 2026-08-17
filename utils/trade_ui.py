"""Player-to-player trade panel with escrow."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

import discord

import config
from items import get_item
from utils.drugs import drug_by_id
from utils.helpers import fmt_amount

if TYPE_CHECKING:
    from discord.ext import commands


def _gear_label(instance_id: int, item_id: str, level: int, broken: bool) -> str:
    item = get_item(item_id)
    name = item.name if item else item_id
    suffix = f" +{level}" if level > 0 else ""
    if broken:
        suffix += " (broken)"
    return f"{name}{suffix} [#{instance_id}]"


def build_trade_embed(
    *,
    initiator: discord.Member,
    receiver: discord.Member,
    nuggets: float,
    drugs: dict[str, int],
    gear_ids: list[int],
    gear_rows: list[object],
    pending: bool = False,
) -> discord.Embed:
    embed = discord.Embed(
        title="🤝 Trade offer" if pending else "🤝 Build trade offer",
        color=discord.Color.gold(),
    )
    embed.add_field(name="From", value=initiator.mention, inline=True)
    embed.add_field(name="To", value=receiver.mention, inline=True)
    lines: list[str] = []
    if nuggets > 0:
        lines.append(f"**{fmt_amount(nuggets)}** {config.CURRENCY_NAME}")
    for drug_id, qty in drugs.items():
        defn = drug_by_id(drug_id)
        name = defn.name if defn else drug_id
        emoji = defn.emoji if defn else ""
        lines.append(f"{emoji} **{name}** ×{qty}")
    gear_by_id = {int(r["instance_id"]): r for r in gear_rows}
    for gid in gear_ids:
        row = gear_by_id.get(gid)
        if row is None:
            continue
        lines.append(
            _gear_label(gid, str(row["item_id"]), int(row["enhancement_level"]), bool(row["is_broken"])),
        )
    embed.add_field(
        name="Offer",
        value="\n".join(lines) if lines else "_Nothing added yet_",
        inline=False,
    )
    if pending:
        embed.set_footer(text=f"Expires in {config.TRADE_EXPIRE_SECONDS // 60} minutes · Accept or decline")
    else:
        embed.set_footer(text="Add items below, then send the offer")
    return embed


class NuggetsModal(discord.ui.Modal, title="Trade goonbux"):
    def __init__(self, view: "TradeBuildView") -> None:
        super().__init__()
        self._view = view
        self.amount = discord.ui.TextInput(
            label="Nuggets to offer",
            placeholder="0",
            default=str(int(view.nuggets)) if view.nuggets else "0",
            max_length=12,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.amount.value.strip().replace(",", "")
        try:
            value = max(0.0, float(raw))
        except ValueError:
            await interaction.response.send_message("Enter a valid number.", ephemeral=True)
            return
        self._view.nuggets = value
        await interaction.response.edit_message(
            embed=await self._view.build_embed(),
            view=self._view,
        )


class TradeDrugSelect(discord.ui.Select):
    def __init__(self, view: "TradeBuildView", inventory: dict[str, int]) -> None:
        self._view = view
        options: list[discord.SelectOption] = []
        for drug_id, qty in list(inventory.items())[:24]:
            defn = drug_by_id(drug_id)
            options.append(
                discord.SelectOption(
                    label=f"{defn.name if defn else drug_id} (×{qty})"[:100],
                    value=drug_id,
                    description="Add 1 to offer",
                    emoji=defn.emoji if defn else None,
                ),
            )
        if not options:
            options.append(
                discord.SelectOption(label="No stash drugs", value="_none", default=True),
            )
        super().__init__(
            placeholder="Add drug from stash…",
            min_values=1,
            max_values=1,
            options=options,
            disabled=not inventory,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        drug_id = self.values[0]
        if drug_id == "_none":
            await interaction.response.defer()
            return
        inv = await self._view.cog.bot.db.get_drug_inventory(
            self._view.initiator_id, self._view.guild_id,
        )
        current = self._view.drugs.get(drug_id, 0)
        if current >= inv.get(drug_id, 0):
            await interaction.response.send_message(
                "You don't have more of that strain to add.", ephemeral=True,
            )
            return
        if len(self._view.drugs) >= config.TRADE_MAX_DRUG_TYPES and drug_id not in self._view.drugs:
            await interaction.response.send_message(
                f"Max **{config.TRADE_MAX_DRUG_TYPES}** drug types per trade.", ephemeral=True,
            )
            return
        self._view.drugs[drug_id] = current + 1
        await interaction.response.edit_message(
            embed=await self._view.build_embed(),
            view=self._view,
        )


class TradeGearSelect(discord.ui.Select):
    def __init__(self, view: "TradeBuildView", instances: list[object]) -> None:
        self._view = view
        options: list[discord.SelectOption] = []
        for row in instances[:24]:
            iid = int(row["instance_id"])
            label = _gear_label(iid, str(row["item_id"]), int(row["enhancement_level"]), bool(row["is_broken"]))
            options.append(
                discord.SelectOption(label=label[:100], value=str(iid), description="Add to offer"),
            )
        if not options:
            options.append(
                discord.SelectOption(label="No tradeable gear", value="_none"),
            )
        super().__init__(
            placeholder="Add enhanced gear…",
            min_values=1,
            max_values=1,
            options=options,
            disabled=not instances,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "_none":
            await interaction.response.defer()
            return
        instance_id = int(self.values[0])
        if instance_id in self._view.gear_ids:
            await interaction.response.send_message("Already in the offer.", ephemeral=True)
            return
        if len(self._view.gear_ids) >= config.TRADE_MAX_GEAR_INSTANCES:
            await interaction.response.send_message(
                f"Max **{config.TRADE_MAX_GEAR_INSTANCES}** gear pieces per trade.", ephemeral=True,
            )
            return
        self._view.gear_ids.append(instance_id)
        await interaction.response.edit_message(
            embed=await self._view.build_embed(),
            view=self._view,
        )


class TradeBuildView(discord.ui.View):
    def __init__(
        self,
        cog: commands.Cog,
        *,
        guild_id: int,
        initiator_id: int,
        receiver: discord.Member,
    ) -> None:
        super().__init__(timeout=float(config.TRADE_EXPIRE_SECONDS))
        self.cog = cog
        self.guild_id = guild_id
        self.initiator_id = initiator_id
        self.receiver = receiver
        self.nuggets = 0.0
        self.drugs: dict[str, int] = {}
        self.gear_ids: list[int] = []
        self._built = False

    async def populate(self) -> None:
        if self._built:
            return
        inventory = await self.cog.bot.db.get_drug_inventory(self.initiator_id, self.guild_id)
        instances = await self.cog.bot.db.list_tradeable_gear_instances(
            self.initiator_id, self.guild_id,
        )
        self.add_item(TradeDrugSelect(self, inventory))
        self.add_item(TradeGearSelect(self, instances))
        self._built = True

    async def build_embed(self) -> discord.Embed:
        guild = self.cog.bot.get_guild(self.guild_id)
        initiator = guild.get_member(self.initiator_id) if guild else None
        instances = await self.cog.bot.db.list_tradeable_gear_instances(
            self.initiator_id, self.guild_id,
        )
        return build_trade_embed(
            initiator=initiator or self.receiver,
            receiver=self.receiver,
            nuggets=self.nuggets,
            drugs=self.drugs,
            gear_ids=self.gear_ids,
            gear_rows=instances,
        )

    @discord.ui.button(label="Set goonbux", style=discord.ButtonStyle.secondary, row=2)
    async def set_nuggets(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        if interaction.user.id != self.initiator_id:
            await interaction.response.send_message("Only the initiator can edit.", ephemeral=True)
            return
        await interaction.response.send_modal(NuggetsModal(self))

    @discord.ui.button(label="Clear offer", style=discord.ButtonStyle.secondary, row=2)
    async def clear_offer(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        if interaction.user.id != self.initiator_id:
            await interaction.response.send_message("Only the initiator can edit.", ephemeral=True)
            return
        self.nuggets = 0.0
        self.drugs.clear()
        self.gear_ids.clear()
        await interaction.response.edit_message(embed=await self.build_embed(), view=self)

    @discord.ui.button(label="Send offer", style=discord.ButtonStyle.success, row=3)
    async def send_offer(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        if interaction.user.id != self.initiator_id:
            await interaction.response.send_message("Only the initiator can send.", ephemeral=True)
            return
        trade_id, err = await self.cog.bot.db.create_pending_trade(
            self.initiator_id,
            self.receiver.id,
            self.guild_id,
            nuggets=self.nuggets,
            drugs=self.drugs,
            gear_instance_ids=self.gear_ids,
        )
        errors = {
            "self_trade": "You can't trade with yourself.",
            "empty_offer": "Add something to trade first.",
            "too_many_drugs": "Too many drug types.",
            "too_many_gear": "Too much gear.",
            "trade_busy": "You or they already have a pending trade.",
            "insufficient_nuggets": "Not enough goonbux.",
            "insufficient_drugs": "Not enough product in stash.",
            "invalid_gear": "Gear unavailable (equipped or missing).",
        }
        if err:
            await interaction.response.send_message(errors.get(err, "Trade failed."), ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"Offer sent to {self.receiver.mention}! (trade #{trade_id})",
            embed=await self.build_embed(),
            view=self,
        )
        recv_view = TradeReceiveView(self.cog, trade_id=int(trade_id), guild_id=self.guild_id)
        await recv_view.send_to_receiver(self.receiver)
        self.stop()

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True


class TradeReceiveView(discord.ui.View):
    def __init__(self, cog: commands.Cog, *, trade_id: int, guild_id: int) -> None:
        super().__init__(timeout=float(config.TRADE_EXPIRE_SECONDS))
        self.cog = cog
        self.trade_id = trade_id
        self.guild_id = guild_id

    async def send_to_receiver(self, receiver: discord.Member) -> None:
        trade = await self.cog.bot.db.get_pending_trade(self.trade_id, self.guild_id)
        if trade is None:
            return
        guild = self.cog.bot.get_guild(self.guild_id)
        initiator = guild.get_member(int(trade["initiator_id"])) if guild else None
        drugs = json.loads(str(trade["offer_drugs"] or "{}"))
        gear_ids = [int(x) for x in json.loads(str(trade["offer_gear"] or "[]"))]
        gear_rows = []
        for gid in gear_ids:
            row = await self.cog.bot.db.get_gear_instance(gid, self.guild_id)
            if row:
                gear_rows.append(row)
        embed = build_trade_embed(
            initiator=initiator or receiver,
            receiver=receiver,
            nuggets=float(trade["offer_nuggets"]),
            drugs={k: int(v) for k, v in drugs.items()},
            gear_ids=gear_ids,
            gear_rows=gear_rows,
            pending=True,
        )
        try:
            await receiver.send(
                f"You received a trade offer in **{guild.name if guild else 'server'}**!",
                embed=embed,
                view=self,
            )
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, row=0)
    async def accept(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        err = await self.cog.bot.db.resolve_trade(
            self.trade_id, self.guild_id, interaction.user.id, "accept",
        )
        if err:
            msgs = {
                "not_found": "Trade no longer available.",
                "expired": "Trade expired.",
                "not_receiver": "Only the receiver can accept.",
            }
            await interaction.response.send_message(msgs.get(err, "Could not accept."), ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="**Trade completed!**", view=self)
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, row=0)
    async def decline(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        err = await self.cog.bot.db.resolve_trade(
            self.trade_id, self.guild_id, interaction.user.id, "decline",
        )
        if err:
            await interaction.response.send_message("Could not decline.", ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Trade declined.", view=self)
        self.stop()


async def open_trade_panel(
    cog: commands.Cog,
    interaction: discord.Interaction,
    receiver: discord.Member,
) -> None:
    if interaction.guild_id is None:
        return
    active = await cog.bot.db.get_active_trade_for_user(interaction.user.id, interaction.guild_id)
    if active is not None:
        await interaction.response.send_message(
            "You already have a pending trade. Finish or wait for it to expire.",
            ephemeral=True,
        )
        return
    view = TradeBuildView(
        cog,
        guild_id=interaction.guild_id,
        initiator_id=interaction.user.id,
        receiver=receiver,
    )
    await view.populate()
    await interaction.response.send_message(
        embed=await view.build_embed(),
        view=view,
        ephemeral=True,
    )
