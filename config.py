from __future__ import annotations

import os
from dataclasses import dataclass
from math import isfinite
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _default_database_path() -> str:
    volume_path = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if volume_path:
        return str(Path(volume_path) / "nuggetbot.sqlite3")
    if Path("/data").exists():
        return "/data/nuggetbot.sqlite3"
    return "nuggetbot.sqlite3"


DATABASE_URL = os.getenv("DATABASE_URL", "")
DATABASE_PUBLIC_URL = os.getenv("DATABASE_PUBLIC_URL", "")
DATABASE_PATH = os.getenv("DATABASE_PATH") or _default_database_path()
ALLOW_SQLITE_ON_RAILWAY = os.getenv("ALLOW_SQLITE_ON_RAILWAY", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
RUNNING_ON_RAILWAY = any(
    os.getenv(name)
    for name in (
        "RAILWAY_ENVIRONMENT",
        "RAILWAY_PROJECT_ID",
        "RAILWAY_SERVICE_ID",
        "RAILWAY_DEPLOYMENT_ID",
    )
)
GUILD_ID = int(os.environ["GUILD_ID"]) if os.getenv("GUILD_ID") else None

DASHBOARD_ENABLED = os.getenv("DASHBOARD_ENABLED", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "0.0.0.0")
DASHBOARD_PORT = int(os.getenv("PORT") or os.getenv("DASHBOARD_PORT", "8080"))
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "")
DASHBOARD_COOKIE_NAME = "nuggetbot_dashboard"

CURRENCY_NAME = "nuggets"
CURRENCY_EMOJI = "🍘"

PASSIVE_CHAT_REWARD = 0.5
PASSIVE_ACTIVE_BONUS = 15.0
VOICE_CHAT_REWARD = 3.0
DAILY_REWARD = 75.0
DAILY_COOLDOWN_SECONDS = 24 * 60 * 60

BOUNTY_MIN_AMOUNT = 50.0
BOUNTY_TAX = 5.0
BOUNTY_TRIGGER_MAX_LENGTH = 32

HEIST_BASE_SUCCESS = 0.20
HEIST_CREW_BONUS = 0.10
HEIST_MAX_SUCCESS = 0.80
HEIST_LOOT_FRACTION = 0.20
HEIST_COOLDOWN_SECONDS = 30 * 60
HEIST_ARREST_WINDOW_SECONDS = 5 * 60
HEIST_ARREST_SECONDS = 60 * 60

HACK_VIRUS_NAME = "hannah hentai hanta virus"
HACK_BASE_PENALTY = 15.0
HACK_PASS_PENALTY = 2.0
HACK_TRANSFER_SECONDS = 60
HACK_COOLDOWN_SECONDS = 5 * 60

LAUNCH_GRANT_JOB_ID = "2026-05-launch-grant-1388136234827649116"
LAUNCH_GRANT_ENABLED = os.getenv("LAUNCH_GRANT_ENABLED", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LAUNCH_GRANT_GUILD_ID = 1388136234827649116
LAUNCH_GRANT_AMOUNT = 150.0
LAUNCH_GRANT_WEAPON_ID = "training_stick"
LAUNCH_GRANT_ARMOR_ID = "cardboard_shield"

BOSS_AUTO_SPAWN_SECONDS = 2 * 60 * 60
BOSS_MIN_HP = 500.0
BOSS_CIRCULATION_HP_FACTOR = 0.02
BOSS_HP_CAP = 15_000.0
BOSS_ATTACK_BONUS_MIN = 1
BOSS_ATTACK_BONUS_MAX = 5
BOSS_UNARMED_MIN = 1
BOSS_UNARMED_MAX = 15
PLAYER_ATTACK_CRIT_MULTIPLIER = 2.0
PLAYER_BASE_CRIT_CHANCE = 0.03
BOSS_DOWN_SECONDS = 2 * 60
PLAYER_BASE_HP = 100
BOSS_VARIANTS = {
    "normal": {"multiplier": 1.0, "counter_chance": 0.08, "threat": 1, "counter_damage": (10, 22), "crit_chance": 0.05},
    "enraged": {"multiplier": 1.5, "counter_chance": 0.12, "threat": 2, "counter_damage": (18, 36), "crit_chance": 0.08},
    "shadow": {"multiplier": 2.0, "counter_chance": 0.16, "threat": 3, "counter_damage": (30, 55), "crit_chance": 0.12},
    "celestial": {"multiplier": 3.0, "counter_chance": 0.20, "threat": 4, "counter_damage": (45, 80), "crit_chance": 0.16},
}

IMPOSTER_CHANCE = 0.01
IMPOSTER_MIN_WORDS = 3
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_API_URL = os.getenv("AI_API_URL", "https://api.openai.com/v1/chat/completions")
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o-mini")
AI_TIMEOUT_SECONDS = 8

TRIVIA_REWARD = 25.0
TRIVIA_SECONDS = 30
TRIVIA_MAX_CHANNELS = 10
TRIVIA_HISTORY_DAYS = 45
TRIVIA_MESSAGES_PER_CHANNEL = 50


@dataclass(frozen=True)
class LiveSetting:
    default: float
    description: str
    minimum: float = 0.0
    maximum: float | None = None
    integer: bool = False

    def validate(self, value: float) -> float:
        if not isfinite(value):
            msg = "must be a finite number"
            raise ValueError(msg)
        if value < self.minimum:
            msg = f"must be at least {self.minimum:g}"
            raise ValueError(msg)
        if self.maximum is not None and value > self.maximum:
            msg = f"must be no more than {self.maximum:g}"
            raise ValueError(msg)
        if self.integer and not value.is_integer():
            msg = "must be a whole number"
            raise ValueError(msg)
        return int(value) if self.integer else value


LIVE_SETTINGS: dict[str, LiveSetting] = {
    "passive_chat_reward": LiveSetting(PASSIVE_CHAT_REWARD, "Per-message earning"),
    "passive_active_bonus": LiveSetting(PASSIVE_ACTIVE_BONUS, "Per active hour earning"),
    "voice_chat_reward": LiveSetting(VOICE_CHAT_REWARD, "Per minute in VC"),
    "daily_reward": LiveSetting(DAILY_REWARD, "/daily claim amount"),
    "bounty_min_amount": LiveSetting(BOUNTY_MIN_AMOUNT, "Minimum bounty", minimum=0.01),
    "bounty_bot_tax": LiveSetting(BOUNTY_TAX, "Bot tax on bounties"),
    "heist_base_success": LiveSetting(
        HEIST_BASE_SUCCESS,
        "Heist success rate",
        maximum=HEIST_MAX_SUCCESS,
    ),
    "heist_cooldown_seconds": LiveSetting(
        HEIST_COOLDOWN_SECONDS,
        "Heist cooldown",
        integer=True,
    ),
    "arrest_lockout_seconds": LiveSetting(
        HEIST_ARREST_SECONDS,
        "Arrest lockout duration",
        integer=True,
    ),
    "hack_timer_seconds": LiveSetting(HACK_TRANSFER_SECONDS, "Hot potato timer", minimum=1, integer=True),
    "hack_base_penalty": LiveSetting(HACK_BASE_PENALTY, "Starting virus penalty"),
    "hack_penalty_increment": LiveSetting(HACK_PASS_PENALTY, "Penalty increase per pass"),
    "hack_cooldown_seconds": LiveSetting(
        HACK_COOLDOWN_SECONDS,
        "/hack user cooldown",
        integer=True,
    ),
    "boss_health_scale_factor": LiveSetting(BOSS_CIRCULATION_HP_FACTOR, "Boss HP scaling"),
    "boss_downed_seconds": LiveSetting(BOSS_DOWN_SECONDS, "Boss downed duration", minimum=1, integer=True),
    "imposter_chance": LiveSetting(IMPOSTER_CHANCE, "Per-message sabotage chance", maximum=1.0),
    "trivia_reward": LiveSetting(TRIVIA_REWARD, "Trivia answer reward"),
}


def live_setting_default(name: str) -> float:
    return LIVE_SETTINGS[name].default
