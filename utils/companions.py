from __future__ import annotations

import random
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
    rarity: str = "common"


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
        "Hannah's henchmen drop extra scrap in your pockets.",
        "scrap",
        "🧌",
        "Hannah's Henchmen",
        "common",
    ),
    "hench_jester_imp": CompanionDefinition(
        "hench_jester_imp",
        "Jester Imp",
        "Court jesters taught it dirty duel tricks.",
        "crit",
        "😈",
        "Court of Kitty's Jesters",
        "uncommon",
    ),
    "hench_vault_rat": CompanionDefinition(
        "hench_vault_rat",
        "Vault Rat",
        "Sniffs out bonus dungeon loot.",
        "dungeon",
        "🐀",
        "Gilded Vault",
        "rare",
    ),
    "hench_medic_slime": CompanionDefinition(
        "hench_medic_slime",
        "Medic Slime",
        "Squeezes a little more raid damage out of you.",
        "boss",
        "🟢",
        "Boss raid",
        "common",
    ),
    "hench_courier_bird": CompanionDefinition(
        "hench_courier_bird",
        "Courier Bird",
        "Delivers bigger job paychecks.",
        "work",
        "🐦",
        "Jobs",
        "common",
    ),
    "hench_plunder_pup": CompanionDefinition(
        "hench_plunder_pup",
        "Plunder Pup",
        "Barks when duel loot is nearby.",
        "plunder",
        "🐕",
        "Duels",
        "rare",
    ),
    "hench_lab_moss": CompanionDefinition(
        "hench_lab_moss",
        "Lab Moss",
        "Grows on your drug profits.",
        "income",
        "🌿",
        "Drug lab",
        "uncommon",
    ),
    "hench_corp_drone": CompanionDefinition(
        "hench_corp_drone",
        "Corp Drone",
        "Files paperwork for your business empire.",
        "business",
        "🤖",
        "Business empire",
        "epic",
    ),
}

ADD_COMPANION_DROPS: dict[str, str] = {
    "henchmen": "hench_scrap_gnome",
    "jesters": "hench_jester_imp",
}

VAULT_COMPANION_DROP = "hench_vault_rat"

COMPANION_ATTACK_VERBS: tuple[str, ...] = (
    "nips",
    "claws",
    "pounces on",
    "savages",
    "chomps",
    "lunges at",
    "pecks",
    "slimes",
)


def companion_by_id(companion_id: str) -> CompanionDefinition | None:
    return COMPANION_DEFINITIONS.get(companion_id)


def companion_display_name(companion_id: str, custom_name: str | None) -> str:
    if custom_name:
        return custom_name.strip()
    defn = companion_by_id(companion_id)
    return defn.name if defn is not None else companion_id


def companion_emoji(companion_id: str) -> str:
    defn = companion_by_id(companion_id)
    return defn.emoji if defn is not None else "🐾"


def evolution_damage_mult(tier: int) -> float:
    safe_tier = max(1, min(tier, config.COMPANION_MAX_EVOLUTION_TIER))
    return 1.0 + (safe_tier - 1) * config.COMPANION_EVOLUTION_DAMAGE_BONUS


def rarity_damage_mult(rarity: str) -> float:
    return config.COMPANION_RARITY_DAMAGE_MULT.get(rarity, 1.0)


def base_tier_damage(evolution_tier: int) -> int:
    tier = max(1, evolution_tier)
    return config.COMPANION_BASE_DAMAGE + (tier - 1) * config.COMPANION_TIER_DAMAGE_STEP


def roll_companion_damage(
    companion_id: str,
    *,
    evolution_tier: int,
    owner_attack_power: int,
) -> tuple[int, bool, str]:
    """Fixed tier base + rarity scaling + 25% inherited owner attack power."""
    defn = companion_by_id(companion_id)
    rarity = defn.rarity if defn is not None else "common"
    base = base_tier_damage(evolution_tier)
    inherit = int(owner_attack_power * config.COMPANION_OWNER_STAT_INHERIT)
    mult = rarity_damage_mult(rarity) * evolution_damage_mult(evolution_tier)
    low = int((base + inherit) * mult * 0.85)
    high = int((base + inherit) * mult * 1.15)
    damage = random.randint(max(1, low), max(1, high))
    crit_chance = 0.05
    if defn is not None and defn.effect == "crit":
        crit_chance += 0.04
    critical = random.random() < crit_chance
    if critical:
        damage = int(damage * 1.5)
    verb = random.choice(COMPANION_ATTACK_VERBS)
    return max(1, damage), critical, verb


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
