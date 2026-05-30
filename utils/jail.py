from __future__ import annotations

from dataclasses import dataclass

import config
from utils.helpers import fmt_amount


def bail_cost_for_tier(arrest_tier: str | None) -> float:
    tier = (arrest_tier or "").strip().lower()
    if tier == "1":
        return config.BAIL_BANK_TIER_1
    if tier == "2":
        return config.BAIL_BANK_TIER_2
    if tier == "3":
        return config.BAIL_BANK_TIER_3
    return config.BAIL_WALLET_HEIST


def format_jail_time_remaining(seconds: float) -> str:
    remaining = max(0, int(seconds))
    hours = remaining // 3600
    minutes = (remaining % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m"
    return f"{remaining}s"


@dataclass
class BailResult:
    ok: bool
    message: str = ""
    error: str | None = None


@dataclass
class JailKeyResult:
    ok: bool
    message: str = ""
    error: str | None = None


async def execute_bail(
    db,
    payer_id: int,
    target_id: int,
    guild_id: int,
) -> BailResult:
    if not await db.is_arrested(target_id, guild_id):
        if payer_id == target_id:
            return BailResult(ok=False, error="You are not in jail.")
        return BailResult(ok=False, error="That player is not in jail.")

    tier = await db.get_arrest_tier(target_id, guild_id)
    cost = bail_cost_for_tier(tier)
    if not await db.debit_wallet(payer_id, guild_id, cost):
        return BailResult(
            ok=False,
            error=f"You need **{fmt_amount(cost)}** in your pocket for bail.",
        )

    await db.clear_arrest(target_id, guild_id)
    if payer_id == target_id:
        return BailResult(
            ok=True,
            message=f"Bail posted — **{fmt_amount(cost)}**. You are free.",
        )
    return BailResult(
        ok=True,
        message=f"Bail posted — **{fmt_amount(cost)}**. Target released from jail.",
    )


async def execute_jail_key(
    db,
    user_id: int,
    target_id: int,
    guild_id: int,
) -> JailKeyResult:
    if not await db.is_arrested(target_id, guild_id):
        if user_id == target_id:
            return JailKeyResult(ok=False, error="You are not in jail.")
        return JailKeyResult(ok=False, error="That player is not in jail.")

    if not await db.consume_inventory_item(user_id, guild_id, "jail_key"):
        return JailKeyResult(ok=False, error="You do not have a **Jail Key**.")

    await db.clear_arrest(target_id, guild_id)
    if user_id == target_id:
        return JailKeyResult(ok=True, message="**Jail Key** used — you are free.")
    return JailKeyResult(
        ok=True,
        message="**Jail Key** used — they are out of jail.",
    )
