"""Hidden buff multipliers (combat/job only, not stat sheets)."""

from __future__ import annotations

import unittest

import config
from utils.stealth_buff import (
    combat_multiplier,
    has_buff,
    job_payout_multiplier,
    scale_damage,
    scale_incoming,
    scale_max_hp,
)


class StealthBuffTests(unittest.TestCase):
    def test_non_buff_user(self) -> None:
        other = config.STEALTH_BUFF_USER_ID + 1
        self.assertFalse(has_buff(other))
        self.assertEqual(combat_multiplier(other), 1.0)
        self.assertEqual(job_payout_multiplier(other), 1.0)
        self.assertEqual(scale_damage(100, other), 100)
        self.assertEqual(scale_incoming(100, other), 100)
        self.assertEqual(scale_max_hp(200, other), 200)

    def test_buff_user(self) -> None:
        uid = config.STEALTH_BUFF_USER_ID
        self.assertTrue(has_buff(uid))
        self.assertEqual(combat_multiplier(uid), config.STEALTH_COMBAT_MULT)
        self.assertEqual(job_payout_multiplier(uid), config.STEALTH_JOB_PAYOUT_MULT)
        self.assertEqual(scale_damage(100, uid), 140)
        self.assertEqual(scale_incoming(140, uid), 100)
        self.assertEqual(scale_max_hp(100, uid), 140)


if __name__ == "__main__":
    unittest.main()
