"""Boss HP and raid damage scaling tests."""
from __future__ import annotations

import unittest

import config
from utils.boss_mechanics import (
    boss_raid_damage_bonus,
    business_boss_reward_mult,
    clamp_boss_personal_reward_mult,
    compute_boss_hp,
)


class BossMechanicsTests(unittest.TestCase):
    def test_mythic_hp_uses_standard_multiplier(self) -> None:
        circulation = 5_000_000.0
        scale = config.BOSS_CIRCULATION_HP_FACTOR
        expected = (
            min(config.BOSS_HP_CAP, circulation * scale)
            * 4.5
            * (1 + 4 * config.BOSS_THREAT_HP_BONUS_PER_TIER)
        )
        self.assertAlmostEqual(compute_boss_hp(circulation, scale, "mythic"), expected)

    def test_mythic_damage_bonus_helps_raid_dps(self) -> None:
        self.assertEqual(boss_raid_damage_bonus("normal"), 1.0)
        self.assertGreater(boss_raid_damage_bonus("mythic"), 1.0)
        self.assertEqual(
            boss_raid_damage_bonus("mythic"),
            config.BOSS_RAID_DAMAGE_BONUS_BY_THREAT[5],
        )

    def test_business_boss_reward_mult_defaults_to_one(self) -> None:
        self.assertEqual(business_boss_reward_mult(), 1.0)
        self.assertEqual(business_boss_reward_mult(None, None), 1.0)

    def test_business_boss_reward_mult_scales_tier_and_prestige(self) -> None:
        tier7 = business_boss_reward_mult(7, 0)
        self.assertAlmostEqual(
            tier7,
            1.0 + config.BOSS_REWARD_BUSINESS_TIER_BONUS * 6,
        )
        bp5 = business_boss_reward_mult(1, 5)
        self.assertAlmostEqual(
            bp5,
            1.0 + config.BOSS_REWARD_BUSINESS_PRESTIGE_BONUS * 5,
        )
        combined = business_boss_reward_mult(7, 5)
        self.assertAlmostEqual(
            combined,
            1.0
            + config.BOSS_REWARD_BUSINESS_TIER_BONUS * 6
            + config.BOSS_REWARD_BUSINESS_PRESTIGE_BONUS * 5,
        )

    def test_clamp_boss_personal_reward_mult_caps_combined(self) -> None:
        under = clamp_boss_personal_reward_mult(1.10, 1.195)
        self.assertAlmostEqual(under, 1.10 * 1.195)
        self.assertLess(under, config.BOSS_REWARD_PERSONAL_MULT_CAP)

        over = clamp_boss_personal_reward_mult(1.20, 1.40)
        self.assertEqual(over, config.BOSS_REWARD_PERSONAL_MULT_CAP)

        self.assertEqual(clamp_boss_personal_reward_mult(1.0, 1.0), 1.0)


if __name__ == "__main__":
    unittest.main()
