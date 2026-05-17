from __future__ import annotations

import math
import re
import time
from collections.abc import Iterable

import discord

import config

WORD_RE = re.compile(r"[A-Za-z0-9_']+")


def now() -> float:
    return time.time()


def fmt_amount(amount: float) -> str:
    """Format nugget amounts for display.

    Positive fractional balances use one decimal place **rounded down** toward zero
    so we never show ``250.0`` when the float is still slightly below ``250`` (which
    would make ``/buy`` look broken next to ``/balance``).
    """
    if not math.isfinite(amount):
        return f"0 {config.CURRENCY_EMOJI}"
    if amount == int(amount):
        value = f"{int(amount):,}"
    elif amount > 0:
        floored_tenth = math.floor(amount * 10) / 10
        value = (
            f"{int(floored_tenth):,}"
            if floored_tenth == int(floored_tenth)
            else f"{floored_tenth:,.1f}"
        )
    else:
        value = f"{amount:,.1f}"
    return f"{value} {config.CURRENCY_EMOJI}"


def valid_amount(amount: float, *, minimum: float = 0.01) -> bool:
    return math.isfinite(amount) and amount >= minimum


def normalize_trigger_word(word: str) -> str | None:
    cleaned = word.strip().lower()
    if not cleaned or len(cleaned) > config.BOUNTY_TRIGGER_MAX_LENGTH:
        return None
    if not re.fullmatch(r"[a-z0-9_'-]+", cleaned):
        return None
    return cleaned


def message_words(content: str) -> list[str]:
    return [match.group(0).lower() for match in WORD_RE.finditer(content)]


def contains_word(content: str, word: str) -> bool:
    return word.lower() in message_words(content)


def member_display(member: discord.abc.User) -> str:
    return getattr(member, "display_name", member.name)


def is_admin(interaction: discord.Interaction) -> bool:
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(permissions and permissions.administrator)


def unique_member_ids(members: Iterable[discord.Member]) -> set[int]:
    return {member.id for member in members}


async def send_error(interaction: discord.Interaction, message: str) -> None:
    from utils.discord_api import safe_interaction_send

    gate = getattr(getattr(interaction, "client", None), "outbound_gate", None)
    await safe_interaction_send(interaction, message, ephemeral=True, gate=gate)


def guild_only_message() -> str:
    return "This command can only be used inside a server."


async def _resolve_channel_by_id(
    guild: discord.Guild,
    channel_id: int | None,
) -> discord.abc.Messageable | None:
    if channel_id is None:
        return None
    channel = guild.get_channel(channel_id)
    if isinstance(channel, discord.TextChannel) and _can_send(guild, channel):
        return channel
    return None


async def resolve_main_channel(
    guild: discord.Guild,
    db: object,
) -> discord.abc.Messageable | None:
    """Return the guild main channel (coin drops / random gifts), with legacy fallbacks."""
    get_main_channel_id = getattr(db, "get_main_channel_id", None)
    if get_main_channel_id is not None:
        channel_id = await get_main_channel_id(guild.id)
        resolved = await _resolve_channel_by_id(guild, channel_id)
        if resolved is not None:
            return resolved

    return _fallback_announcement_channel(guild)


async def resolve_designated_channel(
    guild: discord.Guild,
    db: object,
) -> discord.abc.Messageable | None:
    """Return the guild designated bot channel, with main channel and legacy fallbacks."""
    get_designated = getattr(db, "get_designated_channel_id", None)
    if get_designated is not None:
        channel_id = await get_designated(guild.id)
        resolved = await _resolve_channel_by_id(guild, channel_id)
        if resolved is not None:
            return resolved

    return await resolve_main_channel(guild, db)


async def resolve_bot_announcement_channel(
    guild: discord.Guild,
    db: object,
) -> discord.abc.Messageable | None:
    """Boss and bot announcements: designated when split mode is on, else main."""
    get_split = getattr(db, "get_split_announcement_channels", None)
    if get_split is not None and await get_split(guild.id):
        return await resolve_designated_channel(guild, db)
    return await resolve_main_channel(guild, db)


def _can_send(guild: discord.Guild, channel: discord.abc.GuildChannel) -> bool:
    me = guild.me
    if me is None:
        return True
    perms = channel.permissions_for(me)
    return bool(perms.send_messages)


def _fallback_announcement_channel(guild: discord.Guild) -> discord.abc.Messageable | None:
    bot_member = guild.me
    if guild.system_channel is not None and (
        bot_member is None or _can_send(guild, guild.system_channel)
    ):
        return guild.system_channel
    for channel in guild.text_channels:
        if bot_member is None or _can_send(guild, channel):
            return channel
    return None
