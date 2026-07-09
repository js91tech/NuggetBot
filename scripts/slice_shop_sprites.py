#!/usr/bin/env python3
"""Slice NuggetBot shop sprite sheets into per-item PNGs.

Expects source sheets at:
  assets/sprites/shop_sheet.png
  assets/sprites/battle_worn_sheet.png

Usage (from repo root):
  python scripts/slice_shop_sprites.py
  python scripts/slice_shop_sprites.py path/to/shop.png path/to/battle_worn.png
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SPRITES_DIR = ROOT / "assets" / "sprites"
OUT_DIR = ROOT / "assets" / "items"
ICON_SIZE = 64

# Cell order: left-to-right, top-to-bottom (8 columns × 7 rows).
NORMAL_CELL_IDS: tuple[str | None, ...] = (
    "twig_sword",
    "rusty_dagger",
    "iron_sword",
    "ember_axe",
    "storm_spear",
    "void_blade",
    "sunhammer",
    "dragon_lance",
    "cosmic_greatsword",
    "nugget_excalibur",
    "mythic_voidreaver",
    "apex_nuggetblade",
    "sovereign_cleaver",
    "transcendent_worldsplitter",
    "training_stick",
    "boss_slayer_blade",
    "cap_gun",
    "rust_revolver",
    "iron_pistol",
    "flare_pistol",
    "storm_rifle",
    "void_carbine",
    "sunshot_rifle",
    "dragon_shotgun",
    "cosmic_railgun",
    "nugget_minigun",
    "mythic_annihilator",
    "mythic_raid_blade",
    "paper_hat",
    "padded_hoodie",
    "bronze_vest",
    "iron_plate",
    "ember_mail",
    "stormguard",
    "void_ward",
    "dragon_scale",
    "celestial_aegis",
    "nugget_immortal_plate",
    "mythic_aetherplate",
    "apex_aegis",
    "sovereign_bastion",
    "transcendent_carapace",
    "cardboard_shield",
    "boss_slayer_mail",
    "mythic_raid_mail",
    "trap_bomb",
    "raid_potion",
    "energy_drink",
    None,  # former duel_scroll cell — keep 7×8 sheet layout
    "chia_seeds",
    "alchemy_scrap",
    "nugget_coin",
    "apex_annihilator",
    "sovereign_railcannon",
    "transcendent_voidlance",
    "dominion_worldbreaker",
)

BATTLE_WORN_SKIP = frozenset(
    {
        "training_stick",
        "boss_slayer_blade",
        "mythic_raid_blade",
        "cardboard_shield",
        "boss_slayer_mail",
        "mythic_raid_mail",
        "trap_bomb",
        "raid_potion",
        "energy_drink",
        "chia_seeds",
        "alchemy_scrap",
        "nugget_coin",
    }
)


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


def _foreground_mask(img: Image.Image, bg: tuple[int, int, int], tolerance: int = 42) -> np.ndarray:
    arr = np.asarray(img.convert("RGB"), dtype=np.int16)
    bg_arr = np.array(bg, dtype=np.int16)
    diff = np.abs(arr - bg_arr).sum(axis=2)
    return diff > tolerance


def _content_bounds(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.where(mask)
    if len(xs) == 0:
        h, w = mask.shape
        return 0, 0, w, h
    pad = 2
    left = max(0, int(xs.min()) - pad)
    right = min(mask.shape[1], int(xs.max()) + pad + 1)
    top = max(0, int(ys.min()) - pad)
    bottom = min(mask.shape[0], int(ys.max()) + pad + 1)
    return left, top, right, bottom


def _extract_cell_boxes(mask: np.ndarray, *, rows: int = 7, cols: int = 8) -> list[tuple[int, int, int, int]]:
    left, top, right, bottom = _content_bounds(mask)
    region = mask[top:bottom, left:right]
    height = region.shape[0]
    width = region.shape[1]

    boxes: list[tuple[int, int, int, int]] = []
    for row in range(rows):
        y0 = top + int(row * height / rows)
        y1 = top + int((row + 1) * height / rows)
        for col in range(cols):
            x0 = left + int(col * width / cols)
            x1 = left + int((col + 1) * width / cols)
            cell = mask[y0:y1, x0:x1]
            ys, xs = np.where(cell)
            if len(xs) == 0:
                boxes.append((x0, y0, x1, y1))
                continue
            pad = 2
            cell_left = max(x0, int(xs.min()) + x0 - pad)
            cell_right = min(mask.shape[1], int(xs.max()) + x0 + pad + 1)
            cell_top = max(y0, int(ys.min()) + y0 - pad)
            cell_bottom = min(mask.shape[0], int(ys.max()) + y0 + pad + 1)
            boxes.append((cell_left, cell_top, cell_right, cell_bottom))
    return boxes


def _normalize_icon(img: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    crop = img.crop(box).convert("RGBA")
    bg = _sample_background(crop)
    data = np.array(crop, dtype=np.int16)
    bg_arr = np.array(bg, dtype=np.int16)
    alpha = (np.abs(data[:, :, :3] - bg_arr).sum(axis=2) > 36).astype(np.uint8) * 255
    crop.putalpha(Image.fromarray(alpha, mode="L"))

    bbox = crop.getbbox()
    if bbox is None:
        return Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    crop = crop.crop(bbox)

    canvas = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    cw, ch = crop.size
    scale = min(ICON_SIZE / cw, ICON_SIZE / ch)
    nw = max(1, int(round(cw * scale)))
    nh = max(1, int(round(ch * scale)))
    resized = crop.resize((nw, nh), Image.Resampling.NEAREST)
    ox = (ICON_SIZE - nw) // 2
    oy = (ICON_SIZE - nh) // 2
    canvas.paste(resized, (ox, oy), resized)
    return canvas


def slice_sheet(
    sheet_path: Path,
    *,
    cell_ids: tuple[str | None, ...],
    name_for: Callable[[str], str | None],
    out_dir: Path = OUT_DIR,
) -> list[Path]:
    img = Image.open(sheet_path)
    bg = _sample_background(img)
    mask = _foreground_mask(img, bg)
    boxes = _extract_cell_boxes(mask)
    if len(boxes) != len(cell_ids):
        raise RuntimeError(
            f"Expected {len(cell_ids)} cells in {sheet_path.name}, detected {len(boxes)}.",
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for index, (item_id, box) in enumerate(zip(cell_ids, boxes, strict=True)):
        if item_id is None:
            continue
        out_name = name_for(item_id)
        if out_name is None:
            continue
        icon = _normalize_icon(img, box)
        if icon.getbbox() is None:
            print(f"SKIP empty cell {index + 1}: {item_id}")
            continue
        out_path = out_dir / f"{out_name}.png"
        icon.save(out_path)
        written.append(out_path)
        try:
            label = out_path.relative_to(ROOT)
        except ValueError:
            label = out_path
        print(f"Wrote {label}")
    return written


def battle_worn_name(item_id: str) -> str | None:
    if item_id in BATTLE_WORN_SKIP:
        return None
    return f"boss_weak_{item_id}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Slice NuggetBot shop sprite sheets.")
    parser.add_argument(
        "shop_sheet",
        nargs="?",
        default=str(SPRITES_DIR / "shop_sheet.png"),
        help="Normal shop sprite sheet path",
    )
    parser.add_argument(
        "battle_worn_sheet",
        nargs="?",
        default=str(SPRITES_DIR / "battle_worn_sheet.png"),
        help="Battle-worn sprite sheet path",
    )
    args = parser.parse_args(argv)

    shop_path = Path(args.shop_sheet)
    worn_path = Path(args.battle_worn_sheet)
    missing = [path for path in (shop_path, worn_path) if not path.is_file()]
    if missing:
        print("Missing sprite sheet(s):", file=sys.stderr)
        for path in missing:
            print(f"  - {path}", file=sys.stderr)
        print(
            "\nSave your generated PNGs as:\n"
            f"  {SPRITES_DIR / 'shop_sheet.png'}\n"
            f"  {SPRITES_DIR / 'battle_worn_sheet.png'}",
            file=sys.stderr,
        )
        return 1

    print(f"Slicing normal sheet: {shop_path}")
    normal = slice_sheet(
        shop_path,
        cell_ids=NORMAL_CELL_IDS,
        name_for=lambda item_id: item_id,
    )
    print(f"Slicing battle-worn sheet: {worn_path}")
    worn = slice_sheet(
        worn_path,
        cell_ids=NORMAL_CELL_IDS,
        name_for=battle_worn_name,
    )
    print(f"Done: {len(normal)} normal icons, {len(worn)} battle-worn icons -> {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
