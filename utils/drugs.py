"""Drug trade catalog and pricing math.

A risky, high-reward economy layer: grow product in a lab over time, then sell
it on the street (volatile prices, raid risk) or to other players. Products can
also be consumed for gameplay effects.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

import config

# Legacy fictional ids mapped to current catalog entries (player stash migration).
_LEGACY_DRUG_ALIASES: dict[str, str] = {
    "greenleaf": "blue_dream",
    "bluecrystal": "crystal_meth",
    "whitedust": "cocaine",
    "goldenpoppy": "heroin",
}


@dataclass(frozen=True, slots=True)
class DrugDef:
    drug_id: str
    name: str
    emoji: str
    category: str
    seed_cost: float
    grow_seconds: int
    yield_min: int
    yield_max: int
    street_price: float  # base nuggets per unit
    effect_summary: str
    effect_energy: int = 0
    effect_heal_pct: float = 0.0
    effect_damage_pct: float = 0.0
    effect_boss_mult: float = 1.0
    effect_duel_mult: float = 1.0
    effect_duration: float = 300.0
    overdose_chance: float = 0.0
    overdose_damage_pct: float = 0.0


DRUGS: tuple[DrugDef, ...] = (
    # --- Cannabis (THC strains) ---
    DrugDef(
        "blue_dream", "Blue Dream", "🌿", "cannabis",
        200.0, 30 * 60, 4, 8, 120.0,
        "Relaxed focus — +10 energy, heal 5% HP.",
        effect_energy=10, effect_heal_pct=0.05,
    ),
    DrugDef(
        "og_kush", "OG Kush", "🍃", "cannabis",
        350.0, 45 * 60, 4, 7, 180.0,
        "Heavy indica — heal 10% HP.",
        effect_heal_pct=0.10,
    ),
    DrugDef(
        "girl_scout_cookies", "Girl Scout Cookies", "🍪", "cannabis",
        600.0, 60 * 60, 3, 7, 280.0,
        "Sweet hybrid — next **/attack** deals +15% boss damage.",
        effect_boss_mult=1.15,
    ),
    DrugDef(
        "purple_haze", "Purple Haze", "💜", "cannabis",
        900.0, 75 * 60, 3, 6, 420.0,
        "Psychedelic sativa — next **/duel** deals +15% strike damage.",
        effect_duel_mult=1.15,
    ),
    DrugDef(
        "sour_diesel", "Sour Diesel", "⛽", "cannabis",
        1_200.0, 90 * 60, 3, 6, 550.0,
        "Energizing diesel — +20 energy.",
        effect_energy=20,
    ),
    DrugDef(
        "gorilla_glue", "Gorilla Glue", "🦍", "cannabis",
        1_800.0, 2 * 3600, 3, 5, 750.0,
        "Sticky knockout — heal 15% HP and +10 energy.",
        effect_heal_pct=0.15, effect_energy=10,
    ),
    DrugDef(
        "white_widow", "White Widow", "🕸️", "cannabis",
        2_500.0, 3 * 3600, 2, 5, 950.0,
        "Balanced classic — +5 energy, next **/attack** +10% damage.",
        effect_energy=5, effect_boss_mult=1.10,
    ),
    # --- Stimulants ---
    DrugDef(
        "cocaine", "Cocaine", "❄️", "stimulant",
        5_000.0, 3 * 3600, 2, 5, 2_500.0,
        "Pure stim — +25 energy, next **/duel** +20% damage.",
        effect_energy=25, effect_duel_mult=1.20,
    ),
    DrugDef(
        "crystal_meth", "Crystal Meth", "💎", "stimulant",
        8_000.0, 4 * 3600, 2, 4, 4_000.0,
        "Hard stim — next **/attack** +25% damage, but costs 5% HP.",
        effect_boss_mult=1.25, effect_damage_pct=0.05,
    ),
    DrugDef(
        "mdma", "MDMA", "💊", "stimulant",
        6_500.0, 3 * 3600, 2, 5, 3_200.0,
        "Euphoria — +15 energy, next **/duel** +15% damage.",
        effect_energy=15, effect_duel_mult=1.15,
    ),
    # --- Depressants / opioids ---
    DrugDef(
        "heroin", "Heroin", "🌺", "opioid",
        15_000.0, 6 * 3600, 2, 4, 7_000.0,
        "Numbing high — heal 20% HP, -15 energy.",
        effect_heal_pct=0.20, effect_energy=-15,
    ),
    DrugDef(
        "fentanyl", "Fentanyl", "☠️", "opioid",
        25_000.0, 8 * 3600, 1, 3, 12_000.0,
        "Extreme opioid — heal 25% HP, 15% overdose risk (lose 20% HP).",
        effect_heal_pct=0.25, overdose_chance=0.15, overdose_damage_pct=0.20,
    ),
    # --- Psychedelics ---
    DrugDef(
        "lsd", "LSD", "🌈", "psychedelic",
        10_000.0, 5 * 3600, 2, 4, 5_500.0,
        "Trip — random combat buff: +20% boss or duel damage.",
        effect_boss_mult=1.20, effect_duel_mult=1.20, effect_duration=420.0,
    ),
    DrugDef(
        "shrooms", "Magic Mushrooms", "🍄", "psychedelic",
        4_000.0, 2 * 3600, 3, 6, 2_000.0,
        "Mellow trip — heal 8% HP, +8 energy.",
        effect_heal_pct=0.08, effect_energy=8,
    ),
)

DRUGS_BY_ID: dict[str, DrugDef] = {d.drug_id: d for d in DRUGS}

DRUG_BUFF_PREFIX = "drug_buff:"


def normalize_drug_id(drug_id: str) -> str:
    key = drug_id.strip().lower()
    return _LEGACY_DRUG_ALIASES.get(key, key)


def drug_by_id(drug_id: str) -> DrugDef | None:
    return DRUGS_BY_ID.get(normalize_drug_id(drug_id))


def legacy_ids_for_canonical(canonical_id: str) -> tuple[str, ...]:
    return tuple(k for k, v in _LEGACY_DRUG_ALIASES.items() if v == canonical_id)


def inventory_lookup_ids(defn: DrugDef) -> tuple[str, ...]:
    keys = (defn.drug_id, *legacy_ids_for_canonical(defn.drug_id))
    return tuple(dict.fromkeys(keys))


def drug_buff_key(drug_id: str, variant: str | None = None) -> str:
    base = f"{DRUG_BUFF_PREFIX}{normalize_drug_id(drug_id)}"
    return f"{base}:{variant}" if variant else base


def parse_drug_buff_key(pending: str | None) -> str | None:
    if not pending or not str(pending).startswith(DRUG_BUFF_PREFIX):
        return None
    raw = str(pending)[len(DRUG_BUFF_PREFIX):]
    drug_id = raw.split(":", 1)[0]
    return drug_id if drug_by_id(drug_id) else None


def roll_yield(defn: DrugDef, *, yield_bonus: float = 0.0, rng: random.Random | None = None) -> int:
    """Harvest yield, including any district/equipment bonus."""
    r = rng or random
    base = r.randint(defn.yield_min, defn.yield_max)
    return max(1, int(round(base * (1.0 + max(0.0, yield_bonus)))))


def street_sale_multiplier(*, reputation_level: int = 0, influence_pct: float = 0.0) -> float:
    """Bonus multiplier for street drug sales from business rep and district influence."""
    rep_mult = 1.0 + max(0, int(reputation_level)) * config.DRUG_STREET_REPUTATION_BONUS_PER_LEVEL
    cap = max(1.0, float(config.BUSINESS_DISTRICT_INFLUENCE_MAX))
    inf_ratio = max(0.0, min(float(influence_pct), cap)) / cap
    inf_mult = 1.0 + inf_ratio * config.DRUG_STREET_INFLUENCE_MAX_BONUS
    return rep_mult * inf_mult


def street_price(
    defn: DrugDef,
    *,
    rng: random.Random | None = None,
    sale_mult: float = 1.0,
) -> float:
    """Current street price with random volatility around the base."""
    r = rng or random
    variance = config.DRUG_STREET_PRICE_VARIANCE
    factor = 1.0 + r.uniform(-variance, variance)
    return max(1.0, defn.street_price * factor * max(1.0, sale_mult))


def sale_total(
    defn: DrugDef,
    quantity: int,
    *,
    rng: random.Random | None = None,
    sale_mult: float = 1.0,
) -> float:
    return street_price(defn, rng=rng, sale_mult=sale_mult) * max(0, int(quantity))


def format_street_sale_bonus(sale_mult: float, *, reputation_level: int, influence_pct: float) -> str:
    if sale_mult <= 1.001:
        return ""
    rep_pct = int(max(0, reputation_level) * config.DRUG_STREET_REPUTATION_BONUS_PER_LEVEL * 100)
    cap = max(1.0, float(config.BUSINESS_DISTRICT_INFLUENCE_MAX))
    inf_pct = int(
        max(0.0, min(float(influence_pct), cap)) / cap * config.DRUG_STREET_INFLUENCE_MAX_BONUS * 100,
    )
    return f" (×{sale_mult:.2f} — rep +{rep_pct}%, influence +{inf_pct}%)"


def format_drug_effect(defn: DrugDef) -> str:
    return defn.effect_summary


def format_consume_message(result: dict[str, object]) -> str:
    parts = [f"{result['emoji']} **{result['name']}** — {result['effect_summary']}"]
    if result.get("overdosed"):
        parts.append(f"☠️ **Overdose!** Took **{int(result['damage_amount'])}** damage.")
    else:
        if float(result.get("heal_amount") or 0) > 0:
            parts.append(f"❤️ Healed **{int(result['heal_amount'])}** HP.")
        if float(result.get("damage_amount") or 0) > 0:
            parts.append(f"💔 Took **{int(result['damage_amount'])}** damage.")
    energy_delta = int(result.get("energy_delta") or 0)
    if energy_delta > 0:
        parts.append(f"⚡ +**{energy_delta}** energy.")
    elif energy_delta < 0:
        parts.append(f"⚡ **{energy_delta}** energy.")
    if result.get("boss_buff"):
        pct = int((float(result["boss_buff"]) - 1.0) * 100)
        mins = int(float(result.get("buff_duration") or 300) // 60)
        parts.append(f"Next **/attack** +**{pct}%** boss damage ({mins} min).")
    if result.get("duel_buff"):
        pct = int((float(result["duel_buff"]) - 1.0) * 100)
        mins = int(float(result.get("buff_duration") or 300) // 60)
        parts.append(f"Next **/duel** +**{pct}%** strike damage ({mins} min).")
    return "💨 " + " ".join(parts)


def drugs_by_category() -> dict[str, list[DrugDef]]:
    grouped: dict[str, list[DrugDef]] = {}
    for defn in DRUGS:
        grouped.setdefault(defn.category, []).append(defn)
    return grouped
