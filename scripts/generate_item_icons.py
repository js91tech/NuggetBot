#!/usr/bin/env python3
"""Generate deterministic 64×64 pixel-art item icons for shop gear.

Prefer hand-authored / AI custom icons already present in assets/items/.
This script only fills missing placeholders unless --force is passed.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets" / "items"
ICON_SIZE = 64

NEW_ITEM_IDS: tuple[str, ...] = (
    "apex_annihilator",
    "sovereign_railcannon",
    "transcendent_voidlance",
    "dominion_worldbreaker",
    "dominion_devastator",
    "reaper_fang",
    "reaper_crossbow",
    "apotheosis_carapace",
    "paragon_edge",
    "paragon_repeater",
    "paragon_aegis",
    "eternal_worldcleaver",
    "eternal_obliteratrix",
    "eternal_bastion",
)

ITEM_THEMES: dict[str, tuple[tuple[int, int, int], tuple[int, int, int], str]] = {
    "apex_annihilator": ((212, 175, 55), (255, 230, 120), "gun"),
    "sovereign_railcannon": ((120, 70, 180), (200, 150, 255), "gun"),
    "transcendent_voidlance": ((40, 200, 220), (180, 255, 255), "gun"),
    "dominion_worldbreaker": ((220, 80, 40), (255, 180, 80), "weapon"),
    "dominion_devastator": ((220, 80, 40), (255, 180, 80), "gun"),
    "reaper_fang": ((30, 90, 50), (120, 220, 140), "weapon"),
    "reaper_crossbow": ((30, 90, 50), (120, 220, 140), "gun"),
    "apotheosis_carapace": ((240, 230, 180), (255, 255, 220), "armor"),
    "paragon_edge": ((70, 130, 200), (160, 210, 255), "weapon"),
    "paragon_repeater": ((70, 130, 200), (160, 210, 255), "gun"),
    "paragon_aegis": ((70, 130, 200), (200, 230, 255), "armor"),
    "eternal_worldcleaver": ((180, 40, 60), (255, 120, 90), "weapon"),
    "eternal_obliteratrix": ((180, 40, 60), (255, 120, 90), "gun"),
    "eternal_bastion": ((180, 40, 60), (255, 160, 130), "armor"),
}


def _seed(item_id: str) -> int:
    digest = hashlib.sha256(item_id.encode()).hexdigest()
    return int(digest[:8], 16)


def _draw_weapon(draw: ImageDraw.ImageDraw, primary: tuple[int, int, int], accent: tuple[int, int, int], seed: int) -> None:
    blade_w = 6 + (seed % 3)
    draw.polygon([(44, 10), (44 + blade_w, 10), (18, 54), (18 - blade_w, 54)], fill=primary)
    draw.rectangle((14, 48, 30, 56), fill=accent)
    draw.rectangle((20, 56, 26, 60), fill=(90, 90, 100))


def _draw_gun(draw: ImageDraw.ImageDraw, primary: tuple[int, int, int], accent: tuple[int, int, int], seed: int) -> None:
    barrel_len = 28 + (seed % 6)
    draw.rectangle((8, 28, 8 + barrel_len, 36), fill=primary)
    draw.rectangle((8 + barrel_len - 4, 26, 8 + barrel_len + 2, 38), fill=accent)
    draw.rectangle((34, 24, 50, 40), fill=primary)
    draw.ellipse((46, 30, 54, 38), fill=accent)
    if seed % 2:
        draw.rectangle((36, 40, 44, 52), fill=(80, 80, 90))


def _draw_armor(draw: ImageDraw.ImageDraw, primary: tuple[int, int, int], accent: tuple[int, int, int], seed: int) -> None:
    draw.polygon([(32, 8), (52, 20), (52, 48), (32, 58), (12, 48), (12, 20)], fill=primary)
    draw.polygon([(32, 16), (44, 24), (44, 44), (32, 50), (20, 44), (20, 24)], fill=accent)
    draw.line([(32, 16), (32, 50)], fill=(60, 60, 70), width=2)
    if seed % 2:
        draw.ellipse((28, 30, 36, 38), fill=primary)


def generate_icon(item_id: str, *, size: int = ICON_SIZE) -> Image.Image:
    theme = ITEM_THEMES.get(item_id)
    if theme is None:
        from items import get_item

        item = get_item(item_id)
        category = item.category if item is not None else "weapon"
        primary = (100, 110, 130)
        accent = (180, 190, 210)
    else:
        primary, accent, category = theme
    seed = _seed(item_id)
    img = Image.new("RGBA", (size, size), (32, 36, 44, 255))
    draw = ImageDraw.Draw(img)
    # Subtle tier glow border
    draw.rectangle((2, 2, size - 3, size - 3), outline=accent, width=1)
    if category == "weapon":
        _draw_weapon(draw, primary, accent, seed)
    elif category == "gun":
        _draw_gun(draw, primary, accent, seed)
    else:
        _draw_armor(draw, primary, accent, seed)
    return img


def write_icons(
    item_ids: tuple[str, ...] | list[str],
    out_dir: Path = OUT_DIR,
    *,
    force: bool = False,
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for item_id in item_ids:
        path = out_dir / f"{item_id}.png"
        if path.is_file() and not force:
            # Preserve custom AI / sprite-sheet icons.
            continue
        generate_icon(item_id).save(path)
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate shop item icons.")
    parser.add_argument(
        "item_ids",
        nargs="*",
        help="Item IDs to generate (default: all new endgame items)",
    )
    parser.add_argument(
        "--out-dir",
        default=str(OUT_DIR),
        help="Output directory for PNG icons",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing icons (including custom AI art)",
    )
    args = parser.parse_args()
    ids = tuple(args.item_ids) if args.item_ids else NEW_ITEM_IDS
    paths = write_icons(ids, Path(args.out_dir), force=args.force)
    for path in paths:
        print(path)
    if not paths:
        print("No icons written (existing custom icons preserved; pass --force to overwrite).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
