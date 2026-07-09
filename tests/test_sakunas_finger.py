"""Sakuna's Finger duel deflect consumable."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import Database
from utils.duel_combat import DuelStrike, fighter_from_equipment, format_strike_line, simulate_duel
from utils.sakunas_finger import roll_sakuna_deflect, sakuna_domain_art


class SakunasFingerCombatTests(unittest.TestCase):
    def test_deflect_instantly_downs_attacker(self) -> None:
        attacker = fighter_from_equipment(1, "Attacker", {"weapon": "iron_sword"}, prestige_level=0)
        defender = fighter_from_equipment(2, "Defender", {"weapon": "twig_sword"}, prestige_level=0)
        defender.sakuna_deflect_active = True
        with patch("utils.sakunas_finger.random.random", return_value=0.0):
            result = simulate_duel(attacker, defender)
        self.assertEqual(result.winner_id, defender.user_id)
        self.assertTrue(any(s.sakuna_deflect for s in result.strikes))
        self.assertEqual(attacker.hp, 0)

    def test_format_strike_line_domain_expansion(self) -> None:
        a = fighter_from_equipment(1, "A", {"weapon": "iron_sword"}, prestige_level=0)
        b = fighter_from_equipment(2, "B", {"weapon": "twig_sword"}, prestige_level=0)
        fighters = {a.user_id: a, b.user_id: b}
        strike = DuelStrike(
            attacker_id=1,
            defender_id=2,
            damage=0,
            mitigated=0,
            critical=False,
            verb="is erased by Malevolent Shrine",
            defender_hp_after=100,
            sakuna_deflect=True,
        )
        line = format_strike_line(strike, fighters)
        self.assertIn("Malevolent Shrine", line)
        self.assertIn("instantly downed", line)

    def test_roll_respects_config_chance(self) -> None:
        with patch("utils.sakunas_finger.random.random", return_value=0.5):
            self.assertTrue(roll_sakuna_deflect())
        with patch("utils.sakunas_finger.random.random", return_value=0.99):
            self.assertFalse(roll_sakuna_deflect())


class SakunasFingerDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.db = Database(self.db_path)
        await self.db.connect()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        Path(self.db_path).unlink(missing_ok=True)

    async def test_buff_expires_and_peek(self) -> None:
        guild_id = 42
        user_id = 7
        await self.db.ensure_user(user_id, guild_id)
        expires = await self.db.set_active_sakuna_buff(
            user_id, guild_id, duration_seconds=3600.0,
        )
        active = await self.db.peek_active_sakuna_buff(user_id, guild_id)
        self.assertIsNotNone(active)
        assert active is not None
        self.assertAlmostEqual(float(active["expires"]), expires)

        with patch("database.time.time", return_value=expires + 1.0):
            expired = await self.db.peek_active_sakuna_buff(user_id, guild_id)
        self.assertIsNone(expired)

    async def test_execute_sakuna_duel_steals_wallet_and_bank(self) -> None:
        guild_id = 99
        attacker_id = 1
        defender_id = 2
        for uid, wallet, bank in (
            (attacker_id, 10_000.0, 20_000.0),
            (defender_id, 500.0, 0.0),
        ):
            await self.db.ensure_user(uid, guild_id)
            await self.db.credit_wallet(uid, guild_id, wallet + bank)
            if bank > 0:
                await self.db.deposit_to_bank(uid, guild_id, bank)

        result = await self.db.execute_sakuna_duel(
            guild_id,
            attacker_id,
            defender_id,
            wallet_fraction=0.05,
            bank_fraction=0.07,
            same_target_cooldown_seconds=0.0,
            max_attacks_per_hour=10,
        )
        self.assertIsNotNone(result)
        wallet_loot, bank_loot, _ = result  # type: ignore[misc]
        self.assertAlmostEqual(wallet_loot, 500.0)
        self.assertAlmostEqual(bank_loot, 1400.0)

        attacker_wallet = await self.db.get_balance(attacker_id, guild_id)
        defender_wallet = await self.db.get_balance(defender_id, guild_id)
        self.assertAlmostEqual(attacker_wallet, 9_500.0)
        self.assertAlmostEqual(defender_wallet, 500.0 + 500.0 + 1400.0)


class SakunasFingerArtTests(unittest.TestCase):
    def test_domain_art_prefers_local_file(self) -> None:
        with patch.object(Path, "is_file", return_value=True):
            art = sakuna_domain_art()
        self.assertIsNotNone(art)


if __name__ == "__main__":
    unittest.main()
