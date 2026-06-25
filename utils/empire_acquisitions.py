"""Post-mega acquisition targets with unique passive perks."""
from __future__ import annotations

from dataclasses import dataclass

import config


@dataclass(frozen=True, slots=True)
class AcquisitionDef:
    acquisition_id: str
    name: str
    emoji: str
    cost: float
    perk_label: str


def _build_catalog() -> tuple[AcquisitionDef, ...]:
    items: list[AcquisitionDef] = []
    for aid, raw in config.EMPIRE_ACQUISITIONS.items():
        labels: list[str] = []
        if "reputation_bonus_factor" in raw:
            pct = int(float(raw["reputation_bonus_factor"]) * 100)
            labels.append(f"+{pct}% reputation upgrade effectiveness")
        if "security_bonus" in raw:
            labels.append(f"+{int(raw['security_bonus'])} security rating")
        if "attack_duration_reduction" in raw:
            pct = int(float(raw["attack_duration_reduction"]) * 100)
            labels.append(f"−{pct}% rival attack duration")
        if "drug_grow_time_reduction" in raw:
            pct = int(float(raw["drug_grow_time_reduction"]) * 100)
            labels.append(f"−{pct}% drug grow time")
        items.append(
            AcquisitionDef(
                acquisition_id=aid,
                name=str(raw["name"]),
                emoji=str(raw["emoji"]),
                cost=float(raw["cost"]),
                perk_label=" · ".join(labels) if labels else "Unique passive perk",
            ),
        )
    return tuple(items)


ACQUISITIONS: tuple[AcquisitionDef, ...] = _build_catalog()
ACQUISITIONS_BY_ID: dict[str, AcquisitionDef] = {a.acquisition_id: a for a in ACQUISITIONS}


def acquisition_by_id(acquisition_id: str) -> AcquisitionDef | None:
    return ACQUISITIONS_BY_ID.get(acquisition_id.strip().lower())
