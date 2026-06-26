"""Procedural shop item icon generation from existing tier sprites.

New endgame gear icons are built by remapping reference sprites (same
category / adjacent tier) onto tier palettes, then adding seeded glow,
sparkle, and accent details so each item stays unique but matches the
hand-authored pixel-art style of ``assets/items/``.
"""
from __future__ import annotations

import hashlib
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ICON_SIZE = 64
ITEMS_ROOT = Path(__file__).resolve().parent.parent / "assets" / "items"

# Base silhouette to morph, style palette donor (optional override).
ITEM_ART_SPEC: dict[str, tuple[str, str | None]] = {
    "apex_annihilator": ("nugget_minigun", "apex_nuggetblade"),
    "sovereign_railcannon": ("cosmic_railgun", "sovereign_cleaver"),
    "transcendent_voidlance": ("mythic_annihilator", "transcendent_worldsplitter"),
    "dominion_worldbreaker": ("transcendent_worldsplitter", "sunhammer"),
    "dominion_devastator": ("mythic_annihilator", "sunhammer"),
    "reaper_fang": ("sovereign_cleaver", "void_blade"),
    "reaper_crossbow": ("void_carbine", "void_blade"),
    "apotheosis_carapace": ("transcendent_carapace", "apex_aegis"),
}

# Explicit tier palettes when we want a strong identity beyond donor extraction.
ITEM_PALETTES: dict[str, tuple[tuple[int, int, int], ...]] = {
    "apex_annihilator": (
        (18, 14, 6), (80, 58, 12), (170, 130, 28), (230, 190, 60), (255, 240, 150),
    ),
    "sovereign_railcannon": (
        (12, 8, 24), (52, 28, 88), (110, 62, 170), (170, 110, 230), (230, 190, 255),
    ),
    "transcendent_voidlance": (
        (6, 18, 28), (18, 70, 100), (40, 150, 190), (120, 230, 255), (210, 250, 255),
    ),
    "dominion_worldbreaker": (
        (24, 8, 4), (90, 28, 12), (180, 70, 28), (240, 130, 50), (255, 210, 120),
    ),
    "dominion_devastator": (
        (20, 6, 4), (88, 24, 10), (170, 58, 22), (235, 110, 40), (255, 190, 90),
    ),
    "reaper_fang": (
        (4, 16, 8), (16, 58, 28), (36, 120, 58), (90, 210, 110), (180, 255, 190),
    ),
    "reaper_crossbow": (
        (6, 18, 10), (18, 62, 32), (40, 130, 68), (100, 220, 120), (190, 255, 200),
    ),
    "apotheosis_carapace": (
        (20, 18, 12), (70, 62, 40), (150, 130, 80), (230, 210, 150), (255, 250, 220),
    ),
}

NEW_ITEM_IDS: tuple[str, ...] = tuple(ITEM_ART_SPEC.keys())


def _rng(item_id: str) -> random.Random:
    digest = hashlib.sha256(item_id.encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _load_rgba(item_id: str) -> Image.Image:
    path = ITEMS_ROOT / f"{item_id}.png"
    if not path.is_file():
        raise FileNotFoundError(f"Missing reference icon: {path}")
    img = Image.open(path).convert("RGBA")
    if img.size != (ICON_SIZE, ICON_SIZE):
        img = img.resize((ICON_SIZE, ICON_SIZE), Image.Resampling.NEAREST)
    return img


def _luminance(rgb: np.ndarray) -> np.ndarray:
    return (
        0.299 * rgb[:, :, 0].astype(np.float32)
        + 0.587 * rgb[:, :, 1].astype(np.float32)
        + 0.114 * rgb[:, :, 2].astype(np.float32)
    )


def _extract_palette_stops(img: Image.Image, stops: int = 5) -> list[tuple[int, int, int]]:
    arr = np.array(img.convert("RGBA"))
    mask = arr[:, :, 3] > 40
    if not mask.any():
        return [(40, 44, 52)] * stops
    pixels = arr[mask][:, :3].astype(np.float32)
    lum = 0.299 * pixels[:, 0] + 0.587 * pixels[:, 1] + 0.114 * pixels[:, 2]
    order = np.argsort(lum)
    result: list[tuple[int, int, int]] = []
    for i in range(stops):
        start = int(i * len(order) / stops)
        end = max(start + 1, int((i + 1) * len(order) / stops))
        chunk = pixels[order[start:end]]
        mean = chunk.mean(axis=0)
        result.append((int(mean[0]), int(mean[1]), int(mean[2])))
    return result


def _palette_for_item(item_id: str, style_id: str | None) -> tuple[tuple[int, int, int], ...]:
    if item_id in ITEM_PALETTES:
        return ITEM_PALETTES[item_id]
    if style_id is not None:
        try:
            return tuple(_extract_palette_stops(_load_rgba(style_id)))
        except FileNotFoundError:
            pass
    return (
        (20, 24, 32),
        (60, 70, 90),
        (120, 130, 160),
        (190, 200, 220),
        (240, 245, 255),
    )


def _remap_luminance(
    base: Image.Image,
    palette: tuple[tuple[int, int, int], ...],
) -> np.ndarray:
    arr = np.array(base.convert("RGBA"))
    out = np.zeros_like(arr)
    rgb = arr[:, :, :3].astype(np.float32)
    alpha = arr[:, :, 3]
    lum = _luminance(rgb)
    mask = alpha > 32
    if not mask.any():
        return out

    lo = float(lum[mask].min())
    hi = float(lum[mask].max())
    span = max(hi - lo, 1.0)
    norm = np.clip((lum - lo) / span, 0.0, 1.0)

    stops = np.array(palette, dtype=np.float32)
    if len(stops) == 1:
        stops = np.repeat(stops, 5, axis=0)
    idx = norm * (len(stops) - 1)
    i0 = np.floor(idx).astype(np.int32)
    i1 = np.minimum(i0 + 1, len(stops) - 1)
    t = (idx - i0)[..., np.newaxis]

    mapped = (1 - t) * stops[i0] + t * stops[i1]
    out[:, :, :3] = np.clip(mapped, 0, 255).astype(np.uint8)
    out[:, :, 3] = alpha
    return out


def _add_outer_glow(rgba: np.ndarray, color: tuple[int, int, int], strength: float = 0.55) -> np.ndarray:
    alpha = Image.fromarray(rgba[:, :, 3], mode="L")
    glow = alpha.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(radius=1.2))
    glow_arr = np.array(glow, dtype=np.float32) / 255.0
    result = rgba.astype(np.float32)
    for c in range(3):
        result[:, :, c] = np.clip(
            result[:, :, c] + glow_arr * color[c] * strength,
            0,
            255,
        )
    return result.astype(np.uint8)


def _add_sparkles(
    draw: ImageDraw.ImageDraw,
    rng: random.Random,
    rgba: np.ndarray,
    accent: tuple[int, int, int],
    count: int,
) -> None:
    alpha = rgba[:, :, 3]
    ys, xs = np.where(alpha > 80)
    if len(xs) == 0:
        return
    for _ in range(count):
        i = rng.randrange(len(xs))
        x, y = int(xs[i]), int(ys[i])
        size = rng.choice((1, 1, 2))
        bright = tuple(min(255, c + rng.randint(40, 90)) for c in accent)
        if size == 1:
            rgba[y, x, :3] = bright
            rgba[y, x, 3] = 255
        else:
            draw.rectangle((x - 1, y - 1, x + 1, y + 1), fill=(*bright, 255))


def _add_edge_highlights(rgba: np.ndarray, highlight: tuple[int, int, int]) -> None:
    alpha = rgba[:, :, 3] > 40
    h, w = alpha.shape
    edge = np.zeros_like(alpha)
    edge[1:, :] |= alpha[1:, :] & ~alpha[:-1, :]
    edge[:-1, :] |= alpha[:-1, :] & ~alpha[1:, :]
    edge[:, 1:] |= alpha[:, 1:] & ~alpha[:, :-1]
    edge[:, :-1] |= alpha[:, :-1] & ~alpha[:, 1:]
    ys, xs = np.where(edge)
    for y, x in zip(ys, xs, strict=False):
        for c in range(3):
            rgba[y, x, c] = min(255, int(rgba[y, x, c] * 0.55 + highlight[c] * 0.45))


def _post_process_gun(arr: np.ndarray, item_id: str, rng: random.Random) -> np.ndarray:
    alpha = arr[:, :, 3] > 40
    if not alpha.any():
        return arr
    xs = np.where(alpha)[1]
    left = int(xs.min())
    palette = ITEM_PALETTES.get(
        item_id,
        ((255, 180, 80), (255, 200, 100), (255, 220, 120), (255, 240, 150), (255, 250, 200)),
    )
    glow_color = palette[min(3, len(palette) - 1)]
    for dx in range(3):
        x = max(0, left - dx)
        col = tuple(int(c * (0.5 - dx * 0.12)) for c in glow_color)
        ys = np.where(alpha[:, max(0, x - 1) : x + 2].any(axis=1))[0]
        for y in ys:
            if arr[y, x, 3] < 20:
                arr[y, x, :3] = col
                arr[y, x, 3] = 180 - dx * 40
    if "crossbow" in item_id:
        cx = int(np.mean(xs))
        arr[40:56, cx - 2 : cx + 3, :3] = (
            arr[40:56, cx - 2 : cx + 3, :3].astype(np.int16) * 0.7 + 40
        ).clip(0, 255).astype(np.uint8)
    return arr


def _post_process_weapon(arr: np.ndarray, item_id: str, rng: random.Random) -> np.ndarray:
    alpha = arr[:, :, 3] > 40
    if not alpha.any():
        return arr
    palette = ITEM_PALETTES.get(item_id, ((255, 200, 100),))
    accent = palette[min(3, len(palette) - 1)]
    if "reaper" in item_id:
        # Extra jagged edge glints along the blade.
        ys, xs = np.where(alpha)
        for _ in range(12):
            i = rng.randrange(len(xs))
            x, y = int(xs[i]), int(ys[i])
            arr[y, x, :3] = tuple(min(255, c + 60) for c in accent)
    if "dominion" in item_id:
        # Core heat line.
        cx = int(np.mean(xs := np.where(alpha)[1]))
        for y in range(8, 56):
            if alpha[y, cx]:
                arr[y, cx, :3] = tuple(min(255, int(c * 0.4 + accent[c % 3] * 0.6)) for c in range(3))
    return arr


def _post_process_armor(arr: np.ndarray, item_id: str) -> np.ndarray:
    alpha = arr[:, :, 3] > 40
    if not alpha.any():
        return arr
    palette = ITEM_PALETTES[item_id]
    gem = palette[3]
    cx, cy = ICON_SIZE // 2, ICON_SIZE // 2 + 4
    for dy in range(-3, 4):
        for dx in range(-3, 4):
            if dx * dx + dy * dy <= 8:
                y, x = cy + dy, cx + dx
                if 0 <= y < ICON_SIZE and 0 <= x < ICON_SIZE:
                    arr[y, x, :3] = gem
                    arr[y, x, 3] = 255
    return arr


def generate_item_icon(item_id: str, *, size: int = ICON_SIZE) -> Image.Image:
    """Build a unique 64×64 icon for ``item_id`` from reference sprites."""
    if item_id not in ITEM_ART_SPEC:
        raise KeyError(f"No art spec for item: {item_id}")

    base_id, style_id = ITEM_ART_SPEC[item_id]
    base = _load_rgba(base_id)
    palette = _palette_for_item(item_id, style_id)
    rng = _rng(item_id)

    arr = _remap_luminance(base, palette)
    arr = _add_outer_glow(arr, palette[-1], strength=0.45 if "reaper" in item_id else 0.35)
    _add_edge_highlights(arr, palette[-2])

    from items import get_item

    item = get_item(item_id)
    category = item.category if item is not None else "weapon"

    img = Image.fromarray(arr, mode="RGBA")
    draw = ImageDraw.Draw(img)
    _add_sparkles(draw, rng, arr, palette[-1], count=10 if "transcendent" in item_id or "dominion" in item_id else 6)
    img = Image.fromarray(arr, mode="RGBA")

    if category == "gun":
        arr = np.array(img)
        arr = _post_process_gun(arr, item_id, rng)
        img = Image.fromarray(arr, mode="RGBA")
    elif category == "weapon":
        arr = np.array(img)
        arr = _post_process_weapon(arr, item_id, rng)
        img = Image.fromarray(arr, mode="RGBA")
    elif category == "armor":
        arr = np.array(img)
        arr = _post_process_armor(arr, item_id)
        img = Image.fromarray(arr, mode="RGBA")

    if img.size != (size, size):
        img = img.resize((size, size), Image.Resampling.NEAREST)
    return img


def write_item_icons(
    item_ids: tuple[str, ...] | list[str] | None = None,
    out_dir: Path | None = None,
) -> list[Path]:
    ids = tuple(item_ids or NEW_ITEM_IDS)
    target = out_dir or ITEMS_ROOT
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for item_id in ids:
        path = target / f"{item_id}.png"
        generate_item_icon(item_id).save(path)
        written.append(path)
    return written
