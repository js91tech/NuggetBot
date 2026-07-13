from __future__ import annotations

from dataclasses import dataclass

import config


@dataclass(frozen=True)
class MuseumCategory:
    category_id: str
    name: str
    description: str


MUSEUM_CATEGORIES: tuple[MuseumCategory, ...] = (
    MuseumCategory("gear", "Armory", "Unique gear types owned."),
    MuseumCategory("bosses", "Trophy Hall", "Boss kills assisted."),
    MuseumCategory("strains", "Strain Codex", "Drug strains harvested."),
    MuseumCategory("duels", "Duel Pit", "PvP duel wins."),
    MuseumCategory("relics", "Relic Gallery", "Relics collected."),
    MuseumCategory("companions", "Menagerie", "Henchlings obtained."),
    MuseumCategory("phenotypes", "Phenotype Lab", "Crossbreeds discovered."),
    MuseumCategory("blueprints", "Blueprint Archive", "Codex entries unlocked."),
)

CATEGORY_TOTALS: dict[str, int] = {
    "gear": 49,
    "bosses": 6,
    "strains": 27,
    "duels": 100,
    "relics": 12,
    "companions": 8,
    "phenotypes": 15,
    "blueprints": 15,
}


@dataclass(frozen=True)
class MuseumBonusTier:
    pct_required: float
    income_mult: float
    damage_mult: float
    label: str


MUSEUM_BONUS_TIERS: tuple[MuseumBonusTier, ...] = (
    MuseumBonusTier(25.0, 1.005, 1.005, "Curator"),
    MuseumBonusTier(50.0, 1.01, 1.01, "Archivist"),
    MuseumBonusTier(75.0, 1.015, 1.015, "Historian"),
    MuseumBonusTier(100.0, 1.02, 1.02, "Legend"),
)


def museum_completion_pct(counts: dict[str, int]) -> float:
    if not counts:
        return 0.0
    total_entries = 0
    total_owned = 0
    for cat, cap in CATEGORY_TOTALS.items():
        total_entries += cap
        total_owned += min(int(counts.get(cat, 0)), cap)
    if total_entries <= 0:
        return 0.0
    return round(100.0 * total_owned / total_entries, 1)


def museum_bonuses_for_pct(pct: float) -> tuple[float, float, str]:
    income = 1.0
    damage = 1.0
    label = "Novice"
    for tier in MUSEUM_BONUS_TIERS:
        if pct >= tier.pct_required:
            income = tier.income_mult
            damage = tier.damage_mult
            label = tier.label
    cap = config.PASSIVE_BONUS_CAP
    income = min(income, 1.0 + cap)
    damage = min(damage, 1.0 + cap)
    return income, damage, label
