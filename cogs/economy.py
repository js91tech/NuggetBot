from __future__ import annotations

import asyncio
import logging
import random
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from utils.helpers import fmt_amount, guild_only_message, resolve_main_channel, valid_amount

COIN_DROP_INTERVAL_MINUTES = 30
COIN_DROP_MIN_TYPERS = 3
COIN_DROP_MIN_AMOUNT = 15
COIN_DROP_MAX_AMOUNT = 250
COIN_DROP_CLAIM_SECONDS = 120


class CoinDropView(discord.ui.View):
    """First click wins within ``timeout``; prize is credited on successful claim."""

    def __init__(self, bot: commands.Bot, guild_id: int, amount: float) -> None:
        super().__init__(timeout=float(COIN_DROP_CLAIM_SECONDS))
        self.bot = bot
        self.guild_id = guild_id
        self.amount = amount
        self._claimed = False
        self._claim_lock = asyncio.Lock()

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is None:
            return
        try:
            await self.message.edit(
                content=(
                    f"**Coin drop expired.** Nobody claimed **{fmt_amount(self.amount)}** in "
                    f"{COIN_DROP_CLAIM_SECONDS} seconds."
                ),
                view=self,
            )
        except (discord.HTTPException, discord.NotFound):
            logging.exception("Coin drop: timeout edit failed guild %s", self.guild_id)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success)
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        if interaction.guild_id != self.guild_id:
            await interaction.response.send_message(
                "This drop is not in your server.", ephemeral=True
            )
            return
        if not isinstance(interaction.user, discord.Member) or interaction.user.bot:
            await interaction.response.send_message("You cannot claim this.", ephemeral=True)
            return

        async with self._claim_lock:
            if self._claimed:
                await interaction.response.send_message(
                    "Someone already claimed this drop.", ephemeral=True
                )
                return
            if await self.bot.db.is_restricted(interaction.user.id, self.guild_id):
                await interaction.response.send_message(
                    "You cannot claim rewards right now.", ephemeral=True
                )
                return
            await self.bot.db.credit_wallet(interaction.user.id, self.guild_id, self.amount)
            self._claimed = True
            for item in self.children:
                item.disabled = True
            assert interaction.message is not None
            await interaction.response.edit_message(
                content=(
                    f"**Coin drop claimed!** {interaction.user.mention} grabbed **{fmt_amount(self.amount)}**!"
                ),
                allowed_mentions=discord.AllowedMentions(users=[interaction.user]),
                view=self,
            )
            self.stop()


class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_chatters: set[tuple[int, int]] = set()
        self.coin_drop_typers: dict[int, set[int]] = {}
        self.passive_active_tick.start()
        self.vc_earning_tick.start()
        self.coin_drop_tick.start()

    def cog_unload(self) -> None:
        self.passive_active_tick.cancel()
        self.vc_earning_tick.cancel()
        self.coin_drop_tick.cancel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return

        if await self.bot.db.is_restricted(message.author.id, message.guild.id):
            return

        reward = await self.bot.db.get_config_value(message.guild.id, "passive_chat_reward")
        await self.bot.db.record_message_reward(
            message.author.id,
            message.guild.id,
            reward,
        )
        self.active_chatters.add((message.guild.id, message.author.id))
        bucket = self.coin_drop_typers.setdefault(message.guild.id, set())
        bucket.add(message.author.id)

    @tasks.loop(minutes=COIN_DROP_INTERVAL_MINUTES)
    async def coin_drop_tick(self) -> None:
        for guild in self.bot.guilds:
            typers = self.coin_drop_typers.pop(guild.id, set())
            if len(typers) < COIN_DROP_MIN_TYPERS:
                continue
            amount = float(random.randint(COIN_DROP_MIN_AMOUNT, COIN_DROP_MAX_AMOUNT))
            channel = await resolve_main_channel(guild, self.bot.db)
            if channel is None:
                logging.warning("Coin drop: no channel to announce in guild %s", guild.id)
                continue
            body = (
                f"**Random coin drop!** **{fmt_amount(amount)}** are up for grabs—"
                f"**first to claim** wins. Anyone can press **Claim** for **{COIN_DROP_CLAIM_SECONDS}** seconds!"
            )
            view = CoinDropView(self.bot, guild.id, amount)
            try:
                await channel.send(body, view=view)
            except discord.HTTPException:
                logging.exception("Coin drop: failed to send in guild %s", guild.id)

    @coin_drop_tick.before_loop
    async def before_coin_drop_tick(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(hours=1)
    async def passive_active_tick(self) -> None:
        chatters = self.active_chatters
        self.active_chatters = set()
        for guild_id, user_id in chatters:
            if not await self.bot.db.is_restricted(user_id, guild_id):
                reward = await self.bot.db.get_config_value(guild_id, "passive_active_bonus")
                await self.bot.db.credit_wallet(user_id, guild_id, reward)
                await self.bot.db.set_last_active(user_id, guild_id, time.time())

    @passive_active_tick.before_loop
    async def before_passive_active_tick(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=60)
    async def vc_earning_tick(self) -> None:
        for guild in self.bot.guilds:
            reward = await self.bot.db.get_config_value(guild.id, "voice_chat_reward")
            for voice_channel in guild.voice_channels:
                for member in voice_channel.members:
                    if member.bot or await self.bot.db.is_restricted(member.id, guild.id):
                        continue
                    await self.bot.db.credit_wallet(member.id, guild.id, reward)

    @vc_earning_tick.before_loop
    async def before_vc_earning_tick(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="daily", description="Claim your daily nuggets.")
    @app_commands.guild_only()
    async def daily(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        current = time.time()
        reward = await self.bot.db.get_config_value(interaction.guild_id, "daily_reward")
        remaining = await self.bot.db.claim_daily(
            interaction.user.id,
            interaction.guild_id,
            reward,
            config.DAILY_COOLDOWN_SECONDS,
            current,
        )
        if remaining is not None:
            hours = int(remaining // 3600)
            minutes = int((remaining % 3600) // 60)
            await interaction.response.send_message(
                f"You already claimed daily. Try again in {hours}h {minutes}m.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"You claimed {fmt_amount(reward)}.",
            ephemeral=True,
        )

    @app_commands.command(name="balance", description="Check a wallet balance.")
    @app_commands.describe(user="User to check. Defaults to you.")
    @app_commands.guild_only()
    async def balance(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        target = user or interaction.user
        balance = await self.bot.db.get_balance(target.id, interaction.guild_id)
        await interaction.response.send_message(
            f"{target.mention} has {fmt_amount(balance)}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="leaderboard", description="Show the richest users.")
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        rows = await self.bot.db.leaderboard(interaction.guild.id)
        if not rows:
            await interaction.response.send_message("No wallets yet.")
            return

        lines = []
        for index, row in enumerate(rows, start=1):
            member = interaction.guild.get_member(int(row["user_id"]))
            name = member.display_name if member else f"User {row['user_id']}"
            lines.append(f"**{index}.** {name}: {fmt_amount(float(row['wallet']))}")

        await interaction.response.send_message("\n".join(lines))

    @app_commands.command(name="pay", description="Send nuggets to another user.")
    @app_commands.describe(user="User to pay", amount="Amount to send")
    @app_commands.guild_only()
    async def pay(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        amount: float,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if user.bot or user.id == interaction.user.id:
            await interaction.response.send_message("Choose another non-bot user.", ephemeral=True)
            return
        if not valid_amount(amount):
            await interaction.response.send_message("Enter a positive amount.", ephemeral=True)
            return

        paid = await self.bot.db.transfer_wallet(
            interaction.user.id,
            user.id,
            interaction.guild_id,
            amount,
        )
        if not paid:
            await interaction.response.send_message(
                "You do not have enough nuggets.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"{interaction.user.mention} paid {user.mention} {fmt_amount(amount)}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Economy(bot))
