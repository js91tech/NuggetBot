"""Tier 2 retention: weekly quests, calendar, activity pass, referrals."""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

import config
from database import Database
from utils.quests import TRACK_WEEKLY, ensure_weekly_quests, weekly_reset_key


class Tier2RetentionTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_calendar_claim_and_streak(self) -> None:
        await self.db.ensure_user(self.user_a, self.guild_id)
        now = time.time()
        reward, day, err = await self.db.claim_calendar(self.user_a, self.guild_id, now)
        self.assertIsNone(err)
        self.assertEqual(day, 1)
        self.assertAlmostEqual(reward, float(config.CALENDAR_REWARDS[0]))

        reward2, day2, err2 = await self.db.claim_calendar(
            self.user_a, self.guild_id, now + config.CALENDAR_CLAIM_COOLDOWN_SECONDS + 1,
        )
        self.assertIsNone(err2)
        self.assertEqual(day2, 2)
        self.assertGreater(reward2 or 0, 0)

    async def test_calendar_cooldown(self) -> None:
        await self.db.ensure_user(self.user_a, self.guild_id)
        now = time.time()
        await self.db.claim_calendar(self.user_a, self.guild_id, now)
        _, _, err = await self.db.claim_calendar(self.user_a, self.guild_id, now + 60)
        self.assertEqual(err, "cooldown")

    async def test_pass_xp_and_claim(self) -> None:
        await self.db.ensure_user(self.user_a, self.guild_id)
        await self.db.add_activity_xp(self.user_a, self.guild_id, config.PASS_TIER_XP[0] + 50)
        state = await self.db.get_pass_state(self.user_a, self.guild_id)
        self.assertGreaterEqual(state["pass_xp"], config.PASS_TIER_XP[0])
        total, tiers = await self.db.claim_pass_tiers(self.user_a, self.guild_id)
        self.assertGreater(total, 0)
        self.assertIn(0, tiers)

    async def test_referral_rewards(self) -> None:
        await self.db.ensure_user(self.user_a, self.guild_id)
        await self.db.ensure_user(self.user_b, self.guild_id)
        code = await self.db.ensure_referral_code(self.user_a, self.guild_id)
        err = await self.db.apply_referral_code(self.user_b, self.guild_id, code)
        self.assertIsNone(err)
        self.assertAlmostEqual(
            await self.db.get_balance(self.user_b, self.guild_id),
            config.REFERRAL_REFEREE_REWARD,
        )
        self.assertAlmostEqual(
            await self.db.get_balance(self.user_a, self.guild_id),
            config.REFERRAL_REFERRER_REWARD,
        )

    async def test_referral_once_only(self) -> None:
        await self.db.ensure_user(self.user_a, self.guild_id)
        await self.db.ensure_user(self.user_b, self.guild_id)
        code = await self.db.ensure_referral_code(self.user_a, self.guild_id)
        await self.db.apply_referral_code(self.user_b, self.guild_id, code)
        err = await self.db.apply_referral_code(self.user_b, self.guild_id, code)
        self.assertEqual(err, "already_referred")

    async def test_weekly_quests_assigned(self) -> None:
        await self.db.ensure_user(self.user_a, self.guild_id)
        await ensure_weekly_quests(self.db, self.guild_id, self.user_a)
        rows = await self.db.list_user_quests(self.guild_id, self.user_a, TRACK_WEEKLY)
        self.assertEqual(len(rows), config.WEEKLY_QUEST_COUNT)
        self.assertTrue(all(str(r["reset_key"]) == weekly_reset_key() for r in rows))


if __name__ == "__main__":
    unittest.main()
