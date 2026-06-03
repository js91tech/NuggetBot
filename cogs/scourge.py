from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from utils.bot_players import bot_players_enabled, pvp_target_error
from utils.discord_api import safe_channel_send
from utils.helpers import fmt_amount, guild_only_message, resolve_bot_announcement_channel
from utils.scourge_media import (
    attach_local_warning_gif,
    scourge_warning_embed,
    scourge_warning_files,
)
from utils.stats import hp_bar

if TYPE_CHECKING:
    from database import Database


class Scourge(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._timers: dict[int, asyncio.Task[None]] = {}
        self.scourge_tick.start()

    def cog_unload(self) -> None:
        self.scourge_tick.cancel()
        for task in self._timers.values():
            task.cancel()

    @staticmethod
    def _roll_penalty() -> float:
        return float(
            random.randint(
                int(config.SCOURGE_BANK_PENALTY_MIN),
                int(config.SCOURGE_BANK_PENALTY_MAX),
            ),
        )

    @staticmethod
    def _schedule_next_hourly(now: float) -> float:
        lo, hi = config.SCOURGE_HOURLY_JITTER_SECONDS
        return now + random.randint(lo, hi)


    async def _resolve_scourge_channel(
        self,
        guild: discord.Guild,
        *,
        stored_channel_id: int | None = None,
    ) -> discord.abc.Messageable | None:
        if stored_channel_id is not None:
            existing = guild.get_channel(int(stored_channel_id))
            if isinstance(existing, discord.abc.Messageable):
                return existing
        return await resolve_bot_announcement_channel(guild, self.bot.db)

    def _replace_timer(self, guild_id: int, channel_id: int) -> None:
        old = self._timers.pop(guild_id, None)
        if old is not None:
            old.cancel()
        self._timers[guild_id] = asyncio.create_task(
            self._scourge_timer(guild_id, channel_id),
        )


    async def _shutdown_scourge(self, guild_id: int) -> None:
        old = self._timers.pop(guild_id, None)
        if old is not None:
            old.cancel()
        await self.bot.db.clear_scourge_pot(guild_id)
        await self.bot.db.clear_scourge_event(guild_id)

    async def _scourge_timer(self, guild_id: int, channel_id: int) -> None:
        try:
            pot = await self.bot.db.get_scourge_pot(guild_id)
            if pot is None:
                return
            await asyncio.sleep(max(0.0, float(pot["expires_at"]) - time.time()))
            await self._detonate(guild_id, channel_id)
        except asyncio.CancelledError:
            raise
        finally:
            if self._timers.get(guild_id) is asyncio.current_task():
                self._timers.pop(guild_id, None)

    @staticmethod
    def _virus_embed(
        *,
        title: str,
        holder: discord.Member | None,
        holder_id: int,
        seconds_left: float,
        timer_seconds: float,
        penalty: float,
        pass_count: int,
        color: discord.Color,
        description: str | None = None,
    ) -> discord.Embed:
        elapsed = max(0.0, timer_seconds - seconds_left)
        bar = hp_bar(elapsed, timer_seconds) if timer_seconds > 0 else "░" * 12
        holder_text = holder.mention if holder is not None else f"<@{holder_id}>"
        embed = discord.Embed(title=title, color=color)
        if description:
            embed.description = description
        embed.add_field(name="Infected", value=holder_text, inline=True)
        embed.add_field(
            name="Timer",
            value=f"`{bar}` **{int(seconds_left)}s** left",
            inline=True,
        )
        embed.add_field(
            name="Bank hit if it pops",
            value=fmt_amount(penalty),
            inline=True,
        )
        if pass_count > 0:
            embed.add_field(name="Passes", value=str(pass_count), inline=True)
        embed.set_footer(
            text=f"Use `/scourge-pass` before time runs out · {config.SCOURGE_VIRUS_NAME}",
        )
        return embed

    async def _detonate(self, guild_id: int, channel_id: int) -> None:
        pot = await self.bot.db.get_scourge_pot(guild_id)
        if pot is None:
            return
        holder_id = int(pot["holder_id"])
        penalty = float(pot["penalty_amount"])
        removed = await self.bot.db.debit_bank_up_to(holder_id, guild_id, penalty)
        await self.bot.db.clear_scourge_pot(guild_id)

        channel = self.bot.get_channel(channel_id)
        if isinstance(channel, discord.abc.Messageable):
            embed = discord.Embed(
                title="Scourge Virus detonated",
                description=(
                    f"<@{holder_id}> lost **{fmt_amount(removed)}** from their bank "
                    f"(rolled cap **{fmt_amount(penalty)}**)."
                ),
                color=discord.Color.dark_red(),
            )
            embed.add_field(
                name="Passes before pop",
                value=str(int(pot["pass_count"])),
                inline=True,
            )
            await safe_channel_send(
                channel,
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def _ensure_event_row(
        self,
        guild: discord.Guild,
        channel_id: int,
    ) -> None:
        row = await self.bot.db.get_scourge_event(guild.id)
        if row is not None:
            return
        now = time.time()
        await self.bot.db.upsert_scourge_event(
            guild.id,
            channel_id,
            phase="idle",
            phase_ends_at=0.0,
            next_hourly_roll_at=self._schedule_next_hourly(now),
        )

    async def _send_warning(self, guild: discord.Guild, channel_id: int) -> None:
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return
        embed = scourge_warning_embed(seconds_until_active=config.SCOURGE_WARNING_SECONDS)
        attach_local_warning_gif(embed)
        files = scourge_warning_files()
        await safe_channel_send(
            channel,
            embed=embed,
            files=files or None,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _send_outbreak_start(self, guild: discord.Guild, channel_id: int) -> None:
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return
        embed = discord.Embed(
            title="☣️ SCOURGE OUTBREAK",
            description=(
                f"**{config.SCOURGE_VIRUS_NAME}** is live for "
                f"**{config.SCOURGE_ACTIVE_SECONDS // 60} minutes** — "
                f"one infection from the top **{config.SCOURGE_TOP_TARGETS}** "
                f"every **{config.SCOURGE_INFECTION_INTERVAL_SECONDS}s**."
            ),
            color=discord.Color.red(),
        )
        await safe_channel_send(
            channel,
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _send_outbreak_end(self, guild: discord.Guild, channel_id: int) -> None:
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return
        embed = discord.Embed(
            title="Scourge contained",
            description="The outbreak has ended. Vaults are safe… for now.",
            color=discord.Color.dark_grey(),
        )
        await safe_channel_send(
            channel,
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _top5_candidate_ids(self, guild_id: int) -> list[int]:
        rows = await self.bot.db.leaderboard(guild_id, limit=config.SCOURGE_TOP_TARGETS)
        return [int(row["user_id"]) for row in rows]

    async def _infect_user(
        self,
        guild: discord.Guild,
        channel_id: int,
        user_id: int,
    ) -> None:
        existing = await self.bot.db.get_scourge_pot(guild.id)
        if existing is not None and float(existing["expires_at"]) > time.time():
            return

        await self.bot.db.ensure_user(user_id, guild.id)
        now = time.time()
        penalty = self._roll_penalty()
        timer = float(config.SCOURGE_PASS_SECONDS)
        await self.bot.db.set_scourge_pot(
            guild.id,
            user_id,
            0,
            now,
            now + timer,
            penalty,
        )
        self._replace_timer(guild.id, channel_id)

        member = guild.get_member(user_id)
        embed = self._virus_embed(
            title="☣️ Scourge infection",
            holder=member,
            holder_id=user_id,
            seconds_left=timer,
            timer_seconds=timer,
            penalty=penalty,
            pass_count=0,
            color=discord.Color.dark_purple(),
            description=(
                f"<@{user_id}> was infected during the **{config.SCOURGE_VIRUS_NAME}** outbreak!"
            ),
        )
        channel = self.bot.get_channel(channel_id)
        if isinstance(channel, discord.abc.Messageable):
            await safe_channel_send(
                channel,
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def _infect_random_top5(self, guild: discord.Guild, channel_id: int) -> None:
        candidates = await self._top5_candidate_ids(guild.id)
        if not candidates:
            return

        guild_members: list[int] = []
        for uid in candidates:
            member = guild.get_member(uid)
            if member is None:
                guild_members.append(uid)
                continue
            if member.bot and not bot_players_enabled():
                continue
            guild_members.append(uid)

        if not guild_members:
            guild_members = candidates

        pot = await self.bot.db.get_scourge_pot(guild.id)
        if pot is not None and float(pot["expires_at"]) > time.time():
            holder_id = int(pot["holder_id"])
            guild_members = [uid for uid in guild_members if uid != holder_id]
        if not guild_members:
            return

        await self._infect_user(guild, channel_id, random.choice(guild_members))

    async def _tick_guild(self, guild: discord.Guild) -> None:
        if not await self.bot.db.get_scourge_event_enabled(guild.id):
            row = await self.bot.db.get_scourge_event(guild.id)
            pot = await self.bot.db.get_scourge_pot(guild.id)
            if row is not None or pot is not None or guild.id in self._timers:
                await self._shutdown_scourge(guild.id)
            return

        row = await self.bot.db.get_scourge_event(guild.id)
        stored_channel_id = int(row["channel_id"]) if row is not None else None
        channel = await self._resolve_scourge_channel(
            guild,
            stored_channel_id=stored_channel_id,
        )
        if channel is None:
            logging.warning(
                "Scourge tick skipped for guild %s: no writable announcement channel",
                guild.id,
            )
            return
        channel_id = getattr(channel, "id", None)
        if channel_id is None:
            return

        await self._ensure_event_row(guild, channel_id)
        row = await self.bot.db.get_scourge_event(guild.id)
        if row is None:
            return

        now = time.time()
        phase = str(row["phase"])
        phase_ends = float(row["phase_ends_at"])
        next_hourly = float(row["next_hourly_roll_at"])
        if next_hourly <= 0:
            next_hourly = now
        infections_done = int(row["infections_done"])
        next_infection = float(row["next_infection_at"])

        if phase == "idle":
            if now < next_hourly:
                return
            if random.random() >= config.SCOURGE_HOURLY_TRIGGER_CHANCE:
                await self.bot.db.upsert_scourge_event(
                    guild.id,
                    channel_id,
                    phase="idle",
                    phase_ends_at=0.0,
                    next_hourly_roll_at=self._schedule_next_hourly(now),
                )
                logging.debug(
                    "Scourge hourly roll skipped for guild %s; next attempt scheduled",
                    guild.id,
                )
                return
            next_fire = self._schedule_next_hourly(now)
            await self.bot.db.upsert_scourge_event(
                guild.id,
                channel_id,
                phase="warning",
                phase_ends_at=now + config.SCOURGE_WARNING_SECONDS,
                next_hourly_roll_at=next_fire,
            )
            logging.info("Scourge warning started for guild %s", guild.id)
            await self._send_warning(guild, channel_id)
            return

        if phase == "warning":
            if now < phase_ends:
                return
            await self.bot.db.upsert_scourge_event(
                guild.id,
                channel_id,
                phase="active",
                phase_ends_at=now + config.SCOURGE_ACTIVE_SECONDS,
                next_hourly_roll_at=self._schedule_next_hourly(now),
                infections_done=0,
                next_infection_at=now,
            )
            await self._send_outbreak_start(guild, channel_id)
            await self._infect_random_top5(guild, channel_id)
            await self.bot.db.upsert_scourge_event(
                guild.id,
                channel_id,
                phase="active",
                phase_ends_at=now + config.SCOURGE_ACTIVE_SECONDS,
                next_hourly_roll_at=self._schedule_next_hourly(now),
                infections_done=1,
                next_infection_at=now + config.SCOURGE_INFECTION_INTERVAL_SECONDS,
            )
            return

        if phase == "active":
            if now >= phase_ends:
                await self.bot.db.upsert_scourge_event(
                    guild.id,
                    channel_id,
                    phase="idle",
                    phase_ends_at=0.0,
                    next_hourly_roll_at=self._schedule_next_hourly(now),
                    infections_done=0,
                    next_infection_at=0.0,
                )
                await self._send_outbreak_end(guild, channel_id)
                return

            if (
                infections_done < config.SCOURGE_INFECTIONS_PER_EVENT
                and now >= next_infection
            ):
                await self._infect_random_top5(guild, channel_id)
                infections_done += 1
                await self.bot.db.upsert_scourge_event(
                    guild.id,
                    channel_id,
                    phase="active",
                    phase_ends_at=phase_ends,
                    next_hourly_roll_at=float(row["next_hourly_roll_at"]),
                    infections_done=infections_done,
                    next_infection_at=now + config.SCOURGE_INFECTION_INTERVAL_SECONDS,
                )

    @tasks.loop(seconds=config.SCOURGE_EVENT_POLL_SECONDS)
    async def scourge_tick(self) -> None:
        pause = config.BACKGROUND_GUILD_PAUSE_SECONDS
        for guild in self.bot.guilds:
            try:
                await self._tick_guild(guild)
            except Exception:
                logging.exception("Scourge tick failed for guild %s", guild.id)
            if pause > 0:
                await asyncio.sleep(pause)

    @scourge_tick.before_loop
    async def before_scourge_tick(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="scourge-pass",
        description="Pass the Scourge Virus to another player (bank penalty if it pops).",
    )
    @app_commands.describe(target="Player to infect")
    @app_commands.guild_only()
    async def scourge_pass(self, interaction: discord.Interaction, target: discord.Member) -> None:
        if interaction.guild_id is None or interaction.channel_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if not await self.bot.db.get_scourge_event_enabled(interaction.guild_id):
            await interaction.response.send_message(
                "The Scourge Virus world event is disabled for this server.",
                ephemeral=True,
            )
            return
        target_err = pvp_target_error(target, interaction.user.id)
        if target_err:
            await interaction.response.send_message(target_err, ephemeral=True)
            return

        pot = await self.bot.db.get_scourge_pot(interaction.guild_id)
        current = time.time()
        if pot is None or float(pot["expires_at"]) <= current:
            if pot is not None:
                await self._detonate(interaction.guild_id, interaction.channel_id)
            await interaction.response.send_message(
                "No active Scourge infection to pass.",
                ephemeral=True,
            )
            return
        if int(pot["holder_id"]) != interaction.user.id:
            await interaction.response.send_message(
                "Only the infected player can pass the Scourge Virus.",
                ephemeral=True,
            )
            return

        next_pass = int(pot["pass_count"]) + 1
        timer = float(config.SCOURGE_PASS_SECONDS)
        penalty = float(pot["penalty_amount"])
        await self.bot.db.set_scourge_pot(
            interaction.guild_id,
            target.id,
            next_pass,
            current,
            current + timer,
            penalty,
        )
        self._replace_timer(interaction.guild_id, interaction.channel_id)
        embed = self._virus_embed(
            title="Scourge passed",
            holder=target,
            holder_id=target.id,
            seconds_left=timer,
            timer_seconds=timer,
            penalty=penalty,
            pass_count=next_pass,
            color=discord.Color.orange(),
            description=f"{interaction.user.mention} passed the infection to {target.mention}.",
        )
        await interaction.response.send_message(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Scourge(bot))
