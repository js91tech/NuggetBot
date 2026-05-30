from __future__ import annotations

import asyncio
import time

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.bot_players import pvp_target_error
from utils.gear_sets import hack_penalty_multiplier
from utils.helpers import fmt_amount, guild_only_message
from utils.stats import hp_bar


class Hacker(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.timers: dict[int, asyncio.Task[None]] = {}

    def cog_unload(self) -> None:
        for task in self.timers.values():
            task.cancel()

    async def _penalty(self, guild_id: int, pass_count: int, holder_id: int | None = None) -> float:
        base = await self.bot.db.get_config_value(guild_id, "hack_base_penalty")
        increment = await self.bot.db.get_config_value(guild_id, "hack_penalty_increment")
        penalty = base + pass_count * increment
        if holder_id is not None:
            wallet = await self.bot.db.get_balance(holder_id, guild_id)
            penalty *= hack_penalty_multiplier(wallet)
        return penalty

    def _replace_timer(self, guild_id: int, channel_id: int) -> None:
        old_task = self.timers.pop(guild_id, None)
        if old_task is not None:
            old_task.cancel()
        self.timers[guild_id] = asyncio.create_task(self._virus_timer(guild_id, channel_id))

    async def _virus_timer(self, guild_id: int, channel_id: int) -> None:
        try:
            pot = await self.bot.db.get_hacker_pot(guild_id)
            if pot is None:
                return
            await asyncio.sleep(max(0.0, float(pot["expires_at"]) - time.time()))
            await self._detonate(guild_id, channel_id)
        except asyncio.CancelledError:
            raise
        finally:
            if self.timers.get(guild_id) is asyncio.current_task():
                self.timers.pop(guild_id, None)

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
    ) -> discord.Embed:
        elapsed = max(0.0, timer_seconds - seconds_left)
        bar = hp_bar(elapsed, timer_seconds) if timer_seconds > 0 else "░" * 12
        holder_text = holder.mention if holder is not None else f"<@{holder_id}>"
        embed = discord.Embed(title=title, color=color)
        embed.add_field(name="Holder", value=holder_text, inline=True)
        embed.add_field(name="Timer", value=f"`{bar}` **{int(seconds_left)}s** left", inline=True)
        embed.add_field(
            name="Penalty if it pops",
            value=fmt_amount(penalty),
            inline=True,
        )
        if pass_count > 0:
            embed.add_field(name="Passes", value=str(pass_count), inline=True)
        embed.set_footer(text=f"Use /transfer before time runs out · {config.HACK_VIRUS_NAME}")
        return embed

    async def _detonate(self, guild_id: int, channel_id: int) -> None:
        pot = await self.bot.db.get_hacker_pot(guild_id)
        if pot is None:
            return
        holder_id = int(pot["holder_id"])
        penalty = await self._penalty(guild_id, int(pot["pass_count"]), holder_id)
        removed = await self.bot.db.remove_up_to_balance(holder_id, guild_id, penalty)
        await self.bot.db.clear_hacker_pot(guild_id)

        channel = self.bot.get_channel(channel_id)
        if isinstance(channel, discord.abc.Messageable):
            embed = discord.Embed(
                title="Virus detonated",
                description=(
                    f"<@{int(pot['holder_id'])}> took the hit for **{fmt_amount(removed)}**."
                ),
                color=discord.Color.dark_red(),
            )
            embed.add_field(name="Passes before pop", value=str(int(pot["pass_count"])), inline=True)
            await channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @app_commands.command(name="hack", description="Start a hot-potato virus.")
    @app_commands.describe(target="Initial virus holder")
    @app_commands.guild_only()
    async def hack(self, interaction: discord.Interaction, target: discord.Member) -> None:
        if interaction.guild_id is None or interaction.channel_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        target_err = pvp_target_error(target, interaction.user.id)
        if target_err:
            await interaction.response.send_message(target_err, ephemeral=True)
            return

        existing = await self.bot.db.get_hacker_pot(interaction.guild_id)
        if existing is not None and float(existing["expires_at"]) > time.time():
            await interaction.response.send_message("A virus is already active in this server.", ephemeral=True)
            return
        if existing is not None:
            await self.bot.db.clear_hacker_pot(interaction.guild_id)

        current = time.time()
        cooldown_seconds = await self.bot.db.get_config_value(interaction.guild_id, "hack_cooldown_seconds")
        cooldown_remaining = await self.bot.db.claim_hack_start(
            interaction.guild_id,
            interaction.user.id,
            cooldown_seconds,
            current,
        )
        if cooldown_remaining is not None:
            await interaction.response.send_message(
                f"You can use `/hack` again in {int(cooldown_remaining // 60) + 1} minute(s).",
                ephemeral=True,
            )
            return

        timer_seconds = await self.bot.db.get_config_value(interaction.guild_id, "hack_timer_seconds")
        await self.bot.db.set_hacker_pot(
            interaction.guild_id,
            target.id,
            0,
            current,
            current + timer_seconds,
        )
        self._replace_timer(interaction.guild_id, interaction.channel_id)
        penalty = await self._penalty(interaction.guild_id, 0, target.id)
        embed = self._virus_embed(
            title="Virus deployed",
            holder=target,
            holder_id=target.id,
            seconds_left=float(timer_seconds),
            timer_seconds=float(timer_seconds),
            penalty=penalty,
            pass_count=0,
            color=discord.Color.red(),
        )
        await interaction.response.send_message(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="transfer", description="Pass the virus to someone else.")
    @app_commands.describe(target="New virus holder")
    @app_commands.guild_only()
    async def transfer(self, interaction: discord.Interaction, target: discord.Member) -> None:
        if interaction.guild_id is None or interaction.channel_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        target_err = pvp_target_error(target, interaction.user.id)
        if target_err:
            await interaction.response.send_message(target_err, ephemeral=True)
            return

        pot = await self.bot.db.get_hacker_pot(interaction.guild_id)
        current = time.time()
        if pot is None or float(pot["expires_at"]) <= current:
            if pot is not None:
                await self._detonate(interaction.guild_id, interaction.channel_id)
            await interaction.response.send_message("No active virus is transferable.", ephemeral=True)
            return
        if int(pot["holder_id"]) != interaction.user.id:
            await interaction.response.send_message("Only the current holder can transfer the virus.", ephemeral=True)
            return

        next_pass_count = int(pot["pass_count"]) + 1
        timer_seconds = await self.bot.db.get_config_value(interaction.guild_id, "hack_timer_seconds")
        await self.bot.db.set_hacker_pot(
            interaction.guild_id,
            target.id,
            next_pass_count,
            current,
            current + timer_seconds,
        )
        self._replace_timer(interaction.guild_id, interaction.channel_id)
        penalty = await self._penalty(interaction.guild_id, next_pass_count, target.id)
        seconds_left = float(pot["expires_at"]) - current
        seconds_left = min(float(timer_seconds), max(0.0, seconds_left))
        embed = self._virus_embed(
            title="Virus transferred",
            holder=target,
            holder_id=target.id,
            seconds_left=float(timer_seconds),
            timer_seconds=float(timer_seconds),
            penalty=penalty,
            pass_count=next_pass_count,
            color=discord.Color.orange(),
        )
        embed.description = f"{interaction.user.mention} passed the hot potato to {target.mention}."
        await interaction.response.send_message(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Hacker(bot))
