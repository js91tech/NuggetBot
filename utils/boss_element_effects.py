"""Boss elemental counter procs and hazard descriptions."""
from __future__ import annotations

import random
from dataclasses import dataclass

import config


@dataclass(frozen=True)
class ElementProcRoll:
    """Outcome of rolling an elemental proc on a boss counter."""

    note: str = ""
    frost_slow_until: float | None = None
    verdant_root_until: float | None = None
    fire_burn: tuple[float, int, float] | None = None
    storm_stun_seconds: float | None = None
    void_mana_drain: int | None = None
    debuff_attack_cooldown: float | None = None


def roll_debuff_attack_cooldown() -> float:
    lo, hi = config.BOSS_DEBUFF_ATTACK_COOLDOWN_SECONDS
    return float(random.randint(lo, hi))


def roll_frost_or_root_duration() -> float:
    lo, hi = config.BOSS_FROST_SLOW_SECONDS
    return float(random.randint(lo, hi))


def _element_hazards() -> dict[str, str]:
    lo, hi = config.BOSS_DEBUFF_ATTACK_COOLDOWN_SECONDS
    return {
        "frost": (
            f"❄️ **Frost** — counters may **chill** you "
            f"({lo}–{hi}s between attacks while slowed)."
        ),
        "fire": (
            f"🔥 **Fire** — counters may **burn** you ({config.BOSS_FIRE_BURN_PROC_CHANCE:.0%} chance, "
            f"{config.BOSS_FIRE_BURN_TICKS} ticks every {config.BOSS_FIRE_BURN_INTERVAL_SECONDS}s)."
        ),
        "storm": (
            f"⚡ **Storm** — counters may **stun** you for **{lo}–{hi}s**."
        ),
        "void": (
            f"🌑 **Void** — counters may **drain mana** ({config.BOSS_VOID_DRAIN_PROC_CHANCE:.0%} chance)."
        ),
        "verdant": (
            f"🌿 **Verdant** — counters may **root** you "
            f"({lo}–{hi}s between attacks while rooted)."
        ),
    }


def element_hazard_text(element: str | None) -> str | None:
    if not element:
        return None
    return _element_hazards().get(str(element).lower())


def roll_element_proc(element: str | None, *, now: float) -> ElementProcRoll:
    """Roll whether a boss counter applies an elemental rider effect."""
    if not element:
        return ElementProcRoll()
    elem = str(element).lower()
    debuff_cd = roll_debuff_attack_cooldown()

    if elem == "frost" and random.random() < config.BOSS_FROST_PROC_CHANCE:
        duration = roll_frost_or_root_duration()
        until = now + duration
        return ElementProcRoll(
            note=(
                f" ❄️ **Chilled!** **{int(debuff_cd)}s** between attacks "
                f"for **{int(duration)}s**."
            ),
            frost_slow_until=until,
            debuff_attack_cooldown=debuff_cd,
        )

    if elem == "fire" and random.random() < config.BOSS_FIRE_BURN_PROC_CHANCE:
        tick_damage = float(
            random.randint(config.BOSS_FIRE_BURN_DAMAGE[0], config.BOSS_FIRE_BURN_DAMAGE[1]),
        )
        ticks = config.BOSS_FIRE_BURN_TICKS
        first_tick = now + config.BOSS_FIRE_BURN_INTERVAL_SECONDS
        return ElementProcRoll(
            note=f" 🔥 **Burning!** **{int(tick_damage)}** damage/tick × **{ticks}** ticks.",
            fire_burn=(tick_damage, ticks, first_tick),
        )

    if elem == "storm" and random.random() < config.BOSS_STORM_STUN_PROC_CHANCE:
        lo, hi = config.BOSS_STORM_STUN_SECONDS
        stun_seconds = float(random.randint(lo, hi))
        return ElementProcRoll(
            note=f" ⚡ **Stunned!** Down for **{int(stun_seconds)}s**.",
            storm_stun_seconds=stun_seconds,
        )

    if elem == "void" and random.random() < config.BOSS_VOID_DRAIN_PROC_CHANCE:
        lo, hi = config.BOSS_VOID_MANA_DRAIN
        drain = random.randint(lo, hi)
        return ElementProcRoll(
            note=f" 🌑 **Void tear!** **{drain}** mana drained.",
            void_mana_drain=drain,
        )

    if elem == "verdant" and random.random() < config.BOSS_VERDANT_ROOT_PROC_CHANCE:
        duration = roll_frost_or_root_duration()
        until = now + duration
        return ElementProcRoll(
            note=(
                f" 🌿 **Rooted!** **{int(debuff_cd)}s** between attacks "
                f"for **{int(duration)}s**."
            ),
            verdant_root_until=until,
            debuff_attack_cooldown=debuff_cd,
        )

    return ElementProcRoll()


def attack_cooldown_while_debuffed(
    attack_slow_until: float,
    verdant_root_until: float,
    debuff_attack_cooldown: float,
    *,
    now: float,
) -> float | None:
    """Seconds between boss attacks while chilled or rooted, if a debuff is active."""
    if attack_slow_until <= now and verdant_root_until <= now:
        return None
    if debuff_attack_cooldown > 0:
        return debuff_attack_cooldown
    return roll_debuff_attack_cooldown()
