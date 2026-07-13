"""Loot rolls and hooks for gameplay expansion."""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

import config
from utils.affixes import current_delve_week_id
from utils.companions import ADD_COMPANION_DROPS, VAULT_COMPANION_DROP
from utils.expansion_events import record_expansion_event
from utils.relics import BOSS_RELIC_DROPS, VAULT_RELIC_DROPS

if TYPE_CHECKING:
    from database import Database


async def roll_boss_expansion_loot(
    db: Database,
    guild_id: int,
    user_ids: list[int],
    *,
    variant: str,
    add_type: str | None = None,
) -> list[str]:
    lines: list[str] = []
    drops = BOSS_RELIC_DROPS.get(variant, ())
    for uid in user_ids:
        if drops and random.random() < config.RELIC_BOSS_DROP_CHANCE:
            relic_id = random.choice(drops)
            await db.create_relic_instance(uid, guild_id, relic_id)
            await record_expansion_event(db, guild_id, uid, "relic_obtained")
            await db.increment_museum_category(guild_id, uid, "relics", 1)
            lines.append(f"<@{uid}> · relic `{relic_id}`")
        if add_type and add_type in ADD_COMPANION_DROPS:
            if random.random() < config.COMPANION_DROP_CHANCE:
                cid = ADD_COMPANION_DROPS[add_type]
                if await db.grant_companion(uid, guild_id, cid):
                    await record_expansion_event(db, guild_id, uid, "companion_obtained")
                    await db.increment_museum_category(guild_id, uid, "companions", 1)
                    lines.append(f"<@{uid}> · companion `{cid}`")
        if variant == "mythic":
            await record_expansion_event(db, guild_id, uid, "boss_mythic_kill")
        await record_expansion_event(db, guild_id, uid, "boss_kill")
        await db.increment_museum_category(guild_id, uid, "bosses", 1)
    return lines


async def roll_vault_expansion_loot(db: Database, guild_id: int, user_id: int) -> list[str]:
    lines: list[str] = []
    if random.random() < config.RELIC_VAULT_DROP_CHANCE:
        relic_id = random.choice(VAULT_RELIC_DROPS)
        await db.create_relic_instance(user_id, guild_id, relic_id)
        await record_expansion_event(db, guild_id, user_id, "relic_obtained")
        lines.append(f"Relic: `{relic_id}`")
    if random.random() < config.COMPANION_DROP_CHANCE:
        if await db.grant_companion(user_id, guild_id, VAULT_COMPANION_DROP):
            lines.append(f"Companion: `{VAULT_COMPANION_DROP}`")
    await record_expansion_event(db, guild_id, user_id, "dungeon_vault_clear")
    week = current_delve_week_id()
    if week == "merchants_run" and random.random() < 0.25:
        await db.unlock_blueprint(user_id, guild_id, "bp_affix_reroll")
    return lines


async def on_dungeon_clear(
    db: Database,
    guild_id: int,
    user_id: int,
    *,
    tier_id: str,
) -> None:
    await record_expansion_event(db, guild_id, user_id, "dungeon_clear")
    await db.grant_item(user_id, guild_id, "dungeon_essence")
    if tier_id == "vault":
        await roll_vault_expansion_loot(db, guild_id, user_id)


async def award_duel_season_tokens(
    db: Database,
    guild_id: int,
    winner_id: int,
    loser_id: int,
) -> None:
    season, _ = await db.get_elo_season(guild_id)
    await db.add_season_tokens(winner_id, guild_id, config.SEASON_TOKEN_WIN, season)
    await db.add_season_tokens(loser_id, guild_id, config.SEASON_TOKEN_LOSS, season)
