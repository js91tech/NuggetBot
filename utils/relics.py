from __future__ import annotations

from dataclasses import dataclass

import config


@dataclass(frozen=True)
class RelicDefinition:
    relic_id: str
    name: str
    description: str
    effect: str
    emoji: str = "🗿"


@dataclass
class RelicBonuses:
    damage_mult: float = 1.0
    boss_damage_mult: float = 1.0
    crit_bonus: float = 0.0
    mitigation_bonus: float = 0.0
    hp_bonus: int = 0
    energy_regen_mult: float = 1.0
    duel_steal_mult: float = 1.0
    work_income_mult: float = 1.0
    raid_heal_chance: float = 0.0
    enhance_safety_charges: int = 0
    alchemy_scrap_mult: float = 1.0
    dungeon_reward_mult: float = 1.0


RELIC_DEFINITIONS: dict[str, RelicDefinition] = {
    "relic_jester_bell": RelicDefinition(
        "relic_jester_bell",
        "Court Jester's Bell",
        "Raid attacks sometimes heal the weakest ally.",
        "raid_heal",
        "🔔",
    ),
    "relic_tomass_core": RelicDefinition(
        "relic_tomass_core",
        "TomAss Regen Core",
        "Energy returns faster between job shifts.",
        "energy_regen",
        "💚",
    ),
    "relic_void_heart": RelicDefinition(
        "relic_void_heart",
        "Void Hardener Heart",
        "One free enhance safety charge.",
        "enhance_safety",
        "💜",
    ),
    "relic_hannah_fang": RelicDefinition(
        "relic_hannah_fang",
        "Shattered Fang Charm",
        "Extra bite against raid bosses.",
        "boss_slayer",
        "🦷",
    ),
    "relic_plunder_seal": RelicDefinition(
        "relic_plunder_seal",
        "Plunderer's Seal",
        "Steal more nuggets from duel wins.",
        "plunder",
        "💰",
    ),
    "relic_scrap_gnome": RelicDefinition(
        "relic_scrap_gnome",
        "Scrap Gnome Idol",
        "Boss kills yield more alchemy scrap.",
        "scrap_boost",
        "⚙️",
    ),
    "relic_vault_key": RelicDefinition(
        "relic_vault_key",
        "Gilded Vault Key",
        "Dungeon payouts feel heavier.",
        "dungeon_boost",
        "🗝️",
    ),
    "relic_medic_patch": RelicDefinition(
        "relic_medic_patch",
        "Field Medic Patch",
        "Tougher in raids and duels.",
        "vitality",
        "🩹",
    ),
    "relic_duelists_coin": RelicDefinition(
        "relic_duelists_coin",
        "Duelist's Lucky Coin",
        "Sharper crits in PvP.",
        "crit",
        "🪙",
    ),
    "relic_corp_badge": RelicDefinition(
        "relic_corp_badge",
        "Corporate Sponsor Badge",
        "Job payouts get a corporate kickback.",
        "grafter",
        "🏢",
    ),
    "relic_henchman_totem": RelicDefinition(
        "relic_henchman_totem",
        "Henchman Totem",
        "General combat edge from the underworld.",
        "damage",
        "🗿",
    ),
    "relic_expedition_medal": RelicDefinition(
        "relic_expedition_medal",
        "Expedition Medal",
        "Community victory trophy with balanced bonuses.",
        "balanced",
        "🏅",
    ),
    "relic_wrath_sigil": RelicDefinition(
        "relic_wrath_sigil",
        "Sigil of ZZ's Wrath",
        "Ultra-raid pressure — sharper boss damage.",
        "boss_slayer",
        "☠️",
    ),
    "relic_leviathan_scale": RelicDefinition(
        "relic_leviathan_scale",
        "Leviathan Scale",
        "World-event scale — tougher raids and steadier income.",
        "balanced",
        "🐉",
    ),
    "relic_street_token": RelicDefinition(
        "relic_street_token",
        "Street Raid Token",
        "Starter raid charm — a touch more scrap from bosses.",
        "scrap_boost",
        "🎫",
    ),
}

BOSS_RELIC_DROPS: dict[str, tuple[str, ...]] = {
    "normal": ("relic_street_token",),
    "enraged": ("relic_street_token", "relic_scrap_gnome"),
    "mythic": ("relic_hannah_fang", "relic_henchman_totem"),
    "celestial": ("relic_medic_patch", "relic_duelists_coin"),
    "shadow": ("relic_scrap_gnome",),
    "tomass": ("relic_tomass_core",),
    "freaky_nikki": ("relic_jester_bell",),
    "zz_wrath": ("relic_wrath_sigil", "relic_hannah_fang"),
    "world_leviathan": ("relic_leviathan_scale", "relic_wrath_sigil", "relic_void_heart"),
}

VAULT_RELIC_DROPS: tuple[str, ...] = ("relic_void_heart", "relic_vault_key")
EXPEDITION_RELIC_DROP = "relic_expedition_medal"


def relic_by_id(relic_id: str) -> RelicDefinition | None:
    return RELIC_DEFINITIONS.get(relic_id)


def bonuses_from_relic(relic_id: str) -> RelicBonuses:
    effect = RELIC_DEFINITIONS[relic_id].effect
    if effect == "raid_heal":
        return RelicBonuses(raid_heal_chance=0.10)
    if effect == "energy_regen":
        return RelicBonuses(energy_regen_mult=1.20)
    if effect == "enhance_safety":
        return RelicBonuses(enhance_safety_charges=1)
    if effect == "boss_slayer":
        return RelicBonuses(boss_damage_mult=1.08)
    if effect == "plunder":
        return RelicBonuses(duel_steal_mult=1.05)
    if effect == "scrap_boost":
        return RelicBonuses(alchemy_scrap_mult=1.10)
    if effect == "dungeon_boost":
        return RelicBonuses(dungeon_reward_mult=1.08)
    if effect == "vitality":
        return RelicBonuses(hp_bonus=25, mitigation_bonus=0.02)
    if effect == "crit":
        return RelicBonuses(crit_bonus=0.03)
    if effect == "grafter":
        return RelicBonuses(work_income_mult=1.08)
    if effect == "damage":
        return RelicBonuses(damage_mult=1.05)
    if effect == "balanced":
        return RelicBonuses(damage_mult=1.03, boss_damage_mult=1.03, work_income_mult=1.03)
    return RelicBonuses()


def merge_relic_bonuses(bonuses: list[RelicBonuses]) -> RelicBonuses:
    if not bonuses:
        return RelicBonuses()
    merged = RelicBonuses()
    for b in bonuses:
        merged.damage_mult *= b.damage_mult
        merged.boss_damage_mult *= b.boss_damage_mult
        merged.crit_bonus += b.crit_bonus
        merged.mitigation_bonus += b.mitigation_bonus
        merged.hp_bonus += b.hp_bonus
        merged.energy_regen_mult *= b.energy_regen_mult
        merged.duel_steal_mult *= b.duel_steal_mult
        merged.work_income_mult *= b.work_income_mult
        merged.raid_heal_chance = max(merged.raid_heal_chance, b.raid_heal_chance)
        merged.enhance_safety_charges += b.enhance_safety_charges
        merged.alchemy_scrap_mult *= b.alchemy_scrap_mult
        merged.dungeon_reward_mult *= b.dungeon_reward_mult
    return cap_relic_bonuses(merged)


def cap_relic_bonuses(bonuses: RelicBonuses) -> RelicBonuses:
    cap = config.PASSIVE_BONUS_CAP
    bonuses.damage_mult = min(bonuses.damage_mult, 1.0 + cap)
    bonuses.boss_damage_mult = min(bonuses.boss_damage_mult, 1.0 + cap)
    bonuses.crit_bonus = min(bonuses.crit_bonus, cap)
    bonuses.mitigation_bonus = min(bonuses.mitigation_bonus, cap)
    bonuses.work_income_mult = min(bonuses.work_income_mult, 1.0 + cap)
    bonuses.duel_steal_mult = min(bonuses.duel_steal_mult, 1.0 + cap)
    bonuses.alchemy_scrap_mult = min(bonuses.alchemy_scrap_mult, 1.0 + cap)
    bonuses.dungeon_reward_mult = min(bonuses.dungeon_reward_mult, 1.0 + cap)
    bonuses.raid_heal_chance = min(bonuses.raid_heal_chance, cap)
    return bonuses
