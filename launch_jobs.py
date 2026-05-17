from __future__ import annotations

import logging

import discord
from discord.ext import commands

import config
from utils.helpers import resolve_bot_announcement_channel


async def run_launch_grant(bot: commands.Bot) -> None:
    if not config.LAUNCH_GRANT_ENABLED:
        return
    if await bot.db.is_one_time_job_complete(config.LAUNCH_GRANT_JOB_ID):
        return

    guild = bot.get_guild(config.LAUNCH_GRANT_GUILD_ID)
    if guild is None:
        try:
            guild = await bot.fetch_guild(config.LAUNCH_GRANT_GUILD_ID)
        except discord.HTTPException:
            logging.warning("Launch grant guild %s was not found", config.LAUNCH_GRANT_GUILD_ID)
            return

    members = await _human_members(guild)
    if not members:
        logging.warning("Launch grant found no human members for guild %s", guild.id)
        return

    granted = 0
    for member in members:
        if await bot.db.grant_launch_member_once(
            config.LAUNCH_GRANT_JOB_ID,
            guild.id,
            member.id,
            config.LAUNCH_GRANT_AMOUNT,
            config.LAUNCH_GRANT_WEAPON_ID,
            config.LAUNCH_GRANT_ARMOR_ID,
        ):
            granted += 1

    await bot.db.clear_boss(guild.id)
    await bot.db.replace_boss(guild.id, "Hannah", "normal", 500.0)
    await bot.db.mark_one_time_job_complete(config.LAUNCH_GRANT_JOB_ID)
    config.LAUNCH_GRANT_ENABLED = False

    channel = await resolve_bot_announcement_channel(guild, bot.db)
    if channel is not None:
        try:
            await channel.send(
                "Launch gift delivered! Every human member received "
                f"{int(config.LAUNCH_GRANT_AMOUNT)} nuggets, a Training Stick, and a Cardboard Shield. "
                "Any old boss was cleared and a normal 500 HP Hannah has spawned.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logging.warning("Launch grant completed but announcement failed in guild %s", guild.id)
    logging.info("Launch grant completed for %s members in guild %s", granted, guild.id)


async def _human_members(guild: discord.Guild) -> list[discord.Member]:
    try:
        members = [member async for member in guild.fetch_members(limit=None)]
    except discord.HTTPException:
        members = list(guild.members)
    return [member for member in members if not member.bot]


