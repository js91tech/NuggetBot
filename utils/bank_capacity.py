from __future__ import annotations

import config


def bank_capacity(expansions_by_tier: dict[int, int] | int) -> float:
    """Max nuggets storable in the personal bank for a given expansion breakdown."""
    if isinstance(expansions_by_tier, int):
        expansions_by_tier = {1: max(0, expansions_by_tier)}
    extra = 0.0
    for tier, qty in expansions_by_tier.items():
        spec = config.BANK_EXPANSION_TIERS.get(tier)
        if spec is None:
            continue
        extra += max(0, int(qty)) * float(spec["capacity"])
    return float(config.BANK_BASE_CAPACITY) + extra


def bank_deposit_room(current_bank: float, expansions_by_tier: dict[int, int] | int) -> float:
    """How many more nuggets can be deposited before hitting capacity."""
    return max(0.0, bank_capacity(expansions_by_tier) - max(0.0, current_bank))
