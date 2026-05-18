from __future__ import annotations

from dataclasses import dataclass

import config
from items import ShopItem, get_item


@dataclass(frozen=True)
class SetBonus:
    set_id: str
    name: str
    damage_mult: float
    mitigation_bonus: float


# weapon_id -> set_id (armor ids must share the same set_id for a bonus)
ITEM_SET_MAP: dict[str, str] = {
    "ember_axe": "ember",
    "ember_mail": "ember",
    "storm_spear": "storm",
    "stormguard": "storm",
    "void_blade": "void",
    "void_ward": "void",
    "mythic_voidreaver": "void",
    "dragon_lance": "dragon",
    "dragon_scale": "dragon",
    "cosmic_greatsword": "cosmic",
    "celestial_aegis": "cosmic",
    "nugget_excalibur": "nugget",
    "nugget_immortal_plate": "nugget",
    "mythic_aetherplate": "nugget",
    "mythic_raid_blade": "mythic",
    "mythic_raid_mail": "mythic",
    "boss_slayer_blade": "slayer",
    "boss_slayer_mail": "slayer",
}

SET_DISPLAY_NAMES: dict[str, str] = {
    "ember": "Ember",
    "storm": "Storm",
    "void": "Void",
    "dragon": "Dragon",
    "cosmic": "Cosmic",
    "nugget": "Nugget Royal",
    "mythic": "Mythic Raid",
    "slayer": "Boss Slayer",
}


def detect_set_bonus(weapon: ShopItem | None, armor: ShopItem | None) -> SetBonus | None:
    if weapon is None or armor is None:
        return None
    weapon_set = ITEM_SET_MAP.get(weapon.id)
    armor_set = ITEM_SET_MAP.get(armor.id)
    if weapon_set is None or weapon_set != armor_set:
        return None
    return SetBonus(
        set_id=weapon_set,
        name=SET_DISPLAY_NAMES.get(weapon_set, weapon_set.title()),
        damage_mult=1.0 + config.SET_DAMAGE_BONUS,
        mitigation_bonus=config.SET_MITIGATION_BONUS,
    )


def heist_intimidation_bonus(weapon: ShopItem | None) -> float:
    if weapon is None:
        return 0.0
    return min(config.HEIST_INTIMIDATION_CAP, weapon.power * config.HEIST_INTIMIDATION_PER_POWER)


def hack_penalty_multiplier(wallet: float) -> float:
    """Richer wallets absorb slightly more of the virus shock (lower penalty)."""
    if wallet <= 1000:
        return 1.0
    import math

    reduction = min(
        config.HACK_WALLET_SHIELD_MAX,
        math.log10(wallet / 1000.0) * config.HACK_WALLET_SHIELD_SCALE,
    )
    return 1.0 - reduction


def craft_base_id(weak_item_id: str) -> str | None:
    prefix = "boss_weak_"
    if not weak_item_id.startswith(prefix):
        return None
    return weak_item_id.removeprefix(prefix)


def craft_upgrade_cost(base_item_id: str, *, cost_factor: float | None = None) -> float | None:
    item = get_item(base_item_id)
    if item is None or item.price <= 0:
        return None
    factor = config.CRAFT_UPGRADE_COST_FACTOR if cost_factor is None else cost_factor
    return max(50.0, item.price * factor)
