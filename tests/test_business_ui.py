"""Business panel UI layout tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import discord

from database import Database
from utils.business_ui import BusinessPanelView, build_business_payload


class BusinessPanelViewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.db = Database(str(self.db_path))
        await self.db.connect()
        await self.db.init_schema()
        self.guild_id = 42
        self.user_id = 7
        await self.db.ensure_user(self.user_id, self.guild_id)
        await self.db.credit_wallet(self.user_id, self.guild_id, 10_000.0, apply_bonuses=False)
        await self.db.create_business(self.user_id, self.guild_id)

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def test_panel_view_respects_discord_row_limits(self) -> None:
        view = BusinessPanelView(MagicMock(), self.guild_id, self.user_id)
        rows: dict[int | None, list[str]] = {}
        for child in view.children:
            row = getattr(child, "row", None)
            rows.setdefault(row, []).append(getattr(child, "label", type(child).__name__))
        self.assertLessEqual(len(rows), 5, rows)
        for row, labels in rows.items():
            self.assertLessEqual(len(labels), 5, f"row {row}: {labels}")

    async def test_build_business_payload_returns_view(self) -> None:
        cog = MagicMock()
        cog.bot.db = self.db
        member = MagicMock(spec=discord.Member)
        member.id = self.user_id
        member.display_name = "Tester"
        member.guild.id = self.guild_id
        payload = await build_business_payload(cog, member, self.guild_id, self.user_id)
        self.assertIsNotNone(payload)
        embed, files, view = payload  # type: ignore[misc]
        self.assertEqual(embed.title.split("'s", 1)[0], "🍋 Tester")
        self.assertGreaterEqual(len(files), 1)
        self.assertIsInstance(view, BusinessPanelView)


if __name__ == "__main__":
    unittest.main()
