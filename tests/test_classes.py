from __future__ import annotations

import unittest

import config
from utils.classes import (
    CLASS_MAP,
    can_evolve,
    element_multiplier,
    get_class,
    pvp_matchup_multiplier,
)


class TestClasses(unittest.TestCase):
    def test_starter_count(self) -> None:
        starters = [c for c in CLASS_MAP.values() if c.tier == "starter"]
        self.assertEqual(len(starters), 3)

    def test_hybrid_requires_roots(self) -> None:
        self.assertEqual(can_evolve("vanguard_slayer_reaper", 5000, set()), [])
        options = can_evolve(
            "vanguard_slayer_reaper",
            5000,
            {"vanguard", "shade"},
        )
        ids = {o.class_id for o in options}
        self.assertIn("warlord", ids)

    def test_element_wheel(self) -> None:
        self.assertGreater(element_multiplier("fire", "frost"), 1.0)
        self.assertLess(element_multiplier("fire", "void"), 1.0)

    def test_pvp_role_advantage(self) -> None:
        self.assertGreater(
            pvp_matchup_multiplier("striker", "skirmisher"),
            1.0,
        )

    def test_jester_exists(self) -> None:
        jester = get_class(config.JESTER_CLASS_ID)
        self.assertIsNotNone(jester)
        assert jester is not None
        self.assertEqual(jester.modifiers.duel_damage_mult, config.JESTER_STAT_MULT)
        self.assertEqual(jester.modifiers.crit_bonus, config.JESTER_CRIT_BONUS)
        self.assertEqual(config.JESTER_REFLECT_CHANCE, 0.10)


if __name__ == "__main__":
    unittest.main()
