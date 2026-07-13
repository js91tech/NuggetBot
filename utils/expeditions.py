from __future__ import annotations

import time
from dataclasses import dataclass

import config
from utils.helpers import fmt_amount


@dataclass(frozen=True)
class ExpeditionTemplate:
    expedition_id: str
    name: str
    description: str
    goal_scrap: int
    goal_nuggets: float
    duration_hours: float


EXPEDITION_TEMPLATES: tuple[ExpeditionTemplate, ...] = (
    ExpeditionTemplate(
        "rebuild_docks",
        "Rebuild the Docks",
        "Contribute scrap and nuggets to restore the harbor.",
        500, 25_000.0, 48.0,
    ),
    ExpeditionTemplate(
        "citadel_restoration",
        "Citadel Restoration",
        "Fund repairs to the endgame citadel.",
        800, 50_000.0, 48.0,
    ),
    ExpeditionTemplate(
        "festival_prep",
        "Festival Preparation",
        "Stockpile resources for a server-wide festival boss.",
        350, 15_000.0, 36.0,
    ),
)

EXPEDITION_REWARDS = {
    "income_buff": 1.10,
    "income_duration_hours": 24.0,
    "token_reward": 15,
    "relic_drop": True,
}


def scale_expedition_goal(base_scrap: int, active_players: int) -> int:
    mult = max(1.0, active_players / 10.0)
    return int(base_scrap * mult)


def expedition_progress_pct(contributed_scrap: int, contributed_nuggets: float, template: ExpeditionTemplate) -> float:
    scrap_pct = min(1.0, contributed_scrap / max(1, template.goal_scrap))
    nugget_pct = min(1.0, contributed_nuggets / max(1.0, template.goal_nuggets))
    return round(100.0 * (scrap_pct * 0.6 + nugget_pct * 0.4), 1)


def format_expedition_status(
    template: ExpeditionTemplate,
    contributed_scrap: int,
    contributed_nuggets: float,
    ends_at: float,
) -> str:
    pct = expedition_progress_pct(contributed_scrap, contributed_nuggets, template)
    remaining = max(0, int(ends_at - time.time()))
    return (
        f"**{template.name}** — {pct:.0f}% complete\n"
        f"_{template.description}_\n"
        f"Scrap: **{contributed_scrap}/{template.goal_scrap}** · "
        f"Nuggets: **{fmt_amount(contributed_nuggets)}/{fmt_amount(template.goal_nuggets)}**\n"
        f"Ends <t:{int(ends_at)}:R> ({remaining // 3600}h left)"
    )
