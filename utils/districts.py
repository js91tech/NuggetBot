"""Business districts: placement bonuses, deeds, and influence competition.

Districts are server-wide locations a business can relocate to for an income
bonus. Exclusive deeds let one player own each district; others may still
relocate there as tenants (half the placement bonus, rent to the owner).
Influence remains a competitive 0-100 metric for Market Expansion / wars.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import config

ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "districts"


def district_image_path(district_id: str | None) -> Path | None:
    if not district_id:
        return None
    path = ASSET_DIR / f"{district_id}.png"
    return path if path.is_file() else None


@dataclass(frozen=True, slots=True)
class DistrictDef:
    district_id: str
    name: str
    emoji: str
    income_mult: float
    label: str


# Each district resolves to an effective income multiplier. Flavor (traffic vs.
# tourism vs. lower costs) is captured in the label; the net effect is income.
DISTRICT_MAP: dict[str, DistrictDef] = {
    "downtown": DistrictDef(
        "downtown", "Downtown", "🏙️", 1.20, "+20% customer traffic",
    ),
    "financial": DistrictDef(
        "financial", "Financial District", "🏦", 1.25, "+25% business income",
    ),
    "industrial": DistrictDef(
        "industrial", "Industrial Zone", "🏭", 1.30, "+30% production efficiency",
    ),
    "beachfront": DistrictDef(
        "beachfront", "Beachfront", "🏖️", 1.15, "+15% tourism income",
    ),
    "residential": DistrictDef(
        "residential", "Residential District", "🏘️", 1.10, "-10% operating costs",
    ),
}

DISTRICT_IDS: tuple[str, ...] = tuple(DISTRICT_MAP.keys())


def district_by_id(district_id: str | None) -> DistrictDef | None:
    if not district_id:
        return None
    return DISTRICT_MAP.get(district_id.strip().lower())


def district_income_mult(district_id: str | None) -> float:
    defn = district_by_id(district_id)
    return defn.income_mult if defn is not None else 1.0


def effective_district_mult(district_id: str | None, *, is_owner: bool) -> float:
    """Placement mult for deed owner (full) or tenant (half of the bonus)."""
    base = district_income_mult(district_id)
    if base <= 1.0 or is_owner:
        return base
    return 1.0 + (base - 1.0) * float(config.DISTRICT_TENANT_MULT_SHARE)


def deed_claim_cost(district_id: str) -> float:
    factor = float(config.DISTRICT_DEED_FACTORS.get(district_id, 1.0))
    return round(float(config.DISTRICT_DEED_CLAIM_BASE) * factor, 2)


def relocate_cost(tier: int) -> float:
    """Relocation fee scales with business tier so it stays meaningful."""
    return config.BUSINESS_DISTRICT_RELOCATE_BASE_COST * max(1, int(tier))


def district_bonus_hourly(
    *,
    base_hourly_no_district: float,
    district_id: str | None,
    is_owner: bool = True,
) -> float:
    """Hourly value of the district placement bonus alone."""
    if not district_id:
        return 0.0
    mult = effective_district_mult(district_id, is_owner=is_owner)
    return max(0.0, base_hourly_no_district * mult - base_hourly_no_district)


def buyout_payout(bonus_hourly: float) -> tuple[float, float, float]:
    """Return (owner_receives, burn_amount, buyer_pays) for a hostile buyout."""
    days = float(config.DISTRICT_BUYOUT_DAYS)
    burn_rate = float(config.DISTRICT_BUYOUT_BURN)
    owner_receives = max(0.0, float(bonus_hourly) * 24.0 * days)
    burn_amount = owner_receives * burn_rate
    buyer_pays = owner_receives + burn_amount
    return owner_receives, burn_amount, buyer_pays


def apply_buyout_influence_discount(
    owner_receives: float,
    burn_amount: float,
    buyer_pays: float,
    buyer_influence: float,
) -> tuple[float, float, float, bool]:
    """Reduce burn (and thus buyer_pays) when buyer holds enough influence.

    Returns (owner_receives, burn, buyer_pays, discounted).
    """
    threshold = float(config.DISTRICT_BUYOUT_INFLUENCE_DISCOUNT_THRESHOLD)
    discount = float(config.DISTRICT_BUYOUT_INFLUENCE_DISCOUNT)
    if buyer_influence < threshold or discount <= 0:
        return owner_receives, burn_amount, buyer_pays, False
    burn = burn_amount * (1.0 - discount)
    pays = owner_receives + burn
    return owner_receives, burn, pays, True


def format_influence_race_line(your_score: float, leader_score: float, leader_name: str) -> str:
    """Short race status for the district war board."""
    if leader_score <= 0 and your_score <= 0:
        return "no crew race yet"
    if your_score >= leader_score and your_score > 0:
        return f"your crew leads at **{int(your_score)}**"
    gap = max(0.0, leader_score - your_score)
    return f"your crew **{int(your_score)}** · #{1} **{leader_name}** **{int(leader_score)}** (−{int(gap)})"
