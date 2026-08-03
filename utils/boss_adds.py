"""Raid adds: Hannah's Henchmen and Court of Kitty's Jesters."""
from __future__ import annotations

import random
import time
from dataclasses import dataclass

import config


ADD_TYPES = ("henchman", "court_jester")

ADD_DISPLAY_NAMES = {
    "henchman": "Hannah's Henchman",
    "court_jester": "Court of Kitty's Jester",
}

ADD_SPAWN_ANNOUNCEMENTS = {
    "henchman": "A **Hannah's Henchman** crashes the raid!",
    "court_jester": "A **Court of Kitty's Jester** leaps into the raid!",
}

# Explicit: celestial_shard never in add loot.
FORBIDDEN_ADD_DROPS = frozenset({"celestial_shard"})


@dataclass(frozen=True)
class RaidAddState:
    add_id: int
    add_type: str
    hp: float
    max_hp: float
    spawned_at: float
    expires_at: float

    @property
    def display_name(self) -> str:
        return ADD_DISPLAY_NAMES.get(self.add_type, self.add_type)


def add_max_hp(boss_max_hp: float, threat: int) -> float:
    ratio = 0.05 + min(0.07, threat * 0.01)
    return max(250.0, boss_max_hp * ratio)


def pick_add_type(variant: str) -> str:
    high_tier = variant in (
        "shadow", "celestial", "mythic", "zz_wrath", "freaky_nikki", "world_leviathan",
    )
    if high_tier and random.random() < 0.55:
        return "court_jester"
    return "henchman"


def should_spawn_add(
    *,
    boss_hp_ratio: float,
    phase_crossed: bool = False,
) -> bool:
    if boss_hp_ratio > (1.0 - config.BOSS_ADD_SPAWN_MIN_HP_RATIO):
        return False
    chance = config.BOSS_ADD_SPAWN_CHANCE
    if phase_crossed:
        chance = min(1.0, chance + 0.25)
    return random.random() < chance


def roll_add_loot(add_type: str, boss_variant: str) -> list[tuple[str, int]]:
    """Material drops only — never celestial shards."""
    drops: list[tuple[str, int]] = []
    if add_type == "henchman":
        drops.append(("alchemy_scrap", random.randint(1, 4)))
        if boss_variant in ("celestial", "mythic", "zz_wrath", "world_leviathan") and random.random() < 0.15:
            drops.append(("void_hardener", 1))
    elif add_type == "court_jester":
        drops.append(("void_hardener", random.randint(1, 2)))
        if random.random() < 0.25:
            drops.append(("alchemy_scrap", random.randint(1, 2)))
    return [(item_id, qty) for item_id, qty in drops if item_id not in FORBIDDEN_ADD_DROPS]


def roll_add_companion(add_type: str) -> str | None:
    """Return companion id when the add drops a henchling, else None."""
    from utils.companions import ADD_COMPANION_DROPS

    companion_id = ADD_COMPANION_DROPS.get(add_type)
    if companion_id is None:
        return None
    if random.random() >= config.COMPANION_DROP_CHANCE:
        return None
    return companion_id


def add_expires_at(now: float | None = None) -> float:
    ts = time.time() if now is None else now
    return ts + config.BOSS_ADD_LIFETIME_SECONDS
