"""Player status helpers and cooldown reads."""

from __future__ import annotations

import time
import unittest

from utils.player_status import format_countdown


class PlayerStatusFormatTests(unittest.TestCase):
    def test_ready_now(self) -> None:
        self.assertEqual(format_countdown(0), "Ready now")
        self.assertEqual(format_countdown(-5), "Ready now")

    def test_hours_and_minutes(self) -> None:
        self.assertEqual(format_countdown(3661), "1h 1m")

    def test_minutes_and_seconds(self) -> None:
        self.assertEqual(format_countdown(125), "2m 5s")

    def test_seconds_only(self) -> None:
        self.assertEqual(format_countdown(45), "45s")


if __name__ == "__main__":
    unittest.main()
