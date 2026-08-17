from __future__ import annotations

import random
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from utils.expeditions import (
    EXPEDITION_TEMPLATES,
    expedition_progress_pct,
)
from utils.expansion_events import record_expansion_event
from utils.helpers import fmt_amount, guild_only_message
from utils.meta_hub_ui import send_expeditions_hub
from utils.relics import EXPEDITION_RELIC_DROP


class Expeditions(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.expedition_tick.start()

    def cog_unload(self) -> None:
        self.expedition_tick.cancel()

    @tasks.loop(hours=1)
    async def expedition_tick(self) -> None:
        if not config.EXPEDITION_AUTO_SPAWN:
            return
        for guild in self.bot.guilds:
            gid = guild.id
            active = await self.bot.db.get_active_expedition(gid)
            if active is not None:
                template = next(
                    (t for t in EXPEDITION_TEMPLATES if t.expedition_id == str(active["expedition_id"])),
                    None,
                )
                if template and expedition_progress_pct(
                    int(active["contributed_scrap"]),
                    float(active["contributed_nuggets"]),
                    template,
                ) >= 100.0:
                    contributors = await self.bot.db.complete_expedition(gid)
                    season, _ = await self.bot.db.get_elo_season(gid)
                    for uid in contributors:
                        await self.bot.db.add_season_tokens(
                            uid, gid, config.EXPEDITION_CONTRIBUTOR_TOKEN_REWARD, season,
                        )
                        if random.random() < 0.15:
                            await self.bot.db.create_relic_instance(uid, gid, EXPEDITION_RELIC_DROP)
                continue
            users = await self.bot.db.list_guild_user_ids(gid)
            if len(users) < config.EXPEDITION_MIN_ACTIVE_PLAYERS:
                continue
            template = random.choice(EXPEDITION_TEMPLATES)
            ends = time.time() + template.duration_hours * 3600
            await self.bot.db.start_expedition(gid, template.expedition_id, ends)

    @expedition_tick.before_loop
    async def before_expedition_tick(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="expedition", description="Server-wide cooperative expedition.")
    @app_commands.describe(
        action="Status or contribute",
        scrap="Scrap to contribute",
        nuggets="Nuggets to contribute",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Status", value="status"),
            app_commands.Choice(name="Contribute", value="contribute"),
        ],
    )
    @app_commands.guild_only()
    async def expedition(
        self,
        interaction: discord.Interaction,
        action: str,
        scrap: int | None = None,
        nuggets: float | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        gid = interaction.guild_id
        if action == "status":
            await send_expeditions_hub(interaction, self)
            return
        active = await self.bot.db.get_active_expedition(gid)
        if active is None:
            await interaction.response.send_message(
                "No active expedition. One spawns automatically when the server is active.",
                ephemeral=True,
            )
            return
        template = next(
            (t for t in EXPEDITION_TEMPLATES if t.expedition_id == str(active["expedition_id"])),
            EXPEDITION_TEMPLATES[0],
        )
        if action == "contribute":
            s = max(0, scrap or 0)
            n = max(0.0, nuggets or 0.0)
            if s <= 0 and n <= 0:
                await interaction.response.send_message(
                    "Provide scrap and/or nuggets to contribute.", ephemeral=True,
                )
                return
            result = await self.bot.db.contribute_expedition(gid, interaction.user.id, scrap=s, nuggets=n)
            if result is None:
                await interaction.response.send_message(
                    "Contribution failed — check scrap/nugget balance.", ephemeral=True,
                )
                return
            await record_expansion_event(
                self.bot.db, gid, interaction.user.id, "expedition_contribute",
            )
            await interaction.response.send_message(
                f"Contributed **{s}** scrap and **{fmt_amount(n)}** to **{template.name}**!",
                ephemeral=True,
            )
            return
        await interaction.response.send_message("Unknown action.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Expeditions(bot))
