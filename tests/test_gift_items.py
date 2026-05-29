"""Gift inventory items between players."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from database import Database
from items import CONSUMABLE_USE_IDS, GIFT_ONLY_ITEM_IDS, GIFTABLE_ITEM_IDS


class GiftItemTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.db = Database(self.db_path)
        await self.db.connect()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        Path(self.db_path).unlink(missing_ok=True)

    async def test_gift_chia_seeds_transfers_quantity(self) -> None:
        guild_id = 1
        sender, receiver = 100, 200
        await self.db.ensure_user(sender, guild_id)
        await self.db.ensure_user(receiver, guild_id)
        await self.db.grant_item(sender, guild_id, "chia_seeds")
        await self.db.grant_item(sender, guild_id, "chia_seeds")
        await self.db.grant_item(sender, guild_id, "chia_seeds")

        err = await self.db.gift_inventory_item(
            sender, receiver, guild_id, "chia_seeds", 2,
        )
        self.assertIsNone(err)
        self.assertEqual(
            await self.db.get_inventory_quantity(sender, guild_id, "chia_seeds"),
            1,
        )
        self.assertEqual(
            await self.db.get_inventory_quantity(receiver, guild_id, "chia_seeds"),
            2,
        )

    async def test_gift_increments_stats(self) -> None:
        guild_id = 1
        sender, receiver = 100, 200
        await self.db.ensure_user(sender, guild_id)
        await self.db.ensure_user(receiver, guild_id)
        await self.db.grant_item(sender, guild_id, "honey_jar")
        await self.db.grant_item(sender, guild_id, "honey_jar")
        await self.db.grant_item(sender, guild_id, "honey_jar")

        err = await self.db.gift_inventory_item(
            sender, receiver, guild_id, "honey_jar", 2,
        )
        self.assertIsNone(err)

        sender_progress = await self.db.get_user_progress(sender, guild_id)
        receiver_progress = await self.db.get_user_progress(receiver, guild_id)
        self.assertEqual(int(sender_progress["gifts_sent"]), 2)
        self.assertEqual(int(receiver_progress["gifts_received"]), 2)

    async def test_gift_only_items_not_usable(self) -> None:
        for item_id in GIFT_ONLY_ITEM_IDS:
            self.assertIn(item_id, GIFTABLE_ITEM_IDS)
            self.assertNotIn(item_id, CONSUMABLE_USE_IDS)
        self.assertIn("chia_seeds", CONSUMABLE_USE_IDS)

    async def test_chia_use_restores_energy(self) -> None:
        guild_id = 1
        user_id = 100
        await self.db.ensure_user(user_id, guild_id)
        await self.db.grant_item(user_id, guild_id, "chia_seeds")
        self.assertTrue(
            await self.db.consume_inventory_item(user_id, guild_id, "chia_seeds"),
        )
        new_energy = await self.db.add_energy(user_id, guild_id, 8)
        self.assertGreaterEqual(new_energy, 8)


if __name__ == "__main__":
    unittest.main()
