"""Interactive stock market: buy/sell corporation shares and view your portfolio."""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from utils.helpers import fmt_amount
from utils.quests import record_quest_event
from utils.stock_market import event_label

if TYPE_CHECKING:
    from discord.ext import commands


async def build_market_embed(
    cog: commands.Cog, guild: discord.Guild, user_id: int,
) -> discord.Embed:
    market = await cog.bot.db.list_stock_market(guild.id)
    holdings = await cog.bot.db.get_stock_holdings(user_id, guild.id)
    event_type, _ = await cog.bot.db.get_stock_market_event(guild.id)

    embed = discord.Embed(
        title="📊 Stock Market",
        description=(
            "Invest in corporations. Prices rise with their treasury and headcount; "
            "shareholders earn hourly dividends from the corporate vault."
        ),
        color=discord.Color.green(),
    )
    if event_type:
        embed.add_field(name="Market event", value=event_label(event_type), inline=False)

    if market:
        lines = [
            f"{i}. **{row['crew_name']}** — {fmt_amount(float(row['price']))}/share "
            f"({int(row['members'])} members)"
            for i, row in enumerate(market[:15], 1)
        ]
        embed.add_field(name="Listings", value="\n".join(lines), inline=False)
    else:
        embed.add_field(
            name="Listings",
            value="_No corporations yet — found a crew with `/crew panel`._",
            inline=False,
        )

    if holdings:
        port_lines = [
            f"**{h['crew_name']}** — {int(h['shares'])} shares · {fmt_amount(float(h['value']))}"
            for h in holdings
        ]
        total = sum(float(h["value"]) for h in holdings)
        embed.add_field(
            name=f"Your portfolio ({fmt_amount(total)})",
            value="\n".join(port_lines),
            inline=False,
        )
    return embed


class TradeModal(discord.ui.Modal):
    shares = discord.ui.TextInput(label="Number of shares", placeholder="e.g. 10", required=True, max_length=7)

    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int, crew_name: str, side: str) -> None:
        super().__init__(title=f"{side.title()} {crew_name} shares")
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.crew_name = crew_name
        self.side = side

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            count = int(str(self.shares.value).replace(",", "").strip())
        except ValueError:
            await interaction.response.send_message("Enter a whole number.", ephemeral=True)
            return
        if count <= 0:
            await interaction.response.send_message("Enter a positive amount.", ephemeral=True)
            return
        if self.side == "buy":
            total, err = await self.cog.bot.db.buy_shares(
                self.user_id, self.guild_id, self.crew_name, count,
            )
            messages = {
                "invalid_amount": "Enter a valid share count.",
                "unknown_corp": "That corporation is not listed.",
                "insufficient_funds": f"You need **{fmt_amount(total)}**.",
            }
            note = (
                f"🟢 Bought **{count}** shares of **{self.crew_name}** for **{fmt_amount(total)}**."
                if not err else messages.get(err, "Could not buy.")
            )
        else:
            proceeds, err = await self.cog.bot.db.sell_shares(
                self.user_id, self.guild_id, self.crew_name, count,
            )
            messages = {
                "invalid_amount": "Enter a valid share count.",
                "insufficient_shares": "You don't own that many shares.",
            }
            note = (
                f"🔴 Sold **{count}** shares of **{self.crew_name}** for **{fmt_amount(proceeds)}**."
                if not err else messages.get(err, "Could not sell.")
            )
        if not err:
            await record_quest_event(self.cog.bot.db, self.guild_id, self.user_id, "stock_trade")
        view = StockMarketView(self.cog, self.guild_id, self.user_id)
        await view.populate()
        embed = await build_market_embed(self.cog, interaction.guild, self.user_id)
        embed.description = note
        await interaction.response.edit_message(embed=embed, view=view)


class CorpTradeSelect(discord.ui.Select):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int, options: list[discord.SelectOption]) -> None:
        super().__init__(
            placeholder="Pick a corporation to trade…",
            options=options or [discord.SelectOption(label="No corporations", value="_none")],
            disabled=not options,
            row=0,
        )
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        view: StockMarketView = self.view  # type: ignore[assignment]
        view.selected_crew = self.values[0]
        await interaction.response.send_message(
            f"Selected **{self.values[0]}**. Use Buy or Sell below.", ephemeral=True,
        )


class StockMarketView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.selected_crew: str | None = None

    async def populate(self) -> None:
        market = await self.cog.bot.db.list_stock_market(self.guild_id)
        options = [
            discord.SelectOption(
                label=row["crew_name"],
                value=str(row["crew_name"]),
                description=f"{fmt_amount(float(row['price']))}/share"[:100],
            )
            for row in market[:25]
        ]
        self.add_item(CorpTradeSelect(self.cog, self.guild_id, self.user_id, options))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your market panel.", ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="🟢 Buy", style=discord.ButtonStyle.success, row=1)
    async def buy_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        if not self.selected_crew:
            await interaction.response.send_message("Pick a corporation first.", ephemeral=True)
            return
        await interaction.response.send_modal(
            TradeModal(self.cog, self.guild_id, self.user_id, self.selected_crew, "buy"),
        )

    @discord.ui.button(label="🔴 Sell", style=discord.ButtonStyle.danger, row=1)
    async def sell_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        if not self.selected_crew:
            await interaction.response.send_message("Pick a corporation first.", ephemeral=True)
            return
        await interaction.response.send_modal(
            TradeModal(self.cog, self.guild_id, self.user_id, self.selected_crew, "sell"),
        )

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=1)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        view = StockMarketView(self.cog, self.guild_id, self.user_id)
        await view.populate()
        embed = await build_market_embed(self.cog, interaction.guild, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)


async def send_stock_panel(interaction: discord.Interaction, cog: commands.Cog) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return
    view = StockMarketView(cog, interaction.guild.id, interaction.user.id)
    await view.populate()
    embed = await build_market_embed(cog, interaction.guild, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view)
