"""Bank deposit/withdraw and storage capacity tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import config
from database import Database
from utils.bank_capacity import bank_capacity, max_storage_tokens


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
        moved = await self.db.deposit_to_bank(uid, gid, 200.0)
        self.assertAlmostEqual(moved, 200.0)
        self.assertEqual(await self.db.get_balance(uid, gid), 300.0)
        self.assertEqual(await self.db.get_bank(uid, gid), 200.0)
        self.assertTrue(await self.db.withdraw_from_bank(uid, gid, 50.0))
        self.assertEqual(await self.db.get_balance(uid, gid), 350.0)
        self.assertEqual(await self.db.get_bank(uid, gid), 150.0)

    async def test_deposit_all_respects_capacity(self) -> None:
        uid, gid = 2, 100
        await self.db.credit_wallet(uid, gid, 80_000.0)
        moved = await self.db.deposit_all_to_bank(uid, gid)
        self.assertAlmostEqual(moved, config.BANK_BASE_CAPACITY)
        self.assertAlmostEqual(await self.db.get_bank(uid, gid), config.BANK_BASE_CAPACITY)
        self.assertAlmostEqual(
            await self.db.get_balance(uid, gid),
            80_000.0 - config.BANK_BASE_CAPACITY,
        )

    async def test_deposit_capped_at_remaining_room(self) -> None:
        uid, gid = 3, 100
        await self.db.credit_wallet(uid, gid, 100_000.0)
        await self.db.deposit_to_bank(uid, gid, 40_000.0)
        moved = await self.db.deposit_to_bank(uid, gid, 20_000.0)
        self.assertAlmostEqual(moved, 10_000.0)
        self.assertAlmostEqual(await self.db.get_bank(uid, gid), config.BANK_BASE_CAPACITY)

    async def test_buy_storage_token_increases_capacity(self) -> None:
        uid, gid = 4, 100
        await self.db.credit_wallet(uid, gid, 20_000.0)
        self.assertIsNone(await self.db.buy_bank_storage_token(uid, gid))
        self.assertEqual(await self.db.get_bank_storage_tokens(uid, gid), 1)
        cap = await self.db.get_bank_capacity(uid, gid)
        self.assertAlmostEqual(cap, config.BANK_BASE_CAPACITY + config.BANK_STORAGE_PER_TOKEN)

    async def test_storage_token_costs_wallet(self) -> None:
        uid, gid = 5, 100
        await self.db.credit_wallet(uid, gid, config.BANK_STORAGE_TOKEN_COST)
        self.assertIsNone(await self.db.buy_bank_storage_token(uid, gid))
        self.assertAlmostEqual(await self.db.get_balance(uid, gid), 0.0)

    async def test_max_storage_tokens(self) -> None:
        self.assertEqual(max_storage_tokens(), 45)
        self.assertAlmostEqual(bank_capacity(45), config.BANK_MAX_CAPACITY)

    async def test_leaderboard_uses_net_worth(self) -> None:
        await self.db.credit_wallet(1, 100, 100.0)
        await self.db.credit_wallet(2, 100, 200.0)
        await self.db.deposit_to_bank(2, 100, 150.0)
        rows = await self.db.leaderboard(100, limit=5)
        self.assertEqual(int(rows[0]["user_id"]), 2)
        self.assertEqual(float(rows[0]["net"]), 200.0)


if __name__ == "__main__":
    unittest.main()
