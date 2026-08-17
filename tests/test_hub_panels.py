"""Layout and data sanity tests for the crime/casino/dungeon-lobby/drugs hub panels."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from cogs.dungeon import Dungeon
from database import Database
from utils.casino_hub_ui import CasinoHubView, build_casino_hub_embed
from utils.crime_hub_ui import CrimeHubView, build_bounty_board_embed, build_crime_hub_embed
from utils.drugs_hub_extra import (
    DrugsExtraHubView,
    build_crossbreed_embed,
    build_dealer_rank_embed,
)
from utils.dungeon_lobby_ui import DungeonLobbyView, build_dungeon_lobby_embed


def _assert_respects_discord_row_limits(test: unittest.TestCase, view) -> None:
    rows: dict[int | None, list[str]] = {}
    for child in view.children:
        row = getattr(child, "row", None)
        rows.setdefault(row, []).append(type(child).__name__)
    test.assertLessEqual(len(rows), 5, rows)
    for row, names in rows.items():
        buttons = [n for n in names if n == "Button"]
        selects = [n for n in names if n.endswith("Select")]
        test.assertLessEqual(len(selects), 1, f"row {row}: {names}")
        test.assertLessEqual(len(buttons), 5, f"row {row}: {names}")


class CrimeHubTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.db = Database(str(self.db_path))
        await self.db.connect()
        await self.db.init_schema()
        self.guild_id = 9101
        self.cog = SimpleNamespace(bot=SimpleNamespace(db=self.db))

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    def test_hub_embed_lists_all_hustles(self) -> None:
        embed = build_crime_hub_embed("Tester")
        names = [field.name for field in embed.fields]
        self.assertIn("🥷 Pocket Heist", names)
        self.assertIn("🏦 Bank Heist", names)
        self.assertIn("🎯 Bounty Board", names)
        self.assertIn("🚨 Arrests", names)

    async def test_bounty_board_shows_no_bounties_message(self) -> None:
        guild = MagicMock()
        guild.id = self.guild_id
        embed = await build_bounty_board_embed(self.cog, guild)
        self.assertIn("No active bounties", [f.name for f in embed.fields])

    async def test_bounty_board_sorts_by_amount_descending(self) -> None:
        await self.db.ensure_user(1, self.guild_id)
        await self.db.credit_wallet(1, self.guild_id, 10_000.0, apply_bonuses=False)
        await self.db.create_bounty_with_payment(self.guild_id, 1, 2, 100.0, 5.0, "smol")
        await self.db.create_bounty_with_payment(self.guild_id, 1, 3, 900.0, 5.0, "biggest")

        guild = MagicMock()
        guild.id = self.guild_id
        guild.get_member.return_value = None
        embed = await build_bounty_board_embed(self.cog, guild)
        top_field = next(f for f in embed.fields if f.name == "Top bounties")
        biggest_pos = top_field.value.index("biggest")
        smol_pos = top_field.value.index("smol")
        self.assertLess(biggest_pos, smol_pos)

    def test_view_locked_to_single_user_has_normal_row_layout(self) -> None:
        view = CrimeHubView(self.cog, self.guild_id, user_id=555)
        _assert_respects_discord_row_limits(self, view)
        self.assertEqual(view.user_id, 555)

    def test_view_unlocked_for_public_posts(self) -> None:
        view = CrimeHubView(self.cog, self.guild_id)
        self.assertIsNone(view.user_id)


class CasinoHubTests(unittest.TestCase):
    def test_hub_embed_lists_all_games(self) -> None:
        embed = build_casino_hub_embed("Tester")
        names = [field.name for field in embed.fields]
        self.assertIn("🪙 Coinflip", names)
        self.assertIn("🎰 Slots", names)
        self.assertIn("💰 Jackpot", names)
        self.assertIn("🃏 Blackjack", names)

    def test_view_respects_discord_row_limits(self) -> None:
        view = CasinoHubView(user_id=123)
        _assert_respects_discord_row_limits(self, view)


class DungeonLobbyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.db = Database(str(self.db_path))
        await self.db.connect()
        await self.db.init_schema()
        self.bot = SimpleNamespace(db=self.db)
        self.cog = Dungeon(self.bot)  # type: ignore[arg-type]
        self.guild_id = 9102
        self.user_id = 88

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def test_lobby_embed_shows_solo_and_party_tiers(self) -> None:
        embed = await build_dungeon_lobby_embed(
            self.cog, self.guild_id, self.user_id, member_name="Tester",
        )
        names = [field.name for field in embed.fields]
        self.assertTrue(any("Solo" in n for n in names))
        self.assertTrue(any("Party" in n for n in names))
        self.assertTrue(any("energy" in n.lower() for n in names))

    async def test_view_respects_discord_row_limits(self) -> None:
        view = DungeonLobbyView(self.cog, self.guild_id, self.user_id)
        _assert_respects_discord_row_limits(self, view)


class DrugsExtraHubTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.db = Database(str(self.db_path))
        await self.db.connect()
        await self.db.init_schema()
        self.guild_id = 9103
        self.user_id = 77
        await self.db.ensure_user(self.user_id, self.guild_id)
        self.cog = MagicMock()
        self.cog.bot.db = self.db

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def test_rank_embed_shows_runner_by_default(self) -> None:
        embed = await build_dealer_rank_embed(self.cog, self.guild_id, self.user_id)
        self.assertIn("Runner", embed.title)

    async def test_crossbreed_embed_notes_empty_stash(self) -> None:
        embed = await build_crossbreed_embed(self.cog, self.guild_id, self.user_id)
        strains_field = next(f for f in embed.fields if f.name == "Your strains")
        self.assertIn("Empty", strains_field.value)

    async def test_crossbreed_embed_lists_owned_strains(self) -> None:
        await self.db.grant_drug_units(self.user_id, self.guild_id, "blue_dream", 3)
        embed = await build_crossbreed_embed(self.cog, self.guild_id, self.user_id)
        strains_field = next(f for f in embed.fields if f.name == "Your strains")
        self.assertIn("×3", strains_field.value)

    def test_view_respects_discord_row_limits(self) -> None:
        view = DrugsExtraHubView(self.cog, self.guild_id, self.user_id)
        _assert_respects_discord_row_limits(self, view)


if __name__ == "__main__":
    unittest.main()
