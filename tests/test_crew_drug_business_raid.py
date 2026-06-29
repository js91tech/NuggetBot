"""Drug and business crew raid tests."""
from __future__ import annotations

import os
import random
import tempfile
import unittest

import config
from database import Database
from utils.crew_raid_ui import pick_drug_loot


class DrugLootTests(unittest.TestCase):
    def test_pick_drug_loot_caps_at_available(self) -> None:
        rng = random.Random(0)
        drug_id, qty = pick_drug_loot({"blue_dream": 3}, rng)
        self.assertEqual(drug_id, "blue_dream")
        self.assertLessEqual(qty, 3)
        self.assertGreaterEqual(qty, 1)

    def test_pick_drug_loot_respects_max_roll(self) -> None:
        rng = random.Random(1)
        drug_id, qty = pick_drug_loot({"blue_dream": 100}, rng)
        self.assertEqual(drug_id, "blue_dream")
        self.assertLessEqual(qty, config.CREW_DRUG_RAID_LOOT_MAX)


class CrewDrugRaidDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = Database(self.tmp.name)
        await self.db.connect()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        os.unlink(self.tmp.name)

    async def _seed_crew(
        self, guild_id: int, crew_name: str, member_ids: list[int], *, treasury: float = 5000.0,
    ) -> None:
        wallet_top_up = max(treasury, 1000.0) + 1000.0
        for uid in member_ids:
            await self.db.credit_wallet(uid, guild_id, wallet_top_up, apply_bonuses=False)
            err = await self.db.join_crew(uid, guild_id, crew_name)
            self.assertIsNone(err)

    async def test_settle_drug_raid_transfers_stash(self) -> None:
        guild_id = 1
        await self._seed_crew(guild_id, "Raiders", list(range(100, 105)))
        await self._seed_crew(guild_id, "Growers", list(range(200, 205)))
        async with self.db._write_lock:
            await self.db.conn.execute(
                """
                INSERT INTO crew_cartel_stash (guild_id, crew_name, drug_id, quantity)
                VALUES (?, ?, ?, ?)
                """,
                (guild_id, "Growers", "blue_dream", 10),
            )
            await self.db.conn.commit()

        result = await self.db.settle_crew_drug_raid(
            guild_id, "Raiders", "Growers",
            attacker_won=True, loot_qty=4, drug_id="blue_dream",
        )
        self.assertIsNone(result["error"])
        self.assertEqual(int(result["loot_qty"]), 4)
        growers_stash = await self.db.get_cartel_stash(guild_id, "Growers")
        raiders_stash = await self.db.get_cartel_stash(guild_id, "Raiders")
        self.assertEqual(growers_stash.get("blue_dream", 0), 6)
        self.assertEqual(raiders_stash.get("blue_dream", 0), 4)

    async def test_drug_raid_allows_small_defender_crew(self) -> None:
        guild_id = 1
        await self._seed_crew(guild_id, "Raiders", [100, 101, 102])
        await self._seed_crew(guild_id, "TinyGrow", [200, 201])
        async with self.db._write_lock:
            await self.db.conn.execute(
                """
                INSERT INTO crew_cartel_stash (guild_id, crew_name, drug_id, quantity)
                VALUES (?, ?, ?, ?)
                """,
                (guild_id, "TinyGrow", "blue_dream", 10),
            )
            await self.db.conn.commit()
        err = await self.db.validate_crew_raid(
            guild_id, 100, "Raiders", "TinyGrow", (101, 102), raid_type="drugs",
        )
        self.assertIsNone(err)

    async def test_drug_raid_requires_three_attackers(self) -> None:
        guild_id = 1
        await self._seed_crew(guild_id, "Small", [100, 101])
        await self._seed_crew(guild_id, "Target", [200, 201, 202, 203, 204])
        err = await self.db.validate_crew_raid(
            guild_id, 100, "Small", "Target", (101, 100), raid_type="drugs",
        )
        self.assertEqual(err, "attacker_too_small")

    async def test_settle_business_raid_steals_ten_percent(self) -> None:
        guild_id = 1
        attackers = list(range(100, 105))
        defenders = list(range(200, 205))
        await self._seed_crew(guild_id, "Raiders", attackers, treasury=0.0)
        await self._seed_crew(guild_id, "CorpCo", defenders)
        for uid in defenders[:3]:
            await self.db.credit_wallet(uid, guild_id, 5000.0, apply_bonuses=False)
            err = await self.db.create_business(uid, guild_id)
            self.assertIsNone(err)
            async with self.db._write_lock:
                await self.db.conn.execute(
                    """
                    UPDATE user_businesses SET stored_income = 1000.0
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (uid, guild_id),
                )
                await self.db.conn.commit()

        result = await self.db.settle_crew_business_raid(
            guild_id, "Raiders", "CorpCo", attacker_won=True,
        )
        self.assertIsNone(result["error"])
        self.assertAlmostEqual(float(result["loot"]), 300.0)
        remaining = await self.db.get_crew_business_stored_total(guild_id, "CorpCo")
        self.assertAlmostEqual(remaining, 2700.0)
        raider_stats = await self.db.get_crew_stats(guild_id, "Raiders")
        self.assertAlmostEqual(float(raider_stats["treasury"]), 300.0)


if __name__ == "__main__":
    unittest.main()
