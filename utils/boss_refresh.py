"""Boss refresh helpers: roles, moods, participation, weekly hunt, crew scores."""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime

import config


RAID_ROLES: dict[str, dict[str, float | str]] = {
    "tank": {
        "label": "🛡️ Tank",
        "damage_mult": 0.85,
        "counter_taken_mult": 0.70,
        "aggro_weight": 2.5,
        "blurb": "Soak counters (−30% taken), deal −15% damage.",
    },
    "healer": {
        "label": "💚 Healer",
        "damage_mult": 0.90,
        "counter_taken_mult": 1.0,
        "aggro_weight": 0.8,
        "blurb": "Deal −10% damage; 30% chance to pulse-heal after a hit.",
    },
    "glass": {
        "label": "🗡️ Glass",
        "damage_mult": 1.25,
        "counter_taken_mult": 1.35,
        "aggro_weight": 1.0,
        "blurb": "Deal +25% damage; take +35% from counters.",
    },
}


MOOD_BY_HP_RATIO: tuple[tuple[float, str, str], ...] = (
    (0.75, "calm", "The boss watches calmly."),
    (0.50, "aggressive", "The boss grows aggressive — counters land harder."),
    (0.25, "armored", "Armor plates lock — Glass cuts through; others hit softer."),
    (0.0, "frantic", "Frantic thrashing — race for the killing blow!"),
)


@dataclass(frozen=True)
class BossHuntDef:
    hunt_key: str
    label: str
    variant: str
    kills_required: int
    reward_nuggets: float
    reward_item: str | None
    reward_item_qty: int = 1


def current_boss_hunt_week_id(now: datetime | None = None) -> str:
    ts = now or datetime.now(UTC)
    return ts.strftime("%G-W%V")


def boss_hunt_for_week(week_id: str | None = None) -> BossHuntDef:
    wid = week_id or current_boss_hunt_week_id()
    rotation = config.BOSS_HUNT_ROTATION
    # Stable index from ISO week number
    try:
        week_num = int(wid.split("-W")[1])
    except (IndexError, ValueError):
        week_num = 1
    entry = rotation[(week_num - 1) % len(rotation)]
    return BossHuntDef(
        hunt_key=str(entry["hunt_key"]),
        label=str(entry["label"]),
        variant=str(entry["variant"]),
        kills_required=int(entry["kills_required"]),
        reward_nuggets=float(entry["reward_nuggets"]),
        reward_item=entry.get("reward_item"),
        reward_item_qty=int(entry.get("reward_item_qty", 1)),
    )


def mood_for_hp_ratio(hp_ratio: float) -> tuple[str, str]:
    for threshold, mood_id, blurb in MOOD_BY_HP_RATIO:
        if hp_ratio > threshold:
            return mood_id, blurb
    return MOOD_BY_HP_RATIO[-1][1], MOOD_BY_HP_RATIO[-1][2]


def role_damage_mult(role: str | None) -> float:
    if role is None or role not in RAID_ROLES:
        return 1.0
    return float(RAID_ROLES[role]["damage_mult"])


def role_counter_taken_mult(role: str | None) -> float:
    if role is None or role not in RAID_ROLES:
        return 1.0
    return float(RAID_ROLES[role]["counter_taken_mult"])


def role_aggro_weight(role: str | None) -> float:
    if role is None or role not in RAID_ROLES:
        return 1.0
    return float(RAID_ROLES[role]["aggro_weight"])


def mood_outgoing_damage_mult(mood: str | None, role: str | None) -> float:
    if mood != "armored":
        return 1.0
    if role == "glass":
        return 1.0
    return config.BOSS_MOOD_ARMORED_DAMAGE_MULT


def mood_counter_mult(mood: str | None) -> float:
    if mood == "aggressive":
        return config.BOSS_MOOD_AGGRESSIVE_COUNTER_MULT
    if mood == "frantic":
        return config.BOSS_MOOD_FRANTIC_COUNTER_MULT
    return 1.0


def pick_counter_target(candidate_ids: list[int], role_of) -> int:
    """Weighted pick favoring tanks (role_of(uid) -> role | None)."""
    if not candidate_ids:
        raise ValueError("no candidates")
    weights = [role_aggro_weight(role_of(uid)) for uid in candidate_ids]
    return random.choices(candidate_ids, weights=weights, k=1)[0]


def interesting_loot_pool(variant: str) -> list[str]:
    """Item ids for the top-damager guarantee roll."""
    pool = list(config.BOSS_TOP_DAMAGER_LOOT_POOL)
    if variant in ("mythic", "zz_wrath", "world_leviathan"):
        pool.append("celestial_shard")
    if variant == "world_leviathan":
        pool.extend(["void_hardener", "void_hardener", "celestial_shard"])
    return pool


def participation_eligible(damage: float, min_damage: float) -> bool:
    return damage >= min_damage


def format_role_help() -> str:
    lines = ["Pick a raid role before you strike:"]
    for role_id, meta in RAID_ROLES.items():
        lines.append(f"· {meta['label']} — {meta['blurb']}")
    return "\n".join(lines)
