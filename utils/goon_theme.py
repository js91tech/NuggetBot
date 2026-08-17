"""GoonBot visual design system — charcoal, velvet crimson, warm gold."""
from __future__ import annotations

import discord

BOT_DISPLAY_NAME = "GoonBot"
BOT_TAGLINE = "Adult economy RPG — 18+ only"
FOOTER_BRAND = "GoonBot · 18+ · NSFW channels preferred"

# Charcoal / velvet crimson / warm gold (not purple-indigo glow)
COLOR_PRIMARY = 0x8B1E3F  # velvet crimson
COLOR_ACCENT = 0xC9A227  # warm gold
COLOR_DARK = 0x1A1218  # deep charcoal
COLOR_SUCCESS = 0x2F6B4F
COLOR_DANGER = 0xA33B3B
COLOR_INFO = 0x3D4A5C


def brand_color() -> discord.Color:
    return discord.Color(COLOR_PRIMARY)


def accent_color() -> discord.Color:
    return discord.Color(COLOR_ACCENT)


def dark_color() -> discord.Color:
    return discord.Color(COLOR_DARK)


def success_color() -> discord.Color:
    return discord.Color(COLOR_SUCCESS)


def danger_color() -> discord.Color:
    return discord.Color(COLOR_DANGER)


def info_color() -> discord.Color:
    return discord.Color(COLOR_INFO)


def branded_embed(
    title: str,
    *,
    description: str | None = None,
    color: discord.Color | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=color or brand_color(),
    )
    embed.set_footer(text=FOOTER_BRAND)
    return embed


def panel_title(hub: str, *, member_name: str | None = None) -> str:
    if member_name:
        return f"💋 {hub} — {member_name}"
    return f"💋 {hub}"
