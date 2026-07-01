"""Loadout preset helpers."""
from __future__ import annotations

from items import ShopItem, accessory_equip_slot, get_item, is_accessory, is_damage_dealer


def accessory_score(item: ShopItem) -> float:
    return (
        item.flat_damage * 2
        + item.flat_hp
        + item.flat_crit * 100
        + item.flat_mitigation * 50
    )


def best_accessory(rows: list, slot: str) -> ShopItem | None:
    best: ShopItem | None = None
    best_score = -1.0
    for row in rows:
        item = get_item(str(row["item_id"]))
        if item is None or not is_accessory(item):
            continue
        if accessory_equip_slot(item) != slot:
            continue
        score = accessory_score(item)
        if score > best_score:
            best_score = score
            best = item
    return best


def best_weapon_and_gun(rows: list) -> tuple[ShopItem | None, ShopItem | None]:
    best_weapon: ShopItem | None = None
    best_gun: ShopItem | None = None
    for row in rows:
        item = get_item(str(row["item_id"]))
        if item is None:
            continue
        if item.category == "weapon":
            if best_weapon is None or item.power > best_weapon.power:
                best_weapon = item
        elif item.category == "gun":
            if best_gun is None or item.power > best_gun.power:
                best_gun = item
    return best_weapon, best_gun


def format_preset_slot(item_id: str | None) -> str:
    if not item_id:
        return "—"
    item = get_item(str(item_id))
    return item.name if item is not None else str(item_id)
