"""Inventory display helpers and Discord embed limits."""
from __future__ import annotations

import unittest

from cogs.shop import Shop, _shop_embed_chunks
from items import ACCESSORIES, BOSS_WEAK_ITEMS, WEAPONS, get_item
from utils.stats import format_item_stats


class InventoryDisplayTests(unittest.TestCase):
    def test_accessory_stats_show_flat_bonuses(self) -> None:
        item = ACCESSORIES[0]
        text = format_item_stats(item)
        self.assertNotIn("0% mit", text)
        self.assertIn("+", text)

    def test_mythic_signet_stats(self) -> None:
        item = get_item("mythic_signet")
        assert item is not None
        text = format_item_stats(item)
        self.assertIn("+15 dmg", text)
        self.assertIn("+25 HP", text)
        self.assertIn("+2% crit", text)

    def test_large_inventory_chunks_within_discord_limits(self) -> None:
        equipment: dict[str, str] = {}
        lines = [
            Shop._inventory_line(item.id, 1, equipment)
            for item in (*WEAPONS, *BOSS_WEAK_ITEMS[:60], *ACCESSORIES)
        ]
        description, fields = _shop_embed_chunks(lines)
        if description is not None:
            self.assertLessEqual(len(description), 4096)
        else:
            self.assertTrue(fields)
            for _name, value in fields:
                self.assertLessEqual(len(value), 1024)
            # Whale inventories must spill into fields, not a single description.
            self.assertGreater(len(fields), 0)


if __name__ == "__main__":
    unittest.main()
