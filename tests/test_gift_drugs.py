"""Gift drug stash units between players."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from database import Database


class GiftDrugTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.db = Database(self.db_path)
        await self.db.connect()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        Path(self.db_path).unlink(missing_ok=True)

    async def test_gift_drug_units_transfers_quantity(self) -> None:
        guild_id = 1
        sender, receiver = 100, 200
        await self.db.ensure_user(sender, guild_id)
        await self.db.ensure_user(receiver, guild_id)
        await self.db.grant_drug_units(sender, guild_id, "blue_dream", 5)

        err = await self.db.gift_drug_units(
            sender, receiver, guild_id, "blue_dream", 3,
        )
        self.assertIsNone(err)
        self.assertEqual(
            (await self.db.get_drug_inventory(sender, guild_id)).get("blue_dream", 0),
            2,
        )
        self.assertEqual(
            (await self.db.get_drug_inventory(receiver, guild_id)).get("blue_dream", 0),
            3,
        )

    async def test_gift_drug_units_rejects_insufficient_and_self(self) -> None:
        guild_id = 1
        sender, receiver = 100, 200
        await self.db.ensure_user(sender, guild_id)
        await self.db.ensure_user(receiver, guild_id)
        await self.db.grant_drug_units(sender, guild_id, "og_kush", 1)

        self.assertEqual(
            await self.db.gift_drug_units(sender, sender, guild_id, "og_kush", 1),
            "self_gift",
        )
        self.assertEqual(
            await self.db.gift_drug_units(sender, receiver, guild_id, "og_kush", 2),
            "insufficient_items",
        )
        self.assertEqual(
            await self.db.gift_drug_units(sender, receiver, guild_id, "not_a_drug", 1),
            "invalid_drug",
        )
        # Stash unchanged after failed gifts
        self.assertEqual(
            (await self.db.get_drug_inventory(sender, guild_id)).get("og_kush", 0),
            1,
        )
        self.assertEqual(
            (await self.db.get_drug_inventory(receiver, guild_id)).get("og_kush", 0),
            0,
        )


if __name__ == "__main__":
    unittest.main()
