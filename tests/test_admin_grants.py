"""Discord admin free-grant commands are limited to the silent-power user."""
from __future__ import annotations

import unittest

import config
from cogs.admin import can_use_discord_admin_grants


class AdminGrantAccessTests(unittest.TestCase):
    def test_silent_power_user_can_grant(self) -> None:
        self.assertTrue(can_use_discord_admin_grants(config.SILENT_POWER_USER_ID))

    def test_other_users_cannot_grant(self) -> None:
        self.assertFalse(can_use_discord_admin_grants(1))
        self.assertFalse(can_use_discord_admin_grants(config.JESTER_EXCLUSIVE_USER_ID))


if __name__ == "__main__":
    unittest.main()
