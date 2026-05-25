from __future__ import annotations

import unittest

from utils.quests_display import next_quest_line


class QuestDisplayTests(unittest.TestCase):
    def test_next_quest_line_pending(self) -> None:
        rows = [
            {
                "quest_id": "daily_claim",
                "progress": 0,
                "target": 1,
                "completed_at": None,
            },
        ]
        line = next_quest_line(rows, track="daily")
        self.assertIsNotNone(line)
        assert line is not None
        self.assertIn("Daily Check-in", line)

    def test_next_quest_line_complete(self) -> None:
        rows = [
            {
                "quest_id": "daily_claim",
                "progress": 1,
                "target": 1,
                "completed_at": 1.0,
            },
        ]
        self.assertIsNone(next_quest_line(rows, track="daily"))


if __name__ == "__main__":
    unittest.main()
