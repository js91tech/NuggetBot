"""Cartel lab UI and harvest tests."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from database import Database
from utils.cartel_ui import CartelView


class CartelHarvestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.db = Database(str(self.db_path))
        await self.db.connect()
        await self.db.init_schema()
        self.guild_id = 900
        self.user_id = 42
        self.crew = "Ballas"
        await self.db.ensure_user(self.user_id, self.guild_id)
        await self.db.conn.execute(
            """
            INSERT INTO crew_stats (guild_id, crew_name, treasury, level, xp)
            VALUES (?, ?, 100000, 1, 0)
            """,
            (self.guild_id, self.crew),
        )
        await self.db.conn.execute(
            """
            INSERT INTO crew_members (guild_id, user_id, crew_name, joined_at)
            VALUES (?, ?, ?, ?)
            """,
            (self.guild_id, self.user_id, self.crew, time.time()),
        )
        await self.db.conn.commit()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def test_harvest_cartel_moves_product_to_stash(self) -> None:
        cost, err = await self.db.plant_cartel_drug(
            self.user_id, self.guild_id, self.crew, "heroin",
        )
        self.assertIsNone(err)
        self.assertGreater(cost, 0)
        await self.db.conn.execute(
            "UPDATE crew_cartel_grows SET ready_at = 0 WHERE guild_id = ? AND crew_name = ?",
            (self.guild_id, self.crew),
        )
        await self.db.conn.commit()
        harvested = await self.db.harvest_cartel(self.guild_id, self.crew)
        self.assertIn("heroin", harvested)
        self.assertGreater(harvested["heroin"], 0)
        stash = await self.db.get_cartel_stash(self.guild_id, self.crew)
        self.assertEqual(stash["heroin"], harvested["heroin"])

    async def test_cartel_view_separates_select_and_buttons(self) -> None:
        cog = MagicMock()
        cog.bot.db = self.db
        view = CartelView(cog, self.guild_id, self.user_id, self.crew)
        rows: dict[int | None, list[str]] = {}
        for child in view.children:
            row = getattr(child, "row", None)
            rows.setdefault(row, []).append(type(child).__name__)
        for row, names in rows.items():
            selects = [n for n in names if n.endswith("Select")]
            buttons = [n for n in names if n == "Button"]
            if selects:
                self.assertEqual(buttons, [], f"row {row} mixes select and buttons: {names}")


if __name__ == "__main__":
    unittest.main()
