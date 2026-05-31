"""Boss fight panel embed tests."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import config
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

    async def test_self_heal_costs_2500(self) -> None:
        from unittest.mock import AsyncMock, MagicMock, patch

        uid = 42
        await self.db.ensure_user(uid, self.guild_id)
        await self.db.credit_wallet(uid, self.guild_id, 10_000.0)
        before = await self.db.get_balance(uid, self.guild_id)
        await self.db.set_downed_until(uid, self.guild_id, time.time() + 120)
        member = MagicMock()
        member.id = uid
        member.display_name = "Raider"
        guild = MagicMock()
        guild.id = self.guild_id

        with patch("cogs.boss.record_quest_event", new_callable=AsyncMock):
            result = await self.cog.execute_boss_self_heal(member, guild)
        self.assertIsNone(result.error)
        self.assertFalse(await self.db.is_downed(uid, self.guild_id))
        balance = await self.db.get_balance(uid, self.guild_id)
        self.assertAlmostEqual(before - balance, config.BOSS_SELF_HEAL_COST)

    async def test_self_heal_rejects_insufficient_funds(self) -> None:
        from unittest.mock import MagicMock

        uid = 43
        await self.db.ensure_user(uid, self.guild_id)
        await self.db.set_downed_until(uid, self.guild_id, time.time() + 120)
        member = MagicMock()
        member.id = uid
        member.display_name = "Broke"
        guild = MagicMock()
        guild.id = self.guild_id

        result = await self.cog.execute_boss_self_heal(member, guild)
        self.assertIsNotNone(result.error)
        assert result.error is not None
        self.assertIn("2,500", result.error)


if __name__ == "__main__":
    unittest.main()
