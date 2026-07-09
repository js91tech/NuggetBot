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
    "apex_nuggetblade": "apex",
    "apex_aegis": "apex",
    "apex_annihilator": "apex",
    "sovereign_cleaver": "sovereign",
    "sovereign_bastion": "sovereign",
    "sovereign_railcannon": "sovereign",
    "transcendent_worldsplitter": "transcendent",
    "transcendent_carapace": "transcendent",
    "transcendent_voidlance": "transcendent",
    "dominion_worldbreaker": "dominion",
    "dominion_devastator": "dominion",
    "apotheosis_carapace": "dominion",
    "reaper_fang": "reaper",
    "reaper_crossbow": "reaper",
    "paragon_edge": "paragon",
    "paragon_repeater": "paragon",
    "paragon_aegis": "paragon",
    "eternal_worldcleaver": "eternal",
    "eternal_obliteratrix": "eternal",
    "eternal_bastion": "eternal",
    "mythic_raid_blade": "mythic",
    "mythic_raid_mail": "mythic",
    "boss_slayer_blade": "slayer",
    "boss_slayer_mail": "slayer",
    "flare_pistol": "ember",
    "storm_rifle": "storm",
    "void_carbine": "void",
    "mythic_annihilator": "void",
    "dragon_shotgun": "dragon",
    "cosmic_railgun": "cosmic",
    "nugget_minigun": "nugget",
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
    "apex": "Apex",
    "sovereign": "Sovereign",
    "transcendent": "Transcendent",
    "dominion": "Dominion",
    "reaper": "Reaper",
    "paragon": "Paragon",
    "eternal": "Eternal",
}


def _base_shop_item(item: ShopItem | object | None) -> ShopItem | None:
    if item is None:
        return None
    base = getattr(item, "base", None)
    return base if base is not None else item  # type: ignore[return-value]


def detect_set_bonus(weapon: ShopItem | object | None, armor: ShopItem | object | None) -> SetBonus | None:
    weapon = _base_shop_item(weapon)
    armor = _base_shop_item(armor)
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


def heist_intimidation_bonus(
    weapon: ShopItem | None,
    *,
    off_hand: ShopItem | None = None,
) -> float:
    if weapon is None and off_hand is None:
        return 0.0
    from utils.loadout import off_hand_power_bonus

    power = weapon.power if weapon is not None else 0
    power += off_hand_power_bonus(off_hand)
    return min(config.HEIST_INTIMIDATION_CAP, power * config.HEIST_INTIMIDATION_PER_POWER)


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
