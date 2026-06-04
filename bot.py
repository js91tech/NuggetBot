from __future__ import annotations

import asyncio
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

import config
from dashboard import DashboardServer
from database import Database
from launch_jobs import run_launch_grant
from utils.discord_api import OutboundGate, safe_interaction_send

COGS = (
    "cogs.economy",
    "cogs.status",
    "cogs.bounty",
    "cogs.heist",
    "cogs.hacker",
    "cogs.scourge",
    "cogs.boss",
    "cogs.shop",
    "cogs.aspects",
    "cogs.progression",
    "cogs.imposter",
    "cogs.trivia",
    "cogs.gambling",
    "cogs.quests",
    "cogs.duels",
    "cogs.jobs",
    "cogs.classes",
    "cogs.spells",
    "cogs.avatars",
    "cogs.help",
    "cogs.profile",
    "cogs.loadout",
    "cogs.consumables",
    "cogs.crews",
    "cogs.territories",
    "cogs.dungeon",
    "cogs.season",
    "cogs.alchemy",
    "cogs.admin",
)


class NuggetBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.voice_states = True

        super().__init__(
            command_prefix=commands.when_mentioned_or("!"),
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.db = Database(config.DATABASE_PATH)
        self.dashboard = DashboardServer(self)
        self.outbound_gate = OutboundGate(config.DISCORD_OUTBOUND_MIN_INTERVAL_SEC)
        self._launch_jobs_started = False

    async def setup_hook(self) -> None:
        await self.db.connect()
        logging.info("Database backend: %s", "postgres" if self.db.is_postgres else f"sqlite:{self.db.path}")
        for extension in COGS:
            try:
                await self.load_extension(extension)
            except Exception:
                logging.exception("Failed to load extension %s", extension)

        if config.GUILD_ID is not None:
            guild = discord.Object(id=config.GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logging.info("Synced slash commands to guild %s", config.GUILD_ID)
        else:
            await self.tree.sync()
            logging.info("Synced global slash commands")
        await self.dashboard.start()

    async def close(self) -> None:
        await self.dashboard.close()
        await self.db.close()
        await super().close()

    async def on_ready(self) -> None:
        assert self.user is not None
        logging.info("Logged in as %s (%s)", self.user, self.user.id)
        if not self._launch_jobs_started:
            self._launch_jobs_started = True
            asyncio.create_task(run_launch_grant(self))

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "You do not have permission to use this command."
        elif isinstance(error, app_commands.NoPrivateMessage):
            message = "This command can only be used inside a server."
        else:
            original = getattr(error, "original", error)
            if isinstance(original, discord.HTTPException) and original.status == 429:
                message = (
                    "Discord is temporarily rate-limiting this bot. "
                    "Please wait a minute and try again."
                )
            else:
                logging.error(
                    "Unhandled app command error",
                    exc_info=(type(error), error, error.__traceback__),
                )
                message = "Something went wrong while running that command."

        await safe_interaction_send(
            interaction,
            message,
            ephemeral=True,
            gate=self.outbound_gate,
        )


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        msg = "DISCORD_TOKEN must be set in the environment"
        raise RuntimeError(msg)

    backoff = config.DISCORD_LOGIN_BACKOFF_SECONDS
    attempt = 0
    while True:
        try:
            async with NuggetBot() as bot:
                await bot.start(token)
            return
        except discord.HTTPException as exc:
            if exc.status != 429:
                raise
            delay = backoff[min(attempt, len(backoff) - 1)]
            attempt += 1
            logging.warning(
                "Discord global rate limit during login; waiting %ss before retry (attempt %s)",
                delay,
                attempt,
            )
            await asyncio.sleep(delay)


if __name__ == "__main__":
    asyncio.run(main())
