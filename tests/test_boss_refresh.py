"""Tests for boss refresh helpers and companion/relic fixes."""
from __future__ import annotations

import unittest
from unittest import mock

import config
from utils.boss_adds import roll_add_companion
from utils.boss_refresh import (
    boss_hunt_for_week,
    current_boss_hunt_week_id,
    mood_for_hp_ratio,
    mood_outgoing_damage_mult,
    participation_eligible,
    role_counter_taken_mult,
    role_damage_mult,
)
from utils.companions import ADD_COMPANION_DROPS
from utils.relics import BOSS_RELIC_DROPS, RELIC_DEFINITIONS


class CompanionKeyFixTests(unittest.TestCase):
    def test_companion_keys_match_add_types(self) -> None:
        self.assertIn("henchman", ADD_COMPANION_DROPS)
        self.assertIn("court_jester", ADD_COMPANION_DROPS)
        self.assertNotIn("henchmen", ADD_COMPANION_DROPS)
        self.assertNotIn("jesters", ADD_COMPANION_DROPS)

    def test_roll_add_companion_can_grant(self) -> None:
        with mock.patch("utils.boss_adds.random.random", return_value=0.0):
            self.assertEqual(roll_add_companion("henchman"), "hench_scrap_gnome")
            self.assertEqual(roll_add_companion("court_jester"), "hench_jester_imp")


class RelicPoolTests(unittest.TestCase):
    def test_zz_and_leviathan_have_relics(self) -> None:
        self.assertIn("zz_wrath", BOSS_RELIC_DROPS)
        self.assertIn("world_leviathan", BOSS_RELIC_DROPS)
        self.assertIn("normal", BOSS_RELIC_DROPS)
        for relic_id in BOSS_RELIC_DROPS["zz_wrath"]:
            self.assertIn(relic_id, RELIC_DEFINITIONS)


class DropRebalanceTests(unittest.TestCase):
    def test_inferior_chance_reduced(self) -> None:
        self.assertLess(config.BOSS_INFERIOR_DROP_CHANCE, 0.25)
        self.assertGreater(config.BOSS_EPIC_DROP_CHANCE, 0.05)
        self.assertGreater(config.BOSS_ASPECT_DROP_CHANCE, 0.12)


class RoleMoodTests(unittest.TestCase):
    def test_glass_hits_harder_tank_soaks(self) -> None:
        self.assertGreater(role_damage_mult("glass"), role_damage_mult("tank"))
        self.assertLess(role_counter_taken_mult("tank"), role_counter_taken_mult("glass"))

    def test_armored_mood_nerfs_non_glass(self) -> None:
        self.assertLess(mood_outgoing_damage_mult("armored", "tank"), 1.0)
        self.assertEqual(mood_outgoing_damage_mult("armored", "glass"), 1.0)

    def test_mood_thresholds(self) -> None:
        self.assertEqual(mood_for_hp_ratio(0.9)[0], "calm")
        self.assertEqual(mood_for_hp_ratio(0.6)[0], "aggressive")
        self.assertEqual(mood_for_hp_ratio(0.4)[0], "armored")
        self.assertEqual(mood_for_hp_ratio(0.1)[0], "frantic")


class HuntTests(unittest.TestCase):
    def test_hunt_rotation_stable(self) -> None:
        week = current_boss_hunt_week_id()
        hunt = boss_hunt_for_week(week)
        self.assertTrue(hunt.hunt_key)
        self.assertGreater(hunt.kills_required, 0)
        self.assertIn(hunt.variant, config.BOSS_VARIANTS)

    def test_participation_floor(self) -> None:
        self.assertFalse(participation_eligible(10, config.BOSS_PARTICIPATION_MIN_DAMAGE))
        self.assertTrue(participation_eligible(100, config.BOSS_PARTICIPATION_MIN_DAMAGE))


class WorldBossConfigTests(unittest.TestCase):
    def test_world_leviathan_variant(self) -> None:
        self.assertIn("world_leviathan", config.BOSS_VARIANTS)
        self.assertEqual(config.BOSS_VARIANTS["world_leviathan"]["threat"], 6)
        self.assertEqual(config.BOSS_NAME_WORLD_LEVIATHAN, "World Leviathan")


if __name__ == "__main__":
    unittest.main()
