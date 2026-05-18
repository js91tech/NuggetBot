from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.helpers import fmt_amount, guild_only_message, valid_amount
from utils.quests import record_quest_event

CARD_VALUES = "23456789TJQKA"
SUITS = "♠♥♦♣"


def _card_value(rank: str) -> int:
    if rank in ("T", "J", "Q", "K"):
        return 10
    if rank == "A":
        return 11
    return int(rank)


def _hand_total(cards: list[str]) -> int:
    total = sum(_card_value(card[0]) for card in cards)
    aces = sum(1 for card in cards if card[0] == "A")
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def _draw_card() -> str:
    return random.choice(CARD_VALUES) + random.choice(SUITS)


def _format_hand(cards: list[str]) -> str:
    return " ".join(cards) + f" (**{_hand_total(cards)}**)"


def _payout_after_tax(bet: float, gross_win: float, tax_rate: float) -> float:
    """Return amount to credit (stake + taxed profit)."""
    profit = max(0.0, gross_win - bet)
    tax = profit * tax_rate
    return bet + profit - tax


@dataclass
class CoinflipChallenge:
    guild_id: int
    challenger_id: int
    opponent_id: int
    amount: float


class CoinflipAcceptView(discord.ui.View):
    def __init__(self, cog: Gambling, challenge: CoinflipChallenge) -> None:
        super().__init__(timeout=60.0)
        self.cog = cog
        self.challenge = challenge
        self._resolved = False
        self._lock = asyncio.Lock()

    async def on_timeout(self) -> None:
        self.cog.pending_coinflips.pop(
            (self.challenge.guild_id, self.challenge.challenger_id, self.challenge.opponent_id),
            None,
        )
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="Accept duel", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        async with self._lock:
            if self._resolved:
                await interaction.response.send_message("This duel already finished.", ephemeral=True)
                return
            if interaction.user.id != self.challenge.opponent_id:
                await interaction.response.send_message(
                    "Only the challenged player can accept.", ephemeral=True
                )
                return
            if interaction.guild_id != self.challenge.guild_id:
                await interaction.response.send_message("Wrong server.", ephemeral=True)
                return
            self._resolved = True
            for item in self.children:
                item.disabled = True

            guild_id = self.challenge.guild_id
            tax = await self.cog.bot.db.get_config_value(guild_id, "gambling_house_tax")
            amount = self.challenge.amount
            challenger = self.challenge.challenger_id
            opponent = self.challenge.opponent_id

            if not await self.cog.bot.db.debit_wallet(challenger, guild_id, amount):
                await interaction.response.edit_message(
                    content="Duel cancelled — challenger no longer has enough nuggets.",
                    view=self,
                )
                self.stop()
                return
            if not await self.cog.bot.db.debit_wallet(opponent, guild_id, amount):
                await self.cog.bot.db.credit_wallet(challenger, guild_id, amount)
                await interaction.response.edit_message(
                    content="Duel cancelled — you no longer have enough nuggets.",
                    view=self,
                )
                self.stop()
                return

            winner_id, loser_id = (
                (challenger, opponent) if random.random() < 0.5 else (opponent, challenger)
            )
            pot = amount * 2
            payout = _payout_after_tax(amount, pot, tax)
            await self.cog.bot.db.credit_wallet(winner_id, guild_id, payout)
            burned = pot - payout
            winner = interaction.guild.get_member(winner_id) if interaction.guild else None
            loser = interaction.guild.get_member(loser_id) if interaction.guild else None
            wname = winner.display_name if winner else f"User {winner_id}"
            tax_note = f" (tax {fmt_amount(burned)})" if burned > 0.01 else ""
            await interaction.response.edit_message(
                content=(
                    f"**Coinflip duel!** {wname} wins {fmt_amount(payout)} "
                    f"from {fmt_amount(amount)} stakes{tax_note}."
                ),
                view=self,
            )
            await record_quest_event(
                self.cog.bot.db, guild_id, winner_id, "gamble_play"
            )
            await record_quest_event(
                self.cog.bot.db, guild_id, loser_id, "gamble_play"
            )
            self.cog.pending_coinflips.pop((guild_id, challenger, opponent), None)
            self.stop()


class BlackjackView(discord.ui.View):
    def __init__(
        self,
        cog: Gambling,
        *,
        guild_id: int,
        user_id: int,
        bet: float,
        player: list[str],
        dealer: list[str],
        tax_rate: float,
    ) -> None:
        super().__init__(timeout=120.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.bet = bet
        self.player = player
        self.dealer = dealer
        self.tax_rate = tax_rate
        self._finished = False
        self._lock = asyncio.Lock()

    def _embed(self, *, reveal_dealer: bool) -> discord.Embed:
        dealer_text = (
            _format_hand(self.dealer) if reveal_dealer else f"{self.dealer[0]} ? (**?**)"
        )
        embed = discord.Embed(title="Blackjack", color=discord.Color.dark_green())
        embed.add_field(name="Your hand", value=_format_hand(self.player), inline=False)
        embed.add_field(name="Dealer", value=dealer_text, inline=False)
        embed.set_footer(text=f"Bet {fmt_amount(self.bet)}")
        return embed

    async def _finish(self, interaction: discord.Interaction, outcome: str, credit: float) -> None:
        self._finished = True
        for item in self.children:
            item.disabled = True
        if credit > 0:
            await self.cog.bot.db.credit_wallet(self.user_id, self.guild_id, credit)
        await record_quest_event(self.cog.bot.db, self.guild_id, self.user_id, "gamble_play")
        embed = self._embed(reveal_dealer=True)
        embed.description = outcome
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        async with self._lock:
            if self._finished or interaction.user.id != self.user_id:
                await interaction.response.send_message("Not your hand.", ephemeral=True)
                return
            self.player.append(_draw_card())
            if _hand_total(self.player) > 21:
                await self._finish(
                    interaction,
                    f"Bust! You lose {fmt_amount(self.bet)}.",
                    0.0,
                )
                return
            await interaction.response.edit_message(embed=self._embed(reveal_dealer=False), view=self)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        async with self._lock:
            if self._finished or interaction.user.id != self.user_id:
                await interaction.response.send_message("Not your hand.", ephemeral=True)
                return
            while _hand_total(self.dealer) < 17:
                self.dealer.append(_draw_card())
            player_total = _hand_total(self.player)
            dealer_total = _hand_total(self.dealer)
            if dealer_total > 21 or player_total > dealer_total:
                payout = _payout_after_tax(self.bet, self.bet * 2, self.tax_rate)
                await self._finish(
                    interaction,
                    f"You win {fmt_amount(payout)}!",
                    payout,
                )
            elif player_total == dealer_total:
                await self._finish(interaction, "Push — bet returned.", self.bet)
            else:
                await self._finish(
                    interaction,
                    f"Dealer wins. You lose {fmt_amount(self.bet)}.",
                    0.0,
                )


class Gambling(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.pending_coinflips: dict[tuple[int, int, int], CoinflipChallenge] = {}

    async def _validate_bet(
        self,
        interaction: discord.Interaction,
        amount: float,
    ) -> float | None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return None
        if not valid_amount(amount, minimum=config.GAMBLING_MIN_BET):
            await interaction.response.send_message(
                f"Minimum bet is {fmt_amount(config.GAMBLING_MIN_BET)}.",
                ephemeral=True,
            )
            return None
        if amount > config.GAMBLING_MAX_BET:
            await interaction.response.send_message(
                f"Maximum bet is {fmt_amount(config.GAMBLING_MAX_BET)}.",
                ephemeral=True,
            )
            return None
        if await self.bot.db.is_restricted(interaction.user.id, interaction.guild_id):
            await interaction.response.send_message(
                "You cannot gamble while arrested or downed.",
                ephemeral=True,
            )
            return None
        return amount

    @app_commands.command(name="coinflip", description="50/50 coinflip vs the house for nuggets.")
    @app_commands.describe(amount="Nuggets to wager")
    @app_commands.guild_only()
    async def coinflip(self, interaction: discord.Interaction, amount: float) -> None:
        bet = await self._validate_bet(interaction, amount)
        if bet is None or interaction.guild_id is None:
            return

        if not await self.bot.db.debit_wallet(interaction.user.id, interaction.guild_id, bet):
            await interaction.response.send_message("Insufficient nuggets.", ephemeral=True)
            return

        tax = await self.bot.db.get_config_value(interaction.guild_id, "gambling_house_tax")
        win = random.random() < 0.5
        if win:
            payout = _payout_after_tax(bet, bet * 2, tax)
            await self.bot.db.credit_wallet(interaction.user.id, interaction.guild_id, payout)
            profit = payout - bet
            await interaction.response.send_message(
                f"**Heads!** You win {fmt_amount(payout)} (+{fmt_amount(profit)} after tax).",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"**Tails.** You lose {fmt_amount(bet)}.",
                ephemeral=True,
            )
        await record_quest_event(
            self.bot.db, interaction.guild_id, interaction.user.id, "gamble_play"
        )

    @app_commands.command(
        name="coinflip-duel",
        description="Challenge another player to a coinflip for nuggets.",
    )
    @app_commands.describe(opponent="Player to challenge", amount="Nuggets each player stakes")
    @app_commands.guild_only()
    async def coinflip_duel(
        self,
        interaction: discord.Interaction,
        opponent: discord.Member,
        amount: float,
    ) -> None:
        bet = await self._validate_bet(interaction, amount)
        if bet is None or interaction.guild_id is None:
            return
        if opponent.bot or opponent.id == interaction.user.id:
            await interaction.response.send_message("Pick another player.", ephemeral=True)
            return
        if await self.bot.db.is_restricted(opponent.id, interaction.guild_id):
            await interaction.response.send_message(
                "That player cannot gamble right now.",
                ephemeral=True,
            )
            return

        key = (interaction.guild_id, interaction.user.id, opponent.id)
        if key in self.pending_coinflips:
            await interaction.response.send_message("You already challenged them.", ephemeral=True)
            return

        challenge = CoinflipChallenge(
            interaction.guild_id,
            interaction.user.id,
            opponent.id,
            bet,
        )
        self.pending_coinflips[key] = challenge
        view = CoinflipAcceptView(self, challenge)
        await interaction.response.send_message(
            f"{opponent.mention}, {interaction.user.mention} wants a coinflip for "
            f"**{fmt_amount(bet)}** each!",
            view=view,
            allowed_mentions=discord.AllowedMentions(users=[opponent]),
        )

    @app_commands.command(name="blackjack", description="Play blackjack vs the house.")
    @app_commands.describe(amount="Nuggets to wager")
    @app_commands.guild_only()
    async def blackjack(self, interaction: discord.Interaction, amount: float) -> None:
        bet = await self._validate_bet(interaction, amount)
        if bet is None or interaction.guild_id is None:
            return

        if not await self.bot.db.debit_wallet(interaction.user.id, interaction.guild_id, bet):
            await interaction.response.send_message("Insufficient nuggets.", ephemeral=True)
            return

        tax = await self.bot.db.get_config_value(interaction.guild_id, "gambling_house_tax")
        player = [_draw_card(), _draw_card()]
        dealer = [_draw_card(), _draw_card()]
        view = BlackjackView(
            self,
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            bet=bet,
            player=player,
            dealer=dealer,
            tax_rate=tax,
        )

        if _hand_total(player) == 21:
            if _hand_total(dealer) == 21:
                await self.bot.db.credit_wallet(interaction.user.id, interaction.guild_id, bet)
                await interaction.response.send_message(
                    f"Both blackjack! Push — {fmt_amount(bet)} returned.",
                    ephemeral=True,
                )
            else:
                payout = _payout_after_tax(bet, bet * 2.5, tax)
                await self.bot.db.credit_wallet(interaction.user.id, interaction.guild_id, payout)
                await interaction.response.send_message(
                    f"**Blackjack!** You win {fmt_amount(payout)}.",
                    ephemeral=True,
                )
            await record_quest_event(
                self.bot.db, interaction.guild_id, interaction.user.id, "gamble_play"
            )
            return

        embed = view._embed(reveal_dealer=False)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Gambling(bot))
