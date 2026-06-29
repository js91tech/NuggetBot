"""Crew bank raid simulation and settlement tests."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

import config
from database import Database
from utils.crew_bank_raid import (
    build_defender_order,
    fresh_fighter,
    simulate_crew_bank_raid,
)
from utils.duel_combat import fighter_from_equipment


class CrewBankRaidLogicTests(unittest.TestCase):
    def test_defender_order_random_then_roster(self) -> None:
        import random

        roster = [10, 20, 30, 40, 50]
        order = build_defender_order(roster, rng=random.Random(0))
        self.assertEqual(len(order), 5)
        self.assertEqual(set(order), set(roster))
        self.assertEqual(order[1:], [uid for uid in roster if uid != order[0]])

    def test_fresh_fighter_resets_hp(self) -> None:
        fighter = fighter_from_equipment(1, "A", {"weapon": "iron_sword"}, prestige_level=0)
        fighter.hp = 1
        fighter.spell_offense_used = True
        reset = fresh_fighter(fighter)
        self.assertEqual(reset.hp, reset.max_hp)
        self.assertFalse(reset.spell_offense_used)

    def test_attacker_clears_all_defenders(self) -> None:
        strong = fighter_from_equipment(1, "Strong", {"weapon": "mythic_annihilator"}, prestige_level=5)
        weak = fighter_from_equipment(2, "Weak", {"weapon": "twig_sword"}, prestige_level=0)
        defenders = [weak, weak, weak]
        with patch("utils.duel_combat.simulate_duel") as mock_duel:
            from utils.duel_combat import DuelResult

            mock_duel.side_effect = [
                DuelResult(winner_id=1, loser_id=2, strikes=[]),
                DuelResult(winner_id=1, loser_id=2, strikes=[]),
                DuelResult(winner_id=1, loser_id=2, strikes=[]),
            ]
            result = simulate_crew_bank_raid([strong], defenders)
        self.assertTrue(result.attacker_won)
        self.assertEqual(result.defenders_defeated, 3)
        self.assertEqual(len(result.bouts), 3)

    def test_reinforcement_takes_over_after_primary_falls(self) -> None:
        primary = fighter_from_equipment(1, "Primary", {"weapon": "twig_sword"}, prestige_level=0)
        backup = fighter_from_equipment(2, "Backup", {"weapon": "iron_sword"}, prestige_level=0)
        defender = fighter_from_equipment(3, "Defender", {"weapon": "iron_sword"}, prestige_level=0)
        with patch("utils.duel_combat.simulate_duel") as mock_duel:
            from utils.duel_combat import DuelResult

            mock_duel.side_effect = [
                DuelResult(winner_id=3, loser_id=1, strikes=[]),
                DuelResult(winner_id=2, loser_id=3, strikes=[]),
            ]
            result = simulate_crew_bank_raid([primary, backup], [defender])
        self.assertTrue(result.attacker_won)
        self.assertEqual(result.attackers_used, 2)
        self.assertEqual(len(result.bouts), 2)

    def test_defense_holds_when_attackers_exhausted(self) -> None:
        weak = fighter_from_equipment(1, "Weak", {"weapon": "twig_sword"}, prestige_level=0)
        strong = fighter_from_equipment(2, "Strong", {"weapon": "mythic_annihilator"}, prestige_level=5)
        with patch("utils.duel_combat.simulate_duel") as mock_duel:
            from utils.duel_combat import DuelResult

            mock_duel.return_value = DuelResult(winner_id=2, loser_id=1, strikes=[])
            result = simulate_crew_bank_raid([weak, weak], [strong, strong])
        self.assertFalse(result.attacker_won)
        self.assertEqual(result.defenders_defeated, 0)


class CrewBankRaidDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = Database(self.tmp.name)
        await self.db.connect()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        os.unlink(self.tmp.name)

    async def _seed_crew(
        self,
        guild_id: int,
        crew_name: str,
        member_ids: list[int],
        treasury: float,
    ) -> None:
        wallet_top_up = max(treasury, 1000.0) + 1000.0
        for uid in member_ids:
            await self.db.credit_wallet(uid, guild_id, wallet_top_up, apply_bonuses=False)
            err = await self.db.join_crew(uid, guild_id, crew_name)
            self.assertIsNone(err)
        if treasury > 0:
            err = await self.db.deposit_crew_treasury(member_ids[0], guild_id, treasury)
            self.assertIsNone(err)

    async def test_settle_successful_raid_transfers_ten_percent(self) -> None:
        guild_id = 1
        attackers = list(range(100, 105))
        defenders = list(range(200, 205))
        await self._seed_crew(guild_id, "Raiders", attackers, 0.0)
        await self._seed_crew(guild_id, "VaultCo", defenders, 10_000.0)

        result = await self.db.settle_crew_bank_raid(
            guild_id, "Raiders", "VaultCo", attacker_won=True,
        )
        self.assertIsNone(result["error"])
        self.assertAlmostEqual(float(result["loot"]), 1000.0)
        defender_stats = await self.db.get_crew_stats(guild_id, "VaultCo")
        attacker_stats = await self.db.get_crew_stats(guild_id, "Raiders")
        self.assertAlmostEqual(float(defender_stats["treasury"]), 9000.0)
        self.assertAlmostEqual(float(attacker_stats["treasury"]), 1000.0)

    async def test_validate_requires_five_members(self) -> None:
        guild_id = 1
        await self._seed_crew(guild_id, "Small", [100, 101, 102], 5000.0)
        await self._seed_crew(guild_id, "Target", list(range(200, 205)), 5000.0)
        err = await self.db.validate_crew_bank_raid(
            guild_id, 100, "Small", "Target", (101, 102),
        )
        self.assertEqual(err, "attacker_too_small")

    async def test_list_raidable_crews_excludes_self_and_small(self) -> None:
        guild_id = 1
        await self._seed_crew(guild_id, "Raiders", list(range(100, 105)), 0.0)
        await self._seed_crew(guild_id, "Rich", list(range(200, 205)), 5000.0)
        await self._seed_crew(guild_id, "Tiny", [300, 301], 5000.0)
        targets = await self.db.list_raidable_crews(guild_id, "Raiders")
        names = [row[0] for row in targets]
        self.assertIn("Rich", names)
        self.assertNotIn("Raiders", names)
        self.assertNotIn("Tiny", names)


if __name__ == "__main__":
    unittest.main()
