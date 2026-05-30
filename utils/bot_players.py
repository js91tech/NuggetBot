"""Whether Discord bot accounts can participate in gameplay."""
from __future__ import annotations

import discord

import config


def bot_players_enabled() -> bool:
    return config.ALLOW_BOT_PLAYERS


def is_self_target(actor_id: int, target_id: int) -> bool:
    return actor_id == target_id


def pvp_target_error(target: discord.Member | discord.User, actor_id: int) -> str | None:
    """Return an error string if *target* cannot be used in PvP/economy actions."""
    if is_self_target(actor_id, target.id):
        return "You can't target yourself."
    if target.bot and not bot_players_enabled():
        return "Choose a non-bot player."
    return None


def skip_passive_bot(author: discord.User) -> bool:
    """True when passive income listeners should ignore this author."""
    return author.bot and not config.ALLOW_BOT_PASSIVE_INCOME


def skip_gameplay_bot(author: discord.User) -> bool:
    """True when interactive game listeners should ignore bot messages."""
    return author.bot and not bot_players_enabled()
