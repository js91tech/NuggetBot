from __future__ import annotations

import random
from dataclasses import dataclass

import config
from items import ShopItem
from utils.classes import (
    ClassModifiers,
    element_multiplier,
    get_class,
    is_jester_class,
    pvp_matchup_multiplier,
)
from utils.gear_sets import SetBonus
from utils.loadout import off_hand_crit_bonus, off_hand_power_bonus


@dataclass(frozen=True)
class ReflectResult:
    proc: bool
    steal_amount: float = 0.0


@dataclass(frozen=True)
class AttackContext:
    prestige_level: int = 0
    class_modifiers: ClassModifiers | None = None
    damage_mult: float = 1.0
    extra_crit: float = 0.0
    pvp_matchup_mult: float = 1.0
    boss_element_mult: float = 1.0
    defense_retention: float = 1.0


def _combined_damage_mult(ctx: AttackContext, set_bonus: SetBonus | None) -> float:
    mult = ctx.damage_mult * ctx.pvp_matchup_mult * ctx.boss_element_mult
    if set_bonus is not None:
        mult *= set_bonus.damage_mult
    return mult


def roll_player_damage(
    weapon: ShopItem | None,
    *,
    off_hand: ShopItem | None = None,
    ctx: AttackContext | None = None,
    set_bonus: SetBonus | None = None,
    crit_chance_multiplier: float = 1.0,
    attacker_user_id: int | None = None,
) -> tuple[int, bool, str]:
    ctx = ctx or AttackContext()
    damage_mult = _combined_damage_mult(ctx, set_bonus)
    extra_crit = ctx.extra_crit + ctx.prestige_level * config.PRESTIGE_CRIT_BONUS_PER_LEVEL
    if ctx.class_modifiers is not None:
        extra_crit += ctx.class_modifiers.crit_bonus

    if weapon is None:
        low = int(config.BOSS_UNARMED_MIN * damage_mult)
        high = int(config.BOSS_UNARMED_MAX * damage_mult)
        damage = random.randint(low, max(low, high))
        crit_chance = config.PLAYER_BASE_CRIT_CHANCE + extra_crit
        verb = "hits"
    else:
        attack_power = weapon.power + off_hand_power_bonus(off_hand)
        low = int((attack_power + config.BOSS_ATTACK_BONUS_MIN) * damage_mult)
        high = int((attack_power + config.BOSS_ATTACK_BONUS_MAX) * damage_mult)
        damage = random.randint(low, max(low, high))
        crit_chance = (
            config.PLAYER_BASE_CRIT_CHANCE
            + weapon.crit_chance
            + off_hand_crit_bonus(off_hand)
            + extra_crit
        )
        verb = random.choice(weapon.verbs or ("strikes",))
    crit_chance = max(0.0, crit_chance * crit_chance_multiplier)
    critical = random.random() < crit_chance
    if critical:
        damage = int(damage * config.PLAYER_ATTACK_CRIT_MULTIPLIER)
    if attacker_user_id is not None:
        from utils.stealth_buff import scale_damage

        damage = scale_damage(damage, attacker_user_id)
    return damage, critical, verb


def apply_armor_mitigation(
    raw_damage: int,
    armor: ShopItem | None,
    *,
    set_bonus: SetBonus | None = None,
    class_modifiers: ClassModifiers | None = None,
    defense_retention: float = 1.0,
    defender_user_id: int | None = None,
) -> tuple[int, int]:
    if armor is None:
        damage = raw_damage
        mitigated = 0
    else:
        armor_power = armor.power * max(0.0, defense_retention)
        if class_modifiers is not None:
            armor_power *= class_modifiers.duel_mitigation_mult
        mitigated = int(raw_damage * armor_power / (armor_power + 100))
        if set_bonus is not None:
            mitigated += int(raw_damage * set_bonus.mitigation_bonus)
        mitigated = min(raw_damage - 1, mitigated)
        damage = max(1, raw_damage - mitigated)
    if defender_user_id is not None:
        from utils.stealth_buff import scale_incoming

        scaled = scale_incoming(damage, defender_user_id)
        mitigated += damage - scaled
        damage = scaled
    return damage, mitigated


def max_hp_from_armor(
    armor: ShopItem | None,
    *,
    class_modifiers: ClassModifiers | None = None,
    user_id: int | None = None,
) -> int:
    bonus = armor.hp_bonus if armor is not None else 0
    base = config.PLAYER_BASE_HP + bonus
    if class_modifiers is not None:
        base = int(base * class_modifiers.max_hp_mult)
    if user_id is not None:
        from utils.stealth_buff import scale_max_hp

        return scale_max_hp(base, user_id)
    return base


def roll_jester_reflect(defender_class_id: str | None) -> ReflectResult:
    if not is_jester_class(defender_class_id):
        return ReflectResult(proc=False)
    if random.random() < config.JESTER_REFLECT_CHANCE:
        return ReflectResult(proc=True)
    return ReflectResult(proc=False)


def attack_context_for_class(
    class_id: str | None,
    *,
    prestige_level: int = 0,
    boss_element: str | None = None,
    defender_class_id: str | None = None,
    for_boss: bool = False,
) -> AttackContext:
    cls = get_class(class_id)
    mod = cls.modifiers if cls else None
    elem_mult = 1.0
    pvp_mult = 1.0
    if cls and cls.element and boss_element:
        elem_mult = element_multiplier(cls.element, boss_element)
    if not for_boss and cls and defender_class_id:
        def_cls = get_class(defender_class_id)
        if def_cls:
            pvp_mult = pvp_matchup_multiplier(
                cls.combat_role,
                def_cls.combat_role,
                attacker_element=cls.element,
                defender_element=def_cls.element,
            )
    dmg_mult = 1.0
    if for_boss and mod is not None:
        dmg_mult = mod.boss_damage_mult
    elif mod is not None:
        dmg_mult = mod.duel_damage_mult
    return AttackContext(
        prestige_level=prestige_level,
        class_modifiers=mod,
        damage_mult=dmg_mult,
        pvp_matchup_mult=pvp_mult,
        boss_element_mult=elem_mult,
    )
