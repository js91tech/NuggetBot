"""Tier 3 retention: crew challenge, milestones, contracts, gear market."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import config
from database import Database
from utils.milestones import milestone_eligible
from utils.quests import TRACK_CONTRACT, ensure_contract_quest


class Tier3RetentionTests(unittest.IsolatedAsyncioTestCase):
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

    async def _join_crew(self, user_id: int, crew: str) -> None:
        async with self.db._write_lock:
            await self.db.conn.execute(
                """
                INSERT INTO crew_stats (guild_id, crew_name, treasury)
                VALUES (?, ?, 0)
                ON CONFLICT(guild_id, crew_name) DO NOTHING
                """,
                (self.guild_id, crew),
            )
            await self.db.conn.execute(
                """
                INSERT INTO crew_members (guild_id, user_id, crew_name, joined_at)
                VALUES (?, ?, ?, 0)
                """,
                (self.guild_id, user_id, crew),
            )
            await self.db.conn.commit()

    async def test_crew_weekly_activity_aggregation(self) -> None:
        await self.db.ensure_user(self.user_a, self.guild_id)
        await self._join_crew(self.user_a, "Wolves")
        await self.db.increment_weekly_stat(
            self.user_a, self.guild_id, boss_damage=1000, drug_sales=5,
        )
        rows = await self.db.get_crew_weekly_standings(self.guild_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows[0]["crew_name"]), "Wolves")
        expected = 1000 + 5 * config.CREW_CHALLENGE_DRUG_WEIGHT
        self.assertAlmostEqual(float(rows[0]["activity_score"]), expected)

    async def test_milestone_claim_activity(self) -> None:
        await self.db.ensure_user(self.user_a, self.guild_id)
        await self.db.add_activity_xp(self.user_a, self.guild_id, 5000)
        self.assertTrue(
            await milestone_eligible(self.db, self.user_a, self.guild_id, "activity_10"),
        )
        reward, err = await self.db.claim_milestone(
            self.user_a, self.guild_id, "activity_10",
        )
        self.assertIsNone(err)
        self.assertEqual(reward, config.MILESTONE_REWARDS["activity_10"])

    async def test_gear_market_buy(self) -> None:
        await self.db.ensure_user(self.user_a, self.guild_id)
        await self.db.ensure_user(self.user_b, self.guild_id)
        await self.db.credit_wallet(self.user_b, self.guild_id, 5000.0)
        instance_id = await self.db.create_gear_instance(
            self.user_a, self.guild_id, "twig_sword",
        )
        err = await self.db.create_gear_listing(
            self.user_a, self.guild_id, instance_id, 250.0,
        )
        self.assertIsNone(err)
        listings = await self.db.list_gear_market(self.guild_id)
        self.assertEqual(len(listings), 1)
        buy_err = await self.db.buy_gear_listing(
            self.user_b, self.guild_id, int(listings[0]["listing_id"]),
        )
        self.assertIsNone(buy_err)
        inst = await self.db.get_gear_instance(instance_id, self.guild_id)
        assert inst is not None
        self.assertEqual(int(inst["user_id"]), self.user_b)

    async def test_contract_quest_assigned(self) -> None:
        await self.db.ensure_user(self.user_a, self.guild_id)
        await ensure_contract_quest(self.db, self.guild_id, self.user_a)
        rows = await self.db.list_user_quests(self.guild_id, self.user_a, TRACK_CONTRACT)
        self.assertEqual(len(rows), config.CONTRACT_QUEST_COUNT)

    async def test_has_completed_trade(self) -> None:
        await self.db.ensure_user(self.user_a, self.guild_id)
        await self.db.ensure_user(self.user_b, self.guild_id)
        await self.db.credit_wallet(self.user_a, self.guild_id, 100.0)
        self.assertFalse(await self.db.has_completed_trade(self.user_a, self.guild_id))
        trade_id, _ = await self.db.create_pending_trade(
            self.user_a, self.user_b, self.guild_id,
            nuggets=10.0, drugs={}, gear_instance_ids=[],
        )
        assert trade_id is not None
        await self.db.resolve_trade(trade_id, self.guild_id, self.user_b, "accept")
        self.assertTrue(await self.db.has_completed_trade(self.user_a, self.guild_id))


if __name__ == "__main__":
    unittest.main()
