"""Defeat-side boss reward helpers for the raid refresh."""
from __future__ import annotations

import random
from typing import Any

import config
from items import get_item
from utils.boss_refresh import (
    boss_hunt_for_week,
    current_boss_hunt_week_id,
    interesting_loot_pool,
    participation_eligible,
)
from utils.helpers import fmt_amount


async def grant_participation_rewards(
    db: Any,
    guild_id: int,
    rows: list[Any],
    *,
    display_name,
) -> list[str]:
    lines: list[str] = []
    for row in rows:
        uid = int(row["user_id"])
        dmg = float(row["damage"])
        if not participation_eligible(dmg, config.BOSS_PARTICIPATION_MIN_DAMAGE):
            continue
        await db.credit_wallet(
            uid, guild_id, config.BOSS_PARTICIPATION_PURSE, apply_bonuses=False,
        )
        scrap = random.randint(*config.BOSS_PARTICIPATION_SCRAP)
        for _ in range(scrap):
            await db.grant_item(uid, guild_id, "alchemy_scrap")
        lines.append(
            f"{display_name(uid)}: participation {fmt_amount(config.BOSS_PARTICIPATION_PURSE)} "
            f"+ {scrap} scrap"
        )
    return lines


async def grant_named_bonus(
    db: Any,
    guild_id: int,
    user_id: int,
    amount: float,
    *,
    label: str,
    display_name,
) -> str:
    await db.credit_wallet(user_id, guild_id, amount, apply_bonuses=False)
    return f"{display_name(user_id)}: {label} {fmt_amount(amount)}"


async def grant_top_damager_loot(
    db: Any,
    guild_id: int,
    rows: list[Any],
    variant: str,
    *,
    display_name,
) -> str | None:
    if not rows:
        return None
    top = rows[0]
    uid = int(top["user_id"])
    item_id = random.choice(interesting_loot_pool(variant))
    item = get_item(item_id)
    await db.grant_item(uid, guild_id, item_id)
    label = item.name if item is not None else item_id
    return f"{display_name(uid)}: top damager crate — **{label}**"


async def grant_world_leviathan_stash(
    db: Any,
    guild_id: int,
    contributor_ids: list[int],
    *,
    display_name,
) -> list[str]:
    lines: list[str] = []
    for uid in contributor_ids:
        await db.grant_item(uid, guild_id, "void_hardener")
        scrap = random.randint(3, 8)
        for _ in range(scrap):
            await db.grant_item(uid, guild_id, "alchemy_scrap")
        lines.append(f"{display_name(uid)}: 1× Void Hardener + {scrap} scrap")
    return lines


async def update_hunt_and_crew_scores(
    db: Any,
    guild_id: int,
    rows: list[Any],
    variant: str,
) -> tuple[list[str], list[str]]:
    """Returns (hunt_progress_lines, crew_score_lines)."""
    week_id = current_boss_hunt_week_id()
    hunt = boss_hunt_for_week(week_id)
    hunt_lines: list[str] = []
    crew_damage: dict[str, float] = {}

    for row in rows:
        uid = int(row["user_id"])
        dmg = float(row["damage"])
        kills = await db.record_boss_hunt_kill(
            uid,
            guild_id,
            week_id=week_id,
            hunt_key=hunt.hunt_key,
            variant=variant,
            target_variant=hunt.variant,
        )
        if variant == hunt.variant:
            hunt_lines.append(
                f"<@{uid}> hunt **{hunt.label}**: {kills}/{hunt.kills_required}"
            )
        crew_name = await db.get_crew_membership(uid, guild_id)
        if crew_name:
            crew_damage[crew_name] = crew_damage.get(crew_name, 0.0) + dmg

    crew_lines: list[str] = []
    if crew_damage:
        for crew_name, dmg in sorted(crew_damage.items(), key=lambda kv: kv[1], reverse=True):
            await db.add_crew_weekly_boss_damage(guild_id, crew_name, week_id, dmg)
        top_crew, top_dmg = max(crew_damage.items(), key=lambda kv: kv[1])
        members = await db.list_crew_member_user_ids(guild_id, top_crew)
        crew_lines.append(
            f"Crew **{top_crew}** led this raid with **{fmt_amount(top_dmg)}** damage."
        )
        if members:
            winner = random.choice(members)
            await db.credit_wallet(
                winner, guild_id, config.BOSS_CREW_MVP_BONUS, apply_bonuses=False,
            )
            crew_lines.append(
                f"Crew MVP purse {fmt_amount(config.BOSS_CREW_MVP_BONUS)} → <@{winner}>"
            )
    return hunt_lines, crew_lines
