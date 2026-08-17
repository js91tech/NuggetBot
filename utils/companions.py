from __future__ import annotations

from dataclasses import dataclass

import config


@dataclass(frozen=True)
class CompanionDefinition:
    companion_id: str
    name: str
    description: str
    effect: str
    emoji: str = "🐾"
    source: str = ""


@dataclass
class CompanionBonuses:
    damage_mult: float = 1.0
    crit_bonus: float = 0.0
    boss_damage_mult: float = 1.0
    alchemy_scrap_mult: float = 1.0
    dungeon_reward_mult: float = 1.0
    work_income_mult: float = 1.0
    duel_steal_mult: float = 1.0


COMPANION_DEFINITIONS: dict[str, CompanionDefinition] = {
    "hench_scrap_gnome": CompanionDefinition(
        "hench_scrap_gnome",
        "Scrap Gnome",
        "Velvet Vixen's henchmen drop extra scrap in your pockets.",
        "scrap",
        "🧌",
        "Velvet's Henchmen",
    ),
    "hench_jester_imp": CompanionDefinition(
        "hench_jester_imp",
        "Jester Imp",
        "Court jesters taught it dirty duel tricks.",
        "crit",
        "😈",
        "Court of Kitty's Jesters",
    ),
    "hench_vault_rat": CompanionDefinition(
        "hench_vault_rat",
        "Vault Rat",
        "Sniffs out bonus dungeon loot.",
        "dungeon",
        "🐀",
        "Gilded Vault",
    ),
    "hench_medic_slime": CompanionDefinition(
        "hench_medic_slime",
        "Medic Slime",
        "Squeezes a little more raid damage out of you.",
        "boss",
        "🟢",
        "Boss raid",
    ),
    "hench_courier_bird": CompanionDefinition(
        "hench_courier_bird",
        "Courier Bird",
        "Delivers bigger job paychecks.",
        "work",
        "🐦",
        "Jobs",
    ),
    "hench_plunder_pup": CompanionDefinition(
        "hench_plunder_pup",
        "Plunder Pup",
        "Barks when duel loot is nearby.",
        "plunder",
        "🐕",
        "Duels",
    ),
    "hench_lab_moss": CompanionDefinition(
        "hench_lab_moss",
        "Lab Moss",
        "Grows on your drug profits.",
        "income",
        "🌿",
        "Drug lab",
    ),
    "hench_corp_drone": CompanionDefinition(
        "hench_corp_drone",
        "Corp Drone",
        "Files paperwork for your business empire.",
        "business",
        "🤖",
        "Business empire",
    ),
}

ADD_COMPANION_DROPS: dict[str, str] = {
    "henchman": "hench_scrap_gnome",
    "court_jester": "hench_jester_imp",
}

VAULT_COMPANION_DROP = "hench_vault_rat"


def companion_by_id(companion_id: str) -> CompanionDefinition | None:
    return COMPANION_DEFINITIONS.get(companion_id)


def bonuses_from_companion(companion_id: str) -> CompanionBonuses:
    effect = COMPANION_DEFINITIONS[companion_id].effect
    if effect == "scrap":
        return CompanionBonuses(alchemy_scrap_mult=1.05)
    if effect == "crit":
        return CompanionBonuses(crit_bonus=0.03)
    if effect == "dungeon":
        return CompanionBonuses(dungeon_reward_mult=1.05)
    if effect == "boss":
        return CompanionBonuses(boss_damage_mult=1.04)
    if effect == "work":
        return CompanionBonuses(work_income_mult=1.05)
    if effect == "plunder":
        return CompanionBonuses(duel_steal_mult=1.04)
    if effect == "income":
        return CompanionBonuses(work_income_mult=1.03, dungeon_reward_mult=1.02)
    if effect == "business":
        return CompanionBonuses(work_income_mult=1.04)
    return CompanionBonuses()


def merge_companion_bonuses(bonuses: list[CompanionBonuses]) -> CompanionBonuses:
    if not bonuses:
        return CompanionBonuses()
    merged = CompanionBonuses()
    for b in bonuses:
        merged.damage_mult *= b.damage_mult
        merged.crit_bonus += b.crit_bonus
        merged.boss_damage_mult *= b.boss_damage_mult
        merged.alchemy_scrap_mult *= b.alchemy_scrap_mult
        merged.dungeon_reward_mult *= b.dungeon_reward_mult
        merged.work_income_mult *= b.work_income_mult
        merged.duel_steal_mult *= b.duel_steal_mult
    return cap_companion_bonuses(merged)


def cap_companion_bonuses(bonuses: CompanionBonuses) -> CompanionBonuses:
    cap = config.PASSIVE_BONUS_CAP
    bonuses.damage_mult = min(bonuses.damage_mult, 1.0 + cap)
    bonuses.boss_damage_mult = min(bonuses.boss_damage_mult, 1.0 + cap)
    bonuses.crit_bonus = min(bonuses.crit_bonus, cap)
    bonuses.work_income_mult = min(bonuses.work_income_mult, 1.0 + cap)
    bonuses.duel_steal_mult = min(bonuses.duel_steal_mult, 1.0 + cap)
    bonuses.alchemy_scrap_mult = min(bonuses.alchemy_scrap_mult, 1.0 + cap)
    bonuses.dungeon_reward_mult = min(bonuses.dungeon_reward_mult, 1.0 + cap)
    return bonuses
