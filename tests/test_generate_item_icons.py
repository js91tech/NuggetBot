"""Tests for AI-generated item icon baking."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from utils.item_art import ICON_SIZE, NEW_ITEM_IDS, GENERATED_ROOT, normalize_generated_icon, write_item_icons


class GenerateItemIconsTests(unittest.TestCase):
    def test_generates_all_new_item_icons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            paths = write_item_icons(NEW_ITEM_IDS, out_dir=out_dir, source_dir=GENERATED_ROOT)
            self.assertEqual(len(paths), len(NEW_ITEM_IDS))
            for path in paths:
                self.assertTrue(path.is_file())
                self.assertGreater(path.stat().st_size, 2000)
                with Image.open(path) as icon:
                    self.assertEqual(icon.size, (ICON_SIZE, ICON_SIZE))

    def test_normalize_extracts_foreground(self) -> None:
        source = GENERATED_ROOT / "dominion_worldbreaker_gen.png"
        icon = normalize_generated_icon(Image.open(source))
        arr = np.array(icon.convert("RGBA"))
        opaque = arr[:, :, 3] > 40
        self.assertGreater(int(opaque.sum()), 400)
        unique_colors = len({tuple(px) for px in arr[opaque]})
        self.assertGreater(unique_colors, 100)

    def test_each_item_icon_is_unique(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            write_item_icons(NEW_ITEM_IDS, out_dir=out_dir, source_dir=GENERATED_ROOT)
            a = np.array(Image.open(out_dir / "dominion_devastator.png").convert("RGB"))
            b = np.array(Image.open(out_dir / "reaper_crossbow.png").convert("RGB"))
            self.assertFalse(np.array_equal(a, b))


if __name__ == "__main__":
    unittest.main()
