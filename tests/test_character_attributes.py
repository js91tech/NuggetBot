"""Character attribute and debuff resistance tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from database import Database
from utils.character_attributes import (
    CharacterAttributes,
    apply_cc_duration,
    attribute_points_from_class_xp,
    combat_bonuses_from_attributes,
    debuff_resistance_from_attributes,
    resolve_downed_duration,
    stat_cap_for_prestige,
    total_point_pool_cap,
    unspent_attribute_points,
    xp_required_for_attribute_points,
)
from utils.boss_element_effects import roll_element_proc


class CharacterAttributeUtilTests(unittest.TestCase):
    def test_stat_cap_scales_with_prestige(self) -> None:
        self.assertEqual(stat_cap_for_prestige(0), 15)
        self.assertEqual(stat_cap_for_prestige(10), 25)

    def test_total_pool_cap_at_prestige(self) -> None:
        self.assertEqual(total_point_pool_cap(0), 50)
        self.assertEqual(total_point_pool_cap(10), 100)

    def test_prestige_10_can_max_four_stats_not_five(self) -> None:
        self.assertEqual(stat_cap_for_prestige(10), 25)
        self.assertEqual(total_point_pool_cap(10), 100)
        self.assertLess(total_point_pool_cap(10), stat_cap_for_prestige(10) * 5)

    def test_fast_then_slow_xp_curve(self) -> None:
        self.assertEqual(xp_required_for_attribute_points(20), 20 * config.ATTR_XP_PER_FAST_POINT)
        slow_only = xp_required_for_attribute_points(25) - xp_required_for_attribute_points(20)
        self.assertEqual(slow_only, 5 * config.ATTR_XP_PER_SLOW_POINT)

    def test_first_twenty_points_are_fast(self) -> None:
        xp_for_20 = xp_required_for_attribute_points(20)
        xp_for_21 = xp_required_for_attribute_points(21) - xp_required_for_attribute_points(20)
        self.assertEqual(xp_for_21, config.ATTR_XP_PER_SLOW_POINT)
        self.assertGreater(xp_for_21, config.ATTR_XP_PER_FAST_POINT)
        self.assertEqual(attribute_points_from_class_xp(xp_for_20), 20)

    def test_unspent_points(self) -> None:
        attrs = CharacterAttributes(agility=5)
        xp = xp_required_for_attribute_points(10)
        self.assertEqual(unspent_attribute_points(attrs, xp, 0), 5)

    def test_agi_reduces_cc_duration(self) -> None:
        base_attrs = CharacterAttributes()
        high_agi = CharacterAttributes(agility=15)
        base_resist = debuff_resistance_from_attributes(base_attrs)
        high_resist = debuff_resistance_from_attributes(high_agi)
        self.assertLess(high_resist.cc_duration_mult, base_resist.cc_duration_mult)
        reduced = apply_cc_duration(20.0, high_resist)
        self.assertLess(reduced, 20.0)
        self.assertGreaterEqual(reduced, config.ATTR_MIN_DEBUFF_SECONDS)

    def test_resolve_downed_duration_caps_legacy_config(self) -> None:
        attrs = CharacterAttributes()
        self.assertEqual(resolve_downed_duration(120.0, attrs), 30.0)

    def test_resolve_downed_duration_applies_agi(self) -> None:
        high_agi = CharacterAttributes(agility=15)
        reduced = resolve_downed_duration(30.0, high_agi)
        self.assertLess(reduced, 30.0)
        self.assertGreaterEqual(reduced, config.ATTR_MIN_DEBUFF_SECONDS)

    def test_def_boosts_mitigation_and_dot_resist(self) -> None:
        attrs = CharacterAttributes(defense=12)
        combat = combat_bonuses_from_attributes(attrs)
        resist = debuff_resistance_from_attributes(attrs)
        self.assertGreater(combat.mitigation_bonus, 0.0)
        self.assertLess(resist.dot_damage_mult, 1.0)

    def test_str_and_vit_combat_bonuses(self) -> None:
        attrs = CharacterAttributes(strength=10, vitality=15)
        combat = combat_bonuses_from_attributes(attrs)
        self.assertGreater(combat.damage_mult, 1.0)
        self.assertGreater(combat.hp_bonus, 0)


class CharacterAttributeDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.db = Database(str(self.db_path))
        await self.db.connect()
        await self.db.init_schema()
        self.guild_id = 8001
        self.user_id = 42

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def test_new_characters_start_at_zero(self) -> None:
        await self.db.get_user_character(self.user_id, self.guild_id)
        attrs = await self.db.get_character_attributes(self.user_id, self.guild_id)
        self.assertEqual(attrs.total_points(), 0)

    async def test_allocate_attribute_points(self) -> None:
        await self.db.add_class_xp(self.user_id, self.guild_id, 500)
        ok, msg = await self.db.allocate_attribute_points(
            self.user_id, self.guild_id, "agility", 3,
        )
        self.assertTrue(ok, msg)
        attrs = await self.db.get_character_attributes(self.user_id, self.guild_id)
        self.assertEqual(attrs.agility, 3)

    async def test_allocate_rejects_over_stat_cap(self) -> None:
        await self.db.add_class_xp(self.user_id, self.guild_id, 5000)
        ok, _ = await self.db.allocate_attribute_points(
            self.user_id,
            self.guild_id,
            "agility",
            16,
        )
        self.assertFalse(ok)

    async def test_v3_migration_resets_all_stats(self) -> None:
        await self.db.add_class_xp(self.user_id, self.guild_id, 500)
        await self.db.allocate_attribute_points(self.user_id, self.guild_id, "strength", 5)
        await self.db.conn.execute(
            "DELETE FROM one_time_jobs WHERE job_id = 'character_attributes_v3_reset'",
        )
        await self.db.conn.commit()
        await self.db._migrate_character_attributes_v3_reset()
        attrs = await self.db.get_character_attributes(self.user_id, self.guild_id)
        self.assertEqual(attrs.total_points(), 0)

    async def test_reset_guild_attributes(self) -> None:
        await self.db.add_class_xp(self.user_id, self.guild_id, 500)
        await self.db.allocate_attribute_points(self.user_id, self.guild_id, "agility", 3)
        count = await self.db.reset_guild_character_attributes(self.guild_id)
        self.assertGreaterEqual(count, 0)
        attrs = await self.db.get_character_attributes(self.user_id, self.guild_id)
        self.assertEqual(attrs.total_points(), 0)

    async def test_allocate_respects_prestige_stat_cap(self) -> None:
        await self.db.add_class_xp(self.user_id, self.guild_id, 99999)
        for i in range(15):
            ok, msg = await self.db.allocate_attribute_points(
                self.user_id, self.guild_id, "strength", 1,
            )
            self.assertTrue(ok, f"allocation {i + 1} failed: {msg}")
        ok, _ = await self.db.allocate_attribute_points(
            self.user_id, self.guild_id, "strength", 1,
        )
        self.assertFalse(ok)
        attrs = await self.db.get_character_attributes(self.user_id, self.guild_id)
        self.assertEqual(attrs.strength, 15)


class BossDebuffResistanceTests(unittest.TestCase):
    @patch("utils.boss_element_effects.random.random", return_value=0.0)
    @patch("utils.boss_element_effects.roll_debuff_duration_for_threat", return_value=20.0)
    def test_high_agi_shortens_storm_stun(self, _duration: object, _random: object) -> None:
        high_agi = debuff_resistance_from_attributes(CharacterAttributes(agility=15))
        proc = roll_element_proc("storm", now=100.0, threat=6, resistance=high_agi)
        assert proc.storm_stun_seconds is not None
        self.assertLess(proc.storm_stun_seconds, 20.0)
        self.assertGreaterEqual(proc.storm_stun_seconds, config.ATTR_MIN_DEBUFF_SECONDS)


if __name__ == "__main__":
    unittest.main()
