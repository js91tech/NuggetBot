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
# Fraction of max HP removed per real-time minute while a boss is active (passive anti-stall).
BOSS_PASSIVE_HP_DECAY_FRACTION_PER_MINUTE = 0.01
# How often to run the passive decay job (decay math still uses whole minutes).
BOSS_PASSIVE_DECAY_TICK_SECONDS = 120
BOSS_INFERIOR_DROP_CHANCE = 0.38
BOSS_EPIC_DROP_CHANCE = 0.03
BOSS_MYTHIC_DROP_CHANCE = 0.015
# Minimum spacing between automated channel posts (coin drops, boss embeds, etc.).
DISCORD_OUTBOUND_MIN_INTERVAL_SEC = 1.25
# Pause between guilds in background loops that may post to Discord.
BACKGROUND_GUILD_PAUSE_SECONDS = 1.0
# Backoff when login/start hits a global 429 (seconds per attempt).
DISCORD_LOGIN_BACKOFF_SECONDS: tuple[int, ...] = (60, 120, 300, 600, 900)
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
# /summon caller only: player atk and def operate at this fraction (60% debuff → 40% retained).
SUMMONER_DEBUFF_STAT_RETENTION = 0.4
PLAYER_BASE_HP = 100
BOSS_VARIANTS = {
    "normal": {"multiplier": 1.0, "counter_chance": 0.08, "threat": 1, "counter_damage": (10, 22), "crit_chance": 0.05},
    "enraged": {"multiplier": 1.5, "counter_chance": 0.12, "threat": 2, "counter_damage": (18, 36), "crit_chance": 0.08},
    "shadow": {"multiplier": 2.0, "counter_chance": 0.16, "threat": 3, "counter_damage": (30, 55), "crit_chance": 0.12},
    "celestial": {"multiplier": 3.0, "counter_chance": 0.20, "threat": 4, "counter_damage": (45, 80), "crit_chance": 0.16},
    "mythic": {"multiplier": 4.5, "counter_chance": 0.24, "threat": 5, "counter_damage": (60, 105), "crit_chance": 0.20},
    "tomass": {
        "multiplier": 1.5 * 1.75,
        "counter_chance": 0.14,
        "threat": 3,
        "counter_damage": (22, 42),
        "crit_chance": 0.10,
        "heal_every_attacks": 3,
        "heal_amount_cap": 1000,
        "mirrored_strength_mult": 1.75,
    },
}

BOSS_NAME_TOMASS = "TomAss"
BOSS_AUTO_SPAWN_TOMASS_CHANCE = 0.03
HANNAH_SPAWN_VARIANTS: tuple[str, ...] = ("normal", "enraged", "shadow", "celestial", "mythic")

BOSS_ELEMENTS: tuple[str, ...] = ("fire", "frost", "storm", "void", "verdant")
BOSS_ELEMENT_STRONG_BONUS = 0.12
BOSS_ELEMENT_WEAK_PENALTY = 0.08
# fire beats frost beats storm beats verdant beats void beats fire
BOSS_ELEMENT_BEATS: dict[str, str] = {
    "fire": "frost",
    "frost": "storm",
    "storm": "verdant",
    "verdant": "void",
    "void": "fire",
}

JOB_PAYOUT_MULTIPLIER = 4.5

CLASS_XP_DUEL_WIN = 40
CLASS_XP_DUEL_LOSS = 15
CLASS_XP_PER_BOSS_DAMAGE = 0.05
CLASS_XP_EVOLVE_TIER2 = 500
CLASS_XP_EVOLVE_TIER3 = 2000

PVP_ROLE_ADVANTAGE_BONUS = 0.08
PVP_ROLE_DISADVANTAGE_PENALTY = 0.05
PVP_SAME_ELEMENT_BONUS = 0.03

JESTER_EXCLUSIVE_USER_ID = 1323599263753834557
JESTER_CLASS_ID = "jester"
JESTER_STAT_MULT = 0.4
JESTER_REFLECT_CHANCE = 0.10
JESTER_CRIT_BONUS = 0.50
JESTER_WALLET_STEAL_FRACTION = 0.03

# Mana: non-healers rely on % of damage dealt; healers passively regen over time.
MANA_BASE_CAP = 100
MANA_REGEN_INTERVAL_SECONDS = 45
MANA_REGEN_PER_TICK = 2
MANA_ON_DAMAGE_PCT = 0.18
MANA_HEALER_REGEN_INTERVAL_SECONDS = 20
MANA_HEALER_REGEN_PER_TICK = 7
MANA_HEALER_ON_DAMAGE_PCT = 0.06
PENDING_SPELL_SECONDS = 90

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

PRESTIGE_MIN_WALLET = 100_000.0
PRESTIGE_MAX_LEVEL = 10
PRESTIGE_CRIT_BONUS_PER_LEVEL = 0.01
PRESTIGE_INCOME_BONUS_PER_LEVEL = 0.02

SET_DAMAGE_BONUS = 0.05
SET_MITIGATION_BONUS = 0.03

# Off-hand (second weapon) contributes a fraction of its stats to the primary attack.
OFF_HAND_DAMAGE_FACTOR = 0.40
OFF_HAND_CRIT_FACTOR = 0.50

HEIST_INTIMIDATION_PER_POWER = 0.0004
HEIST_INTIMIDATION_CAP = 0.10

HACK_WALLET_SHIELD_MAX = 0.25
HACK_WALLET_SHIELD_SCALE = 0.08

BOSS_PHASE_THRESHOLDS: tuple[float, ...] = (0.75, 0.50, 0.25)
BOSS_PHASE_ENRAGE_BONUS = 0.05
HEALER_SELF_REWARD = 100.0
HEALER_ALLY_REWARD = 1000.0

CRAFT_UPGRADE_COST_FACTOR = 0.45

GAMBLING_MIN_BET = 10.0
GAMBLING_MAX_BET = 50_000.0
GAMBLING_HOUSE_TAX = 0.05

DUEL_LOSS_FRACTION = 0.10
DUEL_SAME_TARGET_COOLDOWN_SECONDS = 40 * 60
DUEL_MAX_ATTACKS_PER_HOUR = 3
DUEL_MAX_COMBAT_ROUNDS = 25

# Job shifts consume energy; energy refills on a fixed timer up to a cap.
ENERGY_BASE_CAP = 30
ENERGY_CAP_PER_UPGRADE = 15
ENERGY_UPGRADE_COST = 20_000.0
ENERGY_REGEN_INTERVAL_SECONDS = 5 * 60
ENERGY_REGEN_PER_TICK = 5
ENERGY_WORK_COST_DEFAULT = 10

SEASONAL_EVENT_TYPES: tuple[str, ...] = (
    "double_drops",
    "bonus_income",
    "festival_boss",
    "trivia_fiesta",
)


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
    "boss_inferior_drop_chance": LiveSetting(
        BOSS_INFERIOR_DROP_CHANCE,
        "Battle-worn boss drop chance",
        maximum=1.0,
    ),
    "boss_epic_drop_chance": LiveSetting(
        BOSS_EPIC_DROP_CHANCE,
        "Epic boss drop chance",
        maximum=1.0,
    ),
    "boss_mythic_drop_chance": LiveSetting(
        BOSS_MYTHIC_DROP_CHANCE,
        "Mythic boss drop chance",
        maximum=1.0,
    ),
    "craft_upgrade_cost_factor": LiveSetting(
        CRAFT_UPGRADE_COST_FACTOR,
        "Craft upgrade cost multiplier",
        minimum=0.01,
        maximum=2.0,
    ),
    "prestige_min_wallet": LiveSetting(
        PRESTIGE_MIN_WALLET,
        "Minimum wallet to prestige",
        minimum=1000.0,
    ),
    "gambling_house_tax": LiveSetting(
        GAMBLING_HOUSE_TAX,
        "Tax on gambling winnings",
        maximum=0.5,
    ),
    "duel_loss_fraction": LiveSetting(
        DUEL_LOSS_FRACTION,
        "Loser wallet % paid to winner",
        minimum=0.01,
        maximum=0.5,
    ),
    "duel_same_target_cooldown_seconds": LiveSetting(
        DUEL_SAME_TARGET_COOLDOWN_SECONDS,
        "Cooldown before re-attacking same player",
        minimum=60,
        integer=True,
    ),
    "duel_max_attacks_per_hour": LiveSetting(
        DUEL_MAX_ATTACKS_PER_HOUR,
        "Max duels started per hour",
        minimum=1,
        maximum=20,
        integer=True,
    ),
    "energy_regen_per_tick": LiveSetting(
        ENERGY_REGEN_PER_TICK,
        "Energy restored each 5-minute tick",
        minimum=1,
        maximum=50,
        integer=True,
    ),
    "energy_regen_interval_seconds": LiveSetting(
        ENERGY_REGEN_INTERVAL_SECONDS,
        "Seconds between energy regen ticks",
        minimum=60,
        integer=True,
    ),
}


def live_setting_default(name: str) -> float:
    return LIVE_SETTINGS[name].default


ECONOMY_TUNING_SETTINGS: tuple[str, ...] = (
    "boss_inferior_drop_chance",
    "boss_epic_drop_chance",
    "boss_mythic_drop_chance",
    "craft_upgrade_cost_factor",
    "prestige_min_wallet",
    "gambling_house_tax",
    "passive_chat_reward",
    "daily_reward",
)

DUEL_TUNING_SETTINGS: tuple[str, ...] = (
    "duel_loss_fraction",
    "duel_same_target_cooldown_seconds",
    "duel_max_attacks_per_hour",
)

DASHBOARD_SLIDER_SETTINGS: tuple[str, ...] = ECONOMY_TUNING_SETTINGS + DUEL_TUNING_SETTINGS
