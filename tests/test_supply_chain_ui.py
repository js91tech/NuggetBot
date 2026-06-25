"""Business supply chain UI tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from database import Database
from utils.supply_chain_ui import SupplyChainView, build_supply_chain_panel


class SupplyChainUITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.db = Database(str(self.db_path))
        await self.db.connect()
        await self.db.init_schema()
        self.guild_id = 11
        self.user_id = 22

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def test_supply_chain_panel_unlocked_at_tier_five(self) -> None:
        await self.db.ensure_user(self.user_id, self.guild_id)
        await self.db.credit_wallet(self.user_id, self.guild_id, 500_000.0, apply_bonuses=False)
        await self.db.create_business(self.user_id, self.guild_id)
        for _ in range(4):
            await self.db.tier_up_business(self.user_id, self.guild_id)
        cog = MagicMock()
        cog.bot.db = self.db
        embed, view = await build_supply_chain_panel(cog, self.guild_id, self.user_id)
        self.assertIn("Supply Chain", embed.title)
        self.assertNotIn("Unlocks at business tier", embed.description or "")
        self.assertIsInstance(view, SupplyChainView)
        rows: dict[int | None, list[str]] = {}
        for child in view.children:
            row = getattr(child, "row", None)
            rows.setdefault(row, []).append(type(child).__name__)
        self.assertLessEqual(len(rows), 5)
        for names in rows.values():
            selects = [n for n in names if n.endswith("Select")]
            buttons = [n for n in names if n == "Button"]
            if selects:
                self.assertEqual(buttons, [])

    async def test_set_supply_chain_via_db(self) -> None:
        await self.db.ensure_user(self.user_id, self.guild_id)
        await self.db.credit_wallet(self.user_id, self.guild_id, 500_000.0, apply_bonuses=False)
        await self.db.create_business(self.user_id, self.guild_id)
        for _ in range(4):
            await self.db.tier_up_business(self.user_id, self.guild_id)
        err = await self.db.set_supply_chain_drug(self.user_id, self.guild_id, "heroin")
        self.assertIsNone(err)
        row = await self.db.get_business(self.user_id, self.guild_id)
        assert row is not None
        self.assertEqual(str(row["supply_chain_drug_id"]), "heroin")


if __name__ == "__main__":
    unittest.main()
