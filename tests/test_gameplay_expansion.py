"""Tests for gameplay expansion utilities."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from database import Database
from utils.affixes import merge_affix_bonuses, roll_affix_ids
from utils.companions import bonuses_from_companion, merge_companion_bonuses
from utils.museum import museum_bonuses_for_pct, museum_completion_pct
from utils.phenotypes import drug_family, roll_crossbreed
from utils.relics import bonuses_from_relic, merge_relic_bonuses


class RelicBonusTests(unittest.TestCase):
    def test_boss_slayer_relic(self) -> None:
        b = bonuses_from_relic("relic_hannah_fang")
        self.assertAlmostEqual(b.boss_damage_mult, 1.08)

    def test_merge_caps_damage(self) -> None:
        merged = merge_relic_bonuses([
            bonuses_from_relic("relic_henchman_totem"),
            bonuses_from_relic("relic_hannah_fang"),
        ])
        self.assertLessEqual(merged.damage_mult, 1.16)


class CompanionTests(unittest.TestCase):
    def test_scrap_gnome(self) -> None:
        b = bonuses_from_companion("hench_scrap_gnome")
        self.assertAlmostEqual(b.alchemy_scrap_mult, 1.05)


class MuseumTests(unittest.TestCase):
    def test_completion_pct(self) -> None:
        pct = museum_completion_pct({"gear": 10, "bosses": 3})
        self.assertGreater(pct, 0.0)

    def test_tier_bonus(self) -> None:
        income, damage, label = museum_bonuses_for_pct(50.0)
        self.assertEqual(label, "Archivist")
        self.assertGreater(income, 1.0)


class PhenotypeTests(unittest.TestCase):
    def test_drug_family(self) -> None:
        self.assertEqual(drug_family("blue_dream"), "cannabis")
        self.assertEqual(drug_family("cocaine"), "stimulant")


class AffixTests(unittest.TestCase):
    def test_roll_returns_list(self) -> None:
        ids = roll_affix_ids()
        self.assertIsInstance(ids, list)


class ExpansionDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.db = Database(self.db_path)
        await self.db.connect()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        Path(self.db_path).unlink(missing_ok=True)

    async def test_relic_create_and_equip(self) -> None:
        gid, uid = 1, 42
        await self.db.ensure_user(uid, gid)
        iid = await self.db.create_relic_instance(uid, gid, "relic_hannah_fang")
        self.assertGreater(iid, 0)
        self.assertTrue(await self.db.equip_relic_instance(uid, gid, iid))
        row = await self.db.get_equipped_relic_row(uid, gid)
        assert row is not None
        self.assertEqual(row["relic_id"], "relic_hannah_fang")

    async def test_blueprint_unlock(self) -> None:
        gid, uid = 1, 42
        await self.db.ensure_user(uid, gid)
        self.assertTrue(await self.db.unlock_blueprint(uid, gid, "bp_flask_enrage"))
        self.assertTrue(await self.db.has_blueprint(uid, gid, "bp_flask_enrage"))

    async def test_season_tokens(self) -> None:
        gid, uid = 1, 42
        await self.db.ensure_user(uid, gid)
        total = await self.db.add_season_tokens(uid, gid, 50, 1)
        self.assertEqual(total, 50)
        self.assertTrue(await self.db.redeem_season_reward(uid, gid, 1, "title_raider", 50))
        self.assertEqual(await self.db.get_season_tokens(uid, gid, 1), 0)

    async def test_companion_grant(self) -> None:
        gid, uid = 1, 42
        await self.db.ensure_user(uid, gid)
        self.assertTrue(await self.db.grant_companion(uid, gid, "hench_scrap_gnome"))
        ok, err = await self.db.equip_companion(uid, gid, "hench_scrap_gnome")
        self.assertTrue(ok)
        self.assertIsNone(err)

    async def test_contract_progress(self) -> None:
        gid, uid = 1, 42
        await self.db.ensure_user(uid, gid)
        await self.db.set_guild_contracts(gid, ["contract_boss_hits"], 9999999999.0)
        await self.db.increment_contract_progress(gid, uid, "boss_attack", 3)
        rows = await self.db.get_contract_progress_rows(uid, gid)
        self.assertEqual(int(rows[0]["progress"]), 3)


if __name__ == "__main__":
    unittest.main()
