"""One-time retention milestone definitions and eligibility checks."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import config
from utils.activity_levels import level_from_total_xp

if TYPE_CHECKING:
    from database import Database


@dataclass(frozen=True)
class MilestoneDef:
    milestone_id: str
    name: str
    description: str


MILESTONES: tuple[MilestoneDef, ...] = (
    MilestoneDef("activity_10", "Regular", "Reach activity level 10."),
    MilestoneDef("activity_25", "Veteran", "Reach activity level 25."),
    MilestoneDef("activity_50", "Legend", "Reach activity level 50."),
    MilestoneDef("streak_7", "Week Warrior", "Reach a 7-day daily streak."),
    MilestoneDef("streak_14", "Dedicated", "Reach a 14-day daily streak."),
    MilestoneDef("referrals_3", "Recruiter", "Refer 3 players."),
    MilestoneDef("weekly_all", "Challenge Master", "Complete all weekly challenges in one week."),
    MilestoneDef("first_trade", "Dealer", "Complete your first player trade."),
)


async def milestone_eligible(
    db: Database, user_id: int, guild_id: int, milestone_id: str,
) -> bool:
    if milestone_id == "activity_10":
        xp = await db.get_activity_xp(user_id, guild_id)
        level, _, _ = level_from_total_xp(xp)
        return level >= 10
    if milestone_id == "activity_25":
        xp = await db.get_activity_xp(user_id, guild_id)
        level, _, _ = level_from_total_xp(xp)
        return level >= 25
    if milestone_id == "activity_50":
        xp = await db.get_activity_xp(user_id, guild_id)
        level, _, _ = level_from_total_xp(xp)
        return level >= 50
    if milestone_id == "streak_7":
        return await db.get_daily_streak(user_id, guild_id) >= 7
    if milestone_id == "streak_14":
        return await db.get_daily_streak(user_id, guild_id) >= 14
    if milestone_id == "referrals_3":
        return await db.count_referrals_made(user_id, guild_id) >= 3
    if milestone_id == "weekly_all":
        from utils.quests import TRACK_WEEKLY, ensure_weekly_quests

        await ensure_weekly_quests(db, guild_id, user_id)
        rows = await db.list_user_quests(guild_id, user_id, TRACK_WEEKLY)
        return bool(rows) and all(r["completed_at"] is not None for r in rows)
    if milestone_id == "first_trade":
        return await db.has_completed_trade(user_id, guild_id)
    return False


def milestone_reward(milestone_id: str) -> float:
    return float(config.MILESTONE_REWARDS.get(milestone_id, 0.0))
