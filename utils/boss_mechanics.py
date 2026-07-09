from __future__ import annotations

import random

import config


def threat_for_variant(variant: str) -> int:
    return int(config.BOSS_VARIANTS.get(variant, {}).get("threat", 1))


def boss_raid_damage_bonus(variant: str) -> float:
    """Multiplier on raid damage vs high-threat bosses (mythic kill pacing)."""
    threat = threat_for_variant(variant)
    return config.BOSS_RAID_DAMAGE_BONUS_BY_THREAT.get(threat, 1.0)


def compute_boss_hp(
    circulation: float,
    scale_factor: float,
    variant: str,
    *,
    hp_multiplier: float = 1.0,
    mirrored_variant: str | None = None,
) -> float:
    if variant == "tomass":
        mirror = mirrored_variant or "enraged"
        scaled_hp = max(config.BOSS_MIN_HP, circulation * scale_factor)
        base_hp = min(config.BOSS_HP_CAP, scaled_hp)
        mirror_mult = float(config.BOSS_VARIANTS[mirror]["multiplier"])
        strength = float(config.BOSS_VARIANTS["tomass"]["mirrored_strength_mult"])
        hp = base_hp * mirror_mult * strength
        threat = threat_for_variant(variant)
        hp *= 1.0 + (threat - 1) * config.BOSS_THREAT_HP_BONUS_PER_TIER
        return hp * hp_multiplier

    variant_cfg = config.BOSS_VARIANTS[variant]
    fixed = variant_cfg.get("fixed_hp")
    if fixed is not None:
        hp = float(fixed)
    else:
        scaled_hp = max(config.BOSS_MIN_HP, circulation * scale_factor)
        base_hp = min(config.BOSS_HP_CAP, scaled_hp)
        hp = base_hp * float(variant_cfg["multiplier"])
        threat = threat_for_variant(variant)
        hp *= 1.0 + (threat - 1) * config.BOSS_THREAT_HP_BONUS_PER_TIER
    return hp * hp_multiplier


def passive_decay_rate_for_variant(variant: str) -> float:
    threat = threat_for_variant(variant)
    return config.BOSS_PASSIVE_DECAY_BY_THREAT.get(
        threat,
        config.BOSS_PASSIVE_HP_DECAY_FRACTION_PER_MINUTE,
    )


def reward_mult_for_variant(variant: str) -> float:
    threat = threat_for_variant(variant)
    return config.BOSS_REWARD_MULT_BY_THREAT.get(threat, 1.0)


def business_boss_reward_mult(
    tier: int | None = None,
    business_prestige: int | None = None,
) -> float:
    """Personal business bonus on boss nugget slices. No business → 1.0."""
    if tier is None:
        return 1.0
    safe_tier = max(1, int(tier))
    safe_prestige = max(0, int(business_prestige or 0))
    return (
        1.0
        + config.BOSS_REWARD_BUSINESS_TIER_BONUS * max(0, safe_tier - 1)
        + config.BOSS_REWARD_BUSINESS_PRESTIGE_BONUS * safe_prestige
    )


def clamp_boss_personal_reward_mult(income_mult: float, business_mult: float) -> float:
    """Combine income + business mults and soft-cap the personal boss payout."""
    combined = max(0.0, float(income_mult)) * max(0.0, float(business_mult))
    return min(config.BOSS_REWARD_PERSONAL_MULT_CAP, combined)


def raider_damage_mult(distinct_raiders: int) -> float:
    if distinct_raiders >= 4:
        return 1.0
    return config.BOSS_RAIDER_DAMAGE_MULT.get(distinct_raiders, 1.0)


def scale_counter_damage(
    raw_damage: int,
    variant: str,
    *,
    hp_ratio: float,
) -> int:
    threat = threat_for_variant(variant)
    mult = 1.0 + (threat - 1) * config.BOSS_COUNTER_THREAT_SCALE
    if hp_ratio <= config.BOSS_ENRAGE_HP_THRESHOLD:
        mult *= config.BOSS_ENRAGE_COUNTER_MULT
    return max(1, int(round(raw_damage * mult)))


def roll_counter_damage(variant: str, *, hp_ratio: float) -> int:
    low, high = config.BOSS_VARIANTS[variant]["counter_damage"]
    raw = random.randint(int(low), int(high))
    return scale_counter_damage(raw, variant, hp_ratio=hp_ratio)


def boss_expires_at(spawn_ts: float, variant: str) -> float | None:
    despawn = config.BOSS_VARIANTS.get(variant, {}).get("despawn_seconds")
    if despawn is None:
        return None
    return spawn_ts + float(despawn)


def boss_variant_dashboard_label(variant: str) -> str:
    special = {
        "freaky_nikki": config.BOSS_NAME_FREAKY_NIKKI,
        "tomass": config.BOSS_NAME_TOMASS,
        "zz_wrath": config.BOSS_NAME_ZZ_WRATH,
    }
    if variant in special:
        return special[variant]
    return f"Hannah ({variant.title()})"


def dashboard_boss_variants() -> list[tuple[str, str]]:
    """Return (variant_key, display_label) pairs for the dashboard summon dropdown."""
    ordered = [
        variant
        for variant in config.BOSS_DASHBOARD_VARIANT_ORDER
        if variant in config.BOSS_VARIANTS
    ]
    for variant in config.BOSS_VARIANTS:
        if variant not in ordered:
            ordered.append(variant)
    return [(variant, boss_variant_dashboard_label(variant)) for variant in ordered]
