from __future__ import annotations

from dataclasses import dataclass

import config
from items import ShopItem, armor_mitigation_percent, is_damage_dealer
from utils.character_attributes import AttributeCombatBonuses
from utils.enhancement import AccessoryBonuses, EffectiveGear
from utils.gear_sets import SetBonus
from utils.loadout import off_hand_crit_bonus, off_hand_power_bonus


@dataclass(frozen=True)
class CombatStats:
    max_hp: int
    current_hp: int | None
    damage_min: int
    damage_max: int
    crit_chance_pct: int
    crit_damage_max: int
    mitigation_pct: int
    armor_hp_bonus: int


def hp_bar(current: float, maximum: float, *, length: int = 12) -> str:
    if maximum <= 0:
        return "░" * length
    ratio = max(0.0, min(1.0, current / maximum))
    filled = int(round(ratio * length))
    return "█" * filled + "░" * (length - filled)


def format_item_stats(item: ShopItem) -> str:
    if item.category == "consumable":
        return item.description
    if item.category == "accessory":
        parts: list[str] = []
        if item.flat_damage:
            parts.append(f"+{item.flat_damage} dmg")
        if item.flat_hp:
            parts.append(f"+{item.flat_hp} HP")
        if item.flat_crit:
            parts.append(f"+{int(item.flat_crit * 100)}% crit")
        if item.flat_mitigation:
            parts.append(f"+{int(item.flat_mitigation * 100)}% mit")
        return " · ".join(parts) if parts else "flat bonuses"
    if is_damage_dealer(item):
        crit = f", {int(item.crit_chance * 100)}% crit" if item.crit_chance > 0 else ""
        lo = item.power + config.BOSS_ATTACK_BONUS_MIN
        hi = item.power + config.BOSS_ATTACK_BONUS_MAX
        return f"{lo}–{hi} dmg (+3% base crit{crit})"
    mit = armor_mitigation_percent(item.power)
    return f"{mit}% mit, +{item.hp_bonus} HP"


def compute_combat_stats(
    weapon: EffectiveGear | ShopItem | None,
    armor: EffectiveGear | ShopItem | None,
    *,
    off_hand: EffectiveGear | ShopItem | None = None,
    current_hp: float | None = None,
    prestige_level: int = 0,
    set_bonus: SetBonus | None = None,
    attr_bonuses: AttributeCombatBonuses | None = None,
    accessory_bonuses: AccessoryBonuses | None = None,
) -> CombatStats:
    attr = attr_bonuses or AttributeCombatBonuses()
    acc = accessory_bonuses or AccessoryBonuses()
    armor_bonus = armor.hp_bonus if armor is not None else 0
    armor_bonus += acc.flat_hp
    max_hp = config.PLAYER_BASE_HP + armor_bonus + attr.hp_bonus
    damage_mult = set_bonus.damage_mult if set_bonus is not None else 1.0
    damage_mult *= attr.damage_mult
    bonus_power = off_hand_power_bonus(off_hand)
    if weapon is None:
        damage_min = int(config.BOSS_UNARMED_MIN * damage_mult)
        damage_max = int(config.BOSS_UNARMED_MAX * damage_mult)
        crit_pct = int(round(config.PLAYER_BASE_CRIT_CHANCE * 100))
    else:
        attack_power = weapon.power + bonus_power
        damage_min = int((attack_power + config.BOSS_ATTACK_BONUS_MIN) * damage_mult) + acc.flat_damage
        damage_max = int((attack_power + config.BOSS_ATTACK_BONUS_MAX) * damage_mult) + acc.flat_damage
        crit_rate = (
            config.PLAYER_BASE_CRIT_CHANCE
            + weapon.crit_chance
            + off_hand_crit_bonus(off_hand)
            + acc.flat_crit
        )
        crit_rate += prestige_level * config.PRESTIGE_CRIT_BONUS_PER_LEVEL
        crit_rate += attr.extra_crit
        crit_pct = int(round(crit_rate * 100))
    crit_damage_max = int(damage_max * config.PLAYER_ATTACK_CRIT_MULTIPLIER)
    mitigation = armor_mitigation_percent(armor.power) if armor is not None else 0
    if set_bonus is not None and armor is not None:
        mitigation = min(90, mitigation + int(round(set_bonus.mitigation_bonus * 100)))
    if attr.mitigation_bonus > 0:
        mitigation = min(90, mitigation + int(round(attr.mitigation_bonus * 100)))
    if acc.flat_mitigation > 0:
        mitigation = min(90, mitigation + int(round(acc.flat_mitigation * 100)))
    hp_display = int(current_hp) if current_hp is not None else None
    return CombatStats(
        max_hp=max_hp,
        current_hp=hp_display,
        damage_min=damage_min,
        damage_max=damage_max,
        crit_chance_pct=crit_pct,
        crit_damage_max=crit_damage_max,
        mitigation_pct=mitigation,
        armor_hp_bonus=armor_bonus,
    )


def format_combat_stats_block(
    stats: CombatStats,
    *,
    set_bonus: SetBonus | None = None,
    prestige_level: int = 0,
    off_hand: ShopItem | None = None,
    attributes_block: str | None = None,
) -> str:
    hp_line = f"**{stats.max_hp}** max HP"
    if stats.current_hp is not None:
        bar = hp_bar(float(stats.current_hp), float(stats.max_hp))
        hp_line = f"`{bar}` **{stats.current_hp}/{stats.max_hp}** HP"
    lines = [
        hp_line,
        f"**{stats.damage_min}–{stats.damage_max}** damage per hit ({stats.crit_chance_pct}% crit, up to **{stats.crit_damage_max}** on crit)",
    ]
    if stats.mitigation_pct > 0:
        lines.append(f"**{stats.mitigation_pct}%** damage blocked ({stats.armor_hp_bonus} bonus HP from armor)")
    else:
        lines.append("No armor equipped")
    if set_bonus is not None:
        lines.append(f"**{set_bonus.name} set** active (+{int(config.SET_DAMAGE_BONUS * 100)}% dmg)")
    if prestige_level > 0:
        lines.append(
            f"Prestige **{prestige_level}** (+{int(prestige_level * config.PRESTIGE_CRIT_BONUS_PER_LEVEL * 100)}% crit, "
            f"+{int(prestige_level * config.PRESTIGE_INCOME_BONUS_PER_LEVEL * 100)}% income)"
        )
    if off_hand is not None:
        dmg_pct = int(config.OFF_HAND_DAMAGE_FACTOR * 100)
        crit_pct = int(config.OFF_HAND_CRIT_FACTOR * 100)
        lines.append(
            f"Off-hand **{off_hand.name}** (+{dmg_pct}% of its power to damage, "
            f"+{crit_pct}% of its crit)"
        )
    if attributes_block:
        lines.append(attributes_block)
    return "\n".join(lines)