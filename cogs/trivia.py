from __future__ import annotations

import random
import time
from datetime import UTC, datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.bot_players import skip_gameplay_bot
from utils.helpers import fmt_amount, guild_only_message


class Trivia(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_rounds: dict[int, tuple[str, float]] = {}

    @app_commands.command(name="trivia", description="Start a Lore Roulette trivia round.")
    @app_commands.guild_only()
    async def trivia(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.channel_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        active = self.active_rounds.get(interaction.channel_id)
        if active is not None and active[1] > time.time():
            await interaction.response.send_message("A trivia round is already active here.", ephemeral=True)
            return

        await interaction.response.defer()
        puzzle = await self._make_puzzle(interaction.guild)
        if puzzle is None:
            await interaction.followup.send("I could not find a suitable recent message for trivia.")
            return

        prompt, answer = puzzle
        self.active_rounds[interaction.channel_id] = (
            answer.lower(),
            time.time() + config.TRIVIA_SECONDS,
        )
        await interaction.followup.send(
            f"Guess the missing word within {config.TRIVIA_SECONDS} seconds:\n\n> {prompt}"
        )

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
        reward = await self.bot.db.get_config_value(message.guild.id, "trivia_reward")
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
