"""Tests for game UI panels and shared actions."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from cogs.boss import Boss
from database import Database
from utils.spell_actions import execute_cast_skill


class PendingConsumableTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.db = Database(str(self.db_path))
        await self.db.connect()
        await self.db.init_schema()
        self.guild_id = 9001
        self.uid = 42

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def test_get_pending_consumable_id(self) -> None:
        self.assertIsNone(
            await self.db.get_pending_consumable_id(self.uid, self.guild_id),
        )
        await self.db.set_pending_consumable(self.uid, self.guild_id, "raid_potion")
        self.assertEqual(
            await self.db.get_pending_consumable_id(self.uid, self.guild_id),
            "raid_potion",
        )


class BossFightEmbedBuffTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.db = Database(str(self.db_path))
        await self.db.connect()
        await self.db.init_schema()
        self.bot = SimpleNamespace(db=self.db, guilds=[], outbound_gate=None)
        self.cog = Boss(self.bot)  # type: ignore[arg-type]
        self.cog.auto_spawn.cancel()
        self.cog.passive_boss_decay_tick.cancel()
        self.guild_id = 9001
        self.uid = 42
        await self.db.replace_boss(self.guild_id, "Hannah", "normal", 5000.0)
        await self.db.set_class_id(self.uid, self.guild_id, "vanguard")

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def test_build_embed_shows_mana_and_pending(self) -> None:
        await self.db.set_pending_spell(self.uid, self.guild_id, "vg_strike")
        member = MagicMock()
        member.id = self.uid
        embed, err = await self.cog.build_boss_fight_embed(self.guild_id, member=member)
        self.assertIsNone(err)
        assert embed is not None
        buff_field = next(f for f in embed.fields if f.name == "Your mana & buffs")
        self.assertIn("Power Strike", buff_field.value)


class CastSkillTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.db = Database(str(self.db_path))
        await self.db.connect()
        await self.db.init_schema()
        self.guild_id = 9001
        self.uid = 42
        await self.db.set_class_id(self.uid, self.guild_id, "vanguard")

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def test_cast_damage_skill_sets_pending(self) -> None:
        row = await self.db.get_user_character(self.uid, self.guild_id)
        await self.db.conn.execute(
            "UPDATE user_character SET mana = 100 WHERE user_id = ? AND guild_id = ?",
            (self.uid, self.guild_id),
        )
        await self.db.conn.commit()
        result = await execute_cast_skill(
            self.db,
            self.uid,
            self.guild_id,
            "vg_strike",
        )
        self.assertTrue(result.ok)
        pending = await self.db.get_pending_spell_id(self.uid, self.guild_id)
        self.assertEqual(pending, "vg_strike")


if __name__ == "__main__":
    unittest.main()
