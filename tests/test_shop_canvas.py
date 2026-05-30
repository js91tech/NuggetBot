"""Shop canvas renderer tests."""
from __future__ import annotations

import unittest

from items import items_for_category
from utils.shop_canvas import COLS, ITEMS_PER_PAGE, ROWS, render_shop_page


class ShopCanvasTests(unittest.TestCase):
    def test_render_page_returns_png(self) -> None:
        items = items_for_category("weapon")[:ITEMS_PER_PAGE]
        png = render_shop_page(items, wallet=10_000.0)
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertGreater(len(png), 500)

    def test_grid_constants(self) -> None:
        self.assertEqual(COLS * ROWS, ITEMS_PER_PAGE)


if __name__ == "__main__":
    unittest.main()
