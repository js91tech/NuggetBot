"""Interactive district influence: contest, undermine, fortify, suppress, buyout discount."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

import config
from database import Database
from utils.districts import apply_buyout_influence_discount, buyout_payout


class BuyoutDiscountHelperTests(unittest.TestCase):
    def test_discount_reduces_burn_only(self) -> None:
        owner, burn, pays = buyout_payout(1_000.0)
        d_owner, d_burn, d_pays, discounted = apply_buyout_influence_discount(
            owner, burn, pays, config.DISTRICT_BUYOUT_INFLUENCE_DISCOUNT_THRESHOLD,
        )
        self.assertTrue(discounted)
        self.assertEqual(d_owner, owner)
        self.assertLess(d_burn, burn)
        self.assertEqual(d_pays, d_owner + d_burn)

    def test_no_discount_below_threshold(self) -> None:
        owner, burn, pays = buyout_payout(1_000.0)
        _, _, new_pays, discounted = apply_buyout_influence_discount(
            owner, burn, pays, config.DISTRICT_BUYOUT_INFLUENCE_DISCOUNT_THRESHOLD - 1,
        )
        self.assertFalse(discounted)
        self.assertEqual(new_pays, pays)


class DistrictInfluenceOpsDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / "test.sqlite3"))
        await self.db.connect()
        await self.db.init_schema()
        self.guild_id = 77
        self.a = 1001
        self.b = 1002
        self.c = 1003
        for uid in (self.a, self.b, self.c):
            await self.db.ensure_user(uid, self.guild_id)
            await self.db.conn.execute(
                "UPDATE users SET wallet = 500000000 WHERE user_id = ? AND guild_id = ?",
                (uid, self.guild_id),
            )
        await self.db.conn.commit()
        for uid in (self.a, self.b, self.c):
            self.assertIsNone(await self.db.create_business(uid, self.guild_id))
            await self.db.conn.execute(
                """
                UPDATE user_businesses
                SET tier = 7, tier_id = 'corporation', district_id = 'downtown'
                WHERE user_id = ? AND guild_id = ?
                """,
                (uid, self.guild_id),
            )
        await self.db.conn.commit()
        # Crew Alpha: a+b ; Crew Beta: c
        for uid in (self.a, self.b):
            err = await self.db.join_crew(uid, self.guild_id, "Alpha")
            self.assertIsNone(err)
        err = await self.db.join_crew(self.c, self.guild_id, "Beta")
        self.assertIsNone(err)

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def test_contest_seizes_war_control(self) -> None:
        await self.db.add_district_influence(
            self.guild_id, "downtown", "user", str(self.a), 40,
        )
        result = await self.db.contest_district_war(self.a, self.guild_id, "downtown")
        self.assertIsNone(result.get("error"))
        control = await self.db.get_district_war_control(self.guild_id, "downtown")
        self.assertIsNotNone(control)
        self.assertEqual(str(control["crew_name"]), "Alpha")
        left = await self.db.get_user_district_influence(self.a, self.guild_id, "downtown")
        self.assertAlmostEqual(left, 40 - config.DISTRICT_WAR_CONTEST_COST)

    async def test_undermine_strips_leader(self) -> None:
        await self.db.add_district_influence(
            self.guild_id, "downtown", "user", str(self.b), 30,
        )
        await self.db.add_district_influence(
            self.guild_id, "downtown", "user", str(self.a), 10,
        )
        result = await self.db.undermine_district_influence(
            self.a, self.guild_id, "downtown", points=5,
        )
        self.assertIsNone(result.get("error"))
        self.assertEqual(int(result["target_id"]), self.b)
        after = await self.db.get_user_district_influence(self.b, self.guild_id, "downtown")
        self.assertAlmostEqual(after, 25)

    async def test_fortify_adds_temporary_influence(self) -> None:
        result = await self.db.fortify_district_influence(
            self.a, self.guild_id, "downtown", points=5,
        )
        self.assertIsNone(result.get("error"))
        score = await self.db.get_user_district_influence(self.a, self.guild_id, "downtown")
        self.assertAlmostEqual(score, 5)
        # Expired fortify should vanish.
        await self.db.conn.execute(
            """
            UPDATE district_influence_fortify
            SET expires_at = ?
            WHERE guild_id = ? AND district_id = ? AND user_id = ?
            """,
            (time.time() - 1, self.guild_id, "downtown", self.a),
        )
        await self.db.conn.commit()
        score = await self.db.get_user_district_influence(self.a, self.guild_id, "downtown")
        self.assertAlmostEqual(score, 0)

    async def test_suppress_requires_deed_and_blocks_war_bonus(self) -> None:
        cost, err = await self.db.claim_district_deed(self.a, self.guild_id, "downtown")
        self.assertIsNone(err)
        self.assertGreater(cost, 0)
        await self.db.add_district_influence(
            self.guild_id, "downtown", "user", str(self.a), 40,
        )
        await self.db.contest_district_war(self.a, self.guild_id, "downtown")
        # Reset contest cooldown via direct wipe for suppress path.
        await self.db.conn.execute(
            "DELETE FROM business_action_cooldowns WHERE user_id = ?",
            (self.a,),
        )
        await self.db.conn.commit()
        result = await self.db.suppress_district_war(self.a, self.guild_id, "downtown")
        self.assertIsNone(result.get("error"))
        until = await self.db.get_district_war_suppress_until(self.guild_id, "downtown")
        self.assertGreater(until, time.time())
        biz = await self.db.get_business(self.a, self.guild_id)
        mult = await self.db._district_war_income_mult_no_lock(
            self.a, self.guild_id, biz, time.time(),
        )
        self.assertEqual(mult, 1.0)

    async def test_buyout_preview_applies_influence_discount(self) -> None:
        await self.db.claim_district_deed(self.a, self.guild_id, "downtown")
        await self.db.add_district_influence(
            self.guild_id, "downtown", "user", str(self.b),
            float(config.DISTRICT_BUYOUT_INFLUENCE_DISCOUNT_THRESHOLD),
        )
        _, burn_hi, pays_hi, err = await self.db.preview_district_buyout(
            self.guild_id, "downtown", buyer_id=self.b,
        )
        self.assertIsNone(err)
        await self.db.conn.execute(
            """
            UPDATE district_influence SET influence = 0
            WHERE guild_id = ? AND entity_id = ?
            """,
            (self.guild_id, str(self.b)),
        )
        await self.db.conn.commit()
        _, burn_lo, pays_lo, err = await self.db.preview_district_buyout(
            self.guild_id, "downtown", buyer_id=self.b,
        )
        self.assertIsNone(err)
        self.assertLess(burn_hi, burn_lo)
        self.assertLess(pays_hi, pays_lo)

    async def test_crew_standings_include_fortify(self) -> None:
        await self.db.fortify_district_influence(self.a, self.guild_id, "downtown", points=8)
        standings = await self.db.list_district_crew_influence(
            self.guild_id, "downtown", limit=3,
        )
        self.assertTrue(standings)
        self.assertEqual(standings[0][0], "Alpha")
        self.assertGreaterEqual(standings[0][1], 8)


if __name__ == "__main__":
    unittest.main()
