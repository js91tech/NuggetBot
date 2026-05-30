from __future__ import annotations

import config


def max_storage_tokens() -> int:
    extra = config.BANK_MAX_CAPACITY - config.BANK_BASE_CAPACITY
    return int(extra // config.BANK_STORAGE_PER_TOKEN)


def bank_capacity(tokens: int) -> float:
    cap = config.BANK_BASE_CAPACITY + tokens * config.BANK_STORAGE_PER_TOKEN
    return min(config.BANK_MAX_CAPACITY, cap)


def bank_deposit_room(current_bank: float, tokens: int) -> float:
    return max(0.0, bank_capacity(tokens) - current_bank)
