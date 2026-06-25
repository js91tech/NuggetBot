"""Dealer rank progression from lifetime street/market sales."""
from __future__ import annotations

import config


def dealer_rank(units_sold: int) -> int:
    """Return rank 1–10 from total units sold."""
    sold = max(0, int(units_sold))
    rank = 1
    for index, threshold in enumerate(config.DEALER_RANK_THRESHOLDS):
        if sold >= threshold:
            rank = index + 1
    return min(rank, len(config.DEALER_RANK_THRESHOLDS))


def rank_title(rank: int) -> str:
    titles = {
        1: "Runner",
        2: "Corner Dealer",
        3: "Distributor",
        4: "Supplier",
        5: "Lab Boss",
        6: "Regional Plug",
        7: "Wholesaler",
        8: "Kingpin",
        9: "Drug Lord",
        10: "Cartel",
    }
    return titles.get(max(1, rank), "Runner")


def next_rank_threshold(rank: int) -> int | None:
    """Units sold needed for the next rank, or None at max."""
    thresholds = config.DEALER_RANK_THRESHOLDS
    if rank >= len(thresholds):
        return None
    return thresholds[rank]


def lab_slot_count(*, rank: int, legacy_extra: int = 0, cartel: bool = False) -> int:
    """Personal lab slots from base config, rank unlock, and legacy perk."""
    slots = config.DRUG_LAB_SLOTS
    if rank >= config.DEALER_RANK_EXTRA_LAB_SLOT:
        slots += 1
    slots += max(0, legacy_extra)
    if cartel:
        return config.CARTEL_LAB_SLOTS
    return slots


def can_list_on_market(rank: int) -> bool:
    return rank >= config.DEALER_RANK_MARKET_UNLOCK


def can_wholesale(rank: int) -> bool:
    return rank >= config.DEALER_RANK_WHOLESALE_UNLOCK
