"""Lab fertilizer items — boost harvest yield and shorten grow time."""
from __future__ import annotations

from dataclasses import dataclass

FERTILIZER_IDS: frozenset[str] = frozenset({"fertilizer", "xl_fertilizer"})


@dataclass(frozen=True, slots=True)
class FertilizerDef:
    item_id: str
    name: str
    yield_mult: float
    grow_time_mult: float
    emoji: str = "🧪"


FERTILIZERS: tuple[FertilizerDef, ...] = (
    FertilizerDef(
        "fertilizer",
        "Fertilizer",
        yield_mult=1.5,
        grow_time_mult=0.75,
    ),
    FertilizerDef(
        "xl_fertilizer",
        "XL Fertilizer",
        yield_mult=2.0,
        grow_time_mult=0.5,
        emoji="🧴",
    ),
)

FERTILIZER_BY_ID: dict[str, FertilizerDef] = {f.item_id: f for f in FERTILIZERS}


def fertilizer_by_id(item_id: str) -> FertilizerDef | None:
    return FERTILIZER_BY_ID.get(item_id.strip().lower())
