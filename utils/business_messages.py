"""Business command error messages."""
from __future__ import annotations

import config

DEFEND_ERROR_MESSAGES: dict[str, str] = {
    "no_attack": (
        f"No active attack to defend right now. You have "
        f"**{config.BUSINESS_DEFENSE_WINDOW_SECONDS // 60} minutes** after an attack "
        "to respond with **Defend**."
    ),
}


def defend_error_message(code: str) -> str:
    return DEFEND_ERROR_MESSAGES.get(code, "Could not defend right now.")
