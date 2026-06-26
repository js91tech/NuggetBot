"""Bank deposit/withdraw tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import config
from database import Database
from utils.bank_capacity import bank_capacity


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

    async def test_default_bank_capacity_is_100k(self) -> None:
        uid, gid = 3, 100
        self.assertEqual(await self.db.get_bank_capacity(uid, gid), config.BANK_BASE_CAPACITY)

    async def test_deposit_blocked_at_capacity(self) -> None:
        uid, gid = 4, 100
        await self.db.credit_wallet(uid, gid, 150_000.0)
        self.assertTrue(await self.db.deposit_to_bank(uid, gid, 100_000.0))
        self.assertFalse(await self.db.deposit_to_bank(uid, gid, 1.0))
        self.assertEqual(await self.db.get_bank(uid, gid), 100_000.0)

    async def test_deposit_all_respects_capacity(self) -> None:
        uid, gid = 5, 100
        await self.db.credit_wallet(uid, gid, 250_000.0)
        moved = await self.db.deposit_all_to_bank(uid, gid)
        self.assertEqual(moved, 100_000.0)
        self.assertEqual(await self.db.get_bank(uid, gid), 100_000.0)
        self.assertEqual(await self.db.get_balance(uid, gid), 150_000.0)

    async def test_expand_bank_tier1_increases_capacity(self) -> None:
        uid, gid = 6, 100
        await self.db.credit_wallet(uid, gid, 20_000.0)
        ok, reason = await self.db.expand_bank_capacity(uid, gid, 1)
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")
        expected = config.BANK_BASE_CAPACITY + float(config.BANK_EXPANSION_TIERS[1]["capacity"])
        self.assertEqual(await self.db.get_bank_capacity(uid, gid), expected)
        self.assertEqual(await self.db.get_bank_expansions(uid, gid), {1: 1})

    async def test_expand_bank_higher_tiers(self) -> None:
        uid, gid = 9, 100
        await self.db.credit_wallet(uid, gid, 600_000.0)
        ok, _ = await self.db.expand_bank_capacity(uid, gid, 2)
        self.assertTrue(ok)
        ok, _ = await self.db.expand_bank_capacity(uid, gid, 4)
        self.assertTrue(ok)
        expansions = await self.db.get_bank_expansions(uid, gid)
        self.assertEqual(expansions, {2: 1, 4: 1})
        expected = bank_capacity({2: 1, 4: 1})
        self.assertEqual(await self.db.get_bank_capacity(uid, gid), expected)

    async def test_mixed_tier_capacity_sum(self) -> None:
        uid, gid = 10, 100
        await self.db.credit_wallet(uid, gid, 300_000.0)
        await self.db.expand_bank_capacity(uid, gid, 1)
        await self.db.expand_bank_capacity(uid, gid, 1)
        await self.db.expand_bank_capacity(uid, gid, 3)
        expansions = await self.db.get_bank_expansions(uid, gid)
        self.assertEqual(expansions, {1: 2, 3: 1})
        self.assertEqual(await self.db.get_bank_capacity(uid, gid), bank_capacity(expansions))

    async def test_invalid_expansion_tier_rejected(self) -> None:
        uid, gid = 11, 100
        await self.db.credit_wallet(uid, gid, 100_000.0)
        ok, reason = await self.db.expand_bank_capacity(uid, gid, 99)
        self.assertFalse(ok)
        self.assertEqual(reason, "invalid_tier")

    async def test_prestige_below_max_keeps_bank(self) -> None:
        uid, gid = 7, 100
        await self.db.credit_wallet(uid, gid, 200_000.0)
        await self.db.deposit_to_bank(uid, gid, 50_000.0)
        await self.db.expand_bank_capacity(uid, gid, 1)
        for _ in range(9):
            await self.db.credit_wallet(uid, gid, config.PRESTIGE_MIN_WALLET)
            level = await self.db.prestige_user(uid, gid)
        self.assertEqual(level, 9)
        self.assertEqual(await self.db.get_bank(uid, gid), 50_000.0)
        self.assertEqual(await self.db.get_bank_expansions(uid, gid), {1: 1})

    async def test_prestige_10_resets_bank_and_expansions(self) -> None:
        uid, gid = 8, 100
        await self.db.credit_wallet(uid, gid, 200_000.0)
        await self.db.deposit_to_bank(uid, gid, 80_000.0)
        await self.db.expand_bank_capacity(uid, gid, 1)
        for _ in range(9):
            await self.db.credit_wallet(uid, gid, config.PRESTIGE_MIN_WALLET)
            await self.db.prestige_user(uid, gid)
        await self.db.credit_wallet(uid, gid, config.PRESTIGE_MIN_WALLET)
        level = await self.db.prestige_user(uid, gid)
        self.assertEqual(level, 10)
        self.assertEqual(await self.db.get_balance(uid, gid), 0.0)
        self.assertEqual(await self.db.get_bank(uid, gid), 0.0)
        self.assertEqual(await self.db.get_bank_expansions(uid, gid), {})


if __name__ == "__main__":
    unittest.main()
