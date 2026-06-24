"""Character attributes (STR/DEX/AGI/DEF/VIT) earned from class XP."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import config

AttributeName = Literal["strength", "dexterity", "agility", "defense", "vitality"]
STAT_KEYS: tuple[AttributeName, ...] = (
    "strength",
    "dexterity",
    "agility",
    "defense",
    "vitality",
)
STAT_COLUMNS: dict[AttributeName, str] = {
    "strength": "stat_str",
    "dexterity": "stat_dex",
    "agility": "stat_agi",
    "defense": "stat_def",
    "vitality": "stat_vit",
}
STAT_LABELS: dict[AttributeName, str] = {
    "strength": "STR",
    "dexterity": "DEX",
    "agility": "AGI",
    "defense": "DEF",
    "vitality": "VIT",
}
STAT_EMOJI: dict[AttributeName, str] = {
    "strength": "💪",
    "dexterity": "🎯",
    "agility": "💨",
    "defense": "🛡️",
    "vitality": "❤️",
}


@dataclass(frozen=True)
class CharacterAttributes:
    strength: int = config.ATTR_BASE_VALUE
    dexterity: int = config.ATTR_BASE_VALUE
    agility: int = config.ATTR_BASE_VALUE
    defense: int = config.ATTR_BASE_VALUE
    vitality: int = config.ATTR_BASE_VALUE

    def value(self, name: AttributeName) -> int:
        return getattr(self, name)

    def total_points(self) -> int:
        return sum(self.value(name) for name in STAT_KEYS)

    # Back-compat alias
    def points_spent(self) -> int:
        return self.total_points()

    @classmethod
    def from_row(cls, row, *, prestige_level: int | None = None) -> CharacterAttributes:
        cap = (
            stat_cap_for_prestige(prestige_level)
            if prestige_level is not None
            else stat_cap_for_prestige(config.PRESTIGE_MAX_LEVEL)
        )

        def _get(col: str) -> int:
            try:
                raw = row[col]
            except (KeyError, IndexError, TypeError):
                return config.ATTR_BASE_VALUE
            if raw is None:
                return config.ATTR_BASE_VALUE
            return max(0, min(cap, int(raw)))

        return cls(
            strength=_get("stat_str"),
            dexterity=_get("stat_dex"),
            agility=_get("stat_agi"),
            defense=_get("stat_def"),
            vitality=_get("stat_vit"),
        )


def stat_cap_for_prestige(prestige_level: int) -> int:
    """Per-stat cap: 15 at prestige 0, +1 per prestige (25 at prestige 10)."""
    return config.ATTR_STAT_CAP_BASE + prestige_level * config.ATTR_STAT_CAP_PER_PRESTIGE


def total_point_pool_cap(prestige_level: int) -> int:
    """Total points spendable across all stats: 50 at P0, +5/prestige (100 at P10)."""
    return (
        config.ATTR_BASE_TOTAL_POINTS
        + prestige_level * config.ATTR_TOTAL_POINTS_PER_PRESTIGE
    )


def max_total_attribute_points(prestige_level: int) -> int:
    """Alias for total spendable pool (not per-stat max × 5)."""
    return total_point_pool_cap(prestige_level)


def xp_required_for_attribute_points(point_count: int) -> int:
    """Cumulative class XP to earn N attribute points (first 20 are cheaper)."""
    if point_count <= 0:
        return 0
    fast = min(point_count, config.ATTR_FAST_POINT_COUNT)
    slow = max(0, point_count - config.ATTR_FAST_POINT_COUNT)
    return (
        fast * config.ATTR_XP_PER_FAST_POINT
        + slow * config.ATTR_XP_PER_SLOW_POINT
    )


def attribute_points_from_class_xp(class_xp: int) -> int:
    """How many points class XP has earned."""
    max_points = total_point_pool_cap(config.PRESTIGE_MAX_LEVEL)
    lo, hi = 0, max_points
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if xp_required_for_attribute_points(mid) <= class_xp:
            lo = mid
        else:
            hi = mid - 1
    return lo


def unspent_attribute_points(
    attrs: CharacterAttributes,
    class_xp: int,
    prestige_level: int,
) -> int:
    earned = attribute_points_from_class_xp(class_xp)
    allocatable = min(earned, total_point_pool_cap(prestige_level))
    return max(0, allocatable - attrs.total_points())


def xp_until_next_attribute_point(
    class_xp: int,
    prestige_level: int,
    attrs: CharacterAttributes,
) -> int | None:
    """Class XP still needed for the next point, or None if fully allocated."""
    pool_cap = total_point_pool_cap(prestige_level)
    if attrs.total_points() >= pool_cap:
        return None
    earned = attribute_points_from_class_xp(class_xp)
    allocatable = min(earned, pool_cap)
    if allocatable > attrs.total_points():
        return 0
    next_point = earned + 1
    if next_point > pool_cap:
        return None
    return max(0, xp_required_for_attribute_points(next_point) - class_xp)


@dataclass(frozen=True)
class AttributeCombatBonuses:
    """Combat modifiers derived from allocated attributes."""

    damage_mult: float = 1.0
    extra_crit: float = 0.0
    mitigation_bonus: float = 0.0
    hp_bonus: int = 0


@dataclass(frozen=True)
class DebuffResistance:
    """Multipliers applied to boss elemental debuffs (lower = less effect)."""

    cc_duration_mult: float = 1.0
    cc_proc_mult: float = 1.0
    debuff_attack_cd_mult: float = 1.0
    dot_damage_mult: float = 1.0
    void_drain_mult: float = 1.0


def combat_bonuses_from_attributes(attrs: CharacterAttributes) -> AttributeCombatBonuses:
    damage_mult = 1.0 + attrs.strength * config.ATTR_STR_DAMAGE_PCT
    extra_crit = min(
        config.ATTR_MAX_DEX_CRIT_BONUS,
        attrs.dexterity * config.ATTR_DEX_CRIT_PCT,
    )
    mitigation_bonus = min(
        config.ATTR_MAX_DEF_MITIGATION_BONUS,
        attrs.defense * config.ATTR_DEF_MITIGATION_PCT,
    )
    hp_bonus = attrs.vitality * config.ATTR_VIT_HP_PER_POINT
    return AttributeCombatBonuses(
        damage_mult=damage_mult,
        extra_crit=extra_crit,
        mitigation_bonus=mitigation_bonus,
        hp_bonus=hp_bonus,
    )


def debuff_resistance_from_attributes(attrs: CharacterAttributes) -> DebuffResistance:
    cc_duration_reduction = min(
        config.ATTR_MAX_CC_DURATION_REDUCTION,
        attrs.agility * config.ATTR_AGI_CC_DURATION_PCT,
    )
    cc_proc_resist = min(
        config.ATTR_MAX_CC_PROC_RESIST,
        attrs.agility * config.ATTR_AGI_CC_PROC_RESIST_PCT,
    )
    attack_cd_reduction = min(0.45, attrs.agility * config.ATTR_AGI_ATTACK_CD_PCT)
    dot_resist = min(
        config.ATTR_MAX_DOT_RESIST,
        attrs.defense * config.ATTR_DEF_DOT_RESIST_PCT,
    )

    return DebuffResistance(
        cc_duration_mult=max(0.25, 1.0 - cc_duration_reduction),
        cc_proc_mult=max(0.35, 1.0 - cc_proc_resist),
        debuff_attack_cd_mult=max(0.55, 1.0 - attack_cd_reduction),
        dot_damage_mult=max(0.50, 1.0 - dot_resist),
        void_drain_mult=max(0.50, 1.0 - dot_resist),
    )


def apply_cc_duration(duration: float, resistance: DebuffResistance) -> float:
    reduced = duration * resistance.cc_duration_mult
    return max(config.ATTR_MIN_DEBUFF_SECONDS, reduced)


def resolve_downed_duration(config_seconds: float, attrs: CharacterAttributes) -> float:
    """Boss knockdown lockout: cap at max CC duration, then apply AGI reduction."""
    capped = min(float(config_seconds), config.BOSS_DEBUFF_MAX_SECONDS)
    return apply_cc_duration(capped, debuff_resistance_from_attributes(attrs))


def apply_debuff_attack_cooldown(cooldown: float, resistance: DebuffResistance) -> float:
    return max(2.0, cooldown * resistance.debuff_attack_cd_mult)


def format_attributes_block(
    attrs: CharacterAttributes,
    *,
    class_xp: int,
    prestige_level: int = 0,
) -> str:
    stat_cap = stat_cap_for_prestige(prestige_level)
    pool_cap = total_point_pool_cap(prestige_level)
    earned = attribute_points_from_class_xp(class_xp)
    allocatable = min(earned, pool_cap)
    unspent = unspent_attribute_points(attrs, class_xp, prestige_level)
    lines = [
        f"**{STAT_EMOJI[name]} {STAT_LABELS[name]}** **{attrs.value(name)}** / **{stat_cap}**"
        for name in STAT_KEYS
    ]
    lines.append(
        f"Pool: **{attrs.total_points()}/{allocatable}** spent"
        f" (cap **{pool_cap}** · **{stat_cap}**/stat)"
        + (f" · **{unspent}** unspent" if unspent else "")
    )
    xp_left = xp_until_next_attribute_point(class_xp, prestige_level, attrs)
    if xp_left is not None and xp_left > 0:
        lines.append(f"Next point in **{xp_left}** class XP")
    elif attrs.total_points() >= pool_cap:
        lines.append("Prestige up for +5 total points and +1 per-stat cap.")
    combat = combat_bonuses_from_attributes(attrs)
    resist = debuff_resistance_from_attributes(attrs)
    effect_lines = []
    if combat.damage_mult > 1.0:
        effect_lines.append(f"+{int(round((combat.damage_mult - 1) * 100))}% damage (STR)")
    if combat.extra_crit > 0:
        effect_lines.append(f"+{int(round(combat.extra_crit * 100))}% crit (DEX)")
    if combat.mitigation_bonus > 0:
        effect_lines.append(f"+{int(round(combat.mitigation_bonus * 100))}% mitigation (DEF)")
    if combat.hp_bonus > 0:
        effect_lines.append(f"+{combat.hp_bonus} HP (VIT)")
    cc_red = int(round((1.0 - resist.cc_duration_mult) * 100))
    if cc_red > 0:
        effect_lines.append(f"-{cc_red}% stun/root/chill duration (AGI)")
    proc_red = int(round((1.0 - resist.cc_proc_mult) * 100))
    if proc_red > 0:
        effect_lines.append(f"-{proc_red}% debuff proc chance (AGI)")
    dot_red = int(round((1.0 - resist.dot_damage_mult) * 100))
    if dot_red > 0:
        effect_lines.append(f"-{dot_red}% burn/void drain (DEF)")
    if effect_lines:
        lines.append("**Effects** — " + " · ".join(effect_lines))
    return "\n".join(lines)


def normalize_stat_name(raw: str) -> AttributeName | None:
    key = raw.strip().lower()
    aliases: dict[str, AttributeName] = {
        "str": "strength",
        "strength": "strength",
        "dex": "dexterity",
        "dexterity": "dexterity",
        "agi": "agility",
        "agility": "agility",
        "def": "defense",
        "defense": "defense",
        "vit": "vitality",
        "vitality": "vitality",
        "health": "vitality",
        "hp": "vitality",
    }
    return aliases.get(key)
