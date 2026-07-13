from __future__ import annotations

import random
from dataclasses import dataclass

import config


@dataclass(frozen=True)
class AffixDefinition:
    affix_id: str
    name: str
    description: str
    effect: str


AFFIX_DEFINITIONS: dict[str, AffixDefinition] = {
    "affix_vampiric": AffixDefinition(
        "affix_vampiric", "Vampiric", "Small heal on duel hits.", "lifesteal",
    ),
    "affix_unstable": AffixDefinition(
        "affix_unstable", "Unstable", "Higher crit but riskier.", "unstable",
    ),
    "affix_hannah_touched": AffixDefinition(
        "affix_hannah_touched", "Hannah-Touched", "Bonus boss damage.", "boss",
    ),
    "affix_corp_sponsored": AffixDefinition(
        "affix_corp_sponsored", "Corp-Sponsored", "Bonus work income.", "work",
    ),
    "affix_void_etched": AffixDefinition(
        "affix_void_etched", "Void-Etched", "Extra mitigation.", "mitigation",
    ),
    "affix_gilded": AffixDefinition(
        "affix_gilded", "Gilded", "Slightly higher sell value.", "gilded",
    ),
}


@dataclass
class AffixBonuses:
    damage_mult: float = 1.0
    boss_damage_mult: float = 1.0
    crit_bonus: float = 0.0
    mitigation_bonus: float = 0.0
    work_income_mult: float = 1.0
    lifesteal_pct: float = 0.0


def roll_affix_ids(*, delve_bonus: bool = False) -> list[str]:
    count_roll = random.random()
    max_affixes = 2 if delve_bonus or random.random() < 0.35 else 1
    if count_roll < 0.25:
        return []
    pool = list(AFFIX_DEFINITIONS.keys())
    random.shuffle(pool)
    return pool[:max_affixes]


def roll_affix_value() -> float:
    return round(random.uniform(3.0, 12.0), 1)


def bonuses_from_affix(affix_id: str, roll_value: float) -> AffixBonuses:
    pct = roll_value / 100.0
    effect = AFFIX_DEFINITIONS[affix_id].effect
    if effect == "lifesteal":
        return AffixBonuses(lifesteal_pct=pct)
    if effect == "unstable":
        return AffixBonuses(damage_mult=1.0 + pct, crit_bonus=pct)
    if effect == "boss":
        return AffixBonuses(boss_damage_mult=1.0 + pct)
    if effect == "work":
        return AffixBonuses(work_income_mult=1.0 + pct * 0.5)
    if effect == "mitigation":
        return AffixBonuses(mitigation_bonus=pct)
    if effect == "gilded":
        return AffixBonuses(damage_mult=1.0 + pct * 0.3)
    return AffixBonuses()


def merge_affix_bonuses(bonuses: list[AffixBonuses]) -> AffixBonuses:
    if not bonuses:
        return AffixBonuses()
    merged = AffixBonuses()
    for b in bonuses:
        merged.damage_mult *= b.damage_mult
        merged.boss_damage_mult *= b.boss_damage_mult
        merged.crit_bonus += b.crit_bonus
        merged.mitigation_bonus += b.mitigation_bonus
        merged.work_income_mult *= b.work_income_mult
        merged.lifesteal_pct += b.lifesteal_pct
    return merged


def format_affix_line(affix_id: str, roll_value: float) -> str:
    defn = AFFIX_DEFINITIONS[affix_id]
    return f"**{defn.name}** ({roll_value:.1f}%)"


def current_delve_week_id() -> str:
    import time

    week = int(time.time() // config.DELVE_WEEK_SECONDS)
    weeks = config.DELVE_WEEK_ROTATION
    return weeks[week % len(weeks)]


def delve_week_label(week_id: str) -> str:
    labels = {
        "cursed_depths": "Cursed Depths — harder enemies, 2× void hardener drops",
        "merchants_run": "Merchant's Run — bonus nuggets, blueprint shard chance",
        "blood_pact": "Blood Pact — shared risk, accessory drop boost",
    }
    return labels.get(week_id, week_id.replace("_", " ").title())
