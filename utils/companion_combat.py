"""Companion auto-attack and pet duel combat helpers."""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any

import config
from utils.companions import (
    companion_display_name,
    companion_emoji,
    roll_companion_damage,
)


def apply_stamina_regen(
    current: int,
    *,
    updated_at: float,
    now: float | None = None,
    cap: int | None = None,
) -> tuple[int, float]:
    """Regenerate 1 stamina per minute up to the natural cap."""
    now = time.time() if now is None else now
    regen_cap = cap if cap is not None else config.COMPANION_BASE_STAMINA
    elapsed_min = int((now - updated_at) // 60)
    if elapsed_min <= 0:
        return current, updated_at
    refreshed = min(regen_cap, current + elapsed_min * config.COMPANION_STAMINA_REGEN_PER_MINUTE)
    advanced_at = updated_at + elapsed_min * 60
    return refreshed, advanced_at


def owner_attack_power_from_loadout(loadout: Any) -> int:
    """Approximate owner attack power for 25% stat inheritance."""
    primary = loadout.primary
    off_hand = loadout.off_hand
    power = 0
    if primary is not None:
        power += int(primary.power)
    if off_hand is not None:
        power += int(off_hand.power) // 2
    bonuses = loadout.accessory_bonuses
    if bonuses is not None:
        power += int(bonuses.flat_damage)
    return max(0, power)


@dataclass(frozen=True)
class CompanionStrikeResult:
    user_id: int
    companion_id: str
    display_name: str
    emoji: str
    damage: int
    critical: bool
    verb: str
    target_kind: str
    target_name: str
    killed: bool = False
    loot_note: str = ""
    revived_owner: bool = False


def pick_companion_target(has_adds: bool) -> str:
    """Return 'add' or 'boss'. Prioritize adds when present."""
    if has_adds:
        return "add" if random.random() < 0.65 else "boss"
    return "boss"
