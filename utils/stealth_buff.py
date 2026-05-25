from __future__ import annotations

import config


def has_buff(user_id: int) -> bool:
    return user_id == config.STEALTH_BUFF_USER_ID


def combat_multiplier(user_id: int) -> float:
    """Outgoing damage and effective combat HP (not shown on /stats)."""
    if has_buff(user_id):
        return config.STEALTH_COMBAT_MULT
    return 1.0


def job_payout_multiplier(user_id: int) -> float:
    if has_buff(user_id):
        return config.STEALTH_JOB_PAYOUT_MULT
    return 1.0


def scale_damage(dealt: int, attacker_id: int) -> int:
    mult = combat_multiplier(attacker_id)
    if mult == 1.0:
        return dealt
    return max(1, int(dealt * mult))


def scale_incoming(taken: int, defender_id: int) -> int:
    mult = combat_multiplier(defender_id)
    if mult == 1.0:
        return taken
    return max(1, int(taken / mult))


def scale_max_hp(base_hp: int, user_id: int) -> int:
    mult = combat_multiplier(user_id)
    if mult == 1.0:
        return base_hp
    return max(1, int(base_hp * mult))
