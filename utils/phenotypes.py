from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class PhenotypeDefinition:
    phenotype_id: str
    name: str
    description: str
    parent_a: str
    parent_b: str
    buff_effect: str
    emoji: str = "🧬"


PHENOTYPE_DEFINITIONS: dict[str, PhenotypeDefinition] = {
    "pheno_neon_haze": PhenotypeDefinition(
        "pheno_neon_haze", "Neon Haze", "Cannabis × stimulant glow.",
        "cannabis", "stimulant", "work_boost", "💡",
    ),
    "pheno_dream_leak": PhenotypeDefinition(
        "pheno_dream_leak", "Dream Leak", "Opioid × psychedelic haze.",
        "opioid", "psychedelic", "second_wind", "🌙",
    ),
    "pheno_lean_storm": PhenotypeDefinition(
        "pheno_lean_storm", "Lean Storm", "Codeine × lean swirl.",
        "codeine", "lean", "duel_boost", "🌀",
    ),
    "pheno_cartel_gold": PhenotypeDefinition(
        "pheno_cartel_gold", "Cartel Gold", "Cannabis × opioid prestige.",
        "cannabis", "opioid", "income_boost", "👑",
    ),
    "pheno_void_bloom": PhenotypeDefinition(
        "pheno_void_bloom", "Void Bloom", "Psychedelic × stimulant void.",
        "psychedelic", "stimulant", "boss_boost", "🌌",
    ),
    "pheno_street_rocket": PhenotypeDefinition(
        "pheno_street_rocket", "Street Rocket", "Stimulant × lean speed.",
        "stimulant", "lean", "energy_boost", "🚀",
    ),
    "pheno_purple_rain": PhenotypeDefinition(
        "pheno_purple_rain", "Purple Rain", "Codeine × cannabis chill.",
        "codeine", "cannabis", "mitigation_boost", "💜",
    ),
    "pheno_ghost_drip": PhenotypeDefinition(
        "pheno_ghost_drip", "Ghost Drip", "Lean × opioid phantom.",
        "lean", "opioid", "heist_boost", "👻",
    ),
    "pheno_solar_kush": PhenotypeDefinition(
        "pheno_solar_kush", "Solar Kush", "Cannabis × psychedelic sun.",
        "cannabis", "psychedelic", "scrap_boost", "☀️",
    ),
    "pheno_ice_shatter": PhenotypeDefinition(
        "pheno_ice_shatter", "Ice Shatter", "Stimulant × codeine frost.",
        "stimulant", "codeine", "crit_boost", "❄️",
    ),
    "pheno_midnight_syrup": PhenotypeDefinition(
        "pheno_midnight_syrup", "Midnight Syrup", "Lean × psychedelic night.",
        "lean", "psychedelic", "dungeon_boost", "🌃",
    ),
    "pheno_redline_oz": PhenotypeDefinition(
        "pheno_redline_oz", "Redline Oz", "Opioid × stimulant rush.",
        "opioid", "stimulant", "damage_boost", "🔴",
    ),
    "pheno_jungle_fog": PhenotypeDefinition(
        "pheno_jungle_fog", "Jungle Fog", "Cannabis × lean mist.",
        "cannabis", "lean", "regen_boost", "🌴",
    ),
    "pheno_crystal_lean": PhenotypeDefinition(
        "pheno_crystal_lean", "Crystal Lean", "Codeine × opioid crystal.",
        "codeine", "opioid", "bank_boost", "💎",
    ),
    "pheno_chaos_blend": PhenotypeDefinition(
        "pheno_chaos_blend", "Chaos Blend", "Any rare cross — wildcard.",
        "any", "any", "balanced_boost", "🎲",
    ),
}

def drug_family(drug_id: str) -> str:
    from utils.drugs import drug_by_id

    defn = drug_by_id(drug_id)
    return defn.category if defn is not None else "cannabis"


def roll_crossbreed(family_a: str, family_b: str) -> PhenotypeDefinition | None:
    if random.random() > 0.08:
        return None
    for pheno in PHENOTYPE_DEFINITIONS.values():
        if pheno.phenotype_id == "pheno_chaos_blend":
            continue
        pairs = {family_a, family_b}
        if {pheno.parent_a, pheno.parent_b} == pairs:
            return pheno
    if random.random() < 0.02:
        return PHENOTYPE_DEFINITIONS["pheno_chaos_blend"]
    return None


def phenotype_buff_description(effect: str) -> str:
    labels = {
        "work_boost": "+6% /work payouts",
        "second_wind": "+5% duel survival chance",
        "duel_boost": "+4% duel damage",
        "income_boost": "+5% passive income",
        "boss_boost": "+5% boss damage",
        "energy_boost": "+10 energy on harvest",
        "mitigation_boost": "+2% mitigation",
        "heist_boost": "+5% heist success",
        "scrap_boost": "+1 alchemy scrap on boss kill",
        "crit_boost": "+2% crit",
        "dungeon_boost": "+5% dungeon rewards",
        "damage_boost": "+4% combat damage",
        "regen_boost": "+10% energy regen",
        "bank_boost": "+3% bank interest",
        "balanced_boost": "+3% all income",
    }
    return labels.get(effect, "Mysterious buff")
