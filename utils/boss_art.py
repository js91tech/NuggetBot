from __future__ import annotations

from pathlib import Path

import discord

import config

ASSETS_ROOT = Path(__file__).resolve().parent.parent / "assets" / "bosses"
FREAKY_NIKKI_ASSETS = ASSETS_ROOT / "freaky_nikki"

# Velvet Vixen tiers share art keyed by variant; TomAss and ZZ's Wrath have dedicated portraits.
VARIANT_ART_FILES: dict[str, str] = {
    "normal": "hannah_normal.png",
    "enraged": "hannah_enraged.png",
    "shadow": "hannah_shadow.png",
    "celestial": "hannah_celestial.png",
    "mythic": "hannah_mythic.png",
    "tomass": "tomass.png",
    "zz_wrath": "zz_wrath.png",
    "freaky_nikki": "freaky_nikki/spawn.gif",
}

FREAKY_NIKKI_MOMENTS: dict[str, str] = {
    "spawn": "spawn.gif",
    "obsessive_stare": "stare.gif",
    "whisper": "whisper.gif",
    "grab": "grab.gif",
    "psyche_twist": "twist.gif",
    "slap": "slap.gif",
    "down": "down.gif",
    "defeat": "defeat.gif",
}

MOVE_TO_MOMENT: dict[str, str] = {
    "obsessive-stares": "obsessive_stare",
    "unhinged-whispers": "whisper",
    "restraining-grabs": "grab",
    "psyche-twists": "psyche_twist",
    "freak-out-slaps": "slap",
}


def moment_for_move(move: str) -> str:
    """Map a counter move verb to a Freaky Nikki art moment key."""
    for fragment, moment in MOVE_TO_MOMENT.items():
        if fragment in move:
            return moment
    return "spawn"


def boss_art_path(variant: str) -> Path | None:
    if variant == "freaky_nikki":
        return boss_moment_art_path(variant, "spawn")
    filename = VARIANT_ART_FILES.get(variant)
    if filename is None:
        return None
    path = ASSETS_ROOT / filename
    return path if path.is_file() else None


def _resolve_moment_file(base_name: str) -> Path | None:
    stem = Path(base_name).stem
    for ext in (".gif", ".png", ".webp"):
        path = FREAKY_NIKKI_ASSETS / f"{stem}{ext}"
        if path.is_file():
            return path
    return None


def boss_moment_art_path(variant: str, moment: str) -> Path | None:
    if variant != "freaky_nikki":
        return boss_art_path(variant)
    filename = FREAKY_NIKKI_MOMENTS.get(moment)
    if filename is None:
        return None
    path = _resolve_moment_file(filename)
    if path is not None:
        return path
    if moment != "spawn":
        return boss_moment_art_path(variant, "spawn")
    return None


def attach_boss_art(embed: discord.Embed, variant: str) -> discord.File | None:
    url = config.FREAKY_NIKKI_ART_URLS.get("spawn") if variant == "freaky_nikki" else None
    path = boss_art_path(variant)
    if path is None and url:
        embed.set_image(url=url)
        return None
    if path is None:
        return None
    filename = path.name
    embed.set_image(url=f"attachment://{filename}")
    return discord.File(str(path), filename=filename)


def attach_boss_moment_art(
    embed: discord.Embed,
    variant: str,
    moment: str,
) -> discord.File | None:
    """Attach moment-specific boss art. Returns None when using a remote URL."""
    if variant != "freaky_nikki":
        return attach_boss_art(embed, variant)

    url = config.FREAKY_NIKKI_ART_URLS.get(moment) or config.FREAKY_NIKKI_ART_URLS.get("spawn")
    path = boss_moment_art_path(variant, moment)
    if path is None and url:
        embed.set_image(url=url)
        return None
    if path is None:
        return None
    filename = path.name
    embed.set_image(url=f"attachment://{filename}")
    return discord.File(str(path), filename=filename)
