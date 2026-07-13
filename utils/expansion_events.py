from __future__ import annotations

import time
from typing import TYPE_CHECKING

from utils.blueprints import EVENT_BLUEPRINT_UNLOCKS
from utils.contracts import CONTRACT_MAP
from utils.museum import CATEGORY_TOTALS

if TYPE_CHECKING:
    from database import Database


async def record_expansion_event(
    db: Database,
    guild_id: int,
    user_id: int,
    event: str,
    *,
    amount: int = 1,
) -> None:
    """Central hook for contracts, blueprints, and museum progress."""
    await db.increment_contract_progress(guild_id, user_id, event, amount)
    blueprint_id = EVENT_BLUEPRINT_UNLOCKS.get(event)
    if blueprint_id:
        await db.unlock_blueprint(user_id, guild_id, blueprint_id)
    await _update_museum_for_event(db, guild_id, user_id, event, amount)


async def _update_museum_for_event(
    db: Database,
    guild_id: int,
    user_id: int,
    event: str,
    amount: int,
) -> None:
    mapping = {
        "boss_kill": ("bosses", amount),
        "duel_win": ("duels", amount),
        "drug_harvest": ("strains", amount),
        "relic_obtained": ("relics", amount),
        "companion_obtained": ("companions", amount),
        "phenotype_discovered": ("phenotypes", amount),
        "blueprint_unlocked": ("blueprints", amount),
        "gear_obtained": ("gear", amount),
    }
    if event in mapping:
        cat, amt = mapping[event]
        await db.increment_museum_category(guild_id, user_id, cat, amt)


async def ensure_guild_contracts(db: Database, guild_id: int) -> None:
    from utils.contracts import contract_refresh_deadline, pick_active_contracts

    now = time.time()
    deadline = await db.get_contract_refresh_at(guild_id)
    if deadline > now:
        return
    contracts = pick_active_contracts(3)
    await db.set_guild_contracts(
        guild_id,
        [c.contract_id for c in contracts],
        contract_refresh_deadline(now),
    )
