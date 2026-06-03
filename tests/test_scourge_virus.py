"""Scourge virus event tests."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import config
from cogs.scourge import Scourge
from database import Database


class ScourgeDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / "test.sqlite3"))
        await self.db.connect()
        await self.db.init_schema()
        self.guild_id = 8001
        self.user_a = 101
        self.user_b = 102
        await self.db.ensure_user(self.user_a, self.guild_id)
        await self.db.ensure_user(self.user_b, self.guild_id)
        await self.db.credit_wallet(self.user_a, self.guild_id, 5000.0)
        self.assertTrue(
            await self.db.deposit_to_bank(self.user_a, self.guild_id, 5000.0),
        )

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def test_debit_bank_up_to(self) -> None:
        removed = await self.db.debit_bank_up_to(self.user_a, self.guild_id, 2500.0)
        self.assertEqual(removed, 2500.0)
        self.assertEqual(await self.db.get_bank(self.user_a, self.guild_id), 2500.0)
        self.assertEqual(await self.db.get_balance(self.user_a, self.guild_id), 0.0)

    async def test_scourge_pot_roundtrip(self) -> None:
        now = time.time()
        await self.db.set_scourge_pot(
            self.guild_id,
            self.user_a,
            2,
            now,
            now + 70,
            2000.0,
        )
        row = await self.db.get_scourge_pot(self.guild_id)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(int(row["holder_id"]), self.user_a)
        self.assertEqual(int(row["pass_count"]), 2)
        await self.db.clear_scourge_pot(self.guild_id)
        self.assertIsNone(await self.db.get_scourge_pot(self.guild_id))


    async def test_scourge_event_enabled_defaults_on(self) -> None:
        self.assertTrue(await self.db.get_scourge_event_enabled(self.guild_id))
        settings = await self.db.get_guild_channel_settings(self.guild_id)
        self.assertTrue(settings["scourge_event_enabled"])

    async def test_scourge_event_enabled_toggle(self) -> None:
        await self.db.set_scourge_event_enabled(self.guild_id, False)
        self.assertFalse(await self.db.get_scourge_event_enabled(self.guild_id))
        await self.db.set_scourge_event_enabled(self.guild_id, True)
        self.assertTrue(await self.db.get_scourge_event_enabled(self.guild_id))



    def test_schedule_next_hourly_near_one_hour(self) -> None:
        now = 1_000_000.0
        samples = [Scourge._schedule_next_hourly(now) - now for _ in range(30)]
        for delta in samples:
            self.assertGreaterEqual(delta, 54 * 60)
            self.assertLessEqual(delta, 66 * 60)

class ScourgeCogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / "test.sqlite3"))
        await self.db.connect()
        await self.db.init_schema()
        self.bot = SimpleNamespace(db=self.db, guilds=[], outbound_gate=None)
        self.cog = Scourge(self.bot)  # type: ignore[arg-type]
        self.cog.scourge_tick.cancel()
        self.guild_id = 8002
        self.guild = MagicMock()
        self.guild.id = self.guild_id
        self.bot.guilds = [self.guild]
        for uid, bank in ((201, 9000.0), (202, 8000.0), (203, 7000.0)):
            await self.db.ensure_user(uid, self.guild_id)
            await self.db.deposit_to_bank(uid, self.guild_id, bank)

    async def asyncTearDown(self) -> None:
        for task in self.cog._timers.values():
            task.cancel()
        await self.db.close()
        self.tmp.cleanup()

    async def test_top5_candidates(self) -> None:
        ids = await self.cog._top5_candidate_ids(self.guild_id)
        self.assertEqual(len(ids), 3)
        self.assertEqual(ids[0], 201)

    async def test_hourly_roll_triggers_warning(self) -> None:
        channel = MagicMock()
        channel.id = 555
        send_warning = AsyncMock()
        self.cog._send_warning = send_warning
        with (
            patch(
                "cogs.scourge.resolve_bot_announcement_channel",
                new_callable=AsyncMock,
                return_value=channel,
            ),
            patch("cogs.scourge.random.random", return_value=0.0),
        ):
            now = time.time()
            await self.db.upsert_scourge_event(
                self.guild_id,
                channel.id,
                phase="idle",
                phase_ends_at=0.0,
                next_hourly_roll_at=now - 1,
            )
            await self.cog._tick_guild(self.guild)
        row = await self.db.get_scourge_event(self.guild_id)
        assert row is not None
        self.assertEqual(str(row["phase"]), "warning")
        send_warning.assert_awaited()


    async def test_tick_skipped_when_disabled(self) -> None:
        channel = MagicMock()
        channel.id = 555
        send_warning = AsyncMock()
        self.cog._send_warning = send_warning
        await self.db.set_scourge_event_enabled(self.guild_id, False)
        with patch(
            "cogs.scourge.resolve_bot_announcement_channel",
            new_callable=AsyncMock,
            return_value=channel,
        ):
            now = time.time()
            await self.db.upsert_scourge_event(
                self.guild_id,
                channel.id,
                phase="idle",
                phase_ends_at=0.0,
                next_hourly_roll_at=now - 1,
            )
            await self.cog._tick_guild(self.guild)
        row = await self.db.get_scourge_event(self.guild_id)
        self.assertIsNone(row)
        send_warning.assert_not_awaited()

    def test_penalty_in_range(self) -> None:
        for _ in range(20):
            p = Scourge._roll_penalty()
            self.assertGreaterEqual(p, config.SCOURGE_BANK_PENALTY_MIN)
            self.assertLessEqual(p, config.SCOURGE_BANK_PENALTY_MAX)


if __name__ == "__main__":
    unittest.main()
