"""Drug lab panel layout tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from database import Database
from utils.drug_ui import DrugLabView


class DrugLabUIViewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.db = Database(str(self.db_path))
        await self.db.connect()
        await self.db.init_schema()
        self.guild_id = 42
        self.user_id = 7
        await self.db.ensure_user(self.user_id, self.guild_id)

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def test_lab_view_respects_discord_row_limits(self) -> None:
        cog = MagicMock()
        cog.bot.db = self.db
        view = await DrugLabView.build(cog, self.guild_id, self.user_id)
        rows: dict[int | None, list[str]] = {}
        for child in view.children:
            row = getattr(child, "row", None)
            rows.setdefault(row, []).append(type(child).__name__)
        self.assertLessEqual(len(rows), 5, rows)
        for row, names in rows.items():
            selects = [n for n in names if n.endswith("Select")]
            buttons = [n for n in names if n == "Button"]
            self.assertLessEqual(len(selects), 1, f"row {row}: {names}")
            if selects:
                self.assertEqual(buttons, [], f"row {row} mixes select and buttons: {names}")
            self.assertLessEqual(len(buttons), 5, f"row {row}: {names}")

    async def test_fertilize_select_has_option_when_empty(self) -> None:
        cog = MagicMock()
        cog.bot.db = self.db
        view = await DrugLabView.build(cog, self.guild_id, self.user_id)
        fert = next(c for c in view.children if type(c).__name__ == "FertilizeSelect")
        self.assertGreaterEqual(len(fert.options), 1)


if __name__ == "__main__":
    unittest.main()
