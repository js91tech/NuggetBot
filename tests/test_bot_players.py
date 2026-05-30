"""Bot player eligibility helpers."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

import config
from utils.bot_players import bot_players_enabled, pvp_target_error, skip_passive_bot


class BotPlayersTests(unittest.TestCase):
    def test_pvp_allows_bots_when_enabled(self) -> None:
        target = MagicMock()
        target.id = 99
        target.bot = True
        self.assertIsNone(pvp_target_error(target, 1))

    def test_pvp_blocks_self(self) -> None:
        target = MagicMock()
        target.id = 5
        target.bot = False
        self.assertEqual(pvp_target_error(target, 5), "You can't target yourself.")

    def test_passive_skips_bots_by_default(self) -> None:
        author = MagicMock()
        author.bot = True
        self.assertTrue(skip_passive_bot(author))

    def test_config_flag(self) -> None:
        self.assertTrue(bot_players_enabled())
        self.assertFalse(config.ALLOW_BOT_PASSIVE_INCOME)


if __name__ == "__main__":
    unittest.main()
