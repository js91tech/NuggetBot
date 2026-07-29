from __future__ import annotations

import logging
import random
import time
from datetime import UTC, datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from utils.bot_players import skip_gameplay_bot
from utils.helpers import fmt_amount, guild_only_message, resolve_main_channel


class Trivia(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_rounds: dict[int, tuple[str, float]] = {}
        self.trivia_event_tick.start()

    def cog_unload(self) -> None:
        self.trivia_event_tick.cancel()

    @app_commands.command(name="trivia", description="Start a Lore Roulette trivia round.")
    @app_commands.guild_only()
    async def trivia(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.channel_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        if self._channel_has_active_round(interaction.channel_id):
            await interaction.response.send_message("A trivia round is already active here.", ephemeral=True)
            return

        await interaction.response.defer()
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send("Trivia can only run in a text channel.")
            return

        started = await self._start_round(interaction.guild, channel)
        if not started:
            await interaction.followup.send("I could not find a suitable recent message for trivia.")

    @tasks.loop(hours=config.TRIVIA_EVENT_INTERVAL_HOURS)
    async def trivia_event_tick(self) -> None:
        for guild in self.bot.guilds:
            channel = await resolve_main_channel(guild, self.bot.db)
            if channel is None:
                logging.warning("Trivia event: no channel to announce in guild %s", guild.id)
                continue
            if self._channel_has_active_round(channel.id):
                continue
            try:
                await self._start_round(guild, channel, announce_prefix=True)
            except discord.HTTPException:
                logging.exception("Trivia event: failed to start in guild %s", guild.id)

    @trivia_event_tick.before_loop
    async def before_trivia_event_tick(self) -> None:
        await self.bot.wait_until_ready()

    def _channel_has_active_round(self, channel_id: int) -> bool:
        active = self.active_rounds.get(channel_id)
        return active is not None and active[1] > time.time()

    async def _start_round(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        *,
        announce_prefix: bool = False,
    ) -> bool:
        puzzle = await self._make_puzzle(guild)
        if puzzle is None:
            return False

        prompt, answer = puzzle
        self.active_rounds[channel.id] = (
            answer.lower(),
            time.time() + config.TRIVIA_SECONDS,
        )
        header = "**Random Lore Roulette!** " if announce_prefix else ""
        await channel.send(
            f"{header}Guess the missing word within {config.TRIVIA_SECONDS} seconds:\n\n> {prompt}"
        )
        return True

    async def _make_puzzle(self, guild: discord.Guild) -> tuple[str, str] | None:
        if guild.me is None:
            return None

        after = datetime.now(UTC) - timedelta(days=config.TRIVIA_HISTORY_DAYS)
        candidates: list[str] = []
        channels = list(guild.text_channels)
        random.shuffle(channels)

        for channel in channels[: config.TRIVIA_MAX_CHANNELS]:
            permissions = channel.permissions_for(guild.me)
            if not (permissions.read_message_history and permissions.view_channel):
                continue
            try:
                async for message in channel.history(
                    limit=config.TRIVIA_MESSAGES_PER_CHANNEL,
                    after=after,
                    oldest_first=False,
                ):
                    if message.author.bot or not message.content:
                        continue
                    if "http://" in message.content or "https://" in message.content:
                        continue
                    if "@" in message.content or "#" in message.content:
                        continue
                    if len(message.content.split()) >= 4:
                        candidates.append(message.content)
            except discord.HTTPException:
                continue

        if not candidates:
            return None

        for content in random.sample(candidates, k=len(candidates)):
            words = content.split()
            choices = [
                index
                for index, word in enumerate(words)
                if len(word.strip(".,!?;:()[]{}\"'")) >= 3 and word.strip(".,!?;:()[]{}\"'").isalnum()
            ]
            if not choices:
                continue
            index = random.choice(choices)
            answer = words[index].strip(".,!?;:()[]{}\"'")
            words[index] = "_____"
            return " ".join(words), answer

        return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if skip_gameplay_bot(message.author) or message.guild is None:
            return

        active = self.active_rounds.get(message.channel.id)
        if active is None:
            return

        answer, expires_at = active
        if expires_at <= time.time():
            self.active_rounds.pop(message.channel.id, None)
            return

        if message.content.strip().lower() != answer:
            return

        self.active_rounds.pop(message.channel.id, None)
        base_reward = await self.bot.db.get_config_value(message.guild.id, "trivia_reward")
        house_pot = await self.bot.db.get_house_pot(message.guild.id)
        reward = base_reward + house_pot * config.TRIVIA_HOUSE_POOL_SHARE
        mult = await self.bot.db.get_income_multiplier(message.author.id, message.guild.id)
        paid = reward * mult
        await self.bot.db.credit_wallet(
            message.author.id,
            message.guild.id,
            reward,
            apply_bonuses=True,
        )
        await message.channel.send(
            f"{message.author.mention} got it! The answer was `{answer}`. "
            f"Prize: {fmt_amount(paid)}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Trivia(bot))
