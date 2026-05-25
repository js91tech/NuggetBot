from __future__ import annotations

import time
from typing import TYPE_CHECKING

from utils.player_status import format_countdown

if TYPE_CHECKING:
    from database import Database


async def restriction_detail(
    db: Database,
    user_id: int,
    guild_id: int,
    *,
    at: float | None = None,
) -> str | None:
    """Return a player-facing restriction message, or None if clear."""
    now = time.time() if at is None else at
    row = await db.get_user(user_id, guild_id)
    arrested_until = float(row["arrested_until"])
    if arrested_until > now:
        return f"You are **arrested** for {format_countdown(arrested_until - now)}."
    downed_until = float(row["downed_until"])
    if downed_until > now:
        return (
            f"You are **downed** for {format_countdown(downed_until - now)}. "
            "Ask a teammate for `/heal`."
        )
    return None
