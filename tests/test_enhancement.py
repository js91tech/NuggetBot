"""BDO-style enhancement costs, repair, and roll outcomes."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from database import Database
from items import get_item
from utils.enhancement import (
    display_level,
    enhance_attempt_cost,
    material_for_target_level,
    nugget_cost_for_attempt,
    repair_nugget_cost,
    resolve_effective_gear,
    roll_enhancement,
    stat_multiplier_for_level,
)
from utils.loadout import parse_resolved_loadout


class EnhancementCostTests(unittest.TestCase):
    def test_nugget_anchors(self) -> None:
        self.assertAlmostEqual(nugget_cost_for_attempt(10), config.ENHANCE_NUGGET_COST_AT_PLUS_10, delta=1.0)
        self.assertAlmostEqual(nugget_cost_for_attempt(15), config.ENHANCE_NUGGET_COST_AT_PLUS_15, delta=1.0)
        self.assertAlmostEqual(nugget_cost_for_attempt(20), config.ENHANCE_NUGGET_COST_AT_PENTA, delta=1.0)

    def test_material_tiers(self) -> None:
        self.assertEqual(material_for_target_level(5), "alchemy_scrap")
        self.assertEqual(material_for_target_level(12), "void_hardener")
        self.assertEqual(material_for_target_level(17), "celestial_shard")

    def test_repair_is_ten_percent_base_price(self) -> None:
        blade = get_item("iron_sword")
        assert blade is not None
        expected = max(1.0, blade.price * config.ENHANCE_REPAIR_NUGGET_FACTOR)
        self.assertAlmostEqual(repair_nugget_cost("iron_sword"), expected)

    def test_display_levels(self) -> None:
        self.assertEqual(display_level(7), "+7")
        self.assertEqual(display_level(16), "PRI")
        self.assertEqual(display_level(20), "PENTA")

    def test_max_level_has_no_cost(self) -> None:
        self.assertIsNone(enhance_attempt_cost(config.ENHANCE_MAX_LEVEL))

    def test_stat_multiplier_grows(self) -> None:
        self.assertGreater(stat_multiplier_for_level(10), stat_multiplier_for_level(1))
        self.assertGreater(stat_multiplier_for_level(16), stat_multiplier_for_level(15))

    def test_effective_gear_exposes_item_id(self) -> None:
        blade = get_item("iron_sword")
        assert blade is not None
        gear = resolve_effective_gear(blade, enhancement_level=3)
        assert gear is not None
        self.assertEqual(gear.id, blade.id)
        self.assertEqual(gear.name, blade.name)


class EnhancementRollTests(unittest.TestCase):
    def test_roll_at_max_is_noop(self) -> None:
        result = roll_enhancement(config.ENHANCE_MAX_LEVEL)
        self.assertFalse(result.success)
        self.assertEqual(result.new_level, config.ENHANCE_MAX_LEVEL)


class EnhancementDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.db = Database(str(self.db_path))
        await self.db.connect()
        await self.db.init_schema()
        self.guild_id = 501
        self.user_id = 88

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def test_enhance_links_equipped_slot_and_updates_loadout(self) -> None:
        await self.db.ensure_user(self.user_id, self.guild_id)
        await self.db.credit_wallet(self.user_id, self.guild_id, 100_000.0, apply_bonuses=False)
        await self.db.buy_item(self.user_id, self.guild_id, "iron_sword", 500.0, quantity=1)
        await self.db.equip_item(self.user_id, self.guild_id, "weapon", "iron_sword")
        await self.db.ensure_equipment_gear_instance_links(self.user_id, self.guild_id)
        instances = await self.db.list_gear_instances(self.user_id, self.guild_id)
        self.assertEqual(len(instances), 1)
        instance_id = int(instances[0]["instance_id"])
        await self.db.set_gear_instance_level(instance_id, self.guild_id, 4, broken=False)
        await self.db.attach_gear_instance_to_equipped_slots(
            self.user_id, self.guild_id, instance_id,
        )
        records = await self.db.get_equipment_records(self.user_id, self.guild_id)
        self.assertEqual(int(records["weapon"]["gear_instance_id"]), instance_id)
        inst_rows = {
            int(row["instance_id"]): row
            for row in await self.db.list_gear_instances(self.user_id, self.guild_id)
        }
        loadout = parse_resolved_loadout(records, instances=inst_rows)
        assert loadout.primary is not None
        self.assertEqual(loadout.primary.enhancement_level, 4)

    @patch("utils.enhancement.random.random", return_value=0.0)
    async def test_successful_enhance_persists_level(self, _rng: object) -> None:
        await self.db.ensure_user(self.user_id, self.guild_id)
        instance_id = await self.db.create_gear_instance(self.user_id, self.guild_id, "iron_sword")
        result = roll_enhancement(0)
        self.assertTrue(result.success)
        await self.db.set_gear_instance_level(
            instance_id, self.guild_id, result.new_level, broken=result.broken,
        )
        row = await self.db.get_gear_instance(instance_id, self.guild_id)
        assert row is not None
        self.assertEqual(int(row["enhancement_level"]), 1)

    async def test_ensure_links_legacy_equipped_gear(self) -> None:
        await self.db.ensure_user(self.user_id, self.guild_id)
        await self.db.grant_item(self.user_id, self.guild_id, "iron_sword")
        instance_id = await self.db.create_gear_instance(self.user_id, self.guild_id, "iron_sword")
        await self.db.conn.execute(
            """
            INSERT INTO equipment (guild_id, user_id, slot, item_id, gear_instance_id)
            VALUES (?, ?, 'weapon', 'iron_sword', NULL)
            """,
            (self.guild_id, self.user_id),
        )
        await self.db.conn.commit()
        linked = await self.db.ensure_equipment_gear_instance_links(self.user_id, self.guild_id)
        self.assertEqual(linked, 1)
        records = await self.db.get_equipment_records(self.user_id, self.guild_id)
        self.assertEqual(int(records["weapon"]["gear_instance_id"]), instance_id)


if __name__ == "__main__":
    unittest.main()
