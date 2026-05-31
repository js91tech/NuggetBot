from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from utils.helpers import fmt_amount

if TYPE_CHECKING:
    from database import Database


@dataclass(frozen=True)
class QuestDef:
    quest_id: str
    name: str
    description: str
    target: int
    reward: float
    event: str


ONBOARDING_QUESTS: tuple[QuestDef, ...] = (
    QuestDef("claim_daily", "First Rations", "Claim `/daily` once.", 1, 500.0, "daily_claim"),
    QuestDef("buy_gear", "Arm Up", "Buy any item from `/shop`.", 1, 750.0, "shop_buy"),
    QuestDef("raid_once", "Join the Raid", "Deal damage to a boss with `/attack`.", 1, 1000.0, "boss_attack"),
    QuestDef("heal_once", "Field Medic", "Heal a raider with `/heal`.", 1, 750.0, "boss_heal"),
    QuestDef("pay_friend", "Spread the Wealth", "Send nuggets with `/pay`.", 1, 500.0, "wallet_pay"),
)

DAILY_QUEST_POOL: tuple[QuestDef, ...] = (
    QuestDef("daily_claim", "Daily Check-in", "Claim `/daily`.", 1, 400.0, "daily_claim"),
    QuestDef("boss_hits", "Raid Pressure", "Land 5 boss attacks.", 5, 800.0, "boss_attack"),
    QuestDef("heal_allies", "Triage Run", "Heal raiders 3 times.", 3, 700.0, "boss_heal"),
    QuestDef("craft_item", "Workshop Shift", "Craft an upgrade with `/craft`.", 1, 900.0, "craft_done"),
    QuestDef("gamble_once", "Lucky Break", "Play `/coinflip` or `/blackjack`.", 1, 600.0, "gamble_play"),
    QuestDef("messages", "Stay Active", "Earn from 20 chat messages.", 20, 550.0, "chat_message"),
    QuestDef("job_shifts", "Day Job", "Complete 3 instant job shifts with `/work`.", 3, 650.0, "job_work"),
    QuestDef("duel_win", "Duelist", "Win a `/duel`.", 1, 900.0, "duel_win"),
    QuestDef("slots_spin", "Lucky Slots", "Play `/slots` once.", 1, 550.0, "gamble_play"),
    QuestDef("dungeon_clear", "Delver", "Clear a `/dungeon` run.", 1, 1_100.0, "dungeon_clear"),
    QuestDef("territory_claim", "Land Grab", "Claim or capture a territory.", 1, 1_200.0, "territory_claim"),
    QuestDef("territory_guards", "Mercenary", "Hire territory guards (any amount).", 1, 800.0, "territory_guards"),
)

DAILY_QUEST_COUNT = 3
TRACK_ONBOARDING = "onboarding"
TRACK_DAILY = "daily"


def daily_reset_key(timestamp: float | None = None) -> str:
    ts = time.time() if timestamp is None else timestamp
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def quest_by_id(quest_id: str) -> QuestDef | None:
    for quest in (*ONBOARDING_QUESTS, *DAILY_QUEST_POOL):
        if quest.quest_id == quest_id:
            return quest
    return None


def is_veteran(progress) -> bool:
    try:
        prestige = int(progress["prestige_level"])
        bosses = int(progress["bosses_killed"])
    except (KeyError, TypeError, IndexError):
        return False
    return prestige > 0 or bosses >= 3


async def ensure_onboarding_quests(db: Database, guild_id: int, user_id: int) -> None:
    rows = await db.list_user_quests(guild_id, user_id, TRACK_ONBOARDING)
    if rows:
        return
    for quest in ONBOARDING_QUESTS:
        await db.upsert_user_quest(
            guild_id,
            user_id,
            TRACK_ONBOARDING,
            quest.quest_id,
            target=quest.target,
            reset_key="",
        )


async def ensure_daily_quests(db: Database, guild_id: int, user_id: int) -> None:
    reset_key = daily_reset_key()
    rows = await db.list_user_quests(guild_id, user_id, TRACK_DAILY)
    if rows and all(str(row["reset_key"]) == reset_key for row in rows):
        return
    await db.clear_user_quest_track(guild_id, user_id, TRACK_DAILY)
    picks = random.sample(list(DAILY_QUEST_POOL), k=min(DAILY_QUEST_COUNT, len(DAILY_QUEST_POOL)))
    for quest in picks:
        await db.upsert_user_quest(
            guild_id,
            user_id,
            TRACK_DAILY,
            quest.quest_id,
            target=quest.target,
            reset_key=reset_key,
        )


async def record_quest_event(
    db: Database,
    guild_id: int,
    user_id: int,
    event: str,
    *,
    amount: int = 1,
) -> list[str]:
    """Advance matching quests; return quest_ids newly completed this call."""
    progress = await db.get_user_progress(user_id, guild_id)
    tracks: list[str] = []
    if is_veteran(progress):
        await ensure_daily_quests(db, guild_id, user_id)
        tracks.append(TRACK_DAILY)
    else:
        await ensure_onboarding_quests(db, guild_id, user_id)
        onboard_done = await db.count_completed_quests(
            guild_id, user_id, TRACK_ONBOARDING
        ) >= len(ONBOARDING_QUESTS)
        if onboard_done:
            await ensure_daily_quests(db, guild_id, user_id)
            tracks.append(TRACK_DAILY)
        else:
            tracks.append(TRACK_ONBOARDING)

    completed_ids: list[str] = []
    for track in tracks:
        rows = await db.list_user_quests(guild_id, user_id, track)
        for row in rows:
            quest = quest_by_id(str(row["quest_id"]))
            if quest is None or quest.event != event:
                continue
            newly_done, _, _ = await db.advance_quest_progress(
                guild_id,
                user_id,
                track,
                quest.quest_id,
                amount=amount,
            )
            if newly_done:
                await db.credit_wallet(user_id, guild_id, quest.reward)
                completed_ids.append(quest.quest_id)
    return completed_ids


def format_quest_lines(rows: list, *, track: str) -> list[str]:
    lines: list[str] = []
    for row in rows:
        quest = quest_by_id(str(row["quest_id"]))
        if quest is None:
            continue
        progress = int(row["progress"])
        target = int(row["target"])
        done = row["completed_at"] is not None
        mark = "✅" if done else "⬜"
        lines.append(
            f"{mark} **{quest.name}** — {quest.description} "
            f"({progress}/{target}) · Reward {fmt_amount(quest.reward)}"
        )
    if not lines:
        if track == TRACK_ONBOARDING:
            return ["All onboarding quests complete!"]
        return ["No daily goals assigned yet. Run `/quests` to refresh."]
    return lines
