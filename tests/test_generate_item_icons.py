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

    def test_preserves_existing_custom_icons_unless_forced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            item_id = NEW_ITEM_IDS[0]
            existing = out_dir / f"{item_id}.png"
            Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (1, 2, 3, 255)).save(existing)
            before = existing.read_bytes()
            written = write_icons((item_id,), out_dir)
            self.assertEqual(written, [])
            self.assertEqual(existing.read_bytes(), before)
            forced = write_icons((item_id,), out_dir, force=True)
            self.assertEqual(len(forced), 1)
            self.assertNotEqual(existing.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
