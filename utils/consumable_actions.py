from __future__ import annotations

from dataclasses import dataclass

from items import CONSUMABLE_USE_IDS, get_item

BOSS_RAID_CONSUMABLE_IDS: frozenset[str] = frozenset({"raid_potion"})


@dataclass
class UseConsumableResult:
    ok: bool
    message: str = ""
    error: str | None = None
    immediate: bool = False


async def execute_use_consumable(
    db,
    user_id: int,
    guild_id: int,
    item_id: str,
    *,
    boss_context: bool = False,
) -> UseConsumableResult:
    item_id = item_id.strip()
    shop_item = get_item(item_id)
    if shop_item is None or item_id not in CONSUMABLE_USE_IDS:
        return UseConsumableResult(ok=False, error="That item cannot be used.")

    if boss_context and item_id not in BOSS_RAID_CONSUMABLE_IDS:
        return UseConsumableResult(
            ok=False,
            error="Only **Raid Potion** can be used during boss raids.",
        )

    qty = await db.get_inventory_quantity(user_id, guild_id, item_id)
    if qty <= 0:
        return UseConsumableResult(ok=False, error="You do not have that item.")

    if item_id == "energy_drink":
        if not await db.consume_inventory_item(user_id, guild_id, item_id):
            return UseConsumableResult(ok=False, error="Could not consume item.")
        new_energy = await db.add_energy(user_id, guild_id, 15)
        return UseConsumableResult(
            ok=True,
            immediate=True,
            message=f"**Energy Drink** — energy restored to **{new_energy}**.",
        )

    if not await db.consume_inventory_item(user_id, guild_id, item_id):
        return UseConsumableResult(ok=False, error="Could not consume item.")

    await db.set_pending_consumable(user_id, guild_id, item_id)
    hint = {
        "raid_potion": "Next **Attack** deals +20% boss damage.",
        "duel_scroll": "Your next **/duel** deals +15% strike damage.",
    }.get(item_id, "Buff active.")
    return UseConsumableResult(
        ok=True,
        message=f"Used **{shop_item.name}**. {hint} (5 min window)",
    )
