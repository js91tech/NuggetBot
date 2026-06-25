"""Tier 1 retention: streaks, activity XP, trades, weekly stats."""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import config
from database import Database
from utils.activity_levels import level_from_total_xp


class Tier1RetentionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.db = Database(self.db_path)
        await self.db.connect()
        self.guild_id = 1
        self.user_a = 100
        self.user_b = 200

    async def asyncTearDown(self) -> None:
        await self.db.close()
        Path(self.db_path).unlink(missing_ok=True)

    async def test_daily_streak_increments_and_bonus(self) -> None:
        await self.db.ensure_user(self.user_a, self.guild_id)
        now = time.time()
        r1 = await self.db.claim_daily(
            self.user_a, self.guild_id, 100.0, config.DAILY_COOLDOWN_SECONDS, now,
        )
        self.assertIsNone(r1.remaining)
        self.assertEqual(r1.streak, 1)
        self.assertAlmostEqual(r1.streak_bonus_mult, 1.0)

        r2 = await self.db.claim_daily(
            self.user_a,
            self.guild_id,
            100.0,
            config.DAILY_COOLDOWN_SECONDS,
            now + config.DAILY_COOLDOWN_SECONDS + 1,
        )
        self.assertIsNone(r2.remaining)
        self.assertEqual(r2.streak, 2)
        self.assertGreater(r2.streak_bonus_mult, 1.0)

    async def test_daily_streak_resets_after_grace(self) -> None:
        await self.db.ensure_user(self.user_a, self.guild_id)
        now = time.time()
        await self.db.claim_daily(
            self.user_a, self.guild_id, 50.0, config.DAILY_COOLDOWN_SECONDS, now,
        )
        late = now + config.DAILY_COOLDOWN_SECONDS + config.DAILY_STREAK_GRACE_SECONDS + 10
        r = await self.db.claim_daily(
            self.user_a, self.guild_id, 50.0, config.DAILY_COOLDOWN_SECONDS, late,
        )
        self.assertEqual(r.streak, 1)

    async def test_activity_xp_and_levels(self) -> None:
        await self.db.ensure_user(self.user_a, self.guild_id)
        total, old, new = await self.db.add_activity_xp(self.user_a, self.guild_id, 500)
        self.assertEqual(old, 1)
        self.assertGreaterEqual(new, 1)
        level, _, _ = level_from_total_xp(total)
        self.assertEqual(level, new)

    async def test_trade_escrow_and_accept(self) -> None:
        await self.db.ensure_user(self.user_a, self.guild_id)
        await self.db.ensure_user(self.user_b, self.guild_id)
        await self.db.credit_wallet(self.user_a, self.guild_id, 500.0)

        trade_id, err = await self.db.create_pending_trade(
            self.user_a,
            self.user_b,
            self.guild_id,
            nuggets=100.0,
            drugs={},
            gear_instance_ids=[],
        )
        self.assertIsNone(err)
        assert trade_id is not None

        self.assertAlmostEqual(await self.db.get_balance(self.user_a, self.guild_id), 400.0)
        self.assertAlmostEqual(await self.db.get_balance(self.user_b, self.guild_id), 0.0)

        resolve_err = await self.db.resolve_trade(trade_id, self.guild_id, self.user_b, "accept")
        self.assertIsNone(resolve_err)
        self.assertAlmostEqual(await self.db.get_balance(self.user_b, self.guild_id), 100.0)

    async def test_trade_decline_refunds(self) -> None:
        await self.db.ensure_user(self.user_a, self.guild_id)
        await self.db.ensure_user(self.user_b, self.guild_id)
        await self.db.credit_wallet(self.user_a, self.guild_id, 200.0)

        trade_id, _ = await self.db.create_pending_trade(
            self.user_a, self.user_b, self.guild_id,
            nuggets=75.0, drugs={}, gear_instance_ids=[],
        )
        assert trade_id is not None
        await self.db.resolve_trade(trade_id, self.guild_id, self.user_b, "decline")
        self.assertAlmostEqual(await self.db.get_balance(self.user_a, self.guild_id), 200.0)

    async def test_weekly_stats_increment(self) -> None:
        await self.db.ensure_user(self.user_a, self.guild_id)
        await self.db.increment_weekly_stat(
            self.user_a, self.guild_id, boss_damage=1500, drug_sales=10,
        )
        rows = await self.db.weekly_leaderboard(self.guild_id, "boss_damage", limit=5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]["user_id"]), self.user_a)
        self.assertEqual(float(rows[0]["score"]), 1500.0)

    async def test_notify_flags(self) -> None:
        await self.db.ensure_user(self.user_a, self.guild_id)
        flags = await self.db.get_notify_flags(self.user_a, self.guild_id)
        self.assertEqual(flags, config.NOTIFY_DEFAULT_FLAGS)
        await self.db.set_notify_flags(self.user_a, self.guild_id, config.NOTIFY_CROPS)
        self.assertEqual(
            await self.db.get_notify_flags(self.user_a, self.guild_id),
            config.NOTIFY_CROPS,
        )

    async def test_trade_with_drugs(self) -> None:
        await self.db.ensure_user(self.user_a, self.guild_id)
        await self.db.ensure_user(self.user_b, self.guild_id)
        async with self.db._write_lock:
            await self.db.conn.execute(
                """
                INSERT INTO drug_inventory (user_id, guild_id, drug_id, quantity)
                VALUES (?, ?, ?, ?)
                """,
                (self.user_a, self.guild_id, "blue_dream", 5),
            )
            await self.db.conn.commit()

        trade_id, err = await self.db.create_pending_trade(
            self.user_a, self.user_b, self.guild_id,
            nuggets=0, drugs={"blue_dream": 3}, gear_instance_ids=[],
        )
        self.assertIsNone(err)
        assert trade_id is not None
        inv_a = await self.db.get_drug_inventory(self.user_a, self.guild_id)
        self.assertEqual(inv_a.get("blue_dream", 0), 2)

        await self.db.resolve_trade(trade_id, self.guild_id, self.user_b, "accept")
        inv_b = await self.db.get_drug_inventory(self.user_b, self.guild_id)
        self.assertGreaterEqual(inv_b.get("blue_dream", 0), 3)


if __name__ == "__main__":
    unittest.main()
