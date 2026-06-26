from __future__ import annotations

from pathlib import Path

from PIL import Image

ITEMS_ROOT = Path(__file__).resolve().parent.parent / "assets" / "items"
ICON_SIZE = 64

_CATEGORY_EMOJI: dict[str, str] = {
    "weapon": "⚔️",
    "gun": "🔫",
    "armor": "🛡️",
    "consumable": "🧪",
}


def item_icon_path(item_id: str) -> Path:
    return ITEMS_ROOT / f"{item_id}.png"


def load_item_icon(item_id: str, *, size: int = ICON_SIZE) -> Image.Image:
    """Load item PNG or render a procedural / emoji placeholder."""
    path = item_icon_path(item_id)
    if path.is_file() and path.stat().st_size > 800:
        icon = Image.open(path).convert("RGBA")
        if icon.size != (size, size):
            icon = icon.resize((size, size), Image.Resampling.NEAREST)
        return icon
    try:
        from utils.item_art import ITEM_ART_SPEC, generate_item_icon

        if item_id in ITEM_ART_SPEC:
            return generate_item_icon(item_id, size=size)
    except Exception:
        pass
    if path.is_file():
        icon = Image.open(path).convert("RGBA")
        if icon.size != (size, size):
            icon = icon.resize((size, size), Image.Resampling.NEAREST)
        return icon
    return _placeholder_icon(item_id, size=size)


def _placeholder_icon(item_id: str, *, size: int) -> Image.Image:
    from items import get_item

    item = get_item(item_id)
    category = item.category if item is not None else "weapon"
    emoji = _CATEGORY_EMOJI.get(category, "📦")
    img = Image.new("RGBA", (size, size), (40, 44, 52, 255))
    try:
        from PIL import ImageDraw, ImageFont

        font = ImageFont.truetype("DejaVuSans.ttf", max(24, size // 2))
    except OSError:
        from PIL import ImageDraw, ImageFont

        font = ImageFont.load_default()
    draw = ImageDraw.Draw(img)
    draw.text((size // 2, size // 2), emoji, fill=(220, 220, 220, 255), font=font, anchor="mm")
    return img
