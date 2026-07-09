from __future__ import annotations

import random
from dataclasses import dataclass, field

import config
from items import ShopItem
from utils.aspects import AspectBonuses, AspectInstance, bonuses_from_instance
from utils.character_attributes import AttributeCombatBonuses
from utils.combat_engine import (
    AttackContext,
    apply_armor_mitigation,
    max_hp_from_armor,
    roll_jester_reflect,
    roll_player_damage,
)
from utils.gear_sets import SetBonus, detect_set_bonus
from utils.enhancement import AccessoryBonuses, EffectiveGear
from utils.loadout import PlayerLoadout, parse_loadout
from utils.spell_effects import CombatSpellState
from utils.trap_bombs import TrapBombProc


@dataclass
class DuelFighter:
    user_id: int
    display_name: str
    weapon: EffectiveGear | ShopItem | None
    off_hand: EffectiveGear | ShopItem | None
    armor: EffectiveGear | ShopItem | None
    set_bonus: SetBonus | None
    prestige_level: int
    max_hp: int
    hp: int
    class_id: str | None = None
    spell_state: CombatSpellState | None = None
    spell_offense_used: bool = False
    spell_defense_used: bool = False
    aspect_bonuses: AspectBonuses | None = None
    attr_bonuses: AttributeCombatBonuses | None = None
    trap_bomb_count: int = 0
    consumable_boost: float = 1.0
    consumable_boost_used: bool = False
    sakuna_deflect_active: bool = False
    accessory_bonuses: AccessoryBonuses | None = None


@dataclass(frozen=True)
class DuelStrike:
    attacker_id: int
    defender_id: int
    damage: int
    mitigated: int
    critical: bool
    verb: str
    defender_hp_after: int
    jester_reflect: bool = False
    sakuna_deflect: bool = False
    trap_proc: TrapBombProc | None = None
    trap_attacker_hp_after: int | None = None
    second_wind: bool = False


@dataclass(frozen=True)
class DuelResult:
    winner_id: int
    loser_id: int
    strikes: list[DuelStrike] = field(default_factory=list)
    jester_steals: list[tuple[int, int, float]] = field(default_factory=list)


def fighter_from_loadout(
    user_id: int,
    display_name: str,
    loadout: PlayerLoadout,
    *,
    prestige_level: int,
    class_id: str | None = None,
    class_modifiers=None,
    aspect_instance: AspectInstance | None = None,
    aspect_bonuses: AspectBonuses | None = None,
    attr_bonuses: AttributeCombatBonuses | None = None,
    trap_bomb_count: int = 0,
) -> DuelFighter:
    from utils.classes import get_modifiers

    set_bonus = detect_set_bonus(loadout.primary, loadout.armor)
    mods = class_modifiers if class_modifiers is not None else get_modifiers(class_id)
    ab = aspect_bonuses
    if ab is None and aspect_instance is not None:
        ab = bonuses_from_instance(aspect_instance)
    if ab is None:
        ab = AspectBonuses()
    attr = attr_bonuses or AttributeCombatBonuses()
    acc: AccessoryBonuses = loadout.accessory_bonuses
    max_hp = (
        max_hp_from_armor(
            loadout.armor,
            class_modifiers=mods,
            attr_hp_bonus=attr.hp_bonus,
            accessory_bonuses=acc,
        )
        + ab.hp_bonus
    )
    has_aspect = ab != AspectBonuses()
    return DuelFighter(
        user_id=user_id,
        display_name=display_name,
        weapon=loadout.primary,
        off_hand=loadout.off_hand,
        armor=loadout.armor,
        set_bonus=set_bonus,
        prestige_level=prestige_level,
        class_id=class_id,
        max_hp=max_hp,
        hp=max_hp,
        aspect_bonuses=ab if has_aspect else None,
        attr_bonuses=attr if attr != AttributeCombatBonuses() else None,
        trap_bomb_count=trap_bomb_count,
        accessory_bonuses=acc,
    )


def fighter_from_equipment(
    user_id: int,
    display_name: str,
    equipment: dict[str, str],
    *,
    prestige_level: int,
    class_id: str | None = None,
    class_modifiers=None,
    aspect_instance: AspectInstance | None = None,
    aspect_bonuses: AspectBonuses | None = None,
    attr_bonuses: AttributeCombatBonuses | None = None,
    trap_bomb_count: int = 0,
    unstable_slots: set[str] | None = None,
) -> DuelFighter:
    from utils.classes import get_modifiers

    loadout = parse_loadout(equipment, unstable_slots=unstable_slots)
    set_bonus = detect_set_bonus(loadout.primary, loadout.armor)
    mods = class_modifiers if class_modifiers is not None else get_modifiers(class_id)
    ab = aspect_bonuses
    if ab is None and aspect_instance is not None:
        ab = bonuses_from_instance(aspect_instance)
    if ab is None:
        ab = AspectBonuses()
    attr = attr_bonuses or AttributeCombatBonuses()
    acc = loadout.accessory_bonuses
    max_hp = (
        max_hp_from_armor(
            loadout.armor,
            class_modifiers=mods,
            attr_hp_bonus=attr.hp_bonus,
            accessory_bonuses=acc,
        )
        + ab.hp_bonus
    )
    has_aspect = ab != AspectBonuses()
    return DuelFighter(
        user_id=user_id,
        display_name=display_name,
        weapon=loadout.primary,
        off_hand=loadout.off_hand,
        armor=loadout.armor,
        set_bonus=set_bonus,
        prestige_level=prestige_level,
        class_id=class_id,
        max_hp=max_hp,
        hp=max_hp,
        aspect_bonuses=ab if has_aspect else None,
        attr_bonuses=attr if attr != AttributeCombatBonuses() else None,
        trap_bomb_count=trap_bomb_count,
        accessory_bonuses=acc,
    )


def _attack_context(
    attacker: DuelFighter,
    defender: DuelFighter,
) -> AttackContext:
    from utils.combat_engine import attack_context_for_class

    return attack_context_for_class(
        attacker.class_id,
        prestige_level=attacker.prestige_level,
        defender_class_id=defender.class_id,
    )


def _one_strike(attacker: DuelFighter, defender: DuelFighter) -> DuelStrike:
    reflect = roll_jester_reflect(defender.class_id)
    if reflect.proc:
        attacker.hp = 0
        return DuelStrike(
            attacker_id=attacker.user_id,
            defender_id=defender.user_id,
            damage=0,
            mitigated=0,
            critical=False,
            verb="fumbles",
            defender_hp_after=defender.hp,
            jester_reflect=True,
        )

    if defender.sakuna_deflect_active:
        from utils.sakunas_finger import roll_sakuna_deflect

        if roll_sakuna_deflect():
            attacker.hp = 0
            return DuelStrike(
                attacker_id=attacker.user_id,
                defender_id=defender.user_id,
                damage=0,
                mitigated=0,
                critical=False,
                verb="is erased by Malevolent Shrine",
                defender_hp_after=defender.hp,
                sakuna_deflect=True,
            )

    ctx = _attack_context(attacker, defender)
    damage_mult = ctx.damage_mult
    extra_crit = ctx.extra_crit
    if attacker.attr_bonuses is not None:
        damage_mult *= attacker.attr_bonuses.damage_mult
        extra_crit += attacker.attr_bonuses.extra_crit
    if attacker.aspect_bonuses is not None:
        ab = attacker.aspect_bonuses
        damage_mult *= ab.damage_mult * ab.boss_damage_mult
        extra_crit += ab.extra_crit
    if attacker.spell_state is not None and not attacker.spell_offense_used:
        st = attacker.spell_state
        if st.damage_mult > 1.0:
            damage_mult *= st.damage_mult
            attacker.spell_offense_used = True
        if st.extra_crit > 0:
            extra_crit += st.extra_crit
            attacker.spell_offense_used = True
    ctx = AttackContext(
        prestige_level=ctx.prestige_level,
        class_modifiers=ctx.class_modifiers,
        damage_mult=damage_mult,
        extra_crit=extra_crit,
        pvp_matchup_mult=ctx.pvp_matchup_mult,
        boss_element_mult=ctx.boss_element_mult,
    )
    raw, critical, verb = roll_player_damage(
        attacker.weapon,
        off_hand=attacker.off_hand,
        ctx=ctx,
        set_bonus=attacker.set_bonus,
        accessory_bonuses=attacker.accessory_bonuses,
    )
    if not attacker.consumable_boost_used and attacker.consumable_boost > 1.0:
        raw = int(raw * attacker.consumable_boost)
        attacker.consumable_boost_used = True
    from utils.classes import get_modifiers

    fortify_mult = 1.0
    if (
        defender.spell_state is not None
        and not defender.spell_defense_used
        and defender.spell_state.fortify_mult < 1.0
    ):
        fortify_mult = defender.spell_state.fortify_mult
        defender.spell_defense_used = True
    mitigated_raw = max(1, int(raw * fortify_mult)) if fortify_mult < 1.0 else raw
    attr_mit = defender.attr_bonuses.mitigation_bonus if defender.attr_bonuses else 0.0
    extra_mit = defender.aspect_bonuses.mitigation_bonus if defender.aspect_bonuses else 0.0
    damage, mitigated = apply_armor_mitigation(
        mitigated_raw,
        defender.armor,
        set_bonus=defender.set_bonus,
        class_modifiers=get_modifiers(defender.class_id),
        attr_mitigation_bonus=attr_mit,
        accessory_bonuses=defender.accessory_bonuses,
    )
    if extra_mit > 0:
        reduced = max(1, int(damage * (1.0 - extra_mit)))
        mitigated += damage - reduced
        damage = reduced
    defender.hp = max(0, defender.hp - damage)
    second_wind = False
    if (
        defender.hp <= 0
        and defender.aspect_bonuses is not None
        and defender.aspect_bonuses.second_wind_chance > 0
        and random.random() < defender.aspect_bonuses.second_wind_chance
    ):
        defender.hp = 1
        second_wind = True

    trap_proc = None
    trap_attacker_hp: int | None = None
    if defender.trap_bomb_count > 0:
        from utils.trap_bombs import try_trap_proc

        proc = try_trap_proc(defender.trap_bomb_count)
        if proc is not None:
            defender.trap_bomb_count = proc.bombs_remaining
            attacker.hp = max(0, attacker.hp - proc.damage)
            trap_proc = proc
            trap_attacker_hp = attacker.hp

    return DuelStrike(
        attacker_id=attacker.user_id,
        defender_id=defender.user_id,
        damage=damage,
        mitigated=mitigated,
        critical=critical,
        verb=verb,
        defender_hp_after=defender.hp,
        trap_proc=trap_proc,
        trap_attacker_hp_after=trap_attacker_hp,
        second_wind=second_wind,
    )


def simulate_duel(attacker: DuelFighter, defender: DuelFighter) -> DuelResult:
    """Turn-based fight; challenger (attacker) strikes first."""
    strikes: list[DuelStrike] = []
    jester_steals: list[tuple[int, int, float]] = []
    max_turns = config.DUEL_MAX_COMBAT_ROUNDS * 2
    for turn in range(max_turns):
        if attacker.hp <= 0 or defender.hp <= 0:
            break
        if turn % 2 == 0:
            strike = _one_strike(attacker, defender)
        else:
            strike = _one_strike(defender, attacker)
        strikes.append(strike)
        if strike.jester_reflect:
            jester_id = strike.defender_id
            victim_id = strike.attacker_id
            jester_steals.append((jester_id, victim_id, 0.0))

    if attacker.hp > defender.hp:
        winner_id = attacker.user_id
    elif defender.hp > attacker.hp:
        winner_id = defender.user_id
    elif attacker.max_hp >= defender.max_hp:
        winner_id = attacker.user_id
    else:
        winner_id = defender.user_id

    loser_id = defender.user_id if winner_id == attacker.user_id else attacker.user_id
    return DuelResult(winner_id=winner_id, loser_id=loser_id, strikes=strikes, jester_steals=jester_steals)


def format_strike_line(strike: DuelStrike, fighters: dict[int, DuelFighter]) -> str:
    attacker = fighters[strike.attacker_id]
    defender = fighters[strike.defender_id]
    if strike.jester_reflect:
        return (
            f"**{attacker.display_name}** attacks **{defender.display_name}** — "
            f"**who me?** The strike fails and **{attacker.display_name}** is instantly downed!"
        )
    if strike.sakuna_deflect:
        return (
            f"**{attacker.display_name}** attacks **{defender.display_name}** — "
            f"**Domain Expansion: Malevolent Shrine!** **{attacker.display_name}** "
            f"{strike.verb} and is instantly downed!"
        )
    crit = " **CRIT**" if strike.critical else ""
    mit = f" ({strike.mitigated} blocked)" if strike.mitigated else ""
    line = (
        f"**{attacker.display_name}** {strike.verb} **{defender.display_name}** "
        f"for **{strike.damage}**{mit}{crit} → {strike.defender_hp_after}/{defender.max_hp} HP"
    )
    if strike.second_wind:
        line += f"\n🌬️ **Second Wind!** **{defender.display_name}** clings to **1 HP**!"
    if strike.trap_proc is not None:
        hp_note = ""
        if strike.trap_attacker_hp_after is not None:
            atk = fighters[strike.attacker_id]
            hp_note = f" → {strike.trap_attacker_hp_after}/{atk.max_hp} HP"
        td = " **true damage**" if strike.trap_proc.true_damage else ""
        line += (
            f"\n💣 **Trap Bomb!** **{defender.display_name}** detonates a bomb on "
            f"**{attacker.display_name}** for **{strike.trap_proc.damage}**{td}{hp_note}"
        )
    return line
