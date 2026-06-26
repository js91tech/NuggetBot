"""Normalize AI-generated item art into 64×64 shop icons."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

ICON_SIZE = 64
ITEMS_ROOT = Path(__file__).resolve().parent.parent / "assets" / "items"
GENERATED_ROOT = Path(__file__).resolve().parent.parent / "assets" / "sprites" / "generated"

# AI source filename (in assets/sprites/generated/) -> shop item id.
AI_ITEM_SOURCES: dict[str, str] = {
    "apex_annihilator_gen.png": "apex_annihilator",
    "sovereign_railcannon_gen.png": "sovereign_railcannon",
    "transcendent_voidlance_gen.png": "transcendent_voidlance",
    "dominion_worldbreaker_gen.png": "dominion_worldbreaker",
    "dominion_devastator_gen.png": "dominion_devastator",
    "reaper_fang_gen.png": "reaper_fang",
    "reaper_crossbow_gen.png": "reaper_crossbow",
    "apotheosis_carapace_gen.png": "apotheosis_carapace",
}

NEW_ITEM_IDS: tuple[str, ...] = tuple(AI_ITEM_SOURCES.values())


def _sample_background(img: Image.Image) -> tuple[int, int, int]:
    rgb = img.convert("RGB")
    w, h = rgb.size
    corners = [
        rgb.getpixel((2, 2)),
        rgb.getpixel((w - 3, 2)),
        rgb.getpixel((2, h - 3)),
        rgb.getpixel((w - 3, h - 3)),
    ]
    return (
        int(sum(c[0] for c in corners) / 4),
        int(sum(c[1] for c in corners) / 4),
        int(sum(c[2] for c in corners) / 4),
    )


def normalize_generated_icon(img: Image.Image, *, size: int = ICON_SIZE) -> Image.Image:
    """Extract foreground from an AI render and fit it to a transparent square icon."""
    rgb = img.convert("RGB")
    bg = _sample_background(rgb)
    data = np.array(rgb, dtype=np.int16)
    bg_arr = np.array(bg, dtype=np.int16)
    alpha = (np.abs(data - bg_arr).sum(axis=2) > 36).astype(np.uint8) * 255
    rgba = np.dstack([data, alpha]).astype(np.uint8)
    crop = Image.fromarray(rgba, mode="RGBA")

    bbox = crop.getbbox()
    if bbox is None:
        return Image.new("RGBA", (size, size), (0, 0, 0, 0))

    crop = crop.crop(bbox)
    # Preserve chunky pixel-art read at shop size.
    max_dim = max(crop.size)
    if max_dim > size * 4:
        pre_scale = max(1, (size * 4) // max_dim)
        pre_w = max(1, crop.width * pre_scale)
        pre_h = max(1, crop.height * pre_scale)
        crop = crop.resize((pre_w, pre_h), Image.Resampling.NEAREST)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    cw, ch = crop.size
    scale = min(size / cw, size / ch) * 0.92
    nw = max(1, int(round(cw * scale)))
    nh = max(1, int(round(ch * scale)))
    resized = crop.resize((nw, nh), Image.Resampling.NEAREST)
    ox = (size - nw) // 2
    oy = (size - nh) // 2
    canvas.paste(resized, (ox, oy), resized)
    return canvas


def write_item_icons(
    item_ids: tuple[str, ...] | list[str] | None = None,
    *,
    out_dir: Path | None = None,
    source_dir: Path | None = None,
) -> list[Path]:
    ids = set(item_ids or NEW_ITEM_IDS)
    target = out_dir or ITEMS_ROOT
    sources = source_dir or GENERATED_ROOT
    target.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for filename, item_id in AI_ITEM_SOURCES.items():
        if item_id not in ids:
            continue
        source = sources / filename
        if not source.is_file():
            raise FileNotFoundError(f"Missing AI source art: {source}")
        icon = normalize_generated_icon(Image.open(source))
        path = target / f"{item_id}.png"
        icon.save(path)
        written.append(path)
    return written
