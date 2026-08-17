from __future__ import annotations

import random
from urllib.parse import urlparse

import aiohttp
import discord
from discord.ext import commands

import config


class Imposter(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if not isinstance(message.channel, discord.TextChannel):
            return
        if message.attachments or message.stickers:
            return
        if message.content.startswith(("!", "/")):
            return

        words = message.content.split()
        chance = await self.bot.db.get_config_value(message.guild.id, "imposter_chance")
        if len(words) < config.IMPOSTER_MIN_WORDS or random.random() >= chance:
            return

        guild_me = message.guild.me
        if guild_me is None:
            return
        permissions = message.channel.permissions_for(guild_me)
        if not (permissions.manage_messages and permissions.manage_webhooks):
            return

        changed = await self._alter_message(message.content)
        if changed == message.content:
            return

        try:
            await message.delete()
        except discord.HTTPException:
            return

        webhook = await self._get_webhook(message.channel)
        await webhook.send(
            changed,
            username=message.author.display_name,
            avatar_url=message.author.display_avatar.url,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _alter_message(self, content: str) -> str:
        words = content.split()
        replaceable = [index for index, word in enumerate(words) if len(word.strip(".,!?;:")) >= 3]
        if not replaceable:
            return content

        index = random.choice(replaceable)
        replacement = await self._ai_replacement(words[index].strip(".,!?;:"))
        if not replacement:
            replacement = "goon"

        replacement = replacement.split()[0].strip("`\"'.,!?;:")[:32]
        if not replacement:
            replacement = "goon"

        words[index] = self._preserve_edge_punctuation(words[index], replacement)
        return " ".join(words)

    @staticmethod
    def _preserve_edge_punctuation(original: str, replacement: str) -> str:
        prefix = ""
        suffix = ""
        while original and not original[0].isalnum():
            prefix += original[0]
            original = original[1:]
        while original and not original[-1].isalnum():
            suffix = original[-1] + suffix
            original = original[:-1]
        return f"{prefix}{replacement}{suffix}"

    async def _ai_replacement(self, word: str) -> str | None:
        if not config.AI_API_KEY:
            return None

        parsed = urlparse(config.AI_API_URL)
        localhost = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        if parsed.scheme != "https" and not localhost:
            return None

        payload = {
            "model": config.AI_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": "Return exactly one harmless replacement word. No punctuation.",
                },
                {"role": "user", "content": f"Replace this word with one similar silly word: {word}"},
            ],
            "temperature": 0.8,
            "max_tokens": 4,
        }
        headers = {"Authorization": f"Bearer {config.AI_API_KEY}"}
        timeout = aiohttp.ClientTimeout(total=config.AI_TIMEOUT_SECONDS)
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.post(config.AI_API_URL, json=payload, headers=headers) as response,
            ):
                if response.status >= 400:
                    return None
                data = await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError):
            return None

        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError):
            return None

    async def _get_webhook(self, channel: discord.TextChannel) -> discord.Webhook:
        for webhook in await channel.webhooks():
            if webhook.user == self.bot.user and webhook.name == "GoonBot Imposter":
                return webhook
        return await channel.create_webhook(name="GoonBot Imposter")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Imposter(bot))
