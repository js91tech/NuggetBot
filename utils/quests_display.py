from __future__ import annotations

from utils.quests import TRACK_DAILY, format_quest_lines


def next_quest_line(rows: list, *, track: str) -> str | None:
    pending = [row for row in rows if row["completed_at"] is None]
    if not pending:
        return None
    lines = format_quest_lines(pending[:1], track=track)
    return lines[0] if lines else None
