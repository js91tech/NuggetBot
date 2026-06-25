"""Gear instance sync on buy and drug timer / opioid effect tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import Database
from utils.drugs import (
    DRUG_EFFECT_DURATION_MAX_SECONDS,
    DRUG_EFFECT_DURATION_MIN_SECONDS,
    DRUGS,
    drug_by_id,
    drug_effect_duration,
)


class DrugDurationTests(unittest.TestCase):
    def test_lowest_tier_is_thirty_seconds(self) -> None:
        lowest = drug_by_id(DRUGS[0].drug_id)
        assert lowest is not None
        self.assertAlmostEqual(drug_effect_duration(lowest), DRUG_EFFECT_DURATION_MIN_SECONDS)

    def test_highest_tier_is_three_minutes(self) -> None:
        highest = drug_by_id(DRUGS[-1].drug_id)
        assert highest is not None
        self.assertAlmostEqual(drug_effect_duration(highest), DRUG_EFFECT_DURATION_MAX_SECONDS)

    def test_durations_increase_with_tier(self) -> None:
        durations = [drug_effect_duration(defn) for defn in DRUGS]
        self.assertEqual(durations, sorted(durations))


class GearInstanceBuyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.db = Database(str(self.db_path))
        await self.db.connect()
        await self.db.init_schema()
        self.guild_id = 1
        self.user_id = 42

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def test_buy_item_creates_gear_instances(self) -> None:
        await self.db.ensure_user(self.user_id, self.guild_id)
        await self.db.credit_wallet(self.user_id, self.guild_id, 50_000.0, apply_bonuses=False)
        ok = await self.db.buy_item(self.user_id, self.guild_id, "iron_sword", 500.0, quantity=2)
        self.assertTrue(ok)
        instances = await self.db.list_gear_instances(self.user_id, self.guild_id)
        self.assertEqual(len(instances), 2)
        self.assertTrue(all(str(row["item_id"]) == "iron_sword" for row in instances))

    async def test_sync_backfills_missing_instances(self) -> None:
        await self.db.ensure_user(self.user_id, self.guild_id)
        await self.db.grant_item(self.user_id, self.guild_id, "iron_sword")
        await self.db.grant_item(self.user_id, self.guild_id, "iron_sword")
        instances = await self.db.list_gear_instances(self.user_id, self.guild_id)
        for row in instances[:1]:
            await self.db.conn.execute(
                "DELETE FROM gear_instances WHERE instance_id = ?",
                (int(row["instance_id"]),),
            )
            await self.db.conn.commit()
        created = await self.db.sync_gear_instances_from_inventory(self.user_id, self.guild_id)
        self.assertEqual(created, 1)
        self.assertEqual(len(await self.db.list_gear_instances(self.user_id, self.guild_id)), 2)


class DrugBuffDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.db = Database(str(self.db_path))
        await self.db.connect()
        await self.db.init_schema()
        self.guild_id = 9
        self.user_id = 77

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def _stock(self, drug_id: str, qty: int = 1) -> None:
        await self.db.ensure_user(self.user_id, self.guild_id)
        await self.db.conn.execute(
            """
            INSERT INTO drug_inventory (user_id, guild_id, drug_id, quantity)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, guild_id, drug_id) DO UPDATE SET
                quantity = excluded.quantity
            """,
            (self.user_id, self.guild_id, drug_id, qty),
        )
        await self.db.conn.commit()

    async def test_pending_drug_buff_persists_across_reads(self) -> None:
        await self._stock("girl_scout_cookies")
        consumed = await self.db.consume_drug(
            self.user_id, self.guild_id, "girl_scout_cookies", max_hp=200.0,
        )
        self.assertIsNone(consumed["error"])
        self.assertGreater(float(consumed["buff_duration"] or 0), 0)
        first = await self.db.peek_pending_drug_buff(self.user_id, self.guild_id)
        second = await self.db.take_pending_drug_buff(self.user_id, self.guild_id)
        third = await self.db.peek_pending_drug_buff(self.user_id, self.guild_id)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNotNone(third)

    async def test_heroin_grants_cc_immunity_buff(self) -> None:
        await self._stock("heroin")
        result = await self.db.consume_drug(self.user_id, self.guild_id, "heroin", max_hp=200.0)
        self.assertIsNone(result["error"])
        self.assertTrue(result["cc_immunity"])
        buff = await self.db.peek_pending_drug_buff(self.user_id, self.guild_id)
        self.assertIsNotNone(buff)
        assert buff is not None
        self.assertTrue(buff["cc_immunity"])
        self.assertTrue(await self.db.has_active_drug_cc_immunity(self.user_id, self.guild_id))

    @patch("random.random", return_value=0.0)
    async def test_opioid_attack_hp_risk(self, _rng: object) -> None:
        await self._stock("fentanyl")
        await self.db.consume_drug(self.user_id, self.guild_id, "fentanyl", max_hp=200.0)
        await self.db.sync_combat_hp(self.user_id, self.guild_id, 200.0)
        damage, note = await self.db.roll_drug_attack_hp_risk(
            self.user_id, self.guild_id, max_hp=200.0,
        )
        self.assertGreater(damage, 0)
        self.assertIn("Withdrawal", note)
        hp, _ = await self.db.get_combat_state(self.user_id, self.guild_id)
        assert hp is not None
        self.assertLess(hp, 200.0)

    async def test_drug_buff_survives_shop_pending_consumable(self) -> None:
        await self._stock("heroin")
        await self.db.consume_drug(self.user_id, self.guild_id, "heroin", max_hp=200.0)
        await self.db.set_pending_consumable(self.user_id, self.guild_id, "raid_potion")
        self.assertTrue(await self.db.has_active_drug_cc_immunity(self.user_id, self.guild_id))
        self.assertTrue(await self.db.take_pending_consumable(self.user_id, self.guild_id, "raid_potion"))
        self.assertTrue(await self.db.has_active_drug_cc_immunity(self.user_id, self.guild_id))

    async def test_cc_immunity_clears_frost_debuff(self) -> None:
        now = __import__("time").time()
        await self.db.apply_boss_element_status(
            self.guild_id, self.user_id, frost_slow_until=now + 60,
        )
        await self._stock("heroin")
        await self.db.consume_drug(self.user_id, self.guild_id, "heroin", max_hp=200.0)
        status = await self.db.get_boss_raider_status(self.guild_id, self.user_id)
        self.assertIsNotNone(status)
        assert status is not None
        self.assertLessEqual(float(status["attack_slow_until"]), now)

    async def test_cc_immunity_clears_debuff_attack_recovery(self) -> None:
        now = __import__("time").time()
        await self.db.record_boss_attack_time(self.guild_id, self.user_id, now)
        await self.db.apply_boss_element_status(
            self.guild_id,
            self.user_id,
            frost_slow_until=now + 30,
            debuff_attack_cooldown=10.0,
        )
        before = await self.db.boss_attack_cooldown_remaining(
            self.guild_id, self.user_id, at=now + 1,
        )
        self.assertIsNotNone(before)
        assert before is not None
        self.assertGreater(before, 7.0)

        await self._stock("heroin")
        await self.db.consume_drug(self.user_id, self.guild_id, "heroin", max_hp=200.0)
        after = await self.db.boss_attack_cooldown_remaining(
            self.guild_id, self.user_id, at=now + 1,
        )
        self.assertIsNone(after)

    async def test_cc_immunity_ignores_stale_debuff_cooldown(self) -> None:
        now = __import__("time").time()
        await self._stock("heroin")
        await self.db.consume_drug(self.user_id, self.guild_id, "heroin", max_hp=200.0)
        await self.db.record_boss_attack_time(self.guild_id, self.user_id, now)
        await self.db.apply_boss_element_status(
            self.guild_id,
            self.user_id,
            frost_slow_until=now + 30,
            debuff_attack_cooldown=10.0,
        )
        remaining = await self.db.boss_attack_cooldown_remaining(
            self.guild_id, self.user_id, at=now + 1,
        )
        self.assertIsNotNone(remaining)
        assert remaining is not None
        self.assertLess(remaining, 8.0)


if __name__ == "__main__":
    unittest.main()
