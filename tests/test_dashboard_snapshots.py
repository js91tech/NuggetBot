"""Dashboard data-loading tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import config
from dashboard import DashboardServer
from database import Database


class DashboardSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / "test.sqlite3"))
        await self.db.connect()
        await self.db.init_schema()
        self.guild_id = 9001
        guild = MagicMock()
        guild.id = self.guild_id
        guild.name = "Test Guild"
        guild.member_count = 3
        guild.members = []
        guild.text_channels = []
        guild.get_channel = lambda _cid: None
        guild.get_member = lambda _uid: None
        self.bot = SimpleNamespace(db=self.db, guilds=[guild], is_ready=lambda: True)
        self.server = DashboardServer(self.bot)  # type: ignore[arg-type]

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def test_snapshots_load_without_error(self) -> None:
        snapshots = await self.server._snapshots()
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["id"], self.guild_id)
        html = self.server._dashboard_page(snapshots)
        self.assertIn("NuggetBot Control Room", html)
        self.assertIn('value="freaky_nikki"', html)
        self.assertIn("Freaky Nikki", html)
        self.assertIn("Reset all attribute stats", html)
        self.assertIn("Inventory Spy", html)
        self.assertIn("spy-grant-btn", html)

    async def test_hall_of_fame_includes_duel_wins(self) -> None:
        hall = await self.db.hall_of_fame_snapshot(self.guild_id, limit=3)
        self.assertIn("duel_wins", hall)
        self.assertIsInstance(hall["duel_wins"], list)


if __name__ == "__main__":
    unittest.main()
