#!/usr/bin/env python3
"""Generate docs/NuggetBot_How_To_Play.png — run from repo root."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "NuggetBot_How_To_Play.png"

W, H = 1536, 1280
BG = (12, 16, 28)
GOLD = (243, 189, 77)
PINK = (255, 111, 174)
CYAN = (105, 227, 255)
GREEN = (120, 255, 160)
RED = (255, 120, 120)
PURPLE = (180, 140, 255)
WHITE = (246, 247, 251)
MUTED = (170, 179, 200)
CARD_BG = (22, 30, 48)
CARD_LINE = (255, 255, 255, 28)


def _fonts() -> tuple[ImageFont.FreeTypeFont, ImageFont.FreeTypeFont, ImageFont.FreeTypeFont, ImageFont.FreeTypeFont]:
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    bold_path = paths[0] if Path(paths[0]).exists() else None
    reg_path = paths[1] if Path(paths[1]).exists() else None
    if bold_path and reg_path:
        return (
            ImageFont.truetype(bold_path, 36),
            ImageFont.truetype(bold_path, 22),
            ImageFont.truetype(reg_path, 17),
            ImageFont.truetype(reg_path, 14),
        )
    default = ImageFont.load_default()
    return default, default, default, default


def _rounded_rect(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    radius: int,
    fill: tuple[int, ...],
    outline: tuple[int, ...] | None = None,
) -> None:
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=2)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def _card(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    title: str,
    accent: tuple[int, int, int],
    bullets: list[str],
    fonts: tuple,
) -> None:
    title_font, _, body_font, _ = fonts
    border = tuple(int((a + b) / 2) for a, b in zip(accent, (40, 50, 70), strict=True))
    _rounded_rect(draw, box, 20, CARD_BG, outline=border)
    x0, y0, x1, _ = box
    draw.text((x0 + 20, y0 + 16), title, fill=accent, font=title_font)
    y = y0 + 52
    inner_w = (x1 - x0) - 40
    for bullet in bullets:
        for line in _wrap(draw, bullet, body_font, inner_w - 14):
            draw.text((x0 + 28, y), f"• {line}", fill=WHITE, font=body_font)
            y += 22
        y += 4


def build() -> None:
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    fonts = _fonts()
    hero_font, section_font, body_font, small_font = fonts

    # Header
    draw.text((W // 2, 48), "NUGGETBOT", fill=GOLD, font=hero_font, anchor="mm")
    draw.text(
        (W // 2, 88),
        "How to Play  ·  Classes, Mana & Skills",
        fill=MUTED,
        font=section_font,
        anchor="mm",
    )
    badge_box = (W - 200, 24, W - 24, 64)
    _rounded_rect(draw, badge_box, 14, (40, 32, 18), outline=GOLD)
    draw.text((badge_box[0] + 88, 44), "NEW", fill=GOLD, font=section_font, anchor="mm")

    margin = 40
    gap = 20
    cols = 4
    card_w = (W - 2 * margin - (cols - 1) * gap) // cols
    card_h = 380
    y0 = 120

    cards = [
        (
            "Earn Nuggets",
            GOLD,
            [
                "/daily — 24h claim",
                "/jobs · /work — 4.5× payouts",
                "/class choose · /class evolve",
                "/quests — onboarding & daily goals",
            ],
        ),
        (
            "Gear Up",
            PINK,
            [
                "/shop · /buy · /equip",
                "Apex / Sovereign / Transcendent sets",
                "Match sets for +5% dmg",
                "/craft battle-worn drops",
            ],
        ),
        (
            "Boss Raids",
            GREEN,
            [
                "/boss · /attack · /heal",
                "Elements & TomAss raids",
                "Phases at 75/50/25% HP",
                "/raid-leaderboard",
            ],
        ),
        (
            "Crime & Virus",
            RED,
            [
                "/heist · /bounty · /arrest",
                "/hack · /transfer",
                "Hot potato — pass it fast!",
            ],
        ),
        (
            "Quests",
            CYAN,
            [
                "/quests — track goals",
                "New players: onboarding chain",
                "Veterans: 3 daily objectives",
                "/quest-hint for a nudge",
            ],
        ),
        (
            "Mana & Skills",
            CYAN,
            [
                "/mana · /skills · /cast",
                "Warden: fast time regen",
                "DPS: mana from damage",
                "Spells buff /attack or /duel",
            ],
        ),
        (
            "Casino",
            PURPLE,
            [
                "/coinflip — vs the house",
                "/coinflip-duel @user",
                "/blackjack — hit or stand",
                "Tax on winnings (tunable)",
            ],
        ),
        (
            "PvP Duels",
            RED,
            [
                "/duel @player — full battle log",
                "Loser pays 10% wallet → winner",
                "40 min cooldown vs same foe",
                "Max 3 duels started per hour",
            ],
        ),
    ]

    for idx, (title, accent, bullets) in enumerate(cards):
        row, col = divmod(idx, cols)
        x0 = margin + col * (card_w + gap)
        y = y0 + row * (card_h + gap)
        _card(draw, (x0, y, x0 + card_w, y + card_h), title, accent, bullets, fonts)

    # Footer strip
    foot_y = H - 100
    _rounded_rect(draw, (margin, foot_y, W - margin, H - 40), 18, (18, 24, 40), outline=GOLD)
    footer_cmds = (
        "/daily  ·  /shop  ·  /attack  ·  /cast  ·  /duel  ·  /class  ·  /skills  ·  "
        "/mana  ·  /work  ·  /quests  ·  /hall-of-fame  ·  /heist  ·  /stats"
    )
    draw.text((W // 2, foot_y + 22), "Slash commands", fill=GOLD, font=section_font, anchor="mm")
    for i, line in enumerate(_wrap(draw, footer_cmds, small_font, W - 2 * margin - 40)):
        draw.text((W // 2, foot_y + 48 + i * 18), line, fill=MUTED, font=small_font, anchor="mm")

    draw.text(
        (W // 2, H - 22),
        "Type / in Discord  ·  Admins tune economy & duels on the web dashboard",
        fill=MUTED,
        font=small_font,
        anchor="mm",
    )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUTPUT, "PNG", optimize=True)
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    build()
