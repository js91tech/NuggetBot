"""Bail and jail key tests."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from database import Database
from utils.jail import bail_cost_for_tier, execute_bail, execute_jail_key


class BailCostTests(unittest.TestCase):
    def test_wallet_heist_bail(self) -> None:
        self.assertEqual(bail_cost_for_tier("wallet"), 15_000.0)

    def test_bank_tier_bails(self) -> None:
        self.assertEqual(bail_cost_for_tier("1"), 30_000.0)
        self.assertEqual(bail_cost_for_tier("2"), 40_000.0)
        self.assertEqual(bail_cost_for_tier("3"), 55_000.0)

    def test_unknown_tier_defaults_wallet(self) -> None:
        self.assertEqual(bail_cost_for_tier(None), 15_000.0)


class JailFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.db = Database(str(self.db_path))
        await self.db.connect()
        await self.db.init_schema()
        self.guild_id = 9001
        self.uid = 42
        self.helper = 99
        await self.db.credit_wallet(self.uid, self.guild_id, 100_000.0)
        await self.db.credit_wallet(self.helper, self.guild_id, 100_000.0)
        await self.db.grant_item(self.helper, self.guild_id, "jail_key")

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def test_bail_releases_and_charges_wallet(self) -> None:
        await self.db.set_arrested_until(
            self.uid,
            self.guild_id,
            time.time() + 3600,
            arrest_tier="2",
        )
        result = await execute_bail(self.db, self.uid, self.uid, self.guild_id)
        self.assertTrue(result.ok)
        self.assertFalse(await self.db.is_arrested(self.uid, self.guild_id))
        balance = await self.db.get_balance(self.uid, self.guild_id)
        self.assertEqual(balance, 60_000.0)

    async def test_bail_for_other_player(self) -> None:
        await self.db.set_arrested_until(
            self.uid,
            self.guild_id,
            time.time() + 3600,
            arrest_tier="wallet",
        )
        result = await execute_bail(self.db, self.helper, self.uid, self.guild_id)
        self.assertTrue(result.ok)
        self.assertFalse(await self.db.is_arrested(self.uid, self.guild_id))
        self.assertEqual(await self.db.get_balance(self.helper, self.guild_id), 85_000.0)

    async def test_jail_key_on_other(self) -> None:
        await self.db.set_arrested_until(
            self.uid,
            self.guild_id,
            time.time() + 3600,
            arrest_tier="3",
        )
        result = await execute_jail_key(self.db, self.helper, self.uid, self.guild_id)
        self.assertTrue(result.ok)
        self.assertFalse(await self.db.is_arrested(self.uid, self.guild_id))
        self.assertEqual(
            await self.db.get_inventory_quantity(self.helper, self.guild_id, "jail_key"),
            0,
        )


if __name__ == "__main__":
    unittest.main()
