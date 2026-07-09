from __future__ import annotations

import random
from pathlib import Path

import config

SAKUNAS_FINGER_ITEM_ID = "sakunas_finger"
SAKUNAS_FINGER_GIF_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "consumables" / "sakunas_finger.gif"
)


def roll_sakuna_deflect() -> bool:
    return random.random() < config.SAKUNAS_FINGER_DEFLECT_CHANCE


def sakuna_domain_art() -> Path | str | None:
    """Local GIF takes precedence; otherwise optional config URL."""
    if SAKUNAS_FINGER_GIF_PATH.is_file():
        return SAKUNAS_FINGER_GIF_PATH
    url = str(getattr(config, "SAKUNAS_FINGER_GIF_URL", "") or "").strip()
    if url:
        return url
    return None
