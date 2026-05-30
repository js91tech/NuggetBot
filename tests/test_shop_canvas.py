"""Shop canvas renderer tests."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from items import get_item, items_for_category
from utils.shop_canvas import COLS, ITEMS_PER_PAGE, ROWS, item_detail_lines, render_shop_page


class ShopCanvasTests(unittest.TestCase):
    def test_render_page_returns_png(self) -> None:
        items = items_for_category("weapon")[:ITEMS_PER_PAGE]
        png = render_shop_page(items, wallet=10_000.0)
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertGreater(len(png), 500)

    def test_grid_constants(self) -> None:
        self.assertEqual(COLS * ROWS, ITEMS_PER_PAGE)

    def test_weapon_stats_line(self) -> None:
        item = get_item("iron_sword")
        assert item is not None
        draw = MagicMock()
        draw.textlength.side_effect = lambda text, font=None: len(text) * 7
        lines = item_detail_lines(item, draw, MagicMock(), max_w=180)
        self.assertEqual(len(lines), 1)
        self.assertIn("Power 75", lines[0])
        self.assertIn("4% crit", lines[0])

    def test_consumable_description_wrapped(self) -> None:
        item = get_item("raid_potion")
        assert item is not None
        draw = MagicMock()
        draw.textlength.side_effect = lambda text, font=None: len(text) * 7
        lines = item_detail_lines(item, draw, MagicMock(), max_w=180)
        self.assertGreaterEqual(len(lines), 1)
        self.assertIn("boss", " ".join(lines).lower())

    def test_armor_stats_line(self) -> None:
        item = get_item("bronze_vest")
        assert item is not None
        draw = MagicMock()
        draw.textlength.side_effect = lambda text, font=None: len(text) * 7
        lines = item_detail_lines(item, draw, MagicMock(), max_w=180)
        self.assertEqual(len(lines), 1)
        self.assertIn("+90 HP", lines[0])
        self.assertIn("mitigation", lines[0])


if __name__ == "__main__":
    unittest.main()
