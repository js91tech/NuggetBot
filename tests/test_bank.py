"""Bank deposit/withdraw tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database import Database


class BankTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.db = Database(str(self.db_path))
        await self.db.connect()
        await self.db.init_schema()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def test_deposit_and_withdraw(self) -> None:
        uid, gid = 1, 100
        await self.db.credit_wallet(uid, gid, 500.0)
        self.assertTrue(await self.db.deposit_to_bank(uid, gid, 200.0))
        self.assertEqual(await self.db.get_balance(uid, gid), 300.0)
        self.assertEqual(await self.db.get_bank(uid, gid), 200.0)
        self.assertTrue(await self.db.withdraw_from_bank(uid, gid, 50.0))
        self.assertEqual(await self.db.get_balance(uid, gid), 350.0)
        self.assertEqual(await self.db.get_bank(uid, gid), 150.0)

    async def test_deposit_all_leaves_wallet_empty(self) -> None:
        uid, gid = 2, 100
        await self.db.credit_wallet(uid, gid, 120.0)
        moved = await self.db.deposit_all_to_bank(uid, gid)
        self.assertEqual(moved, 120.0)
        self.assertEqual(await self.db.get_balance(uid, gid), 0.0)
        self.assertEqual(await self.db.get_bank(uid, gid), 120.0)

    async def test_leaderboard_uses_net_worth(self) -> None:
        await self.db.credit_wallet(1, 100, 100.0)
        await self.db.credit_wallet(2, 100, 200.0)
        await self.db.deposit_to_bank(2, 100, 150.0)
        rows = await self.db.leaderboard(100, limit=5)
        self.assertEqual(int(rows[0]["user_id"]), 2)
        self.assertEqual(float(rows[0]["net"]), 200.0)


if __name__ == "__main__":
    unittest.main()
