"""Crew panel embed tests."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from cogs.crews import Crews
from database import Database
from utils.crew_ui import CrewPanelView, build_crew_embed, build_no_crew_embed


class CrewEmbedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.db = Database(self.db_path)
        await self.db.connect()
        self.bot = SimpleNamespace(db=self.db)
        self.cog = Crews(self.bot)  # type: ignore[arg-type]
        self.guild_id = 7001
        self.uid = 42
        await self.db.ensure_user(self.uid, self.guild_id)
        await self.db.credit_wallet(self.uid, self.guild_id, 1000.0)
        await self.db.join_crew(self.uid, self.guild_id, "Raiders")
        await self.db.deposit_crew_treasury(self.uid, self.guild_id, 250.0)

    async def asyncTearDown(self) -> None:
        await self.db.close()
        Path(self.db_path).unlink(missing_ok=True)

    async def test_build_crew_embed_shows_treasury(self) -> None:
        guild = MagicMock()
        guild.id = self.guild_id
        guild.get_member.return_value = MagicMock(display_name="Tester")

        embed, err = await build_crew_embed(self.cog, guild, self.uid)
        self.assertIsNone(err)
        assert embed is not None
        self.assertIn("Raiders", embed.title)
        treasury_field = next(f for f in embed.fields if f.name == "Treasury")
        self.assertIn("250", treasury_field.value.replace(",", ""))

    def test_no_crew_embed_has_title(self) -> None:
        embed = build_no_crew_embed()
        self.assertEqual(embed.title, "Crew bank")

    def test_crew_panel_view_fits_discord_row_limit(self) -> None:
        view = CrewPanelView(self.cog, self.guild_id, self.uid)
        rows = view.to_components()
        self.assertLessEqual(len(rows), 5)
        for row in rows:
            self.assertLessEqual(len(row["components"]), 5)


if __name__ == "__main__":
    unittest.main()
