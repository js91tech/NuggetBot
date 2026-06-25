"""Drug lab panel layout tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from database import Database
from utils.drug_ui import DrugLabView, _apply_lab_panel, build_lab_embed
from utils.drugs import DRUGS


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

    async def test_harvest_moves_crop_to_stash(self) -> None:
        cog = MagicMock()
        cog.bot.db = self.db
        await self.db.credit_wallet(self.user_id, self.guild_id, 10_000.0, apply_bonuses=False)
        await self.db.plant_drug(self.user_id, self.guild_id, "blue_dream")
        async with self.db._write_lock:
            await self.db.conn.execute(
                "UPDATE drug_grows SET ready_at = 0 WHERE user_id = ? AND guild_id = ?",
                (self.user_id, self.guild_id),
            )
            await self.db.conn.commit()
        harvested = await self.db.harvest_drugs(self.user_id, self.guild_id)
        self.assertIn("blue_dream", harvested)
        inv = await self.db.get_drug_inventory(self.user_id, self.guild_id)
        self.assertGreater(inv.get("blue_dream", 0), 0)
        grows = await self.db.list_drug_grows(self.user_id, self.guild_id)
        self.assertEqual(grows, [])

    async def test_apply_lab_panel_reattaches_banner(self) -> None:
        cog = MagicMock()
        cog.bot.db = self.db
        embed, banner = await build_lab_embed(cog, self.guild_id, self.user_id)
        self.assertEqual(embed.image.url, "attachment://lab.png")
        self.assertEqual(banner.filename, "lab.png")

        interaction = MagicMock()
        interaction.response.is_done = MagicMock(return_value=True)
        interaction.edit_original_response = AsyncMock()
        await _apply_lab_panel(
            interaction, cog, self.guild_id, self.user_id, description="refreshed",
        )
        interaction.edit_original_response.assert_awaited_once()
        call_kwargs = interaction.edit_original_response.await_args.kwargs
        self.assertIn("attachments", call_kwargs)
        self.assertEqual(call_kwargs["attachments"][0].filename, "lab.png")
        self.assertEqual(call_kwargs["embed"].description, "refreshed")

    async def test_lab_embed_clips_large_stash_field(self) -> None:
        cog = MagicMock()
        cog.bot.db = self.db
        for drug in DRUGS:
            await self.db.conn.execute(
                """
                INSERT INTO drug_inventory (user_id, guild_id, drug_id, quantity)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, guild_id, drug_id) DO UPDATE SET quantity = excluded.quantity
                """,
                (self.user_id, self.guild_id, drug.drug_id, 99),
            )
        await self.db.conn.commit()
        embed, _banner = await build_lab_embed(cog, self.guild_id, self.user_id)
        stash_field = next(f for f in embed.fields if f.name == "Stash")
        self.assertLessEqual(len(stash_field.value), 1024)


if __name__ == "__main__":
    unittest.main()
