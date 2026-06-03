"""Boss elemental counter effect tests."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from database import Database
from utils.boss_element_effects import (
    attack_cooldown_while_debuffed,
    element_hazard_text,
    roll_debuff_attack_cooldown,
    roll_element_proc,
)


class BossElementEffectUtilTests(unittest.TestCase):
    def test_element_hazard_text_known_elements(self) -> None:
        self.assertIn("Frost", element_hazard_text("frost") or "")
        self.assertIn("Fire", element_hazard_text("fire") or "")
        self.assertIn("Storm", element_hazard_text("storm") or "")

    def test_debuff_attack_cooldown_in_range(self) -> None:
        lo, hi = config.BOSS_DEBUFF_ATTACK_COOLDOWN_SECONDS
        for _ in range(20):
            cd = roll_debuff_attack_cooldown()
            self.assertGreaterEqual(cd, lo)
            self.assertLessEqual(cd, hi)

    def test_attack_cooldown_while_debuffed_uses_stored_value(self) -> None:
        now = 1000.0
        cd = attack_cooldown_while_debuffed(
            attack_slow_until=now + 10,
            verdant_root_until=0.0,
            debuff_attack_cooldown=9.0,
            now=now,
        )
        self.assertEqual(cd, 9.0)

    @patch("utils.boss_element_effects.random.random", return_value=0.0)
    def test_roll_frost_proc(self, _random: object) -> None:
        proc = roll_element_proc("frost", now=100.0)
        self.assertIn("Chilled", proc.note)
        self.assertIsNotNone(proc.frost_slow_until)
        self.assertIsNotNone(proc.debuff_attack_cooldown)
        lo, hi = config.BOSS_DEBUFF_ATTACK_COOLDOWN_SECONDS
        assert proc.debuff_attack_cooldown is not None
        self.assertGreaterEqual(proc.debuff_attack_cooldown, lo)
        self.assertLessEqual(proc.debuff_attack_cooldown, hi)

    @patch("utils.boss_element_effects.random.random", return_value=0.0)
    def test_roll_storm_stun_short_window(self, _random: object) -> None:
        proc = roll_element_proc("storm", now=100.0)
        self.assertIn("Stunned", proc.note)
        assert proc.storm_stun_seconds is not None
        lo, hi = config.BOSS_STORM_STUN_SECONDS
        self.assertGreaterEqual(proc.storm_stun_seconds, lo)
        self.assertLessEqual(proc.storm_stun_seconds, hi)


class BossElementDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.db = Database(str(self.db_path))
        await self.db.connect()
        await self.db.init_schema()
        self.guild_id = 7001
        self.user_id = 99
        await self.db.replace_boss(self.guild_id, "Hannah", "normal", 5000.0, element="frost")

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def test_frost_slow_uses_debuff_attack_cooldown(self) -> None:
        now = time.time()
        await self.db.record_boss_attack_time(self.guild_id, self.user_id, now)
        await self.db.apply_boss_element_status(
            self.guild_id,
            self.user_id,
            frost_slow_until=now + 30,
            debuff_attack_cooldown=10.0,
        )
        remaining = await self.db.boss_attack_cooldown_remaining(
            self.guild_id,
            self.user_id,
            at=now + 1,
        )
        self.assertIsNotNone(remaining)
        assert remaining is not None
        self.assertGreater(remaining, 7.0)

    async def test_record_boss_attack_uses_base_cooldown_range(self) -> None:
        now = time.time()
        await self.db.record_boss_attack_time(self.guild_id, self.user_id, now)
        remaining = await self.db.boss_attack_cooldown_remaining(
            self.guild_id,
            self.user_id,
            at=now + 1,
        )
        self.assertIsNotNone(remaining)
        assert remaining is not None
        self.assertLessEqual(
            remaining,
            config.BOSS_ATTACK_COOLDOWN_MAX_SECONDS - 1 + 0.01,
        )
        self.assertGreater(remaining, 0.0)

    async def test_fire_dot_ticks_damage(self) -> None:
        max_hp = 200.0
        await self.db.sync_combat_hp(self.user_id, self.guild_id, max_hp)
        now = time.time()
        await self.db.apply_boss_element_status(
            self.guild_id,
            self.user_id,
            fire_burn=(15.0, 2, now - 1),
        )
        result = await self.db.process_boss_fire_dot(
            self.user_id,
            self.guild_id,
            max_hp,
            at=now,
        )
        self.assertIsNotNone(result)
        assert result is not None
        hp, _, tick_damage, ticks_left = result
        self.assertEqual(tick_damage, 15.0)
        self.assertEqual(ticks_left, 1)
        self.assertLess(hp, max_hp)

    async def test_debuff_summary_lists_active_effects(self) -> None:
        now = time.time()
        await self.db.apply_boss_element_status(
            self.guild_id,
            self.user_id,
            frost_slow_until=now + 20,
            fire_burn=(10.0, 3, now + 5),
        )
        summary = await self.db.boss_raider_debuff_summary(
            self.guild_id,
            self.user_id,
            at=now,
        )
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertIn("Chilled", summary)
        self.assertIn("Burning", summary)


if __name__ == "__main__":
    unittest.main()
