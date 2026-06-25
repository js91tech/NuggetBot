from __future__ import annotations

import asyncio
import logging
import random
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from utils.bot_players import pvp_target_error, skip_passive_bot
from utils.helpers import fmt_amount, guild_only_message, resolve_main_channel, valid_amount
from utils.quests import record_quest_event

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
        self.message: discord.Message | None = None

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is None:
            return
        await self.bot.db.credit_house_pot(self.guild_id, self.amount)
        try:
            await self.message.edit(
                content=(
                    f"**Coin drop expired.** Nobody claimed **{fmt_amount(self.amount)}** in "
                    f"{COIN_DROP_CLAIM_SECONDS} seconds — returned to the house."
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
        if not isinstance(interaction.user, discord.Member) or (
            interaction.user.bot and not config.ALLOW_BOT_PLAYERS
        ):
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
        if skip_passive_bot(message.author) or message.guild is None:
            return

        if await self.bot.db.is_restricted(message.author.id, message.guild.id):
            return

        reward = await self.bot.db.get_config_value(message.guild.id, "passive_chat_reward")
        aspect = await self.bot.db.get_equipped_aspect_bonuses(
            message.author.id,
            message.guild.id,
        )
        reward *= aspect.passive_income_mult
        await self.bot.db.record_message_reward(
            message.author.id,
            message.guild.id,
            reward,
        )
        await record_quest_event(
            self.bot.db,
            message.guild.id,
            message.author.id,
            "chat_message",
        )
        if isinstance(message.author, discord.Member):
            from cogs.retention import grant_activity_xp

            await grant_activity_xp(
                self.bot, message.author, message.guild.id, config.ACTIVITY_XP_PER_MESSAGE,
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
            desired = float(random.randint(COIN_DROP_MIN_AMOUNT, COIN_DROP_MAX_AMOUNT))
            amount = await self.bot.db.debit_house_pot(guild.id, desired)
            if amount <= 0:
                continue
            channel = await resolve_main_channel(guild, self.bot.db)
            if channel is None:
                logging.warning("Coin drop: no channel to announce in guild %s", guild.id)
                await self.bot.db.credit_house_pot(guild.id, amount)
                continue
            body = (
                f"**Random coin drop!** **{fmt_amount(amount)}** from the house are up for grabs—"
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
                    if skip_passive_bot(member) or await self.bot.db.is_restricted(member.id, guild.id):
                        continue
                    await self.bot.db.credit_wallet(member.id, guild.id, reward)
                    from cogs.retention import grant_activity_xp

                    await grant_activity_xp(
                        self.bot, member, guild.id, config.ACTIVITY_XP_PER_VC_TICK,
                    )

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
        base_reward = await self.bot.db.get_config_value(
            interaction.guild_id,
            "daily_reward",
        )
        aspect = await self.bot.db.get_equipped_aspect_bonuses(
            interaction.user.id,
            interaction.guild_id,
        )
        reward = base_reward * aspect.daily_reward_mult
        remaining = await self.bot.db.claim_daily(
            interaction.user.id,
            interaction.guild_id,
            reward,
            config.DAILY_COOLDOWN_SECONDS,
            current,
        )
        if remaining.remaining is not None:
            hours = int(remaining.remaining // 3600)
            minutes = int((remaining.remaining % 3600) // 60)
            await interaction.response.send_message(
                f"You already claimed daily. Try again in {hours}h {minutes}m.",
                ephemeral=True,
            )
            return

        bonus_note = ""
        if aspect.daily_reward_mult > 1.0:
            bonus_note = f" (×{aspect.daily_reward_mult:.2f} **Windfall** aspect)"
        streak_note = ""
        if remaining.streak > 1:
            pct = int((remaining.streak_bonus_mult - 1.0) * 100)
            streak_note = f"\n🔥 **{remaining.streak}-day streak** (+{pct}% bonus)"
        await interaction.response.send_message(
            f"You claimed {fmt_amount(remaining.reward)}.{bonus_note}{streak_note}",
            ephemeral=True,
        )
        await record_quest_event(
            self.bot.db,
            interaction.guild_id,
            interaction.user.id,
            "daily_claim",
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
        wallet = await self.bot.db.get_balance(target.id, interaction.guild_id)
        bank = await self.bot.db.get_bank(target.id, interaction.guild_id)

        if target.id == interaction.user.id:
            from utils.wallet_ui import WalletView, build_wallet_embed_for_user

            view = WalletView(self, interaction.guild_id, target.id)
            embed = await build_wallet_embed_for_user(
                self, target, interaction.guild_id, target.id,
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            return

        from utils.wallet_ui import build_wallet_embed_for_user

        embed = await build_wallet_embed_for_user(
            self, target, interaction.guild_id, target.id,
        )
        await interaction.response.send_message(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="deposit", description="Move nuggets from pocket to bank.")
    @app_commands.describe(amount="Amount to deposit (omit with Dep all in /balance panel)")
    @app_commands.guild_only()
    async def deposit(
        self,
        interaction: discord.Interaction,
        amount: float,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if not valid_amount(amount):
            await interaction.response.send_message("Enter a positive amount.", ephemeral=True)
            return
        wallet = await self.bot.db.get_balance(interaction.user.id, interaction.guild_id)
        if wallet < amount:
            await interaction.response.send_message(
                "You do not have enough nuggets in your pocket.", ephemeral=True
            )
            return
        room = await self.bot.db.get_bank_deposit_room(
            interaction.user.id, interaction.guild_id,
        )
        if room <= 0:
            await interaction.response.send_message(
                f"Your bank is full (**{fmt_amount(await self.bot.db.get_bank_capacity(interaction.user.id, interaction.guild_id))}** cap). "
                f"Run **/expand-bank** ({fmt_amount(config.BANK_EXPANSION_TOKEN_COST)} per "
                f"+{fmt_amount(config.BANK_EXPANSION_CAPACITY_PER_TOKEN)}).",
                ephemeral=True,
            )
            return
        ok = await self.bot.db.deposit_to_bank(
            interaction.user.id,
            interaction.guild_id,
            amount,
        )
        if not ok:
            await interaction.response.send_message(
                "Could not deposit — check pocket balance and bank capacity.", ephemeral=True
            )
            return
        bank = await self.bot.db.get_bank(interaction.user.id, interaction.guild_id)
        capacity = await self.bot.db.get_bank_capacity(interaction.user.id, interaction.guild_id)
        await interaction.response.send_message(
            f"Deposited into your bank. Balance **{fmt_amount(bank)}** / **{fmt_amount(capacity)}**.",
            ephemeral=True,
        )

    @app_commands.command(
        name="expand-bank",
        description="Buy a vault expansion (+bank capacity) from your pocket.",
    )
    @app_commands.guild_only()
    async def expand_bank(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        ok, reason = await self.bot.db.expand_bank_capacity(
            interaction.user.id, interaction.guild_id,
        )
        if not ok:
            if reason == "insufficient_wallet":
                await interaction.response.send_message(
                    f"You need **{fmt_amount(config.BANK_EXPANSION_TOKEN_COST)}** in your pocket.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message("Could not expand vault.", ephemeral=True)
            return
        capacity = await self.bot.db.get_bank_capacity(interaction.user.id, interaction.guild_id)
        expansions = await self.bot.db.get_bank_expansions(interaction.user.id, interaction.guild_id)
        await interaction.response.send_message(
            f"Vault expanded! **{expansions}** token(s) · capacity **{fmt_amount(capacity)}**.",
            ephemeral=True,
        )

    @app_commands.command(name="withdraw", description="Move nuggets from bank to pocket.")
    @app_commands.describe(amount="Amount to withdraw")
    @app_commands.guild_only()
    async def withdraw(
        self,
        interaction: discord.Interaction,
        amount: float,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if not valid_amount(amount):
            await interaction.response.send_message("Enter a positive amount.", ephemeral=True)
            return
        ok = await self.bot.db.withdraw_from_bank(
            interaction.user.id,
            interaction.guild_id,
            amount,
        )
        if not ok:
            await interaction.response.send_message(
                "You do not have enough nuggets in your bank.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Withdrew **{fmt_amount(amount)}** to your pocket.",
            ephemeral=True,
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
            net = float(row["net"])
            lines.append(f"**{index}.** {name}: {fmt_amount(net)}")

        embed = discord.Embed(
            title="Richest players",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(text="Ranked by pocket + bank (net worth)")
        await interaction.response.send_message(embed=embed)

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
        pay_err = pvp_target_error(user, interaction.user.id)
        if pay_err:
            await interaction.response.send_message(pay_err, ephemeral=True)
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
        await record_quest_event(
            self.bot.db,
            interaction.guild_id,
            interaction.user.id,
            "wallet_pay",
        )


    @app_commands.command(
        name="bodyguards",
        description="Hire bodyguards to defend your bank against heists.",
    )
    @app_commands.guild_only()
    async def bodyguards(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        from utils.bodyguard_ui import send_bodyguard_panel

        await send_bodyguard_panel(interaction, self)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Economy(bot))
