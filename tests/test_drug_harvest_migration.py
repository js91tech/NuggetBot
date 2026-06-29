"""Regression tests for drug harvest reputation migration."""
from __future__ import annotations

import inspect
import os
import tempfile
import unittest

from database import Database


class DrugHarvestMigrationSqlTests(unittest.TestCase):
    def test_backfill_query_does_not_use_having_alias(self) -> None:
        """PostgreSQL rejects column aliases in HAVING; use the aggregate instead."""
        source = inspect.getsource(Database._migrate_drug_harvest_reputation)
        self.assertNotIn("HAVING stash", source)
        self.assertIn("HAVING COALESCE(SUM(quantity), 0) > 0", source)


class DrugHarvestMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = Database(self.tmp.name)
        await self.db.connect()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        os.unlink(self.tmp.name)

    async def test_backfill_creates_stats_for_inventory_only_users(self) -> None:
        user_id = 4242
        guild_id = 99
        async with self.db._write_lock:
            await self.db.conn.execute(
                """
                INSERT INTO drug_inventory (user_id, guild_id, drug_id, quantity)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, guild_id, "blue_dream", 7),
            )
            await self.db.conn.execute(
                "DELETE FROM one_time_jobs WHERE job_id = ?",
                ("drug_harvest_rep_v1",),
            )
            await self.db.conn.commit()

        await self.db._migrate_drug_harvest_reputation()

        stats = await self.db.get_drug_stats(user_id, guild_id)
        self.assertEqual(stats["units_harvested"], 7)
        self.assertEqual(stats["units_sold"], 0)


if __name__ == "__main__":
    unittest.main()
