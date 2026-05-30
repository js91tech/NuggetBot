"""Bank heist and unstable gear tests."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import config
from database import Database
from utils.fix_gear_ui import fix_cost_for_item_id
from utils.loadout import parse_loadout


class BankHeistTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.db = Database(self.db_path)
        await self.db.connect()
        await self.db.init_schema()
        self.guild_id = 500
        self.thief = 1
        self.victim = 2
        await self.db.ensure_user(self.thief, self.guild_id)
        await self.db.ensure_user(self.victim, self.guild_id)
        await self.db.credit_wallet(self.victim, self.guild_id, 1000.0)
        await self.db.deposit_to_bank(self.victim, self.guild_id, 1000.0)

    async def asyncTearDown(self) -> None:
        await self.db.close()
        Path(self.db_path).unlink(missing_ok=True)

    async def test_steal_from_bank_moves_funds(self) -> None:
        stolen = await self.db.steal_from_bank(
            self.victim, self.thief, self.guild_id, 100.0,
        )
        self.assertAlmostEqual(stolen, 100.0)
        self.assertAlmostEqual(await self.db.get_bank(self.victim, self.guild_id), 900.0)
        self.assertAlmostEqual(await self.db.get_balance(self.thief, self.guild_id), 100.0)

    async def test_unstable_slot_strips_combat_stats(self) -> None:
        uid = 10
        await self.db.ensure_user(uid, self.guild_id)
        await self.db.grant_item(uid, self.guild_id, "twig_sword")
        await self.db.equip_item(uid, self.guild_id, "weapon", "twig_sword")
        await self.db.mark_slot_unstable(uid, self.guild_id, "weapon")
        equipment = await self.db.get_equipment(uid, self.guild_id)
        unstable = await self.db.list_unstable_slots(uid, self.guild_id)
        loadout = parse_loadout(equipment, unstable_slots=unstable)
        self.assertIsNone(loadout.primary)

    async def test_fix_unstable_slot_costs_wallet(self) -> None:
        uid = 11
        await self.db.ensure_user(uid, self.guild_id)
        await self.db.credit_wallet(uid, self.guild_id, 5000.0)
        await self.db.grant_item(uid, self.guild_id, "twig_sword")
        await self.db.equip_item(uid, self.guild_id, "weapon", "twig_sword")
        await self.db.mark_slot_unstable(uid, self.guild_id, "weapon")
        cost = fix_cost_for_item_id("twig_sword")
        self.assertGreater(cost, 0)
        err = await self.db.fix_unstable_slot(uid, self.guild_id, "weapon")
        self.assertIsNone(err)
        self.assertEqual(await self.db.list_unstable_slots(uid, self.guild_id), set())
        self.assertAlmostEqual(
            await self.db.get_balance(uid, self.guild_id),
            5000.0 - cost,
        )


if __name__ == "__main__":
    unittest.main()
