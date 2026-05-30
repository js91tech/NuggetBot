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
CELL_H = 220
ICON_SIZE = 96

BG = (32, 34, 37)
HEADER_BG = (45, 80, 22)
PRICE_BG = (87, 166, 74)
NAME_COLOR = (255, 215, 100)
PRICE_COLOR = (255, 255, 255)
DIM_OVERLAY = (0, 0, 0, 140)


def _fonts() -> tuple[ImageFont.ImageFont, ImageFont.ImageFont]:
    try:
        bold = ImageFont.truetype("DejaVuSans-Bold.ttf", 15)
        regular = ImageFont.truetype("DejaVuSans.ttf", 14)
        return bold, regular
    except OSError:
        default = ImageFont.load_default()
        return default, default


def _truncate(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    trimmed = text
    while trimmed and draw.textlength(trimmed + "…", font=font) > max_w:
        trimmed = trimmed[:-1]
    return (trimmed + "…") if trimmed else "…"


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
    name_font, price_font = _fonts()
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
        header = (x, y, x + CELL_W, y + 36)
        draw.rounded_rectangle(
            (header[0], header[1], header[2], header[3]),
            radius=8,
            fill=HEADER_BG + (255,),
        )
        draw.rectangle((x, y + 28, x + CELL_W, y + 36), fill=HEADER_BG + (255,))
        label = _truncate(draw, item.name, name_font, CELL_W - 16)
        draw.text((x + CELL_W // 2, y + 18), label, fill=NAME_COLOR, font=name_font, anchor="mm")

        icon = load_item_icon(item.id, size=ICON_SIZE)
        ix = x + (CELL_W - ICON_SIZE) // 2
        iy = y + 44
        canvas.paste(icon, (ix, iy), icon)

        if not afford.get(item.id, True):
            overlay = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), DIM_OVERLAY)
            canvas.paste(overlay, (ix, iy), overlay)

        price_y = y + CELL_H - 32
        draw.rounded_rectangle(
            (x, price_y, x + CELL_W, y + CELL_H),
            radius=8,
            fill=PRICE_BG + (255,),
        )
        draw.rectangle((x, price_y, x + CELL_W, price_y + 8), fill=PRICE_BG + (255,))
        price_text = fmt_amount(item.price)
        draw.text(
            (x + CELL_W // 2, price_y + 16),
            price_text,
            fill=PRICE_COLOR,
            font=price_font,
            anchor="mm",
        )

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()
