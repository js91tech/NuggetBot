"""Server activity XP and level progression."""
from __future__ import annotations

import config


def xp_required_for_level(level: int) -> int:
    """XP cost to advance from ``level`` to ``level + 1``."""
    if level < 1:
        level = 1
    return max(
        1,
        int(round(config.ACTIVITY_LEVEL_XP_BASE * (config.ACTIVITY_LEVEL_XP_GROWTH ** (level - 1)))),
    )


def level_from_total_xp(total_xp: int) -> tuple[int, int, int]:
    """Return (level, xp_into_current_level, xp_needed_for_next)."""
    xp = max(0, int(total_xp))
    level = 1
    while level < config.ACTIVITY_LEVEL_MAX:
        need = xp_required_for_level(level)
        if xp < need:
            return level, xp, need
        xp -= need
        level += 1
    return config.ACTIVITY_LEVEL_MAX, 0, 0


def progress_bar(current: int, total: int, *, length: int = 12) -> str:
    if total <= 0:
        return "█" * length
    filled = int(round((min(current, total) / total) * length))
    filled = max(0, min(length, filled))
    return "█" * filled + "░" * (length - filled)
