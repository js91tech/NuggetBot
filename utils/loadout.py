from __future__ import annotations

from dataclasses import dataclass

import config
from items import ShopItem, get_item


@dataclass(frozen=True)
class PlayerLoadout:
    """Resolved combat loadout: primary drives the attack roll; off-hand adds a bonus."""

    primary: ShopItem | None
    off_hand: ShopItem | None
    armor: ShopItem | None


def parse_loadout(
    equipment: dict[str, str],
    *,
    unstable_slots: set[str] | None = None,
) -> PlayerLoadout:
    unstable = unstable_slots or set()
    weapon_id = equipment.get("weapon") if "weapon" not in unstable else None
    off_id = equipment.get("off_hand") if "off_hand" not in unstable else None
    armor_id = equipment.get("armor") if "armor" not in unstable else None
    weapon_slot = get_item(weapon_id) if weapon_id else None
    off_slot = get_item(off_id) if off_id else None
    armor = get_item(armor_id) if armor_id else None
    primary, off_hand = resolve_primary_off_hand(weapon_slot, off_slot)
    return PlayerLoadout(primary=primary, off_hand=off_hand, armor=armor)


def resolve_primary_off_hand(
    weapon_slot: ShopItem | None,
    off_slot: ShopItem | None,
) -> tuple[ShopItem | None, ShopItem | None]:
    if weapon_slot is None and off_slot is None:
        return None, None
    if weapon_slot is not None and off_slot is not None:
        if weapon_slot.category == "weapon" and off_slot.category == "gun":
            return weapon_slot, off_slot
        if weapon_slot.category == "gun" and off_slot.category == "weapon":
            return off_slot, weapon_slot
        if off_slot.power > weapon_slot.power:
            return off_slot, weapon_slot
        return weapon_slot, off_slot
    return weapon_slot, off_slot


def off_hand_power_bonus(off_hand: ShopItem | None) -> int:
    if off_hand is None:
        return 0
    return int(round(off_hand.power * config.OFF_HAND_DAMAGE_FACTOR))


def off_hand_crit_bonus(off_hand: ShopItem | None) -> float:
    if off_hand is None:
        return 0.0
    return off_hand.crit_chance * config.OFF_HAND_CRIT_FACTOR


def effective_attack_power(primary: ShopItem | None, off_hand: ShopItem | None) -> int:
    if primary is None:
        return 0
    return primary.power + off_hand_power_bonus(off_hand)


def equip_target_slot(item: ShopItem, equipment: dict[str, str]) -> str:
    if item.category == "armor":
        return "armor"
    if item.category == "gun":
        weapon_id = equipment.get("weapon")
        weapon_item = get_item(weapon_id) if weapon_id else None
        if weapon_item is not None:
            if weapon_item.category == "weapon":
                return "off_hand"
            if weapon_item.category == "gun":
                return "off_hand"
        return "weapon"
    # melee weapon
    return "weapon"

