from __future__ import annotations

import io
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

from utils.helpers import fmt_amount
from utils.item_assets import load_item_icon

if TYPE_CHECKING:
    from items import ShopItem

# Dank Memer–inspired shop card layout (3×2 grid per page).
COLS = 3
ROWS = 2
ITEMS_PER_PAGE = COLS * ROWS
PAD = 14
CELL_W = 200
CELL_H = 268
ICON_SIZE = 72
HEADER_H = 42
FOOTER_H = 38
DETAIL_PAD_X = 10

BG = (32, 34, 37)
HEADER_BG = (45, 80, 22)
PRICE_BG = (87, 166, 74)
NAME_COLOR = (255, 215, 100)
PRICE_COLOR = (255, 255, 255)
STAT_COLOR = (186, 220, 255)
DESC_COLOR = (178, 184, 194)
DIM_OVERLAY = (0, 0, 0, 140)


def _fonts() -> tuple[ImageFont.ImageFont, ImageFont.ImageFont, ImageFont.ImageFont]:
    try:
        name_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 18)
        price_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 17)
        detail_font = ImageFont.truetype("DejaVuSans.ttf", 14)
        return name_font, price_font, detail_font
    except OSError:
        default = ImageFont.load_default()
        return default, default, default


def _truncate(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    trimmed = text
    while trimmed and draw.textlength(trimmed + "…", font=font) > max_w:
        trimmed = trimmed[:-1]
    return (trimmed + "…") if trimmed else "…"


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_w: int,
    *,
    max_lines: int,
) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        if draw.textlength(candidate, font=font) <= max_w:
            current = candidate
            continue
        if current:
            lines.append(current)
            if len(lines) >= max_lines:
                break
        current = word
        if draw.textlength(current, font=font) > max_w:
            current = _truncate(draw, word, font, max_w)
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    if len(lines) == max_lines:
        joined_len = len(" ".join(words))
        shown_len = len(" ".join(lines))
        if joined_len > shown_len and lines:
            lines[-1] = _truncate(draw, lines[-1], font, max_w)
    return lines


def item_detail_lines(
    item: ShopItem,
    draw: ImageDraw.ImageDraw,
    font: ImageFont.ImageFont,
    *,
    max_w: int,
) -> list[str]:
    """Stat or description lines shown under the item icon."""
    from items import armor_mitigation_percent

    if item.category in ("weapon", "gun"):
        crit_pct = int(round(item.crit_chance * 100))
        stat = f"Power {item.power}"
        if crit_pct > 0:
            stat += f"  ·  {crit_pct}% crit"
        return [stat]
    if item.category == "armor":
        mit = armor_mitigation_percent(item.power)
        return [f"+{item.hp_bonus} HP  ·  {mit}% mitigation"]
    if item.category == "consumable" and item.description:
        return _wrap_text(draw, item.description, font, max_w, max_lines=3)
    return []


def _line_height(font: ImageFont.ImageFont) -> int:
    try:
        bbox = font.getbbox("Ag")
        return max(16, bbox[3] - bbox[1] + 4)
    except AttributeError:
        return 18


def _draw_detail_block(
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    width: int,
    lines: list[str],
    font: ImageFont.ImageFont,
    color: tuple[int, int, int],
) -> None:
    if not lines:
        return
    line_h = _line_height(font)
    max_w = width - DETAIL_PAD_X * 2
    total_h = line_h * len(lines)
    start_y = y + max(0, (98 - total_h) // 2)
    for index, line in enumerate(lines):
        draw.text(
            (x + width // 2, start_y + index * line_h),
            line,
            fill=color,
            font=font,
            anchor="ma",
        )


def render_shop_page(
    page_items: list[ShopItem],
    *,
    wallet: float,
    can_afford: dict[str, bool] | None = None,
) -> bytes:
    """Render one shop page grid PNG."""
    del wallet  # shown in embed, not drawn on canvas
    width = COLS * CELL_W + (COLS + 1) * PAD
    height = ROWS * CELL_H + (ROWS + 1) * PAD
    canvas = Image.new("RGBA", (width, height), BG + (255,))
    draw = ImageDraw.Draw(canvas)
    name_font, price_font, detail_font = _fonts()
    afford = can_afford or {}

    for index in range(ITEMS_PER_PAGE):
        col = index % COLS
        row = index // COLS
        x = PAD + col * (CELL_W + PAD)
        y = PAD + row * (CELL_H + PAD)
        box = (x, y, x + CELL_W, y + CELL_H)
        draw.rounded_rectangle(box, radius=8, fill=(28, 30, 33, 255), outline=(55, 60, 68, 255))

        if index >= len(page_items):
            continue

        item = page_items[index]
        draw.rounded_rectangle(
            (x, y, x + CELL_W, y + HEADER_H),
            radius=8,
            fill=HEADER_BG + (255,),
        )
        draw.rectangle((x, y + HEADER_H - 8, x + CELL_W, y + HEADER_H), fill=HEADER_BG + (255,))
        label = _truncate(draw, item.name, name_font, CELL_W - 16)
        draw.text(
            (x + CELL_W // 2, y + HEADER_H // 2),
            label,
            fill=NAME_COLOR,
            font=name_font,
            anchor="mm",
        )

        icon_y = y + HEADER_H + 6
        icon = load_item_icon(item.id, size=ICON_SIZE)
        ix = x + (CELL_W - ICON_SIZE) // 2
        canvas.paste(icon, (ix, icon_y), icon)

        if not afford.get(item.id, True):
            overlay = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), DIM_OVERLAY)
            canvas.paste(overlay, (ix, icon_y), overlay)

        detail_y = icon_y + ICON_SIZE + 6
        detail_lines = item_detail_lines(item, draw, detail_font, max_w=CELL_W - DETAIL_PAD_X * 2)
        detail_color = DESC_COLOR if item.category == "consumable" else STAT_COLOR
        _draw_detail_block(
            draw,
            x=x,
            y=detail_y,
            width=CELL_W,
            lines=detail_lines,
            font=detail_font,
            color=detail_color,
        )

        price_y = y + CELL_H - FOOTER_H
        draw.rounded_rectangle(
            (x, price_y, x + CELL_W, y + CELL_H),
            radius=8,
            fill=PRICE_BG + (255,),
        )
        draw.rectangle((x, price_y, x + CELL_W, price_y + 8), fill=PRICE_BG + (255,))
        price_text = fmt_amount(item.price)
        draw.text(
            (x + CELL_W // 2, price_y + FOOTER_H // 2),
            price_text,
            fill=PRICE_COLOR,
            font=price_font,
            anchor="mm",
        )

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()
