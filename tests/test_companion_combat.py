"""Tests for companion combat system."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from database import Database
from utils.companion_combat import apply_stamina_regen
from utils.companions import (
    base_tier_damage,
    evolution_damage_mult,
    roll_companion_damage,
)


class CompanionCombatFormulaTests(unittest.TestCase):
    def test_base_tier_damage_scales(self) -> None:
        self.assertLess(base_tier_damage(1), base_tier_damage(3))

    def test_evolution_mult(self) -> None:
        self.assertGreater(evolution_damage_mult(3), evolution_damage_mult(1))

    def test_roll_damage_positive(self) -> None:
        dmg, _, _ = roll_companion_damage(
            "hench_scrap_gnome",
            evolution_tier=1,
            owner_attack_power=100,
        )
        self.assertGreaterEqual(dmg, 1)

    def test_stamina_regen(self) -> None:
        refreshed, advanced = apply_stamina_regen(10, updated_at=0.0, now=120.0)
        self.assertEqual(refreshed, 12)
        self.assertEqual(advanced, 120.0)

    def test_stamina_regen_caps_at_base(self) -> None:
        refreshed, _ = apply_stamina_regen(48, updated_at=0.0, now=600.0)
        self.assertEqual(refreshed, 50)


class CompanionDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.db = Database(self.db_path)
        await self.db.connect()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        Path(self.db_path).unlink(missing_ok=True)

    async def test_rename_first_free(self) -> None:
        gid, uid = 1, 42
        await self.db.ensure_user(uid, gid)
        await self.db.grant_companion(uid, gid, "hench_scrap_gnome")
        ok, err = await self.db.rename_companion(uid, gid, "hench_scrap_gnome", "Gnorman")
        self.assertTrue(ok)
        self.assertIsNone(err)
        row = await self.db.get_companion_row(uid, gid, "hench_scrap_gnome")
        assert row is not None
        self.assertEqual(row["custom_name"], "Gnorman")
        self.assertEqual(int(row["rename_count"]), 1)

    async def test_multi_equip(self) -> None:
        gid, uid = 1, 42
        await self.db.ensure_user(uid, gid)
        await self.db.grant_companion(uid, gid, "hench_scrap_gnome")
        await self.db.grant_companion(uid, gid, "hench_medic_slime")
        ok1, _ = await self.db.equip_companion(uid, gid, "hench_scrap_gnome")
        ok2, _ = await self.db.equip_companion(uid, gid, "hench_medic_slime")
        self.assertTrue(ok1)
        self.assertTrue(ok2)
        equipped = await self.db.list_equipped_companion_ids(uid, gid)
        self.assertEqual(len(equipped), 2)

    async def test_stamina_spend_and_restore(self) -> None:
        gid, uid = 1, 42
        await self.db.ensure_user(uid, gid)
        await self.db.grant_companion(uid, gid, "hench_scrap_gnome")
        self.assertTrue(
            await self.db.spend_companion_stamina(uid, gid, "hench_scrap_gnome", 5),
        )
        row = await self.db.get_companion_row(uid, gid, "hench_scrap_gnome")
        assert row is not None
        self.assertEqual(int(row["stamina"]), 45)
        new_stamina = await self.db.add_companion_stamina(uid, gid, "hench_scrap_gnome", 25)
        self.assertEqual(new_stamina, 70)

    async def test_evolve_companion(self) -> None:
        gid, uid = 1, 42
        await self.db.ensure_user(uid, gid)
        await self.db.credit_wallet(uid, gid, 500_000)
        await self.db.grant_companion(uid, gid, "hench_scrap_gnome")
        ok, err = await self.db.evolve_companion(uid, gid, "hench_scrap_gnome")
        self.assertTrue(ok)
        self.assertIsNone(err)
        row = await self.db.get_companion_row(uid, gid, "hench_scrap_gnome")
        assert row is not None
        self.assertEqual(int(row["evolution_tier"]), 2)


if __name__ == "__main__":
    unittest.main()
