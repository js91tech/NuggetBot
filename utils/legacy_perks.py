"""Business prestige 10+ legacy perk picks."""
from __future__ import annotations

from dataclasses import dataclass

import config


@dataclass(frozen=True, slots=True)
class LegacyPerkDef:
    perk_id: str
    name: str
    emoji: str
    description: str


def _build_catalog() -> tuple[LegacyPerkDef, ...]:
    items: list[LegacyPerkDef] = []
    for pid, raw in config.BUSINESS_LEGACY_PERKS.items():
        desc_parts: list[str] = []
        if "offline_accrual_bonus" in raw:
            pct = int(float(raw["offline_accrual_bonus"]) * 100)
            desc_parts.append(f"+{pct}% offline accrual efficiency")
        if "extra_lab_slots" in raw:
            desc_parts.append(f"+{int(raw['extra_lab_slots'])} drug lab slot")
        if "action_duration_bonus_hours" in raw:
            hrs = int(raw["action_duration_bonus_hours"])
            desc_parts.append(f"+{hrs}h business PvP action duration")
        items.append(
            LegacyPerkDef(
                perk_id=pid,
                name=str(raw["name"]),
                emoji=str(raw["emoji"]),
                description=" · ".join(desc_parts),
            ),
        )
    return tuple(items)


LEGACY_PERKS: tuple[LegacyPerkDef, ...] = _build_catalog()
LEGACY_PERKS_BY_ID: dict[str, LegacyPerkDef] = {p.perk_id: p for p in LEGACY_PERKS}


def legacy_perk_by_id(perk_id: str) -> LegacyPerkDef | None:
    return LEGACY_PERKS_BY_ID.get(perk_id.strip().lower())


def extra_lab_slots_from_perks(perk_ids: set[str]) -> int:
    total = 0
    if "diversification" in perk_ids:
        total += int(config.BUSINESS_LEGACY_PERKS["diversification"]["extra_lab_slots"])
    return total


def offline_accrual_bonus_from_perks(perk_ids: set[str]) -> float:
    if "automation" in perk_ids:
        return float(config.BUSINESS_LEGACY_PERKS["automation"]["offline_accrual_bonus"])
    return 0.0


def action_duration_bonus_seconds(perk_ids: set[str]) -> int:
    if "hostile_takeover" in perk_ids:
        hrs = float(config.BUSINESS_LEGACY_PERKS["hostile_takeover"]["action_duration_bonus_hours"])
        return int(hrs * 3600)
    return 0
