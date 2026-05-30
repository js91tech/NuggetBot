"""Boss fight panel embed tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from cogs.boss import Boss
from database import Database


class BossFightEmbedTests(unittest.IsolatedAsyncioTestCase):
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
        await self.db.replace_boss(self.guild_id, "Hannah", "normal", 5000.0)

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self.tmp.cleanup()

    async def test_build_embed_shows_boss_hp(self) -> None:
        embed, err = await self.cog.build_boss_fight_embed(self.guild_id)
        self.assertIsNone(err)
        assert embed is not None
        self.assertIn("Hannah", embed.title)
        hp_field = next(f for f in embed.fields if f.name == "HP")
        self.assertIn("5000", hp_field.value.replace(",", ""))

    async def test_build_embed_includes_player_loadout(self) -> None:
        uid = 42
        await self.db.grant_item(uid, self.guild_id, "twig_sword")
        await self.db.equip_item(uid, self.guild_id, "weapon", "twig_sword")
        member = MagicMock()
        member.id = uid
        embed, err = await self.cog.build_boss_fight_embed(self.guild_id, member=member)
        self.assertIsNone(err)
        assert embed is not None
        loadout_field = next(f for f in embed.fields if f.name == "Your loadout")
        self.assertIn("Twig Sword", loadout_field.value)

    async def test_build_embed_no_boss_returns_error(self) -> None:
        await self.db.clear_boss(self.guild_id)
        embed, err = await self.cog.build_boss_fight_embed(self.guild_id)
        self.assertIsNone(embed)
        self.assertEqual(err, "No boss is active right now.")


if __name__ == "__main__":
    unittest.main()
