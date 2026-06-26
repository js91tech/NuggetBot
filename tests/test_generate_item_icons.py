"""Tests for procedural item icon generation."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from scripts.generate_item_icons import ICON_SIZE, NEW_ITEM_IDS, write_icons


class GenerateItemIconsTests(unittest.TestCase):
    def test_generates_all_new_item_icons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            paths = write_icons(NEW_ITEM_IDS, out_dir)
            self.assertEqual(len(paths), len(NEW_ITEM_IDS))
            for path in paths:
                self.assertTrue(path.is_file())
                with Image.open(path) as icon:
                    self.assertEqual(icon.size, (ICON_SIZE, ICON_SIZE))


if __name__ == "__main__":
    unittest.main()
