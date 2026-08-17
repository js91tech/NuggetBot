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
        return str(Path(volume_path) / "goonbot.sqlite3")
    if Path("/data").exists():
        return "/data/goonbot.sqlite3"
    return "goonbot.sqlite3"


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
DASHBOARD_COOKIE_NAME = "goonbot_dashboard"

BOT_DISPLAY_NAME = "GoonBot"
CURRENCY_NAME = "goonbux"
CURRENCY_EMOJI = "💋"

PASSIVE_CHAT_REWARD = 0.5
PASSIVE_ACTIVE_BONUS = 15.0
VOICE_CHAT_REWARD = 3.0
DAILY_REWARD = 125_000.0
DAILY_COOLDOWN_SECONDS = 24 * 60 * 60
DAILY_STREAK_MAX_DAYS = 14
DAILY_STREAK_BONUS_PER_DAY = 0.05  # +5% daily reward per streak day (max +65%)
DAILY_STREAK_GRACE_SECONDS = 24 * 60 * 60  # extra window after cooldown to keep streak

# Activity leveling (chat/VC/raids — separate from class XP)
ACTIVITY_XP_PER_MESSAGE = 12
ACTIVITY_XP_PER_VC_TICK = 20
ACTIVITY_XP_PER_BOSS_DAMAGE = 1  # per damage dealt, capped per hit
ACTIVITY_XP_BOSS_DAMAGE_CAP = 500
ACTIVITY_LEVEL_XP_BASE = 80
ACTIVITY_LEVEL_XP_GROWTH = 1.28
ACTIVITY_LEVEL_MAX = 100
ACTIVITY_ROLE_MILESTONES: tuple[int, ...] = (5, 10, 25, 50)
ACTIVITY_ROLE_NAMES: dict[int, str] = {
    5: "Street Regular",
    10: "Connected",
    25: "Veteran",
    50: "Legend",
}

# Player trades
TRADE_EXPIRE_SECONDS = 300
TRADE_MAX_DRUG_TYPES = 5
TRADE_MAX_GEAR_INSTANCES = 5

# Opt-in DM reminders (see utils/notify_prefs.py)
NOTIFY_TICK_SECONDS = 90
NOTIFY_CROPS = 1
NOTIFY_BOSS = 2
NOTIFY_BUSINESS = 4
NOTIFY_DEFENSE = 8
NOTIFY_USER_CONFIGURED = 16
NOTIFY_CATEGORY_MASK = NOTIFY_CROPS | NOTIFY_BOSS | NOTIFY_BUSINESS | NOTIFY_DEFENSE
NOTIFY_DEFAULT_FLAGS = 0
NOTIFY_ELIGIBLE_DEFAULT_FLAGS = NOTIFY_CATEGORY_MASK
NOTIFY_ACTIVE_MIN_XP = 1
NOTIFY_BUSINESS_FILL_PCT = 0.90

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

# Bank heist — high risk, steals from personal bank (not wallet heists).
BANK_HEIST_COOLDOWN_SECONDS = 60 * 60
BANK_HEIST_TIERS: dict[int, dict[str, float]] = {
    1: {"success": 0.10, "loot_fraction": 0.10, "jail_seconds": 120 * 60},
    2: {"success": 0.08, "loot_fraction": 0.20, "jail_seconds": 4 * 60 * 60},
    3: {"success": 0.05, "loot_fraction": 0.35, "jail_seconds": 12 * 60 * 60, "unstable_chance": 0.60},
}
GEAR_FIX_COST_FRACTION = 0.80

# Personal bank vault — base cap; buy expansions for +capacity each.
BANK_BASE_CAPACITY = 100_000.0
BANK_EXPANSION_TIERS: dict[int, dict[str, float | str]] = {
    1: {"name": "Standard", "cost": 10_000.0, "capacity": 10_000.0},
    2: {"name": "Reinforced", "cost": 50_000.0, "capacity": 50_000.0},
    3: {"name": "Fortified", "cost": 250_000.0, "capacity": 250_000.0},
    4: {"name": "Sovereign", "cost": 500_000.0, "capacity": 500_000.0},
}
# Backward-compatible aliases for tier 1 (Standard).
BANK_EXPANSION_CAPACITY_PER_TOKEN = float(BANK_EXPANSION_TIERS[1]["capacity"])
BANK_EXPANSION_TOKEN_COST = float(BANK_EXPANSION_TIERS[1]["cost"])

# Personal bank bodyguards (defend against /bank-heist).
BODYGUARD_MAX_TOTAL = 5
BODYGUARD_TIERS: dict[int, dict[str, float | str]] = {
    1: {"name": "Rookie", "cost": 10_000.0, "defense": 0.06},
    2: {"name": "Veteran", "cost": 25_000.0, "defense": 0.10},
    3: {"name": "Elite", "cost": 60_000.0, "defense": 0.16},
}
BODYGUARD_REFERENCE_POWER = 400
BODYGUARD_HEIST_TIER_TARGET: dict[int, float] = {1: 0.80, 2: 0.75, 3: 0.60}
BODYGUARD_NO_GEAR_FLOOR = 0.05
BODYGUARD_MAX_GEAR_NO_GUARDS = 0.95

PICK_KEY_ESCAPE_CHANCE = 0.15

# Bot Discord accounts can use commands and be PvP targets (passive chat/VC income stays off).
ALLOW_BOT_PLAYERS = True
ALLOW_BOT_PASSIVE_INCOME = False

HACK_VIRUS_NAME = "velvet vixen love virus"
HACK_BASE_PENALTY = 15.0
HACK_PASS_PENALTY = 2.0
HACK_TRANSFER_SECONDS = 60
HACK_COOLDOWN_SECONDS = 5 * 60


# Scourge virus world event (fixed interval + 7-minute outbreak).
SCOURGE_VIRUS_NAME = "Scourge Virus"
SCOURGE_INTERVAL_SECONDS = 8 * 60 * 60
SCOURGE_WARNING_SECONDS = 90
SCOURGE_ACTIVE_SECONDS = 7 * 60
SCOURGE_INFECTION_INTERVAL_SECONDS = 60
SCOURGE_INFECTIONS_PER_EVENT = 7
SCOURGE_PASS_SECONDS = 70
SCOURGE_BANK_PENALTY_MIN = 1_000.0
SCOURGE_BANK_PENALTY_MAX = 3_000.0
SCOURGE_TOP_TARGETS = 5
SCOURGE_EVENT_POLL_SECONDS = 30
SCOURGE_WARNING_GIF_PATH = os.getenv(
    "SCOURGE_WARNING_GIF_PATH",
    str(Path(__file__).resolve().parent / "assets" / "events" / "scourge_warning.gif"),
)
SCOURGE_WARNING_GIF_URL = os.getenv("SCOURGE_WARNING_GIF_URL", "").strip()

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

# Fixed auto-spawn interval when no boss is active (checked every BOSS_AUTO_SPAWN_POLL_SECONDS).
BOSS_AUTO_SPAWN_MIN_SECONDS = 90 * 60
BOSS_AUTO_SPAWN_MAX_SECONDS = 90 * 60
BOSS_AUTO_SPAWN_POLL_SECONDS = 60
# When a living boss blocks auto-spawn, retry after this many seconds (not the full 90m).
BOSS_AUTO_SPAWN_RETRY_SECONDS = 120
# Fraction of max HP removed per real-time minute while a boss is active (passive anti-stall).
BOSS_PASSIVE_HP_DECAY_FRACTION_PER_MINUTE = 0.01
# How often to run the passive decay job (decay math still uses whole minutes).
BOSS_PASSIVE_DECAY_TICK_SECONDS = 120
# Rebalanced: less battle-worn spam, more meaningful mid/high drops.
BOSS_INFERIOR_DROP_CHANCE = 0.18
BOSS_EPIC_DROP_CHANCE = 0.08
BOSS_MYTHIC_DROP_CHANCE = 0.025
BOSS_ASPECT_DROP_CHANCE = 0.15
BOSS_ACCESSORY_DROP_CHANCE = 0.10
BOSS_HARDENER_DROP_CHANCE = 0.12
BOSS_CELESTIAL_SHARD_DROP_CHANCE = 0.06
DUNGEON_ACCESSORY_DROP_CHANCE = 0.02
DUNGEON_VAULT_ACCESSORY_DROP_CHANCE = 0.05
BOSS_ADD_SPAWN_CHANCE = 0.06
BOSS_ADD_LIFETIME_SECONDS = 180
BOSS_ADD_MAX_CONCURRENT = 2
BOSS_ADD_SPAWN_MIN_HP_RATIO = 0.50
ASPECT_SHOP_PRICE = 25_000.0
ASPECT_MAX_EQUIP_SLOTS = 3
SHOP_MAX_BUY_QUANTITY = 99
SHOP_MAX_SELL_QUANTITY = 99
TRAP_BOMB_BASE_CHANCE = 0.08
TRAP_BOMB_PER_ITEM_CHANCE = 0.05
TRAP_BOMB_MAX_CHANCE = 0.75
TRAP_BOMB_DAMAGE = (75, 125)

SAKUNAS_FINGER_DEFLECT_CHANCE = 0.75
SAKUNAS_FINGER_DURATION_SECONDS = 6 * 3600
SAKUNAS_FINGER_WALLET_STEAL_FRACTION = 0.05
SAKUNAS_FINGER_BANK_STEAL_FRACTION = 0.07
# Optional hotlinked domain expansion GIF. Local assets/consumables/sakunas_finger.gif takes precedence.
SAKUNAS_FINGER_GIF_URL = ""
# Minimum spacing between automated channel posts (coin drops, boss embeds, etc.).
DISCORD_OUTBOUND_MIN_INTERVAL_SEC = 1.25
# Pause between guilds in background loops that may post to Discord.
BACKGROUND_GUILD_PAUSE_SECONDS = 1.0
# Backoff when login/start hits a global 429 (seconds per attempt).
DISCORD_LOGIN_BACKOFF_SECONDS: tuple[int, ...] = (60, 120, 300, 600, 900)
BOSS_MIN_HP = 500.0
BOSS_CIRCULATION_HP_FACTOR = 0.02
BOSS_HP_CAP = 40_000.0
BOSS_THREAT_HP_BONUS_PER_TIER = 0.10
BOSS_MYTHIC_DESPAWN_SECONDS = 12 * 60
BOSS_ULTRA_DESPAWN_SECONDS = 12 * 60
BOSS_ULTRA_SPAWN_CHANCE = 0.12
BOSS_AUTO_SPAWN_FREAKY_NIKKI_CHANCE = 0.15
BOSS_ATTACK_COOLDOWN_MIN_SECONDS = 2
BOSS_ATTACK_COOLDOWN_MAX_SECONDS = 3
# Warn the guild this many seconds before an auto-spawn is due.
BOSS_SPAWN_WARN_SECONDS = 10 * 60
# Participation floor: anyone with at least this much damage gets a purse + scrap.
BOSS_PARTICIPATION_MIN_DAMAGE = 50.0
BOSS_PARTICIPATION_PURSE = 2_500.0
BOSS_PARTICIPATION_SCRAP = (1, 3)
BOSS_FIRST_BLOOD_BONUS = 5_000.0
BOSS_LAST_HIT_BONUS = 7_500.0
BOSS_TOP_DAMAGER_LOOT_POOL: tuple[str, ...] = (
    "void_hardener",
    "alchemy_scrap",
    "raid_potion",
    "energy_drink",
)
BOSS_HEALER_PULSE_CHANCE = 0.30
BOSS_HEALER_PULSE_PCT = 0.08
BOSS_MOOD_ARMORED_DAMAGE_MULT = 0.85
BOSS_MOOD_AGGRESSIVE_COUNTER_MULT = 1.20
BOSS_MOOD_FRANTIC_COUNTER_MULT = 1.10
BOSS_CREW_MVP_BONUS = 10_000.0
# During world_boss_week, this fraction of auto-spawns become the world leviathan.
BOSS_WORLD_EVENT_SPAWN_CHANCE = 0.55
# Legacy default when a row has no stored per-attack cooldown.
BOSS_ATTACK_COOLDOWN_SECONDS = BOSS_ATTACK_COOLDOWN_MAX_SECONDS
BOSS_RAIDER_DAMAGE_MULT: dict[int, float] = {1: 0.65, 2: 0.80, 3: 0.95}
# Extra raid damage vs high-threat bosses so mythics can die before despawn.
BOSS_RAID_DAMAGE_BONUS_BY_THREAT: dict[int, float] = {
    4: 1.05,
    5: 1.12,
    6: 1.18,
}
BOSS_RAID_FATIGUE_SOLO_ATTACKS = 10
BOSS_RAID_FATIGUE_DAMAGE_MULT = 0.75
BOSS_ENRAGE_HP_THRESHOLD = 0.25
BOSS_ENRAGE_COUNTER_MULT = 1.35
BOSS_COUNTER_THREAT_SCALE = 0.12
BOSS_PASSIVE_DECAY_BY_THREAT: dict[int, float] = {
    1: 0.008,
    2: 0.007,
    3: 0.006,
    4: 0.005,
    5: 0.0035,
    6: 0.0025,
}
BOSS_REWARD_MULT_BY_THREAT: dict[int, float] = {
    1: 1.0,
    2: 1.15,
    3: 1.35,
    4: 1.60,
    5: 2.0,
    6: 3.5,
}
# Personal boss nugget slice: income_mult × business bonus, soft-capped.
BOSS_REWARD_BUSINESS_TIER_BONUS = 0.02
BOSS_REWARD_BUSINESS_PRESTIGE_BONUS = 0.015
BOSS_REWARD_PERSONAL_MULT_CAP = 1.50
BOSS_ULTRA_DROP_CHANCE = 0.05
BOSS_NAME_ZZ_WRATH = "ZZ's Wrath"
AUTO_POTION_THRESHOLDS: tuple[int, ...] = (25, 40, 50, 60, 75)
DUNGEON_PLAYER_DAMAGE_TAKEN_MULT = 0.6
BOSS_ATTACK_BONUS_MIN = 1
BOSS_ATTACK_BONUS_MAX = 5
BOSS_UNARMED_MIN = 1
BOSS_UNARMED_MAX = 15
PLAYER_ATTACK_CRIT_MULTIPLIER = 2.0
PLAYER_BASE_CRIT_CHANCE = 0.03
# Paid /summon (dashboard spawns skip cost and summoner penalties).
SUMMON_COST = 20_000.0
# Summoner penalties while their summoned boss is active (retention = 1 - debuff%).
SUMMONER_DEBUFF_ATK_DEF_RETENTION = 0.75
SUMMONER_DEBUFF_CRIT_RETENTION = 0.70
SUMMONER_DEBUFF_MANA_RETENTION = 0.20
SUMMONER_BOSS_COUNTER_MULTIPLIER = 2.0
PLAYER_BASE_HP = 100
BOSS_VARIANTS = {
    "normal": {"multiplier": 1.0, "counter_chance": 0.08, "threat": 1, "counter_damage": (10, 22), "crit_chance": 0.05},
    "enraged": {"multiplier": 1.5, "counter_chance": 0.12, "threat": 2, "counter_damage": (18, 36), "crit_chance": 0.08},
    "shadow": {"multiplier": 2.0, "counter_chance": 0.16, "threat": 3, "counter_damage": (30, 55), "crit_chance": 0.12},
    "celestial": {
        "multiplier": 3.0,
        "counter_chance": 0.20,
        "threat": 4,
        "counter_damage": (45, 80),
        "crit_chance": 0.16,
        "despawn_seconds": BOSS_ULTRA_DESPAWN_SECONDS,
    },
    "mythic": {
        "multiplier": 4.5,
        "counter_chance": 0.24,
        "threat": 5,
        "counter_damage": (60, 105),
        "crit_chance": 0.20,
        "despawn_seconds": BOSS_MYTHIC_DESPAWN_SECONDS,
    },
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
    "zz_wrath": {
        "fixed_hp": 40_000.0,
        "counter_chance": 0.28,
        "threat": 6,
        "counter_damage": (70, 120),
        "crit_chance": 0.22,
        "despawn_seconds": BOSS_ULTRA_DESPAWN_SECONDS,
    },
    "freaky_nikki": {
        "multiplier": 2.8,
        "counter_chance": 0.18,
        "threat": 4,
        "counter_damage": (32, 58),
        "crit_chance": 0.14,
    },
    "world_leviathan": {
        "fixed_hp": 55_000.0,
        "counter_chance": 0.26,
        "threat": 6,
        "counter_damage": (65, 110),
        "crit_chance": 0.20,
        "despawn_seconds": 15 * 60,
    },
}

BOSS_NAME_TOMASS = "TomAss"
BOSS_NAME_FREAKY_NIKKI = "Freaky Nikki"
BOSS_NAME_WORLD_LEVIATHAN = "World Leviathan"
BOSS_HUNT_ROTATION: tuple[dict, ...] = (
    {
        "hunt_key": "mythic_duo",
        "label": "Mythic Duo",
        "variant": "mythic",
        "kills_required": 2,
        "reward_nuggets": 50_000.0,
        "reward_item": "void_hardener",
        "reward_item_qty": 2,
    },
    {
        "hunt_key": "wrath_once",
        "label": "Wrath Protocol",
        "variant": "zz_wrath",
        "kills_required": 1,
        "reward_nuggets": 75_000.0,
        "reward_item": "celestial_shard",
        "reward_item_qty": 1,
    },
    {
        "hunt_key": "nikki_party",
        "label": "Nikki Night",
        "variant": "freaky_nikki",
        "kills_required": 3,
        "reward_nuggets": 40_000.0,
        "reward_item": "raid_potion",
        "reward_item_qty": 3,
    },
    {
        "hunt_key": "shadow_sweep",
        "label": "Shadow Sweep",
        "variant": "shadow",
        "kills_required": 4,
        "reward_nuggets": 35_000.0,
        "reward_item": "alchemy_scrap",
        "reward_item_qty": 8,
    },
    {
        "hunt_key": "leviathan_call",
        "label": "Leviathan Call",
        "variant": "world_leviathan",
        "kills_required": 1,
        "reward_nuggets": 100_000.0,
        "reward_item": "celestial_shard",
        "reward_item_qty": 2,
    },
)
FREAKY_NIKKI_SCRAP_RANGE = (2, 8)
FREAKY_NIKKI_CONSUMABLE_POOL: tuple[str, ...] = (
    "raid_potion",
    "energy_drink",
    "hp_potion_small",
    "hp_potion_medium",
    "trap_bomb",
)
FREAKY_NIKKI_CONSUMABLE_CHANCE = 0.75
FREAKY_NIKKI_CONSUMABLE_QTY_RANGE = (1, 2)
# Optional hotlinked moment art (moment key -> URL). Local files in assets/bosses/freaky_nikki/ take precedence when unset.
FREAKY_NIKKI_ART_URLS: dict[str, str] = {}
BOSS_AUTO_SPAWN_TOMASS_CHANCE = 0.03
BOSS_DISPLAY_NAME = "Velvet Vixen"
HANNAH_SPAWN_VARIANTS: tuple[str, ...] = ("normal", "enraged", "shadow", "celestial", "mythic")
# Dashboard summon dropdown — special bosses first, then Velvet Vixen tiers.
BOSS_DASHBOARD_VARIANT_ORDER: tuple[str, ...] = (
    "world_leviathan",
    "freaky_nikki",
    "zz_wrath",
    "tomass",
    "mythic",
    "celestial",
    "shadow",
    "enraged",
    "normal",
)

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

# Elemental counter procs (applied when the boss lands a counter on a raider).
BOSS_DEBUFF_MAX_SECONDS = 30.0
BOSS_DOWN_SECONDS = int(BOSS_DEBUFF_MAX_SECONDS)
BOSS_DEBUFF_DURATION_BASE_SECONDS = (6, 10)
BOSS_DEBUFF_DURATION_PER_TIER_SECONDS = (0, 1)
BOSS_DEBUFF_ATTACK_COOLDOWN_SECONDS = (2, 3)
BOSS_FROST_PROC_CHANCE = 0.35
BOSS_FROST_SLOW_SECONDS = BOSS_DEBUFF_DURATION_BASE_SECONDS
BOSS_FROST_EXTRA_ATTACK_COOLDOWN = 0

BOSS_FIRE_BURN_PROC_CHANCE = 0.30
BOSS_FIRE_BURN_TICKS = 4
BOSS_FIRE_BURN_INTERVAL_SECONDS = 5
BOSS_FIRE_BURN_DAMAGE = (8, 18)

BOSS_STORM_STUN_PROC_CHANCE = 0.25
BOSS_STORM_STUN_SECONDS = BOSS_DEBUFF_DURATION_BASE_SECONDS

BOSS_VOID_DRAIN_PROC_CHANCE = 0.30
BOSS_VOID_MANA_DRAIN = (12, 28)

BOSS_VERDANT_ROOT_PROC_CHANCE = 0.30
BOSS_VERDANT_ROOT_SECONDS = BOSS_DEBUFF_DURATION_BASE_SECONDS
BOSS_VERDANT_EXTRA_ATTACK_COOLDOWN = 0

# Character attributes (STR/DEX/AGI/DEF/VIT) — earned from class XP, reduce debuffs and boost combat.
ATTR_BASE_VALUE = 0
ATTR_STAT_CAP_BASE = 15
ATTR_STAT_CAP_PER_PRESTIGE = 1
ATTR_BASE_TOTAL_POINTS = 50
ATTR_TOTAL_POINTS_PER_PRESTIGE = 5
ATTR_FAST_POINT_COUNT = 20
ATTR_XP_PER_FAST_POINT = 50
ATTR_XP_PER_SLOW_POINT = 175
ATTR_MIN_DEBUFF_SECONDS = 2.0
# Per allocated point (stats start at 0):
ATTR_STR_DAMAGE_PCT = 0.015
ATTR_DEX_CRIT_PCT = 0.0075
ATTR_AGI_CC_DURATION_PCT = 0.02
ATTR_AGI_CC_PROC_RESIST_PCT = 0.01
ATTR_AGI_ATTACK_CD_PCT = 0.008
ATTR_DEF_MITIGATION_PCT = 0.0075
ATTR_DEF_DOT_RESIST_PCT = 0.01
ATTR_VIT_HP_PER_POINT = 4
ATTR_MAX_CC_DURATION_REDUCTION = 0.75
ATTR_MAX_CC_PROC_RESIST = 0.50
ATTR_MAX_DOT_RESIST = 0.50
ATTR_MAX_DEF_MITIGATION_BONUS = 0.15
ATTR_MAX_DEX_CRIT_BONUS = 0.30

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
JESTER_REFLECT_CHANCE = 0.50
JESTER_WALLET_STEAL_FRACTION = 0.03

# Silent combat power (not shown on /stats).
SILENT_POWER_USER_ID = 235947194174144513
SILENT_POWER_DAMAGE_MULT = 1.15
SILENT_POWER_DEFENSE_MULT = 1.15

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
AVATAR_AI_GENERATION = os.getenv("AVATAR_AI_GENERATION", "true").lower() in ("1", "true", "yes")
AVATAR_IMAGE_API_URL = os.getenv("AVATAR_IMAGE_API_URL", "")
AVATAR_IMAGE_MODEL = os.getenv("AVATAR_IMAGE_MODEL", "dall-e-3")
AVATAR_IMAGE_SIZE = os.getenv("AVATAR_IMAGE_SIZE", "1024x1024")
AVATAR_AI_TIMEOUT_SECONDS = int(os.getenv("AVATAR_AI_TIMEOUT_SECONDS", "45"))

TRIVIA_REWARD = 20_000.0
TRIVIA_HOUSE_POOL_SHARE = 0.07
TRIVIA_SECONDS = 3 * 60
TRIVIA_MAX_CHANNELS = 10
TRIVIA_HISTORY_DAYS = 45
TRIVIA_MESSAGES_PER_CHANNEL = 50
TRIVIA_EVENT_INTERVAL_HOURS = 1
# Instant answers pay the max mult; answers near timeout pay the min mult.
TRIVIA_SPEED_MAX_MULT = 2.0
TRIVIA_SPEED_MIN_MULT = 0.4
# Base free-drug chance on a correct answer; scales up toward +bonus for fast solves.
TRIVIA_DRUG_CHANCE = 0.20
TRIVIA_DRUG_FAST_BONUS = 0.20

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

# Gear enhancement (BDO-style +1..+15, PRI..PENTA)
ENHANCE_MAX_LEVEL = 20
ENHANCE_SCRAP_MAX_LEVEL = 10
ENHANCE_HARDENER_MAX_LEVEL = 15
ENHANCE_NUGGET_COST_AT_PLUS_1 = 2_500.0
ENHANCE_NUGGET_COST_AT_PLUS_10 = 50_000.0
ENHANCE_NUGGET_COST_AT_PLUS_15 = 60_000.0
ENHANCE_NUGGET_COST_AT_PRI = 70_000.0
ENHANCE_NUGGET_COST_AT_PENTA = 150_000.0
ENHANCE_REPAIR_NUGGET_FACTOR = 0.10
ENHANCE_FAIL_DOWNGRADE_FROM = 8
ENHANCE_FAIL_BREAK_FROM = 12
ENHANCE_POWER_BONUS_PER_LEVEL = 0.02
ENHANCE_PRI_BONUS_MULT = 1.15

GAMBLING_MIN_BET = 10.0
GAMBLING_MAX_BET = 50_000.0
GAMBLING_HOUSE_TAX = 0.05

DUEL_LOSS_FRACTION = 0.10
DUEL_SAME_TARGET_COOLDOWN_SECONDS = 40 * 60
DUEL_MAX_ATTACKS_PER_HOUR = 3
DUEL_MAX_COMBAT_ROUNDS = 25
DUEL_ELO_START = 1000
DUEL_ELO_K_FACTOR = 32

JACKPOT_CONTRIBUTION_RATE = 0.02
JACKPOT_WIN_CHANCE_SLOTS = 0.004

SLOTS_MIN_BET = 10.0
SLOTS_MAX_BET = 25_000.0

DUNGEON_ROOMS = 5
DUNGEON_ENTRY_COST = 250.0
DUNGEON_ENERGY_COST = 25
DUNGEON_ROOM_REWARD_MIN = 1_000.0
DUNGEON_ROOM_REWARD_MAX = 2_000.0
DUNGEON_CLEAR_BONUS = 1_200.0
DUNGEON_PARTY_ENTRY_COST = 400.0
DUNGEON_PARTY_ENERGY_COST = 25
DUNGEON_SCRAP_PER_CLEAR = 2
DUNGEON_VAULT_UNLOCK_COST = 50_000.0
DUNGEON_VAULT_ROOM_REWARD_MIN = 3_000.0
DUNGEON_VAULT_ROOM_REWARD_MAX = 7_000.0
DUNGEON_VAULT_CLEAR_BONUS = 3_000.0
DUNGEON_VAULT_SCRAP_PER_CLEAR = 5
DUNGEON_VAULT_MIN_PARTY_SIZE = 3
DUNGEON_NORMAL_DIFFICULTY_MULT = 10
DUNGEON_VAULT_DIFFICULTY_MULT = 100

CUSTOM_AVATAR_UPLOAD_COST = 1_000.0
# Discord allows up to 8 MB per slash-command attachment; default to that cap.
CUSTOM_AVATAR_MAX_BYTES = int(
    os.getenv("CUSTOM_AVATAR_MAX_BYTES", str(8 * 1024 * 1024)),
)


CREW_LOAN_INTEREST_RATE = 0.10
CREW_LOAN_TERM_SECONDS = 7 * 24 * 60 * 60
CREW_LOAN_MIN_AMOUNT = 50.0
CREW_LOAN_MAX_ACTIVE = 1
CREW_LOAN_MAX_TREASURY_FRACTION = 0.25
CREW_LEVEL_LOAN_BONUS_PER_LEVEL = 0.02
CREW_HEIST_SAME_CREW_BONUS = 0.05
CREW_HEIST_SAME_CREW_BONUS_CAP = 0.15
CREW_XP_PER_LEVEL = 2500
CREW_LEVEL_CAP = 10
CREW_WITHDRAW_MIN = 0.01
CREW_BANK_RAID_MIN_MEMBERS = 5
CREW_BANK_RAID_BACKUP_COUNT = 2
CREW_BANK_RAID_LOOT_FRACTION = 0.10
CREW_BANK_RAID_MIN_TREASURY = 100.0
# Twice per hour (shared cadence for bank / drug / business raids).
CREW_BANK_RAID_ATTACK_COOLDOWN_SECONDS = 30 * 60
CREW_BANK_RAID_DEFENSE_COOLDOWN_SECONDS = 30 * 60
CREW_DRUG_RAID_MIN_STASH = 2
CREW_DRUG_RAID_LOOT_MIN = 2
CREW_DRUG_RAID_LOOT_MAX = 5
CREW_BUSINESS_RAID_LOOT_FRACTION = 0.10
CREW_BUSINESS_RAID_MIN_STORED = 100.0
CREW_DRUG_BUSINESS_RAID_MIN_MEMBERS = 3
CREW_DRUG_RAID_COOLDOWN_SECONDS = 30 * 60
CREW_BUSINESS_RAID_COOLDOWN_SECONDS = 30 * 60

# Territory control (crew-owned zones, hourly treasury income, guards, sieges)
TERRITORY_SIEGE_DURATION_SECONDS = 30 * 60
TERRITORY_SIEGE_COOLDOWN_SECONDS = 12 * 60 * 60
TERRITORY_MAX_HELD_PER_CREW = 3
TERRITORY_MIN_CREW_MEMBERS_TO_ATTACK = 2
TERRITORY_GUARD_COST_BASE = 75.0
TERRITORY_GUARD_COST_PER_TIER = 25.0
TERRITORY_GUARD_DEFENSE_BONUS = 0.04
TERRITORY_SIEGE_BASE_CHANCE = 0.28
TERRITORY_SIEGE_PER_MEMBER = 0.05
TERRITORY_SIEGE_ATTACK_CAP = 0.25
TERRITORY_SIEGE_MIN_CHANCE = 0.08
TERRITORY_SIEGE_MAX_CHANCE = 0.72
TERRITORY_INCOME_UNDER_SIEGE_MULT = 0.0
TERRITORY_HOURLY_TICK_SECONDS = 3600
TERRITORY_PERK_MARKET_SELL_BONUS = 0.05
TERRITORY_PERK_FOUNDRY_CRAFT_DISCOUNT = 0.05
TERRITORY_PERK_DOCKS_HEIST_LOOT = 0.05
TERRITORY_PERK_VAULT_HEIST_SUCCESS = 0.03
TERRITORY_PERK_CITADEL_INCOME_BONUS = 0.10

# --- Business Empire ---------------------------------------------------------
# Passive business income accrues into a capped store; players collect it with
# /business collect. All values are in nuggets.
BUSINESS_INCOME_TICK_SECONDS = 5 * 60
# Per-level multiplier contributions for the upgradeable attributes.
BUSINESS_EFFICIENCY_BONUS_PER_LEVEL = 0.05
BUSINESS_REPUTATION_BONUS_PER_LEVEL = 0.04
BUSINESS_PRODUCTION_BRANCH_BONUS_PER_LEVEL = 0.06
# Growth branch drives customer traffic (an income multiplier, like reputation).
BUSINESS_GROWTH_BRANCH_BONUS_PER_LEVEL = 0.08
# Employee satisfaction is 0-100; this is the max +/- income swing at the edges.
BUSINESS_SATISFACTION_SWING = 0.15
BUSINESS_SATISFACTION_START = 50
BUSINESS_SATISFACTION_DECAY_PER_DAY = 1.5
BUSINESS_SATISFACTION_NEUTRAL = 50
# /business manage actions
BUSINESS_MANAGE_WAGE_COST_FRACTION = 0.02  # of effective hourly income
BUSINESS_MANAGE_WAGE_SAT_GAIN = 10
BUSINESS_MANAGE_EVENT_BASE_COST = 500.0
BUSINESS_MANAGE_EVENT_COST_PER_TIER = 250.0
BUSINESS_MANAGE_EVENT_SAT_GAIN = 10
BUSINESS_MANAGE_EVENT_COOLDOWN_SECONDS = 12 * 3600
BUSINESS_MANAGE_NEGLECT_PENALTY = 5
BUSINESS_MANAGE_NEGLECT_HOURS = 24
# Stored-income capacity: a base buffer of N hours plus extra per capacity level.
BUSINESS_BASE_CAPACITY_HOURS = 8.0
BUSINESS_CAPACITY_HOURS_PER_LEVEL = 4.0
BUSINESS_MIN_CAPACITY = 100.0
# Attribute / branch upgrade pricing (tier-indexed base + soft exponential growth).
BUSINESS_UPGRADE_BASE_COST = 500.0  # fallback when tier unknown
BUSINESS_UPGRADE_BASE_BY_TIER: dict[int, float] = {
    1: 50.0,
    2: 150.0,
    3: 500.0,
    4: 2_500.0,
    5: 8_000.0,
    6: 15_000.0,
    7: 25_000.0,
}
BUSINESS_UPGRADE_COST_FRACTION = 0.15  # legacy; unused by new formula
BUSINESS_UPGRADE_COST_GROWTH = 1.35
BUSINESS_ATTRIBUTE_MAX = 15
BUSINESS_BRANCH_MAX = 5
# Income attributes give full bonus per level up to this cap, then half bonus.
BUSINESS_ATTRIBUTE_DIMINISHING_AFTER = 10
BUSINESS_ATTRIBUTE_DIMINISHING_FACTOR = 0.5
# Security rating inputs (used by the Phase 4 defense system).
BUSINESS_SECURITY_PER_LEVEL = 2
BUSINESS_SECURITY_PER_BRANCH_LEVEL = 5
# Business prestige is separate from combat prestige.
BUSINESS_PRESTIGE_INCOME_BONUS_PER_LEVEL = 0.05
BUSINESS_PRESTIGE_MAX_LEVEL = 10
# Districts: relocation fee (scales with tier) and a cooldown between moves.
BUSINESS_DISTRICT_RELOCATE_BASE_COST = 1_000.0
BUSINESS_DISTRICT_RELOCATE_COOLDOWN_SECONDS = 6 * 3600
# Influence: cost per point and the 0-100 cap.
BUSINESS_DISTRICT_INFLUENCE_COST_PER_POINT = 250.0
BUSINESS_DISTRICT_INFLUENCE_MAX = 100
# Exclusive district deeds (Monopoly-style owner + tenant rent).
DISTRICT_DEED_CLAIM_BASE = 25_000_000.0
DISTRICT_DEED_FACTORS: dict[str, float] = {
    "residential": 1.0,
    "beachfront": 1.15,
    "downtown": 1.20,
    "financial": 1.25,
    "industrial": 1.30,
}
DISTRICT_BUYOUT_DAYS = 5
DISTRICT_BUYOUT_BURN = 0.15
DISTRICT_TENANT_MULT_SHARE = 0.5
DISTRICT_TENANT_RENT_RATE = 0.20
DASHBOARD_SPY_MAX_QUANTITY = 100
# Interactive influence / district wars.
DISTRICT_WAR_CONTEST_DURATION_SECONDS = 24 * 3600
DISTRICT_WAR_CONTEST_COOLDOWN_SECONDS = 60 * 60
DISTRICT_UNDERMINE_COST_PER_POINT = 400.0
DISTRICT_UNDERMINE_DEFAULT_POINTS = 5
DISTRICT_UNDERMINE_MAX_POINTS = 15
DISTRICT_UNDERMINE_COOLDOWN_SECONDS = 30 * 60
DISTRICT_FORTIFY_COST_PER_POINT = 300.0
DISTRICT_FORTIFY_DEFAULT_POINTS = 5
DISTRICT_FORTIFY_MAX_POINTS = 20
DISTRICT_FORTIFY_DURATION_SECONDS = 6 * 3600
DISTRICT_FORTIFY_COOLDOWN_SECONDS = 30 * 60
DISTRICT_BUYOUT_INFLUENCE_DISCOUNT_THRESHOLD = 50
DISTRICT_BUYOUT_INFLUENCE_DISCOUNT = 0.15
DISTRICT_OWNER_SUPPRESS_COST = 20
DISTRICT_OWNER_SUPPRESS_DURATION_SECONDS = 12 * 3600
DISTRICT_OWNER_SUPPRESS_COOLDOWN_SECONDS = 6 * 3600

# --- Competition & defense (Phase 4) ----------------------------------------
# All competitive actions are temporary income multipliers; no permanent loss.
BUSINESS_ACTION_COOLDOWN_SECONDS = 3600
BUSINESS_DEFENSE_WINDOW_SECONDS = 15 * 60
# Active defense removes this fraction of an incoming debuff's remaining penalty.
BUSINESS_DEFENSE_MITIGATION = 0.5
# Passive defense: mitigation = security_rating / (security_rating + K).
BUSINESS_SECURITY_MITIGATION_K = 120
BUSINESS_SECURITY_MITIGATION_CAP = 0.75
# Self-buff actions.
BUSINESS_ACTION_MARKETING_COST = 5_000.0
BUSINESS_ACTION_MARKETING_BONUS = 0.10
BUSINESS_ACTION_MARKETING_DURATION = 12 * 3600
BUSINESS_ACTION_TALENT_COST = 6_000.0
BUSINESS_ACTION_TALENT_BONUS = 0.08
BUSINESS_ACTION_TALENT_DURATION = 8 * 3600
# Attack actions (penalty applied to the opponent's income).
BUSINESS_ACTION_PRICE_WAR_COST = 4_000.0
BUSINESS_ACTION_PRICE_WAR_PENALTY = 0.05
BUSINESS_ACTION_PRICE_WAR_DURATION = 6 * 3600
BUSINESS_ACTION_REPUTATION_COST = 4_500.0
BUSINESS_ACTION_REPUTATION_PENALTY = 0.10
BUSINESS_ACTION_REPUTATION_DURATION = 6 * 3600
# Market Expansion is an instant influence purchase in your current district.
BUSINESS_ACTION_MARKET_EXPANSION_COST = 3_000.0
BUSINESS_ACTION_MARKET_EXPANSION_INFLUENCE = 10

# --- Corporations (crew extensions, Phase 5) --------------------------------
# Corporate upgrades are funded from the crew treasury and benefit all members.
CORP_UPGRADE_MAX_LEVEL = 10
CORP_UPGRADE_BASE_COST = 50_000.0
CORP_UPGRADE_COST_GROWTH = 1.5
CORP_UPGRADE_INCOME_BONUS_PER_LEVEL = 0.03   # +% member business income
CORP_UPGRADE_DEFENSE_BONUS_PER_LEVEL = 5     # +security rating for members
CORP_UPGRADE_TERRITORY_BONUS_PER_LEVEL = 0.02
# Corporate wars run on a weekly cadence; the top corporation earns a treasury bonus.
CORP_WAR_TICK_SECONDS = 7 * 24 * 3600
CORP_WAR_TERRITORY_SCORE = 5_000.0
CORP_WAR_WINNER_TREASURY_BONUS = 250_000.0

# --- Stock market (Phase 6) -------------------------------------------------
# Share price is derived from a corporation's treasury and headcount, scaled by
# any active market event.
STOCK_BASE_PRICE = 10.0
STOCK_TREASURY_DIVISOR = 1_000.0
STOCK_PRICE_PER_MEMBER = 5.0
STOCK_MIN_PRICE = 1.0
STOCK_SELL_TAX = 0.05
STOCK_MAX_SHARES_PER_TXN = 100_000
# Dividends are paid hourly from the corporation treasury to shareholders.
STOCK_DIVIDEND_RATE = 0.01
STOCK_DIVIDEND_TICK_SECONDS = 3600
# Market events temporarily scale all share prices.
STOCK_MARKET_EVENTS: dict[str, float] = {
    "tech_boom": 1.25,
    "economic_crash": 0.70,
    "tourism_surge": 1.12,
    "supply_shortage": 0.85,
}
STOCK_EVENT_CHANCE_PER_TICK = 0.20
STOCK_EVENT_DURATION_SECONDS = 6 * 3600

# --- Seasonal business events & mega projects (Phase 7) ---------------------
# Seasonal events scale business income while active (set via /event).
BUSINESS_SEASONAL_EVENTS: dict[str, float] = {
    "summer_festival": 1.15,
    "holiday_rush": 1.25,
    "economic_crisis": 0.90,
    "tech_boom": 1.20,
}
# Personal mega projects grant a permanent business income bonus on completion.
MEGA_PROJECT_INCOME_BONUS_CAP = 1.0

# --- Empire expansion (acquisitions, legacy, district wars) ----------------
# Post-mega acquisition targets with unique passive perks.
EMPIRE_ACQUISITIONS: dict[str, dict[str, float | str]] = {
    "media_conglomerate": {
        "name": "Media Conglomerate",
        "emoji": "📺",
        "cost": 50_000_000_000.0,
        "reputation_bonus_factor": 0.10,
    },
    "private_security": {
        "name": "Private Security Firm",
        "emoji": "🛡️",
        "cost": 75_000_000_000.0,
        "security_bonus": 15.0,
        "attack_duration_reduction": 0.10,
    },
    "pharma_lab": {
        "name": "Pharma Lab",
        "emoji": "💊",
        "cost": 100_000_000_000.0,
        "drug_grow_time_reduction": 0.25,
    },
}
# Business prestige 10+ legacy perk picks (one per prestige cycle, permanent).
BUSINESS_LEGACY_PERKS: dict[str, dict[str, float | str]] = {
    "automation": {
        "name": "Automation",
        "emoji": "🤖",
        "offline_accrual_bonus": 0.10,
    },
    "diversification": {
        "name": "Diversification",
        "emoji": "🧪",
        "extra_lab_slots": 1.0,
    },
    "hostile_takeover": {
        "name": "Hostile Takeover",
        "emoji": "⚔️",
        "action_duration_bonus_hours": 2.0,
    },
}
# Weekly district control bonus for the dominant crew in each district.
DISTRICT_WAR_TICK_SECONDS = 7 * 24 * 3600
DISTRICT_WAR_CONTROL_BONUS = 0.05
DISTRICT_WAR_CONTEST_COST = 15  # personal influence points to contest control
# Business-drug cross-system integration
DRUG_SYNERGY_BUFF_INCOME_BONUS = 0.02
DRUG_SYNERGY_BUFF_DURATION_SECONDS = 3600
DRUG_SYNERGY_BUFF_MAX_STACKS = 3
DRUG_DISTRIBUTION_RAID_REDUCTION_PER_PRESTIGE = 0.05
DRUG_DISTRIBUTION_RAID_REDUCTION_CAP = 0.25
DRUG_SUPPLY_CHAIN_TIER_MIN = 5
DRUG_SUPPLY_CHAIN_GROW_SLOWDOWN = 1.5  # grows take 50% longer when auto-funded
DRUG_WHOLESALE_PRICE_FACTOR = 0.80  # fixed price, no raid risk
# Dealer rank thresholds (total units sold) and unlocks
DEALER_RANK_THRESHOLDS: tuple[int, ...] = (
    0, 50, 200, 500, 1_500, 3_000, 7_500, 15_000, 30_000, 60_000,
)
DEALER_RANK_MARKET_UNLOCK = 3
DEALER_RANK_EXTRA_LAB_SLOT = 5
DEALER_RANK_WHOLESALE_UNLOCK = 7
DEALER_RANK_CARTEL_TITLE = 10
# Crew cartel drug mechanics
CARTEL_LAB_SLOTS = 5
CARTEL_STREET_SELL_CREW_SHARE = 0.80
CARTEL_STREET_SELL_PLAYER_SHARE = 0.20

# --- Drug trade (Phase 8) ---------------------------------------------------
# In-fiction contraband economy: grow product in a lab, then sell on the street
# or to other players. Risky, high-reward, consistent with the heist/bounty tone.
DRUG_LAB_SLOTS = 3
DRUG_STREET_PRICE_VARIANCE = 0.25
DRUG_RAID_CHANCE = 0.10
DRUG_RAID_LOSS_FRACTION = 0.5
DRUG_MARKET_TAX = 0.05
DRUG_INDUSTRIAL_YIELD_BONUS = 0.20
DRUG_MAX_LISTING_QTY = 100_000

# Empire quest track (after onboarding)
TRACK_EMPIRE = "empire"
EMPIRE_QUEST_COUNT = 3


def custom_avatar_max_size_label() -> str:
    """Human-readable upload cap for command messages."""
    n = CUSTOM_AVATAR_MAX_BYTES
    if n >= 1024 * 1024:
        mb = n / (1024 * 1024)
        return f"{mb:.0f} MB" if mb == int(mb) else f"{mb:.1f} MB"
    return f"{max(1, n // 1024)} KB"

# Job shifts consume energy; energy refills on a fixed timer up to a cap.
ENERGY_BASE_CAP = 30
ENERGY_CAP_PER_UPGRADE = 15
ENERGY_UPGRADE_COST = 20_000.0
ENERGY_REGEN_INTERVAL_SECONDS = 5 * 60
ENERGY_REGEN_PER_TICK = 5
ENERGY_WORK_COST_DEFAULT = 10

# --- Gameplay expansion (relics, companions, contracts, museum, expeditions) ---
PASSIVE_BONUS_CAP = 0.15
RELIC_MAX_EQUIP = 1
COMPANION_MAX_EQUIP = 1
COMPANION_DROP_CHANCE = 0.12
RELIC_BOSS_DROP_CHANCE = 0.06
RELIC_VAULT_DROP_CHANCE = 0.10
GEAR_AFFIX_DUNGEON_CHANCE = 0.65
SEASON_TOKEN_WIN = 10
SEASON_TOKEN_LOSS = 3
SEASON_TOKEN_SHOP: dict[str, tuple[int, str]] = {
    "title_raider": (50, "title"),
    "avatar_season_gold": (120, "avatar"),
    "aspect_season_plunder": (200, "aspect"),
    "relic_plunder_seal": (300, "relic"),
}
CONTRACT_REFRESH_SECONDS = 6 * 3600
DELVE_WEEK_SECONDS = 7 * 24 * 3600
DELVE_WEEK_ROTATION: tuple[str, ...] = ("cursed_depths", "merchants_run", "blood_pact")
EXPEDITION_INTERVAL_SECONDS = 72 * 3600
EXPEDITION_AUTO_SPAWN = True
EXPEDITION_MIN_ACTIVE_PLAYERS = 3
PHENOTYPE_CROSSBREED_CHANCE = 0.08
CREW_LEGACY_HOLD_DAYS = 30
CREW_LEGACY_INCOME_BONUS = 0.01
TERRITORY_COSMETIC_SIEGE_WINS = 3
EXPEDITION_INCOME_BUFF = 1.10
EXPEDITION_INCOME_BUFF_HOURS = 24.0
EXPEDITION_CONTRIBUTOR_TOKEN_REWARD = 15

SEASONAL_EVENT_TYPES: tuple[str, ...] = (
    "double_drops",
    "bonus_income",
    "festival_boss",
    "trivia_fiesta",
    "world_boss_week",
    "summer_festival",
    "holiday_rush",
    "economic_crisis",
    "tech_boom",
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
    "nsfw_channel_only": LiveSetting(
        1.0,
        "Require Discord NSFW channels for commands (1=on, 0=off)",
        maximum=1.0,
        integer=True,
    ),
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
    "boss_downed_seconds": LiveSetting(
        BOSS_DOWN_SECONDS,
        "Boss downed duration (max 30s; AGI reduces further)",
        minimum=1,
        maximum=BOSS_DEBUFF_MAX_SECONDS,
        integer=True,
    ),
    "imposter_chance": LiveSetting(IMPOSTER_CHANCE, "Per-message sabotage chance", maximum=1.0),
    "trivia_reward": LiveSetting(TRIVIA_REWARD, "Trivia base reward (+ house pool share)"),
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
    "boss_aspect_drop_chance": LiveSetting(
        BOSS_ASPECT_DROP_CHANCE,
        "Aspect drop chance on boss defeat",
        maximum=1.0,
    ),
    "boss_accessory_drop_chance": LiveSetting(
        BOSS_ACCESSORY_DROP_CHANCE,
        "Accessory drop chance on boss defeat",
        maximum=1.0,
    ),
    "boss_hardener_drop_chance": LiveSetting(
        BOSS_HARDENER_DROP_CHANCE,
        "Void hardener drop chance on boss defeat",
        maximum=1.0,
    ),
    "boss_add_spawn_chance": LiveSetting(
        BOSS_ADD_SPAWN_CHANCE,
        "Raid add spawn chance per attack after 50% boss HP",
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
    "boss_aspect_drop_chance",
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
