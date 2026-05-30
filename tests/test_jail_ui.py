"""Jail panel UI tests."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from cogs.heist import Heist
from database import Database
from utils.jail_ui import build_jail_embed


class JailPanelEmbedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.db = Database(str(self.db_path))
        await self.db.connect()
        await self.db.init_schema()
        self.bot = SimpleNamespace(db=self.db)
        self.cog = Heist(self.bot)  # type: ignore[arg-type]
        self.guild_id = 9001
        self.uid = 42
        member = MagicMock()
        member.id = self.uid
        member.display_name = "TestPlayer"
        self.member = member

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def test_embed_shows_bail_rates(self) -> None:
        embed = await build_jail_embed(self.cog, self.guild_id, self.member)
        self.assertIn("15", embed.fields[0].value.replace(",", ""))
        self.assertIn("30", embed.fields[0].value.replace(",", ""))

    async def test_embed_shows_jail_status_when_arrested(self) -> None:
        await self.db.set_arrested_until(
            self.uid,
            self.guild_id,
            time.time() + 3600,
            arrest_tier="1",
        )
        embed = await build_jail_embed(self.cog, self.guild_id, self.member)
        self.assertIn("in jail", embed.description or "")
        bail_field = next(f for f in embed.fields if f.name == "Your bail")
        self.assertIn("30", bail_field.value.replace(",", ""))


if __name__ == "__main__":
    unittest.main()
