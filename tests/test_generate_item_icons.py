"""Tests for procedural item icon generation."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from utils.item_art import ICON_SIZE, NEW_ITEM_IDS, generate_item_icon, write_item_icons


class GenerateItemIconsTests(unittest.TestCase):
    def test_generates_all_new_item_icons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            paths = write_item_icons(NEW_ITEM_IDS, out_dir)
            self.assertEqual(len(paths), len(NEW_ITEM_IDS))
            for path in paths:
                self.assertTrue(path.is_file())
                self.assertGreater(path.stat().st_size, 2000)
                with Image.open(path) as icon:
                    self.assertEqual(icon.size, (ICON_SIZE, ICON_SIZE))

    def test_icons_have_rich_pixel_detail(self) -> None:
        icon = generate_item_icon("dominion_worldbreaker")
        arr = np.array(icon.convert("RGBA"))
        opaque = arr[:, :, 3] > 40
        self.assertGreater(int(opaque.sum()), 400)
        unique_colors = len({tuple(px) for px in arr[opaque]})
        self.assertGreater(unique_colors, 200)

    def test_each_item_has_unique_palette(self) -> None:
        a = np.array(generate_item_icon("dominion_devastator").convert("RGB"))
        b = np.array(generate_item_icon("reaper_crossbow").convert("RGB"))
        self.assertFalse(np.array_equal(a, b))


if __name__ == "__main__":
    unittest.main()
