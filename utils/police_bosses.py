"""Police bust bosses — themed copies of raid bosses at reduced strength."""
from __future__ import annotations

import random

import config
from utils.boss_mechanics import scale_counter_damage, threat_for_variant


def cop_display_name(source_variant: str) -> str:
    return config.DRUG_COP_DISPLAY_NAMES.get(
        source_variant,
        f"Officer {source_variant.replace('_', ' ').title()}",
    )


def scaled_cop_stats(source_variant: str) -> dict[str, object]:
    """Return combat stats at DRUG_COP_STRENGTH_MULT of the source raid boss."""
    base = config.BOSS_VARIANTS[source_variant]
    scale = config.DRUG_COP_STRENGTH_MULT
    lo, hi = base["counter_damage"]
    return {
        "source_variant": source_variant,
        "threat": int(base.get("threat", 1)),
        "counter_chance": float(base["counter_chance"]) * scale,
        "counter_damage": (max(1, int(lo * scale)), max(1, int(hi * scale))),
        "crit_chance": float(base.get("crit_chance", 0.05)) * scale,
        "multiplier": float(base.get("multiplier", 1.0)) * scale,
    }


def cop_encounter_hp(source_variant: str, player_max_hp: float) -> float:
    """Solo bust HP scaled from the source boss tier and player gear."""
    stats = scaled_cop_stats(source_variant)
    threat = int(stats["threat"])
    mult = float(stats["multiplier"])
    hp = player_max_hp * (0.5 + 0.12 * threat) * max(0.5, mult) * config.DRUG_COP_STRENGTH_MULT
    return max(60.0, min(round(hp), player_max_hp * 2.5))


def roll_cop_counter_damage(source_variant: str, *, hp_ratio: float) -> int:
    stats = scaled_cop_stats(source_variant)
    lo, hi = stats["counter_damage"]
    raw = random.randint(int(lo), int(hi))
    return scale_counter_damage(raw, source_variant, hp_ratio=hp_ratio)


def roll_cop_crit(source_variant: str) -> bool:
    stats = scaled_cop_stats(source_variant)
    return random.random() < float(stats["crit_chance"])


def pick_cop_variant(rng: random.Random | None = None) -> str:
    r = rng or random
    return r.choice(config.DRUG_COP_SOURCE_VARIANTS)
