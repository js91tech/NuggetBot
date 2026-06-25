from __future__ import annotations

import asyncio
import logging
import socket
import time
from collections.abc import Iterable
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any

import aiosqlite
import asyncpg

import config


def _spendable_cents(value: object) -> int:
    """Floor wallet/price to cents for comparisons (avoids float vs shop integer prices)."""
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, int) and not isinstance(value, bool):
        d = Decimal(value)
    elif isinstance(value, float):
        d = Decimal(repr(value))
    else:
        d = Decimal(str(value))
    d = d.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
    return int(d * 100)


class PostgresCursor:
    def __init__(
        self,
        rows: list[asyncpg.Record] | None = None,
        *,
        lastrowid: int | None = None,
        rowcount: int = 0,
    ) -> None:
        self._rows = rows or []
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    async def fetchone(self) -> asyncpg.Record | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[asyncpg.Record]:
        return self._rows


class PostgresConnection:
    """asyncpg forbids concurrent operations on one connection; ``sql_lock`` serializes them."""

    def __init__(self, url: str, *, sql_lock: asyncio.Lock | None = None) -> None:
        self.url = url
        self._sql_lock = sql_lock
        self._conn: asyncpg.Connection | None = None

    async def connect(self) -> None:
        self._conn = await asyncpg.connect(self.url)

    @property
    def conn(self) -> asyncpg.Connection:
        if self._conn is None:
            msg = "Postgres connection has not been opened"
            raise RuntimeError(msg)
        return self._conn

    async def close(self) -> None:
        if self._conn is None:
            return
        if self._sql_lock is None:
            await self._conn.close()
            self._conn = None
            return
        async with self._sql_lock:
            await self._conn.close()
            self._conn = None

    async def commit(self) -> None:
        if self._sql_lock is None:
            await self.conn.execute("COMMIT")
            return
        async with self._sql_lock:
            await self.conn.execute("COMMIT")

    async def rollback(self) -> None:
        if self._sql_lock is None:
            await self.conn.execute("ROLLBACK")
            return
        async with self._sql_lock:
            await self.conn.execute("ROLLBACK")

    async def executescript(self, script: str) -> None:
        if self._sql_lock is None:
            for statement in script.split(";"):
                sql = statement.strip()
                if sql:
                    await self.execute(sql)
            return
        async with self._sql_lock:
            for statement in script.split(";"):
                sql = statement.strip()
                if sql:
                    await self._execute_unlocked(sql)

    async def execute(self, query: str, params: tuple[Any, ...] = ()) -> PostgresCursor:
        if self._sql_lock is None:
            return await self._execute_unlocked(query, params)
        async with self._sql_lock:
            return await self._execute_unlocked(query, params)

    async def _execute_unlocked(self, query: str, params: tuple[Any, ...] = ()) -> PostgresCursor:
        normalized = self._normalize_query(query)
        if normalized is None:
            return PostgresCursor()
        sql = self._convert_placeholders(normalized)
        if "RETURNING" in sql.upper():
            rows = await self.conn.fetch(sql, *params)
            lastrowid = None
            if rows:
                row = rows[0]
                for col in ("instance_id", "id"):
                    if col in row:
                        lastrowid = int(row[col])
                        break
            return PostgresCursor(list(rows), lastrowid=lastrowid, rowcount=len(rows))
        if sql.lstrip().upper().startswith(("SELECT", "WITH")):
            return PostgresCursor(list(await self.conn.fetch(sql, *params)))
        status = await self.conn.execute(sql, *params)
        rowcount = 0
        if status:
            parts = str(status).split()
            if parts and parts[-1].isdigit():
                rowcount = int(parts[-1])
        return PostgresCursor(rowcount=rowcount)

    @staticmethod
    def _convert_placeholders(query: str) -> str:
        parts = query.split("?")
        if len(parts) == 1:
            return query
        rebuilt = [parts[0]]
        for index, part in enumerate(parts[1:], start=1):
            rebuilt.append(f"${index}")
            rebuilt.append(part)
        return "".join(rebuilt)

    _INSERT_OR_IGNORE_CONFLICTS: dict[str, str] = {
        "users": "(user_id, guild_id)",
        "duel_elo": "(guild_id, user_id)",
        "player_avatar_unlocks": "(guild_id, user_id, avatar_id)",
        "equipped_aspect_slots": "(guild_id, user_id, slot)",
    }

    @classmethod
    def _convert_insert_or_ignore(cls, sql: str) -> str:
        import re

        if "INSERT OR IGNORE" not in sql.upper():
            return sql
        for table, conflict_cols in cls._INSERT_OR_IGNORE_CONFLICTS.items():
            pattern = rf"INSERT\s+OR\s+IGNORE\s+INTO\s+{re.escape(table)}\b"
            if re.search(pattern, sql, flags=re.IGNORECASE):
                sql = re.sub(
                    pattern,
                    f"INSERT INTO {table}",
                    sql,
                    count=1,
                    flags=re.IGNORECASE,
                )
                if "ON CONFLICT" not in sql.upper():
                    sql = f"{sql.rstrip()} ON CONFLICT {conflict_cols} DO NOTHING"
                return sql
        return sql.replace("INSERT OR IGNORE", "INSERT")

    @classmethod
    def _normalize_query(cls, query: str) -> str | None:
        stripped = query.strip()
        upper = stripped.upper()
        if upper.startswith("PRAGMA"):
            return None
        if upper == "BEGIN IMMEDIATE":
            return "BEGIN"
        sql = query
        sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        sql = cls._convert_insert_or_ignore(sql)
        sql = sql.replace("SET hp = MAX(hp - ?, 0)", "SET hp = GREATEST(hp - ?, 0)")
        return sql


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self.urls = self._postgres_urls()
        self.url = self.urls[0][1] if self.urls else ""
        self.is_postgres = bool(self.urls)
        self._conn: aiosqlite.Connection | PostgresConnection | None = None
        self._write_lock = asyncio.Lock()
        self._postgres_sql_lock: asyncio.Lock | None = None
        self._config_cache: dict[int, dict[str, float]] = {}

    @staticmethod
    def _postgres_urls() -> list[tuple[str, str]]:
        urls = []
        seen = set()
        for name, value in (
            ("DATABASE_URL", config.DATABASE_URL),
            ("DATABASE_PUBLIC_URL", config.DATABASE_PUBLIC_URL),
        ):
            if value and value not in seen:
                urls.append((name, value))
                seen.add(value)
        return urls

    @property
    def conn(self) -> aiosqlite.Connection | PostgresConnection:
        if self._conn is None:
            msg = "Database connection has not been opened"
            raise RuntimeError(msg)
        return self._conn

    async def connect(self) -> None:
        if (
            config.RUNNING_ON_RAILWAY
            and not self.is_postgres
            and not config.ALLOW_SQLITE_ON_RAILWAY
        ):
            msg = (
                "DATABASE_URL is required on Railway. Refusing to use SQLite because Railway "
                "local files can be wiped on redeploy. Set DATABASE_URL to the Postgres service "
                "internal URL, or set DATABASE_PUBLIC_URL to the Postgres public URL if internal DNS "
                "is unavailable. Set ALLOW_SQLITE_ON_RAILWAY=true only if you mounted persistent storage."
            )
            raise RuntimeError(msg)
        if self.is_postgres:
            await self._connect_postgres()
        else:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            sqlite = await aiosqlite.connect(self.path)
            sqlite.row_factory = aiosqlite.Row
            self._conn = sqlite
            await self.conn.execute("PRAGMA foreign_keys = ON")
            await self.conn.execute("PRAGMA journal_mode = WAL")
            await self.conn.execute("PRAGMA busy_timeout = 5000")
            await self.conn.commit()
        await self.init_schema()

    async def _connect_postgres(self) -> None:
        last_error: Exception | None = None
        for name, url in self.urls:
            if self._postgres_sql_lock is None:
                self._postgres_sql_lock = asyncio.Lock()
            postgres = PostgresConnection(url, sql_lock=self._postgres_sql_lock)
            try:
                await postgres.connect()
            except socket.gaierror as exc:
                last_error = exc
                logging.warning(
                    "Could not resolve Postgres host from %s; trying next URL if configured", name
                )
                continue
            self.url = url
            self._conn = postgres
            logging.info("Connected to Postgres using %s", name)
            return

        msg = (
            "Could not resolve any configured Postgres host. If DATABASE_URL uses an internal Railway "
            "hostname that is unavailable to this service, add DATABASE_PUBLIC_URL from the Postgres "
            "service variables to the NuggetBot service."
        )
        raise RuntimeError(msg) from last_error

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def init_schema(self) -> None:
        await self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT NOT NULL,
                guild_id BIGINT NOT NULL,
                wallet REAL NOT NULL DEFAULT 0 CHECK (wallet >= 0),
                bank REAL NOT NULL DEFAULT 0 CHECK (bank >= 0),
                last_daily REAL NOT NULL DEFAULT 0,
                last_heist REAL NOT NULL DEFAULT 0,
                last_bank_heist REAL NOT NULL DEFAULT 0,
                last_active_ts REAL NOT NULL DEFAULT 0,
                arrested_until REAL NOT NULL DEFAULT 0,
                downed_until REAL NOT NULL DEFAULT 0,
                total_earned REAL NOT NULL DEFAULT 0 CHECK (total_earned >= 0),
                messages_sent INTEGER NOT NULL DEFAULT 0 CHECK (messages_sent >= 0),
                PRIMARY KEY (user_id, guild_id)
            );

            CREATE TABLE IF NOT EXISTS bounties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id BIGINT NOT NULL,
                placer_id BIGINT NOT NULL,
                target_id BIGINT NOT NULL,
                amount REAL NOT NULL CHECK (amount > 0),
                trigger_word TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS hacker_pots (
                guild_id BIGINT PRIMARY KEY,
                holder_id BIGINT NOT NULL,
                pass_count INTEGER NOT NULL DEFAULT 0 CHECK (pass_count >= 0),
                started_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS hacker_cooldowns (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                last_hack REAL NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );


            CREATE TABLE IF NOT EXISTS scourge_pots (
                guild_id BIGINT PRIMARY KEY,
                holder_id BIGINT NOT NULL,
                pass_count INTEGER NOT NULL DEFAULT 0 CHECK (pass_count >= 0),
                started_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                penalty_amount REAL NOT NULL CHECK (penalty_amount > 0)
            );

            CREATE TABLE IF NOT EXISTS scourge_events (
                guild_id BIGINT PRIMARY KEY,
                channel_id BIGINT NOT NULL,
                phase TEXT NOT NULL DEFAULT 'idle',
                phase_ends_at REAL NOT NULL DEFAULT 0,
                next_hourly_roll_at REAL NOT NULL DEFAULT 0,
                infections_done INTEGER NOT NULL DEFAULT 0,
                next_infection_at REAL NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS boss_sessions (
                guild_id BIGINT PRIMARY KEY,
                name TEXT NOT NULL,
                variant TEXT NOT NULL,
                hp REAL NOT NULL CHECK (hp >= 0),
                max_hp REAL NOT NULL CHECK (max_hp > 0),
                spawned_at REAL NOT NULL,
                passive_decay_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS boss_damage (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                damage REAL NOT NULL DEFAULT 0 CHECK (damage >= 0),
                PRIMARY KEY (guild_id, user_id),
                FOREIGN KEY (guild_id) REFERENCES boss_sessions(guild_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS boss_heals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id BIGINT NOT NULL,
                healer_id BIGINT NOT NULL,
                target_id BIGINT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS inventory (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                item_id TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
                PRIMARY KEY (guild_id, user_id, item_id)
            );

            CREATE TABLE IF NOT EXISTS equipment (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                slot TEXT NOT NULL CHECK (slot IN ('weapon', 'off_hand', 'armor')),
                item_id TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id, slot)
            );

            CREATE TABLE IF NOT EXISTS combat_state (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                hp REAL NOT NULL CHECK (hp >= 0),
                max_hp REAL NOT NULL CHECK (max_hp > 0),
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS one_time_jobs (
                job_id TEXT PRIMARY KEY,
                completed_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS one_time_member_jobs (
                job_id TEXT NOT NULL,
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                completed_at REAL NOT NULL,
                PRIMARY KEY (job_id, guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS guild_config (
                guild_id BIGINT NOT NULL,
                setting TEXT NOT NULL,
                value REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (guild_id, setting)
            );

            CREATE TABLE IF NOT EXISTS guild_channels (
                guild_id BIGINT PRIMARY KEY,
                main_channel_id BIGINT
            );

            CREATE INDEX IF NOT EXISTS idx_users_guild_wallet
                ON users(guild_id, wallet DESC);
            CREATE INDEX IF NOT EXISTS idx_bounties_guild
                ON bounties(guild_id);

            CREATE TABLE IF NOT EXISTS user_progress (
                user_id BIGINT NOT NULL,
                guild_id BIGINT NOT NULL,
                prestige_level INTEGER NOT NULL DEFAULT 0 CHECK (prestige_level >= 0),
                bosses_killed INTEGER NOT NULL DEFAULT 0 CHECK (bosses_killed >= 0),
                heists_won INTEGER NOT NULL DEFAULT 0 CHECK (heists_won >= 0),
                heals_given INTEGER NOT NULL DEFAULT 0 CHECK (heals_given >= 0),
                mythic_kills INTEGER NOT NULL DEFAULT 0 CHECK (mythic_kills >= 0),
                crafts_done INTEGER NOT NULL DEFAULT 0 CHECK (crafts_done >= 0),
                PRIMARY KEY (user_id, guild_id)
            );

            CREATE TABLE IF NOT EXISTS achievements (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                achievement_id TEXT NOT NULL,
                unlocked_at REAL NOT NULL,
                PRIMARY KEY (guild_id, user_id, achievement_id)
            );

            CREATE TABLE IF NOT EXISTS guild_events (
                guild_id BIGINT PRIMARY KEY,
                event_type TEXT NOT NULL,
                multiplier REAL NOT NULL DEFAULT 1.0,
                ends_at REAL NOT NULL
            );
            """
        )
        await self.conn.commit()
        if self.is_postgres:
            await self._migrate_postgres_discord_snowflakes_to_bigint()
        await self._migrate_boss_passive_decay()
        await self._migrate_guild_channel_split()
        await self._migrate_progression_tables()
        await self._migrate_quest_tables()
        await self._migrate_equipment_off_hand()
        await self._migrate_duel_history()
        await self._migrate_boss_summoned()
        await self._migrate_boss_summoner_id()
        await self._migrate_user_character()
        await self._migrate_class_system()
        await self._migrate_boss_class_fields()
        await self._migrate_mana_system()
        await self._migrate_aspect_system()
        await self._migrate_aspect_equip_slots()
        await self._migrate_player_avatars()
        await self._migrate_dlc_expansion()
        await self._migrate_dlc_followup()
        await self._migrate_crew_banking()
        await self._migrate_territories()
        await self._migrate_territory_integration()
        await self._migrate_personal_bank()
        await self._migrate_bank_capacity()
        await self._migrate_bank_heist()
        await self._migrate_dungeon_tiers()
        await self._migrate_scourge_virus()
        await self._migrate_boss_rebalance()
        await self._migrate_boss_element_status()
        await self._migrate_boss_attack_pacing()
        await self._migrate_jail_bodyguards_house()
        await self._migrate_character_attributes()
        await self._migrate_character_attributes_reset()
        await self._migrate_character_attributes_v3_reset()
        await self._migrate_gear_enhancement()
        await self._migrate_business_empire()
        await self._migrate_business_districts()
        await self._migrate_business_competition()
        await self._migrate_corporations()
        await self._migrate_stock_market()
        await self._migrate_mega_projects()
        await self._migrate_drug_trade()
        await self._migrate_active_drug_buff()
        await self._migrate_drug_grow_fertilizer()
        await self._migrate_empire_expansion()

    async def _migrate_character_attributes(self) -> None:
        import config

        base = config.ATTR_BASE_VALUE
        cols = [
            ("stat_str", f"INTEGER NOT NULL DEFAULT {base}"),
            ("stat_dex", f"INTEGER NOT NULL DEFAULT {base}"),
            ("stat_agi", f"INTEGER NOT NULL DEFAULT {base}"),
            ("stat_def", f"INTEGER NOT NULL DEFAULT {base}"),
            ("stat_vit", f"INTEGER NOT NULL DEFAULT {base}"),
        ]
        if self.is_postgres:
            for col, typedef in cols:
                cursor = await self.conn.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = ANY (current_schemas(true))
                      AND table_name = 'user_character' AND column_name = ?
                    """,
                    (col,),
                )
                if await cursor.fetchone() is None:
                    await self.conn.execute(
                        f"ALTER TABLE user_character ADD COLUMN {col} {typedef}",
                    )
        else:
            cursor = await self.conn.execute("PRAGMA table_info(user_character)")
            existing = {row[1] for row in await cursor.fetchall()}
            for col, typedef in cols:
                if col not in existing:
                    await self.conn.execute(
                        f"ALTER TABLE user_character ADD COLUMN {col} {typedef}",
                    )
        await self.conn.commit()

    async def _migrate_character_attributes_reset(self) -> None:
        """One-time reset: stats start at 0; caps derived from prestige."""
        if await self.is_one_time_job_complete("character_attributes_v2_reset"):
            return
        if self.is_postgres:
            cursor = await self.conn.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = ANY (current_schemas(true))
                  AND table_name = 'user_character' AND column_name = 'stat_str'
                """,
            )
            if await cursor.fetchone() is None:
                await self.mark_one_time_job_complete("character_attributes_v2_reset")
                return
        else:
            cursor = await self.conn.execute("PRAGMA table_info(user_character)")
            cols = {row[1] for row in await cursor.fetchall()}
            if "stat_str" not in cols:
                await self.mark_one_time_job_complete("character_attributes_v2_reset")
                return
        await self.conn.execute(
            """
            UPDATE user_character
            SET stat_str = 0, stat_dex = 0, stat_agi = 0, stat_def = 0, stat_vit = 0
            """,
        )
        await self.conn.commit()
        await self.mark_one_time_job_complete("character_attributes_v2_reset")

    async def _migrate_character_attributes_v3_reset(self) -> None:
        """One-time global reset: zero all stats so players re-allocate under correct rules."""
        if await self.is_one_time_job_complete("character_attributes_v3_reset"):
            return
        if self.is_postgres:
            cursor = await self.conn.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = ANY (current_schemas(true))
                  AND table_name = 'user_character' AND column_name = 'stat_str'
                """,
            )
            if await cursor.fetchone() is None:
                await self.mark_one_time_job_complete("character_attributes_v3_reset")
                return
        else:
            cursor = await self.conn.execute("PRAGMA table_info(user_character)")
            cols = {row[1] for row in await cursor.fetchall()}
            if "stat_str" not in cols:
                await self.mark_one_time_job_complete("character_attributes_v3_reset")
                return
        await self.conn.execute(
            """
            UPDATE user_character
            SET stat_str = 0, stat_dex = 0, stat_agi = 0, stat_def = 0, stat_vit = 0
            """,
        )
        await self.conn.commit()
        await self.mark_one_time_job_complete("character_attributes_v3_reset")

    async def reset_all_character_attributes(self) -> int:
        """Zero attribute stats for every character in every guild."""
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                UPDATE user_character
                SET stat_str = 0, stat_dex = 0, stat_agi = 0, stat_def = 0, stat_vit = 0
                """,
            )
            await self.conn.commit()
            return int(cursor.rowcount or 0)

    async def _migrate_jail_bodyguards_house(self) -> None:
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_bodyguards (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                tier INTEGER NOT NULL CHECK (tier IN (1, 2, 3)),
                quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
                PRIMARY KEY (guild_id, user_id, tier)
            )
            """,
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_house_pot (
                guild_id BIGINT PRIMARY KEY,
                balance REAL NOT NULL DEFAULT 0 CHECK (balance >= 0)
            )
            """,
        )
        await self.conn.commit()

    async def _migrate_boss_attack_pacing(self) -> None:
        """Per-attack cooldown + elemental debuff pacing columns."""
        if self.is_postgres:
            for table, column, ddl in (
                (
                    "boss_attack_cooldowns",
                    "cooldown_seconds",
                    (
                        "ALTER TABLE boss_attack_cooldowns ADD COLUMN cooldown_seconds "
                        "REAL NOT NULL DEFAULT 3"
                    ),
                ),
                (
                    "boss_raider_status",
                    "debuff_attack_cooldown",
                    (
                        "ALTER TABLE boss_raider_status ADD COLUMN debuff_attack_cooldown "
                        "REAL NOT NULL DEFAULT 0"
                    ),
                ),
            ):
                cursor = await self.conn.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = ANY (current_schemas(true))
                      AND table_name = ?
                      AND column_name = ?
                    """,
                    (table, column),
                )
                if await cursor.fetchone() is not None:
                    continue
                await self.conn.execute(ddl)
            await self.conn.commit()
            return

        for table, column, sqlite_ddl in (
            (
                "boss_attack_cooldowns",
                "cooldown_seconds",
                "ALTER TABLE boss_attack_cooldowns ADD COLUMN cooldown_seconds REAL NOT NULL DEFAULT 3",
            ),
            (
                "boss_raider_status",
                "debuff_attack_cooldown",
                "ALTER TABLE boss_raider_status ADD COLUMN debuff_attack_cooldown REAL NOT NULL DEFAULT 0",
            ),
        ):
            cursor = await self.conn.execute(f"PRAGMA table_info({table})")
            cols = {row[1] for row in await cursor.fetchall()}
            if column not in cols:
                await self.conn.execute(sqlite_ddl)
        await self.conn.commit()

    async def _migrate_scourge_virus(self) -> None:
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scourge_pots (
                guild_id BIGINT PRIMARY KEY,
                holder_id BIGINT NOT NULL,
                pass_count INTEGER NOT NULL DEFAULT 0 CHECK (pass_count >= 0),
                started_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                penalty_amount REAL NOT NULL CHECK (penalty_amount > 0)
            )
            """,
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scourge_events (
                guild_id BIGINT PRIMARY KEY,
                channel_id BIGINT NOT NULL,
                phase TEXT NOT NULL DEFAULT 'idle',
                phase_ends_at REAL NOT NULL DEFAULT 0,
                next_hourly_roll_at REAL NOT NULL DEFAULT 0,
                infections_done INTEGER NOT NULL DEFAULT 0,
                next_infection_at REAL NOT NULL DEFAULT 0
            )
            """,
        )
        await self.conn.commit()

    async def _migrate_boss_element_status(self) -> None:
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS boss_raider_status (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                attack_slow_until REAL NOT NULL DEFAULT 0,
                verdant_root_until REAL NOT NULL DEFAULT 0,
                dot_ticks_remaining INTEGER NOT NULL DEFAULT 0,
                dot_damage REAL NOT NULL DEFAULT 0,
                dot_next_tick_at REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
            """,
        )
        await self.conn.commit()

    async def _migrate_boss_rebalance(self) -> None:
        session_cols = [
            ("expires_at", "REAL"),
            ("solo_attack_streak", "INTEGER NOT NULL DEFAULT 0"),
        ]
        progress_cols = [
            ("auto_potion_item_id", "TEXT"),
            ("auto_potion_threshold_pct", "INTEGER NOT NULL DEFAULT 0"),
            ("ultra_kills", "INTEGER NOT NULL DEFAULT 0"),
        ]
        if self.is_postgres:
            for col, typedef in session_cols:
                cursor = await self.conn.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = ANY (current_schemas(true))
                      AND table_name = 'boss_sessions' AND column_name = ?
                    """,
                    (col,),
                )
                if await cursor.fetchone() is None:
                    await self.conn.execute(
                        f"ALTER TABLE boss_sessions ADD COLUMN {col} {typedef}",
                    )
            for col, typedef in progress_cols:
                cursor = await self.conn.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = ANY (current_schemas(true))
                      AND table_name = 'user_progress' AND column_name = ?
                    """,
                    (col,),
                )
                if await cursor.fetchone() is None:
                    await self.conn.execute(
                        f"ALTER TABLE user_progress ADD COLUMN {col} {typedef}",
                    )
        else:
            cursor = await self.conn.execute("PRAGMA table_info(boss_sessions)")
            existing = {row[1] for row in await cursor.fetchall()}
            for col, typedef in session_cols:
                if col not in existing:
                    await self.conn.execute(
                        f"ALTER TABLE boss_sessions ADD COLUMN {col} {typedef}",
                    )
            cursor = await self.conn.execute("PRAGMA table_info(user_progress)")
            existing = {row[1] for row in await cursor.fetchall()}
            for col, typedef in progress_cols:
                if col not in existing:
                    await self.conn.execute(
                        f"ALTER TABLE user_progress ADD COLUMN {col} {typedef}",
                    )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS boss_attack_cooldowns (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                last_attack REAL NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
            """,
        )
        await self.conn.commit()

    async def _migrate_bank_heist(self) -> None:

        if self.is_postgres:
            cursor = await self.conn.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = ANY (current_schemas(true))
                  AND table_name = 'users' AND column_name = 'last_bank_heist'
                """,
            )
            if await cursor.fetchone() is None:
                await self.conn.execute(
                    "ALTER TABLE users ADD COLUMN last_bank_heist REAL NOT NULL DEFAULT 0",
                )
        else:
            cursor = await self.conn.execute("PRAGMA table_info(users)")
            existing = {row[1] for row in await cursor.fetchall()}
            if "last_bank_heist" not in existing:
                await self.conn.execute(
                    "ALTER TABLE users ADD COLUMN last_bank_heist REAL NOT NULL DEFAULT 0",
                )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS equipment_unstable (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                slot TEXT NOT NULL CHECK (slot IN ('weapon', 'off_hand', 'armor')),
                PRIMARY KEY (guild_id, user_id, slot)
            )
            """,
        )
        await self.conn.commit()

    async def _migrate_dungeon_tiers(self) -> None:
        progress_cols = [
            ("vault_dungeon_unlocked", "INTEGER NOT NULL DEFAULT 0"),
        ]
        run_cols = [
            ("tier", "TEXT NOT NULL DEFAULT 'normal'"),
        ]
        if self.is_postgres:
            for col, typedef in progress_cols:
                cursor = await self.conn.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = ANY (current_schemas(true))
                      AND table_name = 'user_progress' AND column_name = ?
                    """,
                    (col,),
                )
                if await cursor.fetchone() is None:
                    await self.conn.execute(
                        f"ALTER TABLE user_progress ADD COLUMN {col} {typedef}",
                    )
            for col, typedef in run_cols:
                cursor = await self.conn.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = ANY (current_schemas(true))
                      AND table_name = 'dungeon_runs' AND column_name = ?
                    """,
                    (col,),
                )
                if await cursor.fetchone() is None:
                    await self.conn.execute(
                        f"ALTER TABLE dungeon_runs ADD COLUMN {col} {typedef}",
                    )
        else:
            cursor = await self.conn.execute("PRAGMA table_info(user_progress)")
            existing = {row[1] for row in await cursor.fetchall()}
            for col, typedef in progress_cols:
                if col not in existing:
                    await self.conn.execute(
                        f"ALTER TABLE user_progress ADD COLUMN {col} {typedef}",
                    )
            cursor = await self.conn.execute("PRAGMA table_info(dungeon_runs)")
            existing = {row[1] for row in await cursor.fetchall()}
            for col, typedef in run_cols:
                if col not in existing:
                    await self.conn.execute(
                        f"ALTER TABLE dungeon_runs ADD COLUMN {col} {typedef}",
                    )
        party_cols = [
            ("tier", "TEXT NOT NULL DEFAULT 'normal'"),
        ]
        if self.is_postgres:
            for col, typedef in party_cols:
                cursor = await self.conn.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = ANY (current_schemas(true))
                      AND table_name = 'dungeon_parties' AND column_name = ?
                    """,
                    (col,),
                )
                if await cursor.fetchone() is None:
                    await self.conn.execute(
                        f"ALTER TABLE dungeon_parties ADD COLUMN {col} {typedef}",
                    )
        else:
            cursor = await self.conn.execute("PRAGMA table_info(dungeon_parties)")
            existing = {row[1] for row in await cursor.fetchall()}
            for col, typedef in party_cols:
                if col not in existing:
                    await self.conn.execute(
                        f"ALTER TABLE dungeon_parties ADD COLUMN {col} {typedef}",
                    )
        await self.conn.commit()

    async def _migrate_personal_bank(self) -> None:
        if self.is_postgres:
            cursor = await self.conn.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = ANY (current_schemas(true))
                  AND table_name = 'users' AND column_name = 'bank'
                """,
            )
            if await cursor.fetchone() is None:
                await self.conn.execute(
                    "ALTER TABLE users ADD COLUMN bank REAL NOT NULL DEFAULT 0 CHECK (bank >= 0)",
                )
        else:
            cursor = await self.conn.execute("PRAGMA table_info(users)")
            existing = {row[1] for row in await cursor.fetchall()}
            if "bank" not in existing:
                await self.conn.execute(
                    "ALTER TABLE users ADD COLUMN bank REAL NOT NULL DEFAULT 0 CHECK (bank >= 0)",
                )
        await self.conn.commit()

    async def _migrate_bank_capacity(self) -> None:
        if self.is_postgres:
            cursor = await self.conn.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = ANY (current_schemas(true))
                  AND table_name = 'users' AND column_name = 'bank_expansions'
                """,
            )
            if await cursor.fetchone() is None:
                await self.conn.execute(
                    "ALTER TABLE users ADD COLUMN bank_expansions INTEGER NOT NULL DEFAULT 0 CHECK (bank_expansions >= 0)",
                )
        else:
            cursor = await self.conn.execute("PRAGMA table_info(users)")
            existing = {row[1] for row in await cursor.fetchall()}
            if "bank_expansions" not in existing:
                await self.conn.execute(
                    "ALTER TABLE users ADD COLUMN bank_expansions INTEGER NOT NULL DEFAULT 0 CHECK (bank_expansions >= 0)",
                )
        await self.conn.commit()

    async def _migrate_territory_integration(self) -> None:
        territory_cols = [
            ("siege_attacker_user_id", "BIGINT"),
            ("siege_channel_id", "BIGINT"),
            ("siege_message_id", "BIGINT"),
        ]
        progress_cols = [
            ("territories_claimed", "INTEGER NOT NULL DEFAULT 0"),
            ("sieges_won", "INTEGER NOT NULL DEFAULT 0"),
        ]
        if self.is_postgres:
            for col, typedef in territory_cols:
                cursor = await self.conn.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = ANY (current_schemas(true))
                      AND table_name = 'territory_control' AND column_name = ?
                    """,
                    (col,),
                )
                if await cursor.fetchone() is None:
                    await self.conn.execute(
                        f"ALTER TABLE territory_control ADD COLUMN {col} {typedef}",
                    )
            for col, typedef in progress_cols:
                cursor = await self.conn.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = ANY (current_schemas(true))
                      AND table_name = 'user_progress' AND column_name = ?
                    """,
                    (col,),
                )
                if await cursor.fetchone() is None:
                    await self.conn.execute(
                        f"ALTER TABLE user_progress ADD COLUMN {col} {typedef}",
                    )
        else:
            cursor = await self.conn.execute("PRAGMA table_info(territory_control)")
            existing = {row[1] for row in await cursor.fetchall()}
            for col, typedef in territory_cols:
                if col not in existing:
                    await self.conn.execute(
                        f"ALTER TABLE territory_control ADD COLUMN {col} {typedef}",
                    )
            cursor = await self.conn.execute("PRAGMA table_info(user_progress)")
            existing = {row[1] for row in await cursor.fetchall()}
            for col, typedef in progress_cols:
                if col not in existing:
                    await self.conn.execute(
                        f"ALTER TABLE user_progress ADD COLUMN {col} {typedef}",
                    )
        await self.conn.commit()

    async def _migrate_territories(self) -> None:
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS territory_control (
                guild_id BIGINT NOT NULL,
                territory_id TEXT NOT NULL,
                owner_crew_name TEXT,
                guards INTEGER NOT NULL DEFAULT 0 CHECK (guards >= 0),
                last_income_at REAL NOT NULL,
                siege_attacker_crew TEXT,
                siege_ends_at REAL,
                siege_started_at REAL,
                last_siege_at REAL,
                PRIMARY KEY (guild_id, territory_id)
            )
            """,
        )
        await self.conn.commit()

    async def _migrate_business_empire(self) -> None:
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_businesses (
                user_id BIGINT NOT NULL,
                guild_id BIGINT NOT NULL,
                tier INTEGER NOT NULL DEFAULT 1 CHECK (tier >= 1),
                tier_id TEXT NOT NULL DEFAULT 'lemon_stand',
                district_id TEXT,
                security INTEGER NOT NULL DEFAULT 0 CHECK (security >= 0),
                reputation INTEGER NOT NULL DEFAULT 0 CHECK (reputation >= 0),
                efficiency INTEGER NOT NULL DEFAULT 0 CHECK (efficiency >= 0),
                capacity INTEGER NOT NULL DEFAULT 0 CHECK (capacity >= 0),
                employee_satisfaction INTEGER NOT NULL DEFAULT 50
                    CHECK (employee_satisfaction >= 0 AND employee_satisfaction <= 100),
                branch_security INTEGER NOT NULL DEFAULT 0 CHECK (branch_security >= 0),
                branch_growth INTEGER NOT NULL DEFAULT 0 CHECK (branch_growth >= 0),
                branch_production INTEGER NOT NULL DEFAULT 0 CHECK (branch_production >= 0),
                stored_income REAL NOT NULL DEFAULT 0 CHECK (stored_income >= 0),
                last_income_at REAL NOT NULL DEFAULT 0,
                business_prestige INTEGER NOT NULL DEFAULT 0 CHECK (business_prestige >= 0),
                created_at REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, guild_id)
            )
            """,
        )
        await self.conn.commit()

    async def _migrate_business_districts(self) -> None:
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS district_influence (
                guild_id BIGINT NOT NULL,
                district_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                influence REAL NOT NULL DEFAULT 0 CHECK (influence >= 0),
                updated_at REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, district_id, entity_type, entity_id)
            )
            """,
        )
        # Add the relocation-cooldown column to user_businesses if missing.
        if self.is_postgres:
            cursor = await self.conn.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = ANY (current_schemas(true))
                  AND table_name = 'user_businesses' AND column_name = 'last_relocate_at'
                """,
            )
            if await cursor.fetchone() is None:
                await self.conn.execute(
                    "ALTER TABLE user_businesses ADD COLUMN last_relocate_at REAL NOT NULL DEFAULT 0",
                )
        else:
            cursor = await self.conn.execute("PRAGMA table_info(user_businesses)")
            existing = {row[1] for row in await cursor.fetchall()}
            if "last_relocate_at" not in existing:
                await self.conn.execute(
                    "ALTER TABLE user_businesses ADD COLUMN last_relocate_at REAL NOT NULL DEFAULT 0",
                )
        await self.conn.commit()

    async def _migrate_business_competition(self) -> None:
        pk = "SERIAL PRIMARY KEY" if self.is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
        await self.conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS business_buffs (
                buff_id {pk},
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                buff_type TEXT NOT NULL,
                multiplier REAL NOT NULL DEFAULT 1.0,
                ends_at REAL NOT NULL,
                source_attack_id BIGINT
            )
            """,
        )
        await self.conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS business_attacks (
                attack_id {pk},
                guild_id BIGINT NOT NULL,
                attacker_id BIGINT NOT NULL,
                defender_id BIGINT NOT NULL,
                action_type TEXT NOT NULL,
                penalty REAL NOT NULL DEFAULT 0,
                started_at REAL NOT NULL,
                ends_at REAL NOT NULL,
                defended INTEGER NOT NULL DEFAULT 0,
                notify_expires_at REAL NOT NULL DEFAULT 0
            )
            """,
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_action_cooldowns (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                action_type TEXT NOT NULL,
                last_used_at REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, action_type)
            )
            """,
        )
        await self.conn.commit()

    async def _migrate_corporations(self) -> None:
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS crew_corporate_upgrades (
                guild_id BIGINT NOT NULL,
                crew_name TEXT NOT NULL,
                upgrade_type TEXT NOT NULL,
                level INTEGER NOT NULL DEFAULT 0 CHECK (level >= 0),
                PRIMARY KEY (guild_id, crew_name, upgrade_type)
            )
            """,
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS crew_corporate_projects (
                guild_id BIGINT NOT NULL,
                crew_name TEXT NOT NULL,
                project_id TEXT NOT NULL,
                funded_amount REAL NOT NULL DEFAULT 0 CHECK (funded_amount >= 0),
                completed_at REAL,
                PRIMARY KEY (guild_id, crew_name, project_id)
            )
            """,
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS corporate_war_scores (
                guild_id BIGINT NOT NULL,
                week_id INTEGER NOT NULL,
                crew_name TEXT NOT NULL,
                total_score REAL NOT NULL DEFAULT 0,
                recorded_at REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, week_id, crew_name)
            )
            """,
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS corporate_war_state (
                guild_id BIGINT PRIMARY KEY,
                last_tick_at REAL NOT NULL DEFAULT 0,
                current_week INTEGER NOT NULL DEFAULT 0
            )
            """,
        )
        await self.conn.commit()

    async def _migrate_stock_market(self) -> None:
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stock_holdings (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                crew_name TEXT NOT NULL,
                shares INTEGER NOT NULL DEFAULT 0 CHECK (shares >= 0),
                PRIMARY KEY (guild_id, user_id, crew_name)
            )
            """,
        )
        pk = "SERIAL PRIMARY KEY" if self.is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
        await self.conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS stock_transactions (
                txn_id {pk},
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                crew_name TEXT NOT NULL,
                shares INTEGER NOT NULL,
                price REAL NOT NULL,
                txn_type TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """,
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stock_market_event (
                guild_id BIGINT PRIMARY KEY,
                event_type TEXT,
                multiplier REAL NOT NULL DEFAULT 1.0,
                ends_at REAL NOT NULL DEFAULT 0,
                last_dividend_at REAL NOT NULL DEFAULT 0
            )
            """,
        )
        await self.conn.commit()

    async def _migrate_mega_projects(self) -> None:
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_mega_projects (
                user_id BIGINT NOT NULL,
                guild_id BIGINT NOT NULL,
                project_id TEXT NOT NULL,
                funded_amount REAL NOT NULL DEFAULT 0 CHECK (funded_amount >= 0),
                completed_at REAL,
                PRIMARY KEY (user_id, guild_id, project_id)
            )
            """,
        )
        await self.conn.commit()

    async def _migrate_drug_trade(self) -> None:
        pk = "SERIAL PRIMARY KEY" if self.is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
        await self.conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS drug_grows (
                grow_id {pk},
                user_id BIGINT NOT NULL,
                guild_id BIGINT NOT NULL,
                drug_id TEXT NOT NULL,
                planted_at REAL NOT NULL,
                ready_at REAL NOT NULL
            )
            """,
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS drug_inventory (
                user_id BIGINT NOT NULL,
                guild_id BIGINT NOT NULL,
                drug_id TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
                PRIMARY KEY (user_id, guild_id, drug_id)
            )
            """,
        )
        await self.conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS drug_market_listings (
                listing_id {pk},
                guild_id BIGINT NOT NULL,
                seller_id BIGINT NOT NULL,
                drug_id TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                price_per_unit REAL NOT NULL CHECK (price_per_unit > 0),
                created_at REAL NOT NULL
            )
            """,
        )
        await self.conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS drug_transactions (
                txn_id {pk},
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                drug_id TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                amount REAL NOT NULL,
                txn_type TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """,
        )
        await self.conn.commit()

    async def _migrate_active_drug_buff(self) -> None:
        """Dedicated columns for timed drug highs (separate from shop pending consumables)."""
        from utils.drugs import DRUG_BUFF_PREFIX

        cols = [
            ("active_drug_buff", "TEXT"),
            ("active_drug_buff_expires", "REAL"),
        ]
        if self.is_postgres:
            for col, typedef in cols:
                cursor = await self.conn.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = ANY (current_schemas(true))
                      AND table_name = 'user_character' AND column_name = ?
                    """,
                    (col,),
                )
                if await cursor.fetchone() is None:
                    await self.conn.execute(
                        f"ALTER TABLE user_character ADD COLUMN {col} {typedef}",
                    )
        else:
            cursor = await self.conn.execute("PRAGMA table_info(user_character)")
            existing = {row[1] for row in await cursor.fetchall()}
            for col, typedef in cols:
                if col not in existing:
                    await self.conn.execute(
                        f"ALTER TABLE user_character ADD COLUMN {col} {typedef}",
                    )
        prefix = f"{DRUG_BUFF_PREFIX}%"
        await self.conn.execute(
            """
            UPDATE user_character
            SET active_drug_buff = pending_consumable,
                active_drug_buff_expires = pending_consumable_expires
            WHERE pending_consumable LIKE ?
              AND (active_drug_buff IS NULL OR active_drug_buff = '')
            """,
            (prefix,),
        )
        await self.conn.execute(
            """
            UPDATE user_character
            SET pending_consumable = NULL, pending_consumable_expires = NULL
            WHERE pending_consumable LIKE ?
            """,
            (prefix,),
        )
        await self.conn.commit()

    async def _migrate_drug_grow_fertilizer(self) -> None:
        cols = [("yield_mult", "REAL NOT NULL DEFAULT 1.0")]
        if self.is_postgres:
            for col, typedef in cols:
                cursor = await self.conn.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = ANY (current_schemas(true))
                      AND table_name = 'drug_grows' AND column_name = ?
                    """,
                    (col,),
                )
                if await cursor.fetchone() is None:
                    await self.conn.execute(
                        f"ALTER TABLE drug_grows ADD COLUMN {col} {typedef}",
                    )
        else:
            cursor = await self.conn.execute("PRAGMA table_info(drug_grows)")
            existing = {row[1] for row in await cursor.fetchall()}
            for col, typedef in cols:
                if col not in existing:
                    await self.conn.execute(
                        f"ALTER TABLE drug_grows ADD COLUMN {col} {typedef}",
                    )
        await self.conn.commit()

    async def _migrate_empire_expansion(self) -> None:
        """Empire expansion: satisfaction, dealer stats, acquisitions, legacy, cartel, district wars."""
        pk = "SERIAL PRIMARY KEY" if self.is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
        biz_cols = [
            ("last_satisfaction_at", "REAL NOT NULL DEFAULT 0"),
            ("last_team_event_at", "REAL NOT NULL DEFAULT 0"),
            ("supply_chain_drug_id", "TEXT"),
            ("synergy_stacks", "INTEGER NOT NULL DEFAULT 0"),
            ("synergy_expires", "REAL NOT NULL DEFAULT 0"),
        ]
        if self.is_postgres:
            for col, typedef in biz_cols:
                cursor = await self.conn.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = ANY (current_schemas(true))
                      AND table_name = 'user_businesses' AND column_name = ?
                    """,
                    (col,),
                )
                if await cursor.fetchone() is None:
                    await self.conn.execute(
                        f"ALTER TABLE user_businesses ADD COLUMN {col} {typedef}",
                    )
        else:
            cursor = await self.conn.execute("PRAGMA table_info(user_businesses)")
            existing = {row[1] for row in await cursor.fetchall()}
            for col, typedef in biz_cols:
                if col not in existing:
                    await self.conn.execute(
                        f"ALTER TABLE user_businesses ADD COLUMN {col} {typedef}",
                    )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_drug_stats (
                user_id BIGINT NOT NULL,
                guild_id BIGINT NOT NULL,
                units_sold INTEGER NOT NULL DEFAULT 0 CHECK (units_sold >= 0),
                PRIMARY KEY (user_id, guild_id)
            )
            """,
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_empire_acquisitions (
                user_id BIGINT NOT NULL,
                guild_id BIGINT NOT NULL,
                acquisition_id TEXT NOT NULL,
                completed_at REAL NOT NULL,
                PRIMARY KEY (user_id, guild_id, acquisition_id)
            )
            """,
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_legacy_perks (
                user_id BIGINT NOT NULL,
                guild_id BIGINT NOT NULL,
                perk_id TEXT NOT NULL,
                granted_at REAL NOT NULL,
                PRIMARY KEY (user_id, guild_id, perk_id)
            )
            """,
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS district_war_control (
                guild_id BIGINT NOT NULL,
                district_id TEXT NOT NULL,
                crew_name TEXT NOT NULL,
                bonus_ends_at REAL NOT NULL,
                PRIMARY KEY (guild_id, district_id)
            )
            """,
        )
        await self.conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS crew_cartel_grows (
                grow_id {pk},
                guild_id BIGINT NOT NULL,
                crew_name TEXT NOT NULL,
                drug_id TEXT NOT NULL,
                planted_at REAL NOT NULL,
                ready_at REAL NOT NULL,
                yield_mult REAL NOT NULL DEFAULT 1.0
            )
            """,
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS crew_cartel_stash (
                guild_id BIGINT NOT NULL,
                crew_name TEXT NOT NULL,
                drug_id TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
                PRIMARY KEY (guild_id, crew_name, drug_id)
            )
            """,
        )
        await self.conn.commit()

    async def _migrate_crew_banking(self) -> None:
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS crew_member_contributions (
                guild_id BIGINT NOT NULL,
                crew_name TEXT NOT NULL,
                user_id BIGINT NOT NULL,
                contributed REAL NOT NULL DEFAULT 0 CHECK (contributed >= 0),
                PRIMARY KEY (guild_id, crew_name, user_id)
            )
            """,
        )
        pk = "SERIAL PRIMARY KEY" if self.is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
        await self.conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS crew_loans (
                loan_id {pk},
                guild_id BIGINT NOT NULL,
                crew_name TEXT NOT NULL,
                borrower_id BIGINT NOT NULL,
                principal REAL NOT NULL CHECK (principal > 0),
                remaining REAL NOT NULL CHECK (remaining >= 0),
                interest_rate REAL NOT NULL,
                created_at REAL NOT NULL,
                due_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
            )
            """,
        )
        await self.conn.commit()

    async def _migrate_dlc_followup(self) -> None:
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_elo_season (
                guild_id BIGINT PRIMARY KEY,
                season_number INTEGER NOT NULL DEFAULT 1,
                last_reset_at REAL NOT NULL DEFAULT 0
            )
            """,
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dungeon_parties (
                guild_id BIGINT NOT NULL,
                leader_id BIGINT NOT NULL,
                room INTEGER NOT NULL DEFAULT 1,
                enemy_hp REAL NOT NULL,
                started_at REAL NOT NULL,
                PRIMARY KEY (guild_id, leader_id)
            )
            """,
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dungeon_party_members (
                guild_id BIGINT NOT NULL,
                leader_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                player_hp REAL NOT NULL,
                max_hp REAL NOT NULL,
                PRIMARY KEY (guild_id, leader_id, user_id)
            )
            """,
        )
        blob_type = "BYTEA" if self.is_postgres else "BLOB"
        await self.conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS custom_avatar_assets (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                file_ext TEXT NOT NULL,
                portrait_data {blob_type} NOT NULL,
                victory_data {blob_type} NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
            """,
        )
        await self.conn.commit()

    async def _migrate_dlc_expansion(self) -> None:
        progress_cols = [
            ("duel_wins", "INTEGER NOT NULL DEFAULT 0"),
            ("gambles_won", "INTEGER NOT NULL DEFAULT 0"),
            ("dungeons_cleared", "INTEGER NOT NULL DEFAULT 0"),
        ]
        if self.is_postgres:
            for col, typedef in progress_cols:
                cursor = await self.conn.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = ANY (current_schemas(true))
                      AND table_name = 'user_progress' AND column_name = ?
                    """,
                    (col,),
                )
                if await cursor.fetchone() is None:
                    await self.conn.execute(
                        f"ALTER TABLE user_progress ADD COLUMN {col} {typedef}",
                    )
        else:
            cursor = await self.conn.execute("PRAGMA table_info(user_progress)")
            existing = {row[1] for row in await cursor.fetchall()}
            for col, typedef in progress_cols:
                if col not in existing:
                    await self.conn.execute(
                        f"ALTER TABLE user_progress ADD COLUMN {col} {typedef}",
                    )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_jackpot (
                guild_id BIGINT PRIMARY KEY,
                pool REAL NOT NULL DEFAULT 0 CHECK (pool >= 0)
            )
            """,
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS duel_elo (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                rating INTEGER NOT NULL DEFAULT 1000,
                wins INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
            """,
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS crew_stats (
                guild_id BIGINT NOT NULL,
                crew_name TEXT NOT NULL,
                treasury REAL NOT NULL DEFAULT 0 CHECK (treasury >= 0),
                level INTEGER NOT NULL DEFAULT 1 CHECK (level >= 1),
                xp INTEGER NOT NULL DEFAULT 0 CHECK (xp >= 0),
                PRIMARY KEY (guild_id, crew_name)
            )
            """,
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS crew_members (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                crew_name TEXT NOT NULL,
                joined_at REAL NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
            """,
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS loadout_presets (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                slot INTEGER NOT NULL CHECK (slot >= 1 AND slot <= 3),
                name TEXT NOT NULL,
                weapon_id TEXT,
                off_hand_id TEXT,
                armor_id TEXT,
                PRIMARY KEY (guild_id, user_id, slot)
            )
            """,
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dungeon_runs (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                room INTEGER NOT NULL DEFAULT 1,
                player_hp REAL NOT NULL,
                max_hp REAL NOT NULL,
                enemy_hp REAL NOT NULL DEFAULT 0,
                started_at REAL NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
            """,
        )
        char_cols = [
            ("pending_consumable", "TEXT"),
            ("pending_consumable_expires", "REAL"),
        ]
        if self.is_postgres:
            for col, typedef in char_cols:
                cursor = await self.conn.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = ANY (current_schemas(true))
                      AND table_name = 'user_character' AND column_name = ?
                    """,
                    (col,),
                )
                if await cursor.fetchone() is None:
                    await self.conn.execute(
                        f"ALTER TABLE user_character ADD COLUMN {col} {typedef}",
                    )
        else:
            cursor = await self.conn.execute("PRAGMA table_info(user_character)")
            existing = {row[1] for row in await cursor.fetchall()}
            for col, typedef in char_cols:
                if col not in existing:
                    await self.conn.execute(
                        f"ALTER TABLE user_character ADD COLUMN {col} {typedef}",
                    )
        await self.conn.commit()

    async def _migrate_player_avatars(self) -> None:
        cols = [("avatar_id", "TEXT NOT NULL DEFAULT 'nugget_raider'")]
        if self.is_postgres:
            for col, typedef in cols:
                cursor = await self.conn.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = ANY (current_schemas(true))
                      AND table_name = 'user_character' AND column_name = ?
                    """,
                    (col,),
                )
                if await cursor.fetchone() is None:
                    await self.conn.execute(
                        f"ALTER TABLE user_character ADD COLUMN {col} {typedef}",
                    )
        else:
            cursor = await self.conn.execute("PRAGMA table_info(user_character)")
            existing = {row[1] for row in await cursor.fetchall()}
            for col, typedef in cols:
                if col not in existing:
                    await self.conn.execute(
                        f"ALTER TABLE user_character ADD COLUMN {col} {typedef}",
                    )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS player_avatar_unlocks (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                avatar_id TEXT NOT NULL,
                unlocked_at REAL NOT NULL,
                PRIMARY KEY (guild_id, user_id, avatar_id)
            )
            """,
        )
        await self.conn.commit()

    async def _migrate_aspect_system(self) -> None:
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS aspect_instances (
                instance_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                aspect_id TEXT NOT NULL,
                roll_pct REAL NOT NULL CHECK (roll_pct > 0),
                created_at REAL NOT NULL
            )
            """,
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS equipped_aspect (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                slot INTEGER NOT NULL CHECK (slot >= 1 AND slot <= 3),
                instance_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id, slot)
            )
            """,
        )
        await self.conn.commit()

    async def _migrate_aspect_equip_slots(self) -> None:
        """Allow up to 3 equipped aspects (slot 1–3)."""
        has_slot = False
        if self.is_postgres:
            cursor = await self.conn.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = ANY (current_schemas(true))
                  AND table_name = 'equipped_aspect' AND column_name = 'slot'
                """,
            )
            has_slot = await cursor.fetchone() is not None
        else:
            cursor = await self.conn.execute("PRAGMA table_info(equipped_aspect)")
            has_slot = any(row[1] == "slot" for row in await cursor.fetchall())

        if has_slot:
            return

        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS equipped_aspect_slots (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                slot INTEGER NOT NULL CHECK (slot >= 1 AND slot <= 3),
                instance_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id, slot)
            )
            """,
        )
        try:
            cursor = await self.conn.execute(
                "SELECT guild_id, user_id, instance_id FROM equipped_aspect",
            )
            for row in await cursor.fetchall():
                await self.conn.execute(
                    """
                    INSERT OR IGNORE INTO equipped_aspect_slots
                        (guild_id, user_id, slot, instance_id)
                    VALUES (?, ?, 1, ?)
                    """,
                    (int(row["guild_id"]), int(row["user_id"]), int(row["instance_id"])),
                )
        except Exception:
            pass
        await self.conn.execute("DROP TABLE IF EXISTS equipped_aspect")
        await self.conn.execute(
            "ALTER TABLE equipped_aspect_slots RENAME TO equipped_aspect",
        )
        await self.conn.commit()

    async def _migrate_class_system(self) -> None:
        cols = [
            ("class_id", "TEXT"),
            ("class_xp", "INTEGER NOT NULL DEFAULT 0"),
            ("master_roots", "TEXT NOT NULL DEFAULT ''"),
        ]
        if self.is_postgres:
            for col, typedef in cols:
                cursor = await self.conn.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = ANY (current_schemas(true))
                      AND table_name = 'user_character' AND column_name = ?
                    """,
                    (col,),
                )
                if await cursor.fetchone() is None:
                    await self.conn.execute(
                        f"ALTER TABLE user_character ADD COLUMN {col} {typedef}",
                    )
        else:
            cursor = await self.conn.execute("PRAGMA table_info(user_character)")
            existing = {row[1] for row in await cursor.fetchall()}
            for col, typedef in cols:
                if col not in existing:
                    await self.conn.execute(
                        f"ALTER TABLE user_character ADD COLUMN {col} {typedef}",
                    )
        await self.conn.commit()

    async def _migrate_mana_system(self) -> None:
        cols = [
            ("mana", "INTEGER NOT NULL DEFAULT 100"),
            ("mana_cap", "INTEGER NOT NULL DEFAULT 100"),
            ("mana_updated_at", "REAL NOT NULL DEFAULT 0"),
            ("pending_spell", "TEXT"),
            ("pending_spell_expires", "REAL"),
            ("heist_spell_bonus", "REAL NOT NULL DEFAULT 0"),
        ]
        if self.is_postgres:
            for col, typedef in cols:
                cursor = await self.conn.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = ANY (current_schemas(true))
                      AND table_name = 'user_character' AND column_name = ?
                    """,
                    (col,),
                )
                if await cursor.fetchone() is None:
                    await self.conn.execute(
                        f"ALTER TABLE user_character ADD COLUMN {col} {typedef}",
                    )
        else:
            cursor = await self.conn.execute("PRAGMA table_info(user_character)")
            existing = {row[1] for row in await cursor.fetchall()}
            for col, typedef in cols:
                if col not in existing:
                    await self.conn.execute(
                        f"ALTER TABLE user_character ADD COLUMN {col} {typedef}",
                    )
        await self.conn.commit()
        now = time.time()
        await self.conn.execute(
            """
            UPDATE user_character
            SET mana_updated_at = ?
            WHERE mana_updated_at IS NULL OR mana_updated_at = 0
            """,
            (now,),
        )
        await self.conn.commit()

    async def _migrate_boss_class_fields(self) -> None:
        cols = [
            ("element", "TEXT NOT NULL DEFAULT 'fire'"),
            ("attack_count", "INTEGER NOT NULL DEFAULT 0"),
            ("mirrored_variant", "TEXT"),
        ]
        if self.is_postgres:
            for col, typedef in cols:
                cursor = await self.conn.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = ANY (current_schemas(true))
                      AND table_name = 'boss_sessions' AND column_name = ?
                    """,
                    (col,),
                )
                if await cursor.fetchone() is None:
                    await self.conn.execute(
                        f"ALTER TABLE boss_sessions ADD COLUMN {col} {typedef}",
                    )
        else:
            cursor = await self.conn.execute("PRAGMA table_info(boss_sessions)")
            existing = {row[1] for row in await cursor.fetchall()}
            for col, typedef in cols:
                if col not in existing:
                    await self.conn.execute(
                        f"ALTER TABLE boss_sessions ADD COLUMN {col} {typedef}",
                    )
        await self.conn.commit()

    async def _migrate_user_character(self) -> None:
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_character (
                user_id BIGINT NOT NULL,
                guild_id BIGINT NOT NULL,
                energy INTEGER NOT NULL DEFAULT 0 CHECK (energy >= 0),
                energy_cap INTEGER NOT NULL DEFAULT 0 CHECK (energy_cap > 0),
                cap_upgrades INTEGER NOT NULL DEFAULT 0 CHECK (cap_upgrades >= 0),
                energy_updated_at REAL NOT NULL,
                PRIMARY KEY (user_id, guild_id)
            )
            """,
        )
        await self.conn.commit()

    async def _migrate_boss_summoned(self) -> None:
        """Legacy flag from first summon debuff iteration (unused after summoner_id)."""
        if self.is_postgres:
            cursor = await self.conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = ANY (current_schemas(true))
                  AND table_name = 'boss_sessions'
                  AND column_name = 'summoned'
                """,
            )
            if await cursor.fetchone() is None:
                await self.conn.execute(
                    "ALTER TABLE boss_sessions ADD COLUMN summoned INTEGER NOT NULL DEFAULT 0",
                )
        else:
            cursor = await self.conn.execute("PRAGMA table_info(boss_sessions)")
            cols = {row[1] for row in await cursor.fetchall()}
            if "summoned" not in cols:
                await self.conn.execute(
                    "ALTER TABLE boss_sessions ADD COLUMN summoned INTEGER NOT NULL DEFAULT 0",
                )
        await self.conn.commit()

    async def _migrate_boss_summoner_id(self) -> None:
        """Track who used /summon for per-player combat debuff."""
        if self.is_postgres:
            cursor = await self.conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = ANY (current_schemas(true))
                  AND table_name = 'boss_sessions'
                  AND column_name = 'summoner_id'
                """,
            )
            if await cursor.fetchone() is None:
                await self.conn.execute(
                    "ALTER TABLE boss_sessions ADD COLUMN summoner_id BIGINT",
                )
        else:
            cursor = await self.conn.execute("PRAGMA table_info(boss_sessions)")
            cols = {row[1] for row in await cursor.fetchall()}
            if "summoner_id" not in cols:
                await self.conn.execute(
                    "ALTER TABLE boss_sessions ADD COLUMN summoner_id INTEGER",
                )
        await self.conn.commit()

    async def _equipment_slot_allows_off_hand(self) -> bool:
        if self.is_postgres:
            cursor = await self.conn.execute(
                """
                SELECT pg_get_constraintdef(c.oid) AS def
                FROM pg_constraint c
                JOIN pg_class rel ON rel.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = rel.relnamespace
                WHERE rel.relname = 'equipment'
                  AND c.contype = 'c'
                  AND n.nspname = ANY (current_schemas(true))
                """,
            )
            rows = await cursor.fetchall()
            return any("off_hand" in str(row["def"]) for row in rows)

        cursor = await self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'equipment'",
        )
        row = await cursor.fetchone()
        if row is None:
            return True
        ddl = str(row[0] if not hasattr(row, "keys") else row["sql"])
        return "off_hand" in ddl

    async def _rebuild_equipment_for_off_hand(self) -> None:
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS equipment_dual (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                slot TEXT NOT NULL CHECK (slot IN ('weapon', 'off_hand', 'armor')),
                item_id TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id, slot)
            )
            """,
        )
        await self.conn.execute(
            """
            INSERT INTO equipment_dual (guild_id, user_id, slot, item_id)
            SELECT guild_id, user_id, slot, item_id FROM equipment
            """,
        )
        await self.conn.execute("DROP TABLE equipment")
        if self.is_postgres:
            await self.conn.execute("ALTER TABLE equipment_dual RENAME TO equipment")
        else:
            await self.conn.execute("ALTER TABLE equipment_dual RENAME TO equipment")

    async def _migrate_equipment_off_hand(self) -> None:
        """Allow off_hand equipment slot on existing databases."""
        if await self._equipment_slot_allows_off_hand():
            return
        await self._rebuild_equipment_for_off_hand()
        await self.conn.commit()

    async def _migrate_gear_enhancement(self) -> None:
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gear_instances (
                instance_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                item_id TEXT NOT NULL,
                enhancement_level INTEGER NOT NULL DEFAULT 0 CHECK (enhancement_level >= 0),
                is_broken INTEGER NOT NULL DEFAULT 0 CHECK (is_broken IN (0, 1)),
                created_at REAL NOT NULL
            )
            """,
        )
        await self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gear_instances_owner
            ON gear_instances(guild_id, user_id)
            """,
        )
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS boss_raid_adds (
                add_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id BIGINT NOT NULL,
                add_type TEXT NOT NULL,
                hp REAL NOT NULL CHECK (hp >= 0),
                max_hp REAL NOT NULL CHECK (max_hp > 0),
                spawned_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
            """,
        )
        await self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_boss_raid_adds_guild
            ON boss_raid_adds(guild_id)
            """,
        )
        if not await self._equipment_has_gear_instance_id():
            await self._rebuild_equipment_for_enhancement()
        await self.conn.commit()

    async def _equipment_has_gear_instance_id(self) -> bool:
        if self.is_postgres:
            cursor = await self.conn.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = ANY (current_schemas(true))
                  AND table_name = 'equipment' AND column_name = 'gear_instance_id'
                """,
            )
            return await cursor.fetchone() is not None
        cursor = await self.conn.execute("PRAGMA table_info(equipment)")
        return "gear_instance_id" in {row[1] for row in await cursor.fetchall()}

    async def _rebuild_equipment_for_enhancement(self) -> None:
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS equipment_enhanced (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                slot TEXT NOT NULL CHECK (
                    slot IN ('weapon', 'off_hand', 'armor', 'ring', 'amulet')
                ),
                item_id TEXT NOT NULL,
                gear_instance_id BIGINT,
                PRIMARY KEY (guild_id, user_id, slot)
            )
            """,
        )
        await self.conn.execute(
            """
            INSERT INTO equipment_enhanced (guild_id, user_id, slot, item_id, gear_instance_id)
            SELECT guild_id, user_id, slot, item_id, NULL FROM equipment
            """,
        )
        await self.conn.execute("DROP TABLE equipment")
        await self.conn.execute("ALTER TABLE equipment_enhanced RENAME TO equipment")
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS equipment_unstable_new (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                slot TEXT NOT NULL CHECK (
                    slot IN ('weapon', 'off_hand', 'armor', 'ring', 'amulet')
                ),
                PRIMARY KEY (guild_id, user_id, slot)
            )
            """,
        )
        await self.conn.execute(
            """
            INSERT INTO equipment_unstable_new (guild_id, user_id, slot)
            SELECT guild_id, user_id, slot FROM equipment_unstable
            """,
        )
        await self.conn.execute("DROP TABLE IF EXISTS equipment_unstable")
        await self.conn.execute("ALTER TABLE equipment_unstable_new RENAME TO equipment_unstable")

    async def _migrate_duel_history(self) -> None:
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS duel_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id BIGINT NOT NULL,
                attacker_id BIGINT NOT NULL,
                defender_id BIGINT NOT NULL,
                winner_id BIGINT NOT NULL,
                loot_amount REAL NOT NULL CHECK (loot_amount >= 0),
                created_at REAL NOT NULL
            )
            """
        )
        await self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_duel_attacker_time
            ON duel_history(guild_id, attacker_id, created_at)
            """
        )
        await self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_duel_attacker_defender_time
            ON duel_history(guild_id, attacker_id, defender_id, created_at)
            """
        )
        await self.conn.commit()

    async def _migrate_quest_tables(self) -> None:
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_quests (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                track TEXT NOT NULL,
                quest_id TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0 CHECK (progress >= 0),
                target INTEGER NOT NULL DEFAULT 1 CHECK (target > 0),
                completed_at REAL,
                assigned_at REAL NOT NULL,
                reset_key TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (guild_id, user_id, track, quest_id)
            )
            """
        )
        await self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_user_quests_track
            ON user_quests(guild_id, user_id, track)
            """
        )
        await self.conn.commit()

    async def _migrate_guild_channel_split(self) -> None:
        """Add designated channel + split-announcements toggle for guild_channels."""
        if self.is_postgres:
            for column, ddl in (
                (
                    "designated_channel_id",
                    "ALTER TABLE guild_channels ADD COLUMN designated_channel_id BIGINT",
                ),
                (
                    "split_announcement_channels",
                    (
                        "ALTER TABLE guild_channels ADD COLUMN split_announcement_channels "
                        "INTEGER NOT NULL DEFAULT 0"
                    ),
                ),
                (
                    "scourge_event_enabled",
                    (
                        "ALTER TABLE guild_channels ADD COLUMN scourge_event_enabled "
                        "INTEGER NOT NULL DEFAULT 1"
                    ),
                ),
            ):
                cursor = await self.conn.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = ANY (current_schemas(true))
                      AND table_name = 'guild_channels'
                      AND column_name = ?
                    """,
                    (column,),
                )
                if await cursor.fetchone() is not None:
                    continue
                await self.conn.execute(ddl)
            await self.conn.commit()
            return

        cursor = await self.conn.execute("PRAGMA table_info(guild_channels)")
        cols = {row[1] for row in await cursor.fetchall()}
        if "designated_channel_id" not in cols:
            await self.conn.execute(
                "ALTER TABLE guild_channels ADD COLUMN designated_channel_id BIGINT",
            )
        if "split_announcement_channels" not in cols:
            await self.conn.execute(
                """
                ALTER TABLE guild_channels
                ADD COLUMN split_announcement_channels INTEGER NOT NULL DEFAULT 0
                """,
            )
        if "scourge_event_enabled" not in cols:
            await self.conn.execute(
                """
                ALTER TABLE guild_channels
                ADD COLUMN scourge_event_enabled INTEGER NOT NULL DEFAULT 1
                """,
            )
        await self.conn.commit()

    async def _migrate_postgres_discord_snowflakes_to_bigint(self) -> None:
        """Upgrade legacy int4 columns so Discord snowflake IDs bind correctly under asyncpg.

        A previous version only checked ``users.user_id``. If a migration crashed after
        altering ``user_id`` but before ``guild_id``, startup would skip the rest and
        ``guild_id`` stayed int4 — breaking almost every query that filters by guild.
        """
        cursor = await self.conn.execute(
            """
            SELECT DISTINCT n.nspname::text AS table_schema,
                   c.relname::text AS table_name,
                   a.attname::text AS column_name
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_type t ON t.oid = a.atttypid
            WHERE c.relkind = 'r'
              AND n.nspname = ANY (current_schemas(true))
              AND c.relname IN (
                  'users', 'bounties', 'hacker_pots', 'hacker_cooldowns',
                  'boss_sessions', 'boss_damage', 'boss_heals', 'inventory',
                  'equipment', 'combat_state', 'one_time_member_jobs', 'guild_config',
                  'guild_channels'
              )
              AND a.attname IN (
                  'user_id', 'guild_id', 'placer_id', 'target_id',
                  'holder_id', 'healer_id'
              )
              AND t.typname IN ('int4', 'int2')
              AND a.attnum > 0
              AND NOT a.attisdropped
            ORDER BY 1, 2, 3
            """,
        )
        rows = list(await cursor.fetchall())
        if not rows:
            return

        logging.info(
            "Migrating %s Postgres Discord ID column(s) from int4/int2 to BIGINT",
            len(rows),
        )

        try:
            await self._apply_postgres_discord_bigint_alters(rows)
        except Exception:
            logging.exception("Postgres BIGINT migration failed")
            raise

    async def _apply_postgres_discord_bigint_alters(self, rows: list[Any]) -> None:
        def qident(raw: str) -> str:
            s = str(raw)
            if not s.replace("_", "").isalnum():
                msg = f"Unsafe SQL identifier {raw!r}"
                raise RuntimeError(msg)
            return '"' + s.replace('"', '""') + '"'

        need_boss_fk_drop = any(
            (str(r["table_name"]) == "boss_sessions" and str(r["column_name"]) == "guild_id")
            or (str(r["table_name"]) == "boss_damage" and str(r["column_name"]) == "guild_id")
            for r in rows
        )
        if need_boss_fk_drop:
            fk_cur = await self.conn.execute(
                """
                SELECT n.nspname::text AS table_schema, c.conname::text AS conname
                FROM pg_constraint c
                JOIN pg_class cl ON cl.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = cl.relnamespace
                WHERE cl.relname = 'boss_damage'
                  AND n.nspname = ANY (current_schemas(true))
                  AND c.contype = 'f'
                """,
            )
            for fk_row in await fk_cur.fetchall():
                sch = qident(str(fk_row["table_schema"]))
                cname = str(fk_row["conname"]).replace('"', '""')
                bdt = qident("boss_damage")
                await self.conn.execute(
                    f'ALTER TABLE {sch}.{bdt} DROP CONSTRAINT IF EXISTS "{cname}"'
                )

        def _alter_sort_key(r: Any) -> tuple[int, str, str, str]:
            t = str(r["table_name"])
            # Parent guild_id before child so FK drop path stays valid.
            pri = 0 if t == "boss_sessions" else 1 if t == "boss_damage" else 2
            return (pri, str(r["table_schema"]), t, str(r["column_name"]))

        for rec in sorted(rows, key=_alter_sort_key):
            sch = qident(str(rec["table_schema"]))
            tbl = qident(str(rec["table_name"]))
            col = qident(str(rec["column_name"]))
            await self.conn.execute(f"ALTER TABLE {sch}.{tbl} ALTER COLUMN {col} TYPE BIGINT")

        if need_boss_fk_drop:
            fk_exists = await self.conn.execute(
                """
                SELECT 1
                FROM pg_constraint c
                JOIN pg_class cl ON cl.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = cl.relnamespace
                WHERE cl.relname = 'boss_damage'
                  AND n.nspname = ANY (current_schemas(true))
                  AND c.contype = 'f'
                  AND c.conname = 'boss_damage_guild_id_fkey'
                LIMIT 1
                """,
            )
            if await fk_exists.fetchone() is None:
                res_bs = await self.conn.execute(
                    """
                    SELECT n.nspname::text AS sch
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE c.relname = 'boss_sessions' AND c.relkind = 'r'
                      AND n.nspname = ANY (current_schemas(true))
                    ORDER BY n.nspname
                    LIMIT 1
                    """,
                )
                res_bd = await self.conn.execute(
                    """
                    SELECT n.nspname::text AS sch
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE c.relname = 'boss_damage' AND c.relkind = 'r'
                      AND n.nspname = ANY (current_schemas(true))
                    ORDER BY n.nspname
                    LIMIT 1
                    """,
                )
                bs_row = await res_bs.fetchone()
                bd_row = await res_bd.fetchone()
                if bs_row is not None and bd_row is not None:
                    bss = qident(str(bs_row["sch"]))
                    bds = qident(str(bd_row["sch"]))
                    bdt = qident("boss_damage")
                    bst = qident("boss_sessions")
                    gcol = qident("guild_id")
                    await self.conn.execute(
                        f"ALTER TABLE {bds}.{bdt} ADD CONSTRAINT boss_damage_guild_id_fkey "
                        f"FOREIGN KEY ({gcol}) REFERENCES {bss}.{bst}({gcol}) ON DELETE CASCADE"
                    )

        await self.conn.commit()


    async def _migrate_boss_passive_decay(self) -> None:
        """Add passive_decay_at for boss HP erosion over real time."""
        if self.is_postgres:
            cursor = await self.conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = ANY (current_schemas(true))
                  AND table_name = 'boss_sessions'
                  AND column_name = 'passive_decay_at'
                """,
            )
            if await cursor.fetchone() is not None:
                return
            await self.conn.execute(
                "ALTER TABLE boss_sessions ADD COLUMN passive_decay_at DOUBLE PRECISION",
            )
            await self.conn.execute(
                """
                UPDATE boss_sessions
                SET passive_decay_at = spawned_at
                WHERE passive_decay_at IS NULL
                """,
            )
            await self.conn.commit()
            return

        cursor = await self.conn.execute("PRAGMA table_info(boss_sessions)")
        cols = [row[1] for row in await cursor.fetchall()]
        if "passive_decay_at" in cols:
            return
        await self.conn.execute(
            "ALTER TABLE boss_sessions ADD COLUMN passive_decay_at REAL",
        )
        await self.conn.execute(
            """
            UPDATE boss_sessions
            SET passive_decay_at = spawned_at
            WHERE passive_decay_at IS NULL
            """,
        )
        await self.conn.commit()

    async def _migrate_progression_tables(self) -> None:
        """Progression tables and boss phase tracking for existing databases."""
        statements = (
            """
            CREATE TABLE IF NOT EXISTS user_progress (
                user_id BIGINT NOT NULL,
                guild_id BIGINT NOT NULL,
                prestige_level INTEGER NOT NULL DEFAULT 0,
                bosses_killed INTEGER NOT NULL DEFAULT 0,
                heists_won INTEGER NOT NULL DEFAULT 0,
                heals_given INTEGER NOT NULL DEFAULT 0,
                mythic_kills INTEGER NOT NULL DEFAULT 0,
                crafts_done INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, guild_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS achievements (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                achievement_id TEXT NOT NULL,
                unlocked_at REAL NOT NULL,
                PRIMARY KEY (guild_id, user_id, achievement_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS guild_events (
                guild_id BIGINT PRIMARY KEY,
                event_type TEXT NOT NULL,
                multiplier REAL NOT NULL DEFAULT 1.0,
                ends_at REAL NOT NULL
            )
            """,
        )
        for ddl in statements:
            await self.conn.execute(ddl)

        if self.is_postgres:
            cursor = await self.conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = ANY (current_schemas(true))
                  AND table_name = 'boss_sessions'
                  AND column_name = 'phases_announced'
                """,
            )
            if await cursor.fetchone() is None:
                await self.conn.execute(
                    "ALTER TABLE boss_sessions ADD COLUMN phases_announced INTEGER NOT NULL DEFAULT 0",
                )
        else:
            cursor = await self.conn.execute("PRAGMA table_info(boss_sessions)")
            cols = {row[1] for row in await cursor.fetchall()}
            if "phases_announced" not in cols:
                await self.conn.execute(
                    "ALTER TABLE boss_sessions ADD COLUMN phases_announced INTEGER NOT NULL DEFAULT 0",
                )
        await self.conn.commit()

    async def _load_config_no_lock(self, guild_id: int) -> dict[str, float]:
        cursor = await self.conn.execute(
            "SELECT setting, value FROM guild_config WHERE guild_id = ?",
            (guild_id,),
        )
        rows = await cursor.fetchall()
        values = {name: spec.default for name, spec in config.LIVE_SETTINGS.items()}
        for row in rows:
            setting = str(row["setting"])
            if setting in values:
                values[setting] = float(row["value"])
        self._config_cache[guild_id] = values
        return values

    async def is_one_time_job_complete(self, job_id: str) -> bool:
        cursor = await self.conn.execute(
            "SELECT job_id FROM one_time_jobs WHERE job_id = ?",
            (job_id,),
        )
        return await cursor.fetchone() is not None

    async def mark_one_time_job_complete(self, job_id: str) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT INTO one_time_jobs (job_id, completed_at)
                VALUES (?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    completed_at = excluded.completed_at
                """,
                (job_id, time.time()),
            )
            await self.conn.commit()

    async def grant_launch_member_once(
        self,
        job_id: str,
        guild_id: int,
        user_id: int,
        amount: float,
        weapon_id: str,
        armor_id: str,
    ) -> bool:
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.conn.execute(
                    """
                    SELECT user_id
                    FROM one_time_member_jobs
                    WHERE job_id = ? AND guild_id = ? AND user_id = ?
                    """,
                    (job_id, guild_id, user_id),
                )
                if await cursor.fetchone() is not None:
                    await self.conn.rollback()
                    return False
                await self._ensure_user_no_lock(user_id, guild_id)
                await self.conn.execute(
                    """
                    UPDATE users
                    SET wallet = wallet + ?,
                        total_earned = total_earned + ?
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (amount, amount, user_id, guild_id),
                )
                for item_id, slot in ((weapon_id, "weapon"), (armor_id, "armor")):
                    await self.conn.execute(
                        """
                        INSERT INTO inventory (guild_id, user_id, item_id, quantity)
                        VALUES (?, ?, ?, 1)
                        ON CONFLICT(guild_id, user_id, item_id) DO UPDATE SET
                            quantity = inventory.quantity + 1
                        """,
                        (guild_id, user_id, item_id),
                    )
                    inst_cursor = await self.conn.execute(
                        """
                        INSERT INTO gear_instances (
                            guild_id, user_id, item_id, enhancement_level, is_broken, created_at
                        )
                        VALUES (?, ?, ?, 0, 0, ?)
                        """,
                        (guild_id, user_id, item_id, time.time()),
                    )
                    instance_id = int(inst_cursor.lastrowid)
                    await self.conn.execute(
                        """
                        INSERT INTO equipment (guild_id, user_id, slot, item_id, gear_instance_id)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(guild_id, user_id, slot) DO UPDATE SET
                            item_id = excluded.item_id,
                            gear_instance_id = excluded.gear_instance_id
                        """,
                        (guild_id, user_id, slot, item_id, instance_id),
                    )
                await self.conn.execute(
                    """
                    INSERT INTO one_time_member_jobs (job_id, guild_id, user_id, completed_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (job_id, guild_id, user_id, time.time()),
                )
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
            return True

    async def get_config_values(self, guild_id: int) -> dict[str, float]:
        cached = self._config_cache.get(guild_id)
        if cached is not None:
            return dict(cached)
        return dict(await self._load_config_no_lock(guild_id))

    async def get_config_value(self, guild_id: int, setting: str) -> float:
        if setting not in config.LIVE_SETTINGS:
            msg = f"Unknown setting: {setting}"
            raise KeyError(msg)
        values = await self.get_config_values(guild_id)
        return values[setting]

    async def set_config_value(self, guild_id: int, setting: str, value: float) -> float:
        spec = config.LIVE_SETTINGS.get(setting)
        if spec is None:
            msg = f"Unknown setting: {setting}"
            raise KeyError(msg)
        normalized = spec.validate(float(value))
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT INTO guild_config (guild_id, setting, value, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, setting) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (guild_id, setting, normalized, time.time()),
            )
            await self.conn.commit()
            self._config_cache.pop(guild_id, None)
        return float(normalized)

    async def reset_config_value(self, guild_id: int, setting: str) -> None:
        if setting not in config.LIVE_SETTINGS:
            msg = f"Unknown setting: {setting}"
            raise KeyError(msg)
        async with self._write_lock:
            await self.conn.execute(
                "DELETE FROM guild_config WHERE guild_id = ? AND setting = ?",
                (guild_id, setting),
            )
            await self.conn.commit()
            self._config_cache.pop(guild_id, None)

    async def custom_config_names(self, guild_id: int) -> set[str]:
        cursor = await self.conn.execute(
            "SELECT setting FROM guild_config WHERE guild_id = ?",
            (guild_id,),
        )
        return {
            setting
            for row in await cursor.fetchall()
            if (setting := str(row["setting"])) in config.LIVE_SETTINGS
        }

    async def _get_guild_channels_row(self, guild_id: int) -> Any | None:
        cursor = await self.conn.execute(
            """
            SELECT main_channel_id, designated_channel_id, split_announcement_channels,
                   scourge_event_enabled
            FROM guild_channels
            WHERE guild_id = ?
            """,
            (guild_id,),
        )
        return await cursor.fetchone()

    async def get_main_channel_id(self, guild_id: int) -> int | None:
        row = await self._get_guild_channels_row(guild_id)
        if row is None or row["main_channel_id"] is None:
            return None
        return int(row["main_channel_id"])

    async def get_designated_channel_id(self, guild_id: int) -> int | None:
        row = await self._get_guild_channels_row(guild_id)
        if row is None or row["designated_channel_id"] is None:
            return None
        return int(row["designated_channel_id"])

    async def get_split_announcement_channels(self, guild_id: int) -> bool:
        row = await self._get_guild_channels_row(guild_id)
        if row is None:
            return False
        return bool(int(row["split_announcement_channels"]))

    async def get_scourge_event_enabled(self, guild_id: int) -> bool:
        row = await self._get_guild_channels_row(guild_id)
        if row is None:
            return True
        return bool(int(row["scourge_event_enabled"]))

    async def get_guild_channel_settings(self, guild_id: int) -> dict[str, int | bool | None]:
        row = await self._get_guild_channels_row(guild_id)
        if row is None:
            return {
                "main_channel_id": None,
                "designated_channel_id": None,
                "split_announcement_channels": False,
                "scourge_event_enabled": True,
            }
        return {
            "main_channel_id": (
                int(row["main_channel_id"]) if row["main_channel_id"] is not None else None
            ),
            "designated_channel_id": (
                int(row["designated_channel_id"])
                if row["designated_channel_id"] is not None
                else None
            ),
            "split_announcement_channels": bool(int(row["split_announcement_channels"])),
            "scourge_event_enabled": bool(int(row["scourge_event_enabled"])),
        }

    async def set_main_channel_id(self, guild_id: int, channel_id: int | None) -> None:
        async with self._write_lock:
            if channel_id is None:
                await self.conn.execute(
                    """
                    UPDATE guild_channels
                    SET main_channel_id = NULL
                    WHERE guild_id = ?
                    """,
                    (guild_id,),
                )
                await self._prune_guild_channels_row(guild_id)
            else:
                await self.conn.execute(
                    """
                    INSERT INTO guild_channels (guild_id, main_channel_id)
                    VALUES (?, ?)
                    ON CONFLICT(guild_id) DO UPDATE SET
                        main_channel_id = excluded.main_channel_id
                    """,
                    (guild_id, channel_id),
                )
            await self.conn.commit()

    async def set_designated_channel_id(self, guild_id: int, channel_id: int | None) -> None:
        async with self._write_lock:
            if channel_id is None:
                await self.conn.execute(
                    """
                    UPDATE guild_channels
                    SET designated_channel_id = NULL
                    WHERE guild_id = ?
                    """,
                    (guild_id,),
                )
                await self._prune_guild_channels_row(guild_id)
            else:
                await self.conn.execute(
                    """
                    INSERT INTO guild_channels (guild_id, designated_channel_id)
                    VALUES (?, ?)
                    ON CONFLICT(guild_id) DO UPDATE SET
                        designated_channel_id = excluded.designated_channel_id
                    """,
                    (guild_id, channel_id),
                )
            await self.conn.commit()

    async def set_split_announcement_channels(self, guild_id: int, enabled: bool) -> None:
        async with self._write_lock:
            if enabled:
                await self.conn.execute(
                    """
                    INSERT INTO guild_channels (guild_id, split_announcement_channels)
                    VALUES (?, 1)
                    ON CONFLICT(guild_id) DO UPDATE SET
                        split_announcement_channels = 1
                    """,
                    (guild_id,),
                )
            else:
                await self.conn.execute(
                    """
                    UPDATE guild_channels
                    SET split_announcement_channels = 0
                    WHERE guild_id = ?
                    """,
                    (guild_id,),
                )
                await self._prune_guild_channels_row(guild_id)
            await self.conn.commit()

    async def set_scourge_event_enabled(self, guild_id: int, enabled: bool) -> None:
        async with self._write_lock:
            if enabled:
                await self.conn.execute(
                    """
                    INSERT INTO guild_channels (guild_id, scourge_event_enabled)
                    VALUES (?, 1)
                    ON CONFLICT(guild_id) DO UPDATE SET
                        scourge_event_enabled = 1
                    """,
                    (guild_id,),
                )
            else:
                await self.conn.execute(
                    """
                    INSERT INTO guild_channels (guild_id, scourge_event_enabled)
                    VALUES (?, 0)
                    ON CONFLICT(guild_id) DO UPDATE SET
                        scourge_event_enabled = 0
                    """,
                    (guild_id,),
                )
                await self.conn.execute(
                    "DELETE FROM scourge_pots WHERE guild_id = ?",
                    (guild_id,),
                )
                await self.conn.execute(
                    "DELETE FROM scourge_events WHERE guild_id = ?",
                    (guild_id,),
                )
            await self.conn.commit()
            if enabled:
                await self._prune_guild_channels_row(guild_id)

    async def _prune_guild_channels_row(self, guild_id: int) -> None:
        """Remove empty guild_channels rows after partial clears."""
        await self.conn.execute(
            """
            DELETE FROM guild_channels
            WHERE guild_id = ?
              AND main_channel_id IS NULL
              AND designated_channel_id IS NULL
              AND split_announcement_channels = 0
              AND scourge_event_enabled = 1
            """,
            (guild_id,),
        )

    async def _ensure_user_no_lock(self, user_id: int, guild_id: int) -> None:
        await self.conn.execute(
            """
            INSERT OR IGNORE INTO users (user_id, guild_id)
            VALUES (?, ?)
            """,
            (user_id, guild_id),
        )

    async def ensure_user(self, user_id: int, guild_id: int) -> None:
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            await self.conn.commit()

    async def ensure_users(self, user_ids: Iterable[int], guild_id: int) -> None:
        async with self._write_lock:
            for user_id in set(user_ids):
                await self._ensure_user_no_lock(user_id, guild_id)
            await self.conn.commit()

    async def get_user(self, user_id: int, guild_id: int) -> aiosqlite.Row:
        await self.ensure_user(user_id, guild_id)
        cursor = await self.conn.execute(
            "SELECT * FROM users WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        row = await cursor.fetchone()
        if row is None:
            msg = "Expected user row to exist"
            raise RuntimeError(msg)
        return row

    async def get_balance(self, user_id: int, guild_id: int) -> float:
        row = await self.get_user(user_id, guild_id)
        return float(row["wallet"])

    async def get_bank(self, user_id: int, guild_id: int) -> float:
        row = await self.get_user(user_id, guild_id)
        return float(row["bank"])

    async def get_bank_expansions(self, user_id: int, guild_id: int) -> int:
        row = await self.get_user(user_id, guild_id)
        try:
            return max(0, int(row["bank_expansions"]))
        except (KeyError, TypeError, ValueError):
            return 0

    async def get_bank_capacity(self, user_id: int, guild_id: int) -> float:
        from utils.bank_capacity import bank_capacity

        expansions = await self.get_bank_expansions(user_id, guild_id)
        return bank_capacity(expansions)

    async def get_bank_deposit_room(self, user_id: int, guild_id: int) -> float:
        from utils.bank_capacity import bank_deposit_room

        bank = await self.get_bank(user_id, guild_id)
        expansions = await self.get_bank_expansions(user_id, guild_id)
        return bank_deposit_room(bank, expansions)

    async def expand_bank_capacity(self, user_id: int, guild_id: int) -> tuple[bool, str]:
        """Buy one bank expansion token (+capacity) for the configured nugget cost."""
        import config

        cost = float(config.BANK_EXPANSION_TOKEN_COST)
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            cursor = await self.conn.execute(
                "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            row = await cursor.fetchone()
            if row is None or float(row["wallet"]) < cost:
                await self.conn.commit()
                return False, "insufficient_wallet"
            await self.conn.execute(
                """
                UPDATE users
                SET wallet = wallet - ?, bank_expansions = bank_expansions + 1
                WHERE user_id = ? AND guild_id = ?
                """,
                (cost, user_id, guild_id),
            )
            await self.conn.commit()
        return True, "ok"

    async def get_net_worth(self, user_id: int, guild_id: int) -> float:
        row = await self.get_user(user_id, guild_id)
        return float(row["wallet"]) + float(row["bank"])

    async def deposit_to_bank(self, user_id: int, guild_id: int, amount: float) -> bool:
        if amount <= 0:
            return False
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            cursor = await self.conn.execute(
                "SELECT wallet, bank, bank_expansions FROM users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            row = await cursor.fetchone()
            if row is None:
                await self.conn.commit()
                return False
            wallet = float(row["wallet"])
            if wallet <= 0:
                await self.conn.commit()
                return False
            from utils.bank_capacity import bank_deposit_room

            expansions = max(0, int(row["bank_expansions"] or 0))
            room = bank_deposit_room(float(row["bank"]), expansions)
            if room <= 0:
                await self.conn.commit()
                return False
            actual = min(amount, wallet, room)
            if actual <= 0:
                await self.conn.commit()
                return False
            await self.conn.execute(
                """
                UPDATE users
                SET wallet = wallet - ?, bank = bank + ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (actual, actual, user_id, guild_id),
            )
            await self.conn.commit()
            return True

    async def withdraw_from_bank(self, user_id: int, guild_id: int, amount: float) -> bool:
        if amount <= 0:
            return False
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            cursor = await self.conn.execute(
                "SELECT bank FROM users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            row = await cursor.fetchone()
            if row is None or float(row["bank"]) < amount:
                await self.conn.commit()
                return False
            await self.conn.execute(
                """
                UPDATE users
                SET wallet = wallet + ?, bank = bank - ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (amount, amount, user_id, guild_id),
            )
            await self.conn.commit()
            return True

    async def deposit_all_to_bank(self, user_id: int, guild_id: int) -> float:
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            cursor = await self.conn.execute(
                """
                SELECT wallet, bank, bank_expansions FROM users
                WHERE user_id = ? AND guild_id = ?
                """,
                (user_id, guild_id),
            )
            row = await cursor.fetchone()
            wallet = float(row["wallet"]) if row is not None else 0.0
            if wallet <= 0:
                await self.conn.commit()
                return 0.0
            from utils.bank_capacity import bank_deposit_room

            expansions = max(0, int(row["bank_expansions"] or 0)) if row is not None else 0
            bank = float(row["bank"]) if row is not None else 0.0
            room = bank_deposit_room(bank, expansions)
            amount = min(wallet, room)
            if amount <= 0:
                await self.conn.commit()
                return 0.0
            await self.conn.execute(
                """
                UPDATE users
                SET wallet = wallet - ?, bank = bank + ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (amount, amount, user_id, guild_id),
            )
            await self.conn.commit()
            return amount

    async def withdraw_all_from_bank(self, user_id: int, guild_id: int) -> float:
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            cursor = await self.conn.execute(
                "SELECT bank FROM users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            row = await cursor.fetchone()
            amount = float(row["bank"]) if row is not None else 0.0
            if amount <= 0:
                await self.conn.commit()
                return 0.0
            await self.conn.execute(
                """
                UPDATE users
                SET wallet = wallet + ?, bank = 0
                WHERE user_id = ? AND guild_id = ?
                """,
                (amount, user_id, guild_id),
            )
            await self.conn.commit()
            return amount

    async def credit_wallet(
        self,
        user_id: int,
        guild_id: int,
        amount: float,
        *,
        apply_bonuses: bool = True,
    ) -> None:
        if amount <= 0:
            return
        if apply_bonuses:
            amount = await self._apply_income_bonuses(user_id, guild_id, amount)
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            await self.conn.execute(
                """
                UPDATE users
                SET wallet = wallet + ?,
                    total_earned = total_earned + ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (amount, amount, user_id, guild_id),
            )
            await self.conn.commit()

    async def credit_wallets(self, user_ids: Iterable[int], guild_id: int, amount: float) -> int:
        unique_ids = set(user_ids)
        if amount <= 0 or not unique_ids:
            return 0
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                for user_id in unique_ids:
                    await self._ensure_user_no_lock(user_id, guild_id)
                    await self.conn.execute(
                        """
                        UPDATE users
                        SET wallet = wallet + ?,
                            total_earned = total_earned + ?
                        WHERE user_id = ? AND guild_id = ?
                        """,
                        (amount, amount, user_id, guild_id),
                    )
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
            return len(unique_ids)

    async def set_wallet(self, user_id: int, guild_id: int, amount: float) -> None:
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            await self.conn.execute(
                """
                UPDATE users
                SET wallet = ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (amount, user_id, guild_id),
            )
            await self.conn.commit()

    async def reset_user(self, user_id: int, guild_id: int) -> None:
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            await self.conn.execute(
                """
                UPDATE users
                SET wallet = 0,
                    bank = 0,
                    bank_expansions = 0,
                    last_daily = 0,
                    last_heist = 0,
                    last_active_ts = 0,
                    arrested_until = 0,
                    downed_until = 0,
                    total_earned = 0,
                    messages_sent = 0
                WHERE user_id = ? AND guild_id = ?
                """,
                (user_id, guild_id),
            )
            await self.conn.execute(
                "DELETE FROM inventory WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            await self.conn.execute(
                "DELETE FROM equipment WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            await self.conn.execute(
                "DELETE FROM combat_state WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            await self.conn.execute(
                "DELETE FROM user_progress WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            await self.conn.execute(
                "DELETE FROM user_character WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            await self.conn.execute(
                "DELETE FROM player_avatar_unlocks WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            await self.conn.execute(
                "DELETE FROM crew_members WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            await self.conn.execute(
                "DELETE FROM loadout_presets WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            await self.conn.execute(
                "DELETE FROM dungeon_runs WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            await self.conn.execute(
                "DELETE FROM duel_elo WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            await self.conn.execute(
                "DELETE FROM achievements WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            await self.conn.commit()

    async def buy_item(
        self,
        user_id: int,
        guild_id: int,
        item_id: str,
        unit_price: float,
        quantity: int = 1,
    ) -> bool:
        if unit_price <= 0:
            return False
        qty = max(1, min(int(quantity), config.SHOP_MAX_BUY_QUANTITY))
        total_cents = _spendable_cents(unit_price) * qty
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                await self._ensure_user_no_lock(user_id, guild_id)
                cursor = await self.conn.execute(
                    "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                    (user_id, guild_id),
                )
                row = await cursor.fetchone()
                if row is None or _spendable_cents(row["wallet"]) < total_cents:
                    await self.conn.rollback()
                    return False
                total_price = total_cents / 100.0
                await self.conn.execute(
                    """
                    UPDATE users
                    SET wallet = wallet - ?
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (total_price, user_id, guild_id),
                )
                await self.conn.execute(
                    """
                    INSERT INTO inventory (guild_id, user_id, item_id, quantity)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(guild_id, user_id, item_id) DO UPDATE SET
                        quantity = inventory.quantity + excluded.quantity
                    """,
                    (guild_id, user_id, item_id, qty),
                )
                from items import get_item, is_gear_instance_item

                item = get_item(item_id)
                if item is not None and is_gear_instance_item(item):
                    import time

                    now = time.time()
                    for _ in range(qty):
                        await self.conn.execute(
                            """
                            INSERT INTO gear_instances (
                                guild_id, user_id, item_id, enhancement_level, is_broken, created_at
                            )
                            VALUES (?, ?, ?, 0, 0, ?)
                            """,
                            (guild_id, user_id, item_id, now),
                        )
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
            return True

    async def sell_one_item(
        self,
        user_id: int,
        guild_id: int,
        item_id: str,
        unit_refund: float,
        quantity: int = 1,
    ) -> int:
        """Sell up to quantity copies. Returns how many were sold (0 on failure)."""
        if unit_refund <= 0:
            return 0
        want = max(1, min(int(quantity), config.SHOP_MAX_SELL_QUANTITY))
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                await self._ensure_user_no_lock(user_id, guild_id)
                cursor = await self.conn.execute(
                    """
                    SELECT quantity
                    FROM inventory
                    WHERE guild_id = ? AND user_id = ? AND item_id = ?
                    """,
                    (guild_id, user_id, item_id),
                )
                row = await cursor.fetchone()
                if row is None or int(row["quantity"]) <= 0:
                    await self.conn.rollback()
                    return 0
                owned = int(row["quantity"])
                sold = min(want, owned)
                new_qty = owned - sold
                if new_qty <= 0:
                    await self.conn.execute(
                        """
                        DELETE FROM inventory
                        WHERE guild_id = ? AND user_id = ? AND item_id = ?
                        """,
                        (guild_id, user_id, item_id),
                    )
                    await self.conn.execute(
                        """
                        DELETE FROM equipment
                        WHERE guild_id = ? AND user_id = ? AND item_id = ?
                        """,
                        (guild_id, user_id, item_id),
                    )
                else:
                    await self.conn.execute(
                        """
                        UPDATE inventory
                        SET quantity = ?
                        WHERE guild_id = ? AND user_id = ? AND item_id = ?
                        """,
                        (new_qty, guild_id, user_id, item_id),
                    )
                total_refund = unit_refund * sold
                await self.conn.execute(
                    """
                    UPDATE users
                    SET wallet = wallet + ?,
                        total_earned = total_earned + ?
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (total_refund, total_refund, user_id, guild_id),
                )
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
            return sold

    async def get_inventory(self, user_id: int, guild_id: int) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT item_id, quantity
            FROM inventory
            WHERE guild_id = ? AND user_id = ? AND quantity > 0
            ORDER BY item_id
            """,
            (guild_id, user_id),
        )
        return list(await cursor.fetchall())

    async def pick_gear_instance_for_equip(
        self, user_id: int, guild_id: int, item_id: str,
    ) -> int | None:
        from items import get_item, is_gear_instance_item

        item = get_item(item_id)
        if not is_gear_instance_item(item):
            return None
        records = await self.get_equipment_records(user_id, guild_id)
        equipped_ids = {
            int(rec["gear_instance_id"])
            for rec in records.values()
            if rec.get("gear_instance_id") is not None
        }
        instances = await self.list_gear_instances(user_id, guild_id)
        candidates = [
            row
            for row in instances
            if str(row["item_id"]) == item_id
            and int(row["instance_id"]) not in equipped_ids
            and not bool(int(row["is_broken"]))
        ]
        if not candidates:
            qty = await self.get_inventory_quantity(user_id, guild_id, item_id)
            if qty > 0:
                return await self.create_gear_instance(user_id, guild_id, item_id)
            return None
        candidates.sort(
            key=lambda row: (-int(row["enhancement_level"]), int(row["instance_id"])),
        )
        return int(candidates[0]["instance_id"])

    async def equip_item(self, user_id: int, guild_id: int, slot: str, item_id: str) -> bool:
        instance_id = await self.pick_gear_instance_for_equip(user_id, guild_id, item_id)
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.conn.execute(
                    """
                    SELECT quantity
                    FROM inventory
                    WHERE guild_id = ? AND user_id = ? AND item_id = ?
                    """,
                    (guild_id, user_id, item_id),
                )
                row = await cursor.fetchone()
                if row is None or int(row["quantity"]) <= 0:
                    await self.conn.rollback()
                    return False
                await self.conn.execute(
                    """
                    INSERT INTO equipment (guild_id, user_id, slot, item_id, gear_instance_id)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(guild_id, user_id, slot) DO UPDATE SET
                        item_id = excluded.item_id,
                        gear_instance_id = excluded.gear_instance_id
                    """,
                    (guild_id, user_id, slot, item_id, instance_id),
                )
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
            return True

    async def equip_gear_item(self, user_id: int, guild_id: int, item_id: str) -> str | None:
        """Equip with dual-wield rules. Returns slot name or None if not owned."""
        from items import get_item
        from utils.loadout import equip_target_slot

        item = get_item(item_id)
        if item is None:
            return None

        equipment = await self.get_equipment(user_id, guild_id)
        records = await self.get_equipment_records(user_id, guild_id)
        slot = equip_target_slot(item, equipment)
        instance_id = await self.pick_gear_instance_for_equip(user_id, guild_id, item_id)

        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.conn.execute(
                    """
                    SELECT quantity
                    FROM inventory
                    WHERE guild_id = ? AND user_id = ? AND item_id = ?
                    """,
                    (guild_id, user_id, item_id),
                )
                row = await cursor.fetchone()
                if row is None or int(row["quantity"]) <= 0:
                    await self.conn.rollback()
                    return None

                if item.category == "weapon" and slot == "weapon":
                    current_weapon_id = equipment.get("weapon")
                    current_weapon = get_item(current_weapon_id) if current_weapon_id else None
                    if current_weapon is not None and current_weapon.category == "gun":
                        weapon_rec = records.get("weapon", {})
                        off_inst = weapon_rec.get("gear_instance_id")
                        await self.conn.execute(
                            """
                            INSERT INTO equipment (guild_id, user_id, slot, item_id, gear_instance_id)
                            VALUES (?, ?, 'off_hand', ?, ?)
                            ON CONFLICT(guild_id, user_id, slot) DO UPDATE SET
                                item_id = excluded.item_id,
                                gear_instance_id = excluded.gear_instance_id
                            """,
                            (guild_id, user_id, current_weapon_id, off_inst),
                        )

                await self.conn.execute(
                    """
                    INSERT INTO equipment (guild_id, user_id, slot, item_id, gear_instance_id)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(guild_id, user_id, slot) DO UPDATE SET
                        item_id = excluded.item_id,
                        gear_instance_id = excluded.gear_instance_id
                    """,
                    (guild_id, user_id, slot, item_id, instance_id),
                )
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
            return slot

    async def grant_item(
        self, user_id: int, guild_id: int, item_id: str, *, equip_slot: str | None = None
    ) -> int | None:
        from items import get_item, is_gear_instance_item

        instance_id: int | None = None
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                item = get_item(item_id)
                if item is not None and is_gear_instance_item(item):
                    import time

                    cursor = await self.conn.execute(
                        """
                        INSERT INTO gear_instances (
                            guild_id, user_id, item_id, enhancement_level, is_broken, created_at
                        )
                        VALUES (?, ?, ?, 0, 0, ?)
                        """,
                        (guild_id, user_id, item_id, time.time()),
                    )
                    instance_id = int(cursor.lastrowid)
                await self.conn.execute(
                    """
                    INSERT INTO inventory (guild_id, user_id, item_id, quantity)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT(guild_id, user_id, item_id) DO UPDATE SET
                        quantity = inventory.quantity + 1
                    """,
                    (guild_id, user_id, item_id),
                )
                if equip_slot is not None:
                    await self.conn.execute(
                        """
                        INSERT INTO equipment (guild_id, user_id, slot, item_id, gear_instance_id)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(guild_id, user_id, slot) DO UPDATE SET
                            item_id = excluded.item_id,
                            gear_instance_id = excluded.gear_instance_id
                        """,
                        (guild_id, user_id, equip_slot, item_id, instance_id),
                    )
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
        return instance_id

    async def get_equipment(self, user_id: int, guild_id: int) -> dict[str, str]:
        cursor = await self.conn.execute(
            """
            SELECT slot, item_id
            FROM equipment
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        )
        return {str(row["slot"]): str(row["item_id"]) for row in await cursor.fetchall()}

    async def get_equipment_records(self, user_id: int, guild_id: int) -> dict[str, dict[str, str | int | None]]:
        cursor = await self.conn.execute(
            """
            SELECT slot, item_id, gear_instance_id
            FROM equipment
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        )
        out: dict[str, dict[str, str | int | None]] = {}
        for row in await cursor.fetchall():
            inst = row["gear_instance_id"]
            out[str(row["slot"])] = {
                "item_id": str(row["item_id"]),
                "gear_instance_id": int(inst) if inst is not None else None,
            }
        return out

    async def create_gear_instance(self, user_id: int, guild_id: int, item_id: str) -> int:
        import time

        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                INSERT INTO gear_instances (guild_id, user_id, item_id, enhancement_level, is_broken, created_at)
                VALUES (?, ?, ?, 0, 0, ?)
                """,
                (guild_id, user_id, item_id, time.time()),
            )
            await self.conn.commit()
            return int(cursor.lastrowid)

    async def sync_gear_instances_from_inventory(self, user_id: int, guild_id: int) -> int:
        """Create gear_instances rows for owned gear that lacks enhanceable copies."""
        from items import get_item, is_gear_instance_item

        created = 0
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                await self._ensure_user_no_lock(user_id, guild_id)
                cursor = await self.conn.execute(
                    """
                    SELECT item_id, quantity
                    FROM inventory
                    WHERE guild_id = ? AND user_id = ? AND quantity > 0
                    """,
                    (guild_id, user_id),
                )
                import time

                now = time.time()
                for row in await cursor.fetchall():
                    item = get_item(str(row["item_id"]))
                    if not is_gear_instance_item(item):
                        continue
                    owned = int(row["quantity"])
                    count_cursor = await self.conn.execute(
                        """
                        SELECT COUNT(*) AS cnt
                        FROM gear_instances
                        WHERE guild_id = ? AND user_id = ? AND item_id = ?
                        """,
                        (guild_id, user_id, str(row["item_id"])),
                    )
                    have = int((await count_cursor.fetchone())["cnt"])
                    missing = max(0, owned - have)
                    for _ in range(missing):
                        await self.conn.execute(
                            """
                            INSERT INTO gear_instances (
                                guild_id, user_id, item_id, enhancement_level, is_broken, created_at
                            )
                            VALUES (?, ?, ?, 0, 0, ?)
                            """,
                            (guild_id, user_id, str(row["item_id"]), now),
                        )
                        created += 1
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
        return created

    async def get_gear_instance(self, instance_id: int, guild_id: int) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(
            """
            SELECT * FROM gear_instances
            WHERE instance_id = ? AND guild_id = ?
            """,
            (instance_id, guild_id),
        )
        return await cursor.fetchone()

    async def list_gear_instances(self, user_id: int, guild_id: int) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT * FROM gear_instances
            WHERE guild_id = ? AND user_id = ?
            ORDER BY instance_id DESC
            """,
            (guild_id, user_id),
        )
        return list(await cursor.fetchall())

    async def list_broken_gear_instances(self, user_id: int, guild_id: int) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT * FROM gear_instances
            WHERE guild_id = ? AND user_id = ? AND is_broken = 1
            ORDER BY instance_id DESC
            """,
            (guild_id, user_id),
        )
        return list(await cursor.fetchall())

    async def ensure_gear_instance_for_equipped(
        self, user_id: int, guild_id: int, slot: str, item_id: str,
    ) -> int:
        records = await self.get_equipment_records(user_id, guild_id)
        rec = records.get(slot)
        if rec and rec.get("gear_instance_id"):
            return int(rec["gear_instance_id"])
        instance_id = await self.create_gear_instance(user_id, guild_id, item_id)
        async with self._write_lock:
            await self.conn.execute(
                """
                UPDATE equipment SET gear_instance_id = ?
                WHERE guild_id = ? AND user_id = ? AND slot = ?
                """,
                (instance_id, guild_id, user_id, slot),
            )
            await self.conn.commit()
        return instance_id

    async def set_gear_instance_level(
        self, instance_id: int, guild_id: int, level: int, *, broken: bool,
    ) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                UPDATE gear_instances
                SET enhancement_level = ?, is_broken = ?
                WHERE instance_id = ? AND guild_id = ?
                """,
                (level, 1 if broken else 0, instance_id, guild_id),
            )
            await self.conn.commit()

    async def repair_gear_instance(self, instance_id: int, guild_id: int) -> bool:
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                UPDATE gear_instances SET is_broken = 0
                WHERE instance_id = ? AND guild_id = ? AND is_broken = 1
                """,
                (instance_id, guild_id),
            )
            await self.conn.commit()
            return cursor.rowcount > 0

    async def equip_gear_instance(self, user_id: int, guild_id: int, instance_id: int) -> str | None:
        from items import accessory_equip_slot, get_item, is_accessory
        from utils.loadout import equip_target_slot

        row = await self.get_gear_instance(instance_id, guild_id)
        if row is None or int(row["user_id"]) != user_id:
            return None
        item = get_item(str(row["item_id"]))
        if item is None:
            return None
        equipment = await self.get_equipment(user_id, guild_id)
        if is_accessory(item):
            slot = accessory_equip_slot(item)
        else:
            slot = equip_target_slot(item, equipment)
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT INTO equipment (guild_id, user_id, slot, item_id, gear_instance_id)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id, slot) DO UPDATE SET
                    item_id = excluded.item_id,
                    gear_instance_id = excluded.gear_instance_id
                """,
                (guild_id, user_id, slot, item.id, instance_id),
            )
            await self.conn.commit()
        return slot

    async def list_raid_adds(self, guild_id: int) -> list[aiosqlite.Row]:
        import time

        now = time.time()
        cursor = await self.conn.execute(
            """
            SELECT * FROM boss_raid_adds
            WHERE guild_id = ? AND expires_at > ?
            ORDER BY add_id ASC
            """,
            (guild_id, now),
        )
        return list(await cursor.fetchall())

    async def create_raid_add(
        self, guild_id: int, add_type: str, hp: float, max_hp: float, expires_at: float,
    ) -> int:
        import time

        async with self._write_lock:
            count_cursor = await self.conn.execute(
                "SELECT COUNT(*) AS c FROM boss_raid_adds WHERE guild_id = ?",
                (guild_id,),
            )
            count_row = await count_cursor.fetchone()
            if count_row and int(count_row["c"]) >= config.BOSS_ADD_MAX_CONCURRENT:
                oldest = await self.conn.execute(
                    """
                    SELECT add_id FROM boss_raid_adds
                    WHERE guild_id = ? ORDER BY add_id ASC LIMIT 1
                    """,
                    (guild_id,),
                )
                old_row = await oldest.fetchone()
                if old_row:
                    await self.conn.execute(
                        "DELETE FROM boss_raid_adds WHERE add_id = ?",
                        (int(old_row["add_id"]),),
                    )
            cursor = await self.conn.execute(
                """
                INSERT INTO boss_raid_adds (guild_id, add_type, hp, max_hp, spawned_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (guild_id, add_type, hp, max_hp, time.time(), expires_at),
            )
            await self.conn.commit()
            return int(cursor.lastrowid)

    async def damage_raid_add(self, add_id: int, guild_id: int, damage: float) -> tuple[float, bool]:
        async with self._write_lock:
            cursor = await self.conn.execute(
                "SELECT hp FROM boss_raid_adds WHERE add_id = ? AND guild_id = ?",
                (add_id, guild_id),
            )
            row = await cursor.fetchone()
            if row is None:
                return 0.0, False
            new_hp = max(0.0, float(row["hp"]) - damage)
            if new_hp <= 0:
                await self.conn.execute(
                    "DELETE FROM boss_raid_adds WHERE add_id = ? AND guild_id = ?",
                    (add_id, guild_id),
                )
                await self.conn.commit()
                return 0.0, True
            await self.conn.execute(
                "UPDATE boss_raid_adds SET hp = ? WHERE add_id = ? AND guild_id = ?",
                (new_hp, add_id, guild_id),
            )
            await self.conn.commit()
            return new_hp, False

    async def clear_raid_adds(self, guild_id: int) -> None:
        async with self._write_lock:
            await self.conn.execute("DELETE FROM boss_raid_adds WHERE guild_id = ?", (guild_id,))
            await self.conn.commit()

    async def get_combat_loadout(self, user_id: int, guild_id: int):
        from utils.loadout import parse_resolved_loadout

        records = await self.get_equipment_records(user_id, guild_id)
        unstable = await self.list_unstable_slots(user_id, guild_id)
        instances: dict[int, aiosqlite.Row] = {}
        for rec in records.values():
            inst_id = rec.get("gear_instance_id")
            if inst_id is not None:
                row = await self.get_gear_instance(int(inst_id), guild_id)
                if row is not None:
                    instances[int(inst_id)] = row
        return parse_resolved_loadout(records, instances=instances, unstable_slots=unstable)

    async def sync_combat_hp(self, user_id: int, guild_id: int, max_hp: float) -> aiosqlite.Row:
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.conn.execute(
                    """
                    SELECT hp, max_hp
                    FROM combat_state
                    WHERE guild_id = ? AND user_id = ?
                    """,
                    (guild_id, user_id),
                )
                row = await cursor.fetchone()
                if row is None:
                    await self.conn.execute(
                        """
                        INSERT INTO combat_state (guild_id, user_id, hp, max_hp)
                        VALUES (?, ?, ?, ?)
                        """,
                        (guild_id, user_id, max_hp, max_hp),
                    )
                else:
                    old_max = float(row["max_hp"])
                    old_hp = float(row["hp"])
                    hp = (
                        max_hp if old_max <= 0 else min(max_hp, old_hp + max(0.0, max_hp - old_max))
                    )
                    await self.conn.execute(
                        """
                        UPDATE combat_state
                        SET hp = ?, max_hp = ?
                        WHERE guild_id = ? AND user_id = ?
                        """,
                        (hp, max_hp, guild_id, user_id),
                    )
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
            cursor = await self.conn.execute(
                "SELECT hp, max_hp FROM combat_state WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            row = await cursor.fetchone()
        if row is None:
            msg = "Expected combat state row"
            raise RuntimeError(msg)
        return row

    async def heal_player(
        self,
        user_id: int,
        guild_id: int,
        amount: float,
        max_hp: float,
    ) -> tuple[float, float]:
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.conn.execute(
                    """
                    SELECT hp, max_hp FROM combat_state
                    WHERE guild_id = ? AND user_id = ?
                    """,
                    (guild_id, user_id),
                )
                row = await cursor.fetchone()
                hp = max_hp if row is None else min(max_hp, float(row["hp"]) + max(0.0, amount))
                await self.conn.execute(
                    """
                    INSERT INTO combat_state (guild_id, user_id, hp, max_hp)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(guild_id, user_id) DO UPDATE SET
                        hp = excluded.hp,
                        max_hp = excluded.max_hp
                    """,
                    (guild_id, user_id, hp, max_hp),
                )
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
            return hp, max_hp

    async def damage_player(
        self,
        user_id: int,
        guild_id: int,
        amount: float,
        max_hp: float,
    ) -> tuple[float, float]:
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.conn.execute(
                    """
                    SELECT hp, max_hp
                    FROM combat_state
                    WHERE guild_id = ? AND user_id = ?
                    """,
                    (guild_id, user_id),
                )
                row = await cursor.fetchone()
                hp = max_hp if row is None else min(max_hp, float(row["hp"]))
                new_hp = max(0.0, hp - max(0.0, amount))
                await self.conn.execute(
                    """
                    INSERT INTO combat_state (guild_id, user_id, hp, max_hp)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(guild_id, user_id) DO UPDATE SET
                        hp = excluded.hp,
                        max_hp = excluded.max_hp
                    """,
                    (guild_id, user_id, new_hp, max_hp),
                )
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
            return new_hp, max_hp

    async def restore_player_hp(self, user_id: int, guild_id: int, max_hp: float) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT INTO combat_state (guild_id, user_id, hp, max_hp)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    hp = excluded.hp,
                    max_hp = excluded.max_hp
                """,
                (guild_id, user_id, max_hp, max_hp),
            )
            await self.conn.commit()

    async def get_combat_state(self, user_id: int, guild_id: int) -> tuple[float, float] | None:
        cursor = await self.conn.execute(
            """
            SELECT hp, max_hp
            FROM combat_state
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return float(row["hp"]), float(row["max_hp"])

    async def get_boss_damage(self, user_id: int, guild_id: int) -> float:
        cursor = await self.conn.execute(
            """
            SELECT damage
            FROM boss_damage
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return 0.0
        return float(row["damage"])

    async def debit_wallet(self, user_id: int, guild_id: int, amount: float) -> bool:
        if amount <= 0:
            return True
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            cursor = await self.conn.execute(
                "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            row = await cursor.fetchone()
            if row is None or float(row["wallet"]) < amount:
                await self.conn.commit()
                return False
            await self.conn.execute(
                """
                UPDATE users
                SET wallet = wallet - ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (amount, user_id, guild_id),
            )
            await self.conn.commit()
            return True

    async def remove_up_to_balance(self, user_id: int, guild_id: int, amount: float) -> float:
        if amount <= 0:
            return 0.0
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            cursor = await self.conn.execute(
                "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            row = await cursor.fetchone()
            balance = float(row["wallet"]) if row is not None else 0.0
            removed = min(balance, amount)
            if removed:
                await self.conn.execute(
                    """
                    UPDATE users
                    SET wallet = wallet - ?
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (removed, user_id, guild_id),
                )
            await self.conn.commit()
            return removed

    async def remove_up_to_bank(self, user_id: int, guild_id: int, amount: float) -> float:
        if amount <= 0:
            return 0.0
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            cursor = await self.conn.execute(
                "SELECT bank FROM users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            row = await cursor.fetchone()
            bank = float(row["bank"]) if row is not None else 0.0
            removed = min(bank, amount)
            if removed:
                await self.conn.execute(
                    """
                    UPDATE users
                    SET bank = bank - ?, wallet = wallet + ?
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (removed, removed, user_id, guild_id),
                )
            await self.conn.commit()
            return removed

    async def steal_from_bank(
        self,
        target_id: int,
        thief_id: int,
        guild_id: int,
        amount: float,
    ) -> float:
        """Remove up to amount from target bank and credit thief wallet."""
        if amount <= 0 or target_id == thief_id:
            return 0.0
        async with self._write_lock:
            await self._ensure_user_no_lock(target_id, guild_id)
            await self._ensure_user_no_lock(thief_id, guild_id)
            cursor = await self.conn.execute(
                "SELECT bank FROM users WHERE user_id = ? AND guild_id = ?",
                (target_id, guild_id),
            )
            row = await cursor.fetchone()
            bank = float(row["bank"]) if row is not None else 0.0
            stolen = min(bank, amount)
            if stolen <= 0:
                await self.conn.commit()
                return 0.0
            await self.conn.execute(
                """
                UPDATE users
                SET bank = bank - ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (stolen, target_id, guild_id),
            )
            await self.conn.execute(
                """
                UPDATE users
                SET wallet = wallet + ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (stolen, thief_id, guild_id),
            )
            await self.conn.commit()
            return stolen

    async def list_unstable_slots(self, user_id: int, guild_id: int) -> set[str]:
        cursor = await self.conn.execute(
            """
            SELECT slot FROM equipment_unstable
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        )
        return {str(row["slot"]) for row in await cursor.fetchall()}

    async def mark_slot_unstable(self, user_id: int, guild_id: int, slot: str) -> None:
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            await self.conn.execute(
                """
                INSERT INTO equipment_unstable (guild_id, user_id, slot)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, user_id, slot) DO NOTHING
                """,
                (guild_id, user_id, slot),
            )
            await self.conn.commit()

    async def mark_random_equipped_unstable(
        self,
        user_id: int,
        guild_id: int,
        *,
        chance: float,
    ) -> str | None:
        import random

        if random.random() >= chance:
            return None
        equipment = await self.get_equipment(user_id, guild_id)
        slots = [slot for slot in ("weapon", "off_hand", "armor") if equipment.get(slot)]
        if not slots:
            return None
        slot = random.choice(slots)
        await self.mark_slot_unstable(user_id, guild_id, slot)
        return slot

    async def clear_slot_unstable(self, user_id: int, guild_id: int, slot: str) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                DELETE FROM equipment_unstable
                WHERE guild_id = ? AND user_id = ? AND slot = ?
                """,
                (guild_id, user_id, slot),
            )
            await self.conn.commit()

    async def fix_unstable_slot(self, user_id: int, guild_id: int, slot: str) -> str | None:
        import config
        from items import get_item

        unstable = await self.list_unstable_slots(user_id, guild_id)
        if slot not in unstable:
            return "not_unstable"
        equipment = await self.get_equipment(user_id, guild_id)
        item_id = equipment.get(slot)
        if not item_id:
            await self.clear_slot_unstable(user_id, guild_id, slot)
            return None
        item = get_item(item_id)
        if item is None:
            await self.clear_slot_unstable(user_id, guild_id, slot)
            return None
        base_id = item_id.removeprefix("boss_weak_") if item_id.startswith("boss_weak_") else item_id
        base = get_item(base_id)
        price = float(base.price if base is not None else item.price)
        cost = max(1.0, price * config.GEAR_FIX_COST_FRACTION)
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            cursor = await self.conn.execute(
                "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            row = await cursor.fetchone()
            if row is None or float(row["wallet"]) < cost:
                await self.conn.commit()
                return "insufficient_funds"
            await self.conn.execute(
                "UPDATE users SET wallet = wallet - ? WHERE user_id = ? AND guild_id = ?",
                (cost, user_id, guild_id),
            )
            await self.conn.execute(
                """
                DELETE FROM equipment_unstable
                WHERE guild_id = ? AND user_id = ? AND slot = ?
                """,
                (guild_id, user_id, slot),
            )
            await self.conn.commit()
        return None

    async def transfer_wallet(
        self,
        payer_id: int,
        receiver_id: int,
        guild_id: int,
        amount: float,
    ) -> bool:
        if amount <= 0 or payer_id == receiver_id:
            return False
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                await self._ensure_user_no_lock(payer_id, guild_id)
                await self._ensure_user_no_lock(receiver_id, guild_id)
                cursor = await self.conn.execute(
                    "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                    (payer_id, guild_id),
                )
                row = await cursor.fetchone()
                if row is None or float(row["wallet"]) < amount:
                    await self.conn.rollback()
                    return False
                await self.conn.execute(
                    """
                    UPDATE users
                    SET wallet = wallet - ?
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (amount, payer_id, guild_id),
                )
                await self.conn.execute(
                    """
                    UPDATE users
                    SET wallet = wallet + ?,
                        total_earned = total_earned + ?
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (amount, amount, receiver_id, guild_id),
                )
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
            return True

    async def record_message_reward(self, user_id: int, guild_id: int, amount: float) -> None:
        amount = await self._apply_income_bonuses(user_id, guild_id, amount)
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            await self.conn.execute(
                """
                UPDATE users
                SET wallet = wallet + ?,
                    total_earned = total_earned + ?,
                    messages_sent = messages_sent + 1
                WHERE user_id = ? AND guild_id = ?
                """,
                (amount, amount, user_id, guild_id),
            )
            await self.conn.commit()

    async def claim_daily(
        self,
        user_id: int,
        guild_id: int,
        reward: float,
        cooldown_seconds: float,
        timestamp: float,
    ) -> float | None:
        bonus_reward = await self._apply_income_bonuses(user_id, guild_id, reward)
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                await self._ensure_user_no_lock(user_id, guild_id)
                cursor = await self.conn.execute(
                    "SELECT last_daily FROM users WHERE user_id = ? AND guild_id = ?",
                    (user_id, guild_id),
                )
                row = await cursor.fetchone()
                last_daily = float(row["last_daily"]) if row is not None else 0.0
                remaining = (last_daily + cooldown_seconds) - timestamp if last_daily > 0 else -1
                if remaining > 0:
                    await self.conn.rollback()
                    return remaining
                await self.conn.execute(
                    """
                    UPDATE users
                    SET wallet = wallet + ?,
                        total_earned = total_earned + ?,
                        last_daily = ?
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (bonus_reward, bonus_reward, timestamp, user_id, guild_id),
                )
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
            return None

    async def set_last_daily(self, user_id: int, guild_id: int, timestamp: float) -> None:
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            await self.conn.execute(
                """
                UPDATE users
                SET last_daily = ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (timestamp, user_id, guild_id),
            )
            await self.conn.commit()

    async def set_last_heist(self, user_id: int, guild_id: int, timestamp: float) -> None:
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            await self.conn.execute(
                """
                UPDATE users
                SET last_heist = ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (timestamp, user_id, guild_id),
            )
            await self.conn.commit()

    async def set_last_bank_heist(self, user_id: int, guild_id: int, timestamp: float) -> None:
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            await self.conn.execute(
                """
                UPDATE users
                SET last_bank_heist = ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (timestamp, user_id, guild_id),
            )
            await self.conn.commit()

    async def set_last_active(self, user_id: int, guild_id: int, timestamp: float) -> None:
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            await self.conn.execute(
                """
                UPDATE users
                SET last_active_ts = ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (timestamp, user_id, guild_id),
            )
            await self.conn.commit()

    async def set_arrested_until(self, user_id: int, guild_id: int, timestamp: float) -> None:
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            await self.conn.execute(
                """
                UPDATE users
                SET arrested_until = ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (timestamp, user_id, guild_id),
            )
            await self.conn.commit()

    async def clear_arrested(self, user_id: int, guild_id: int) -> None:
        await self.set_arrested_until(user_id, guild_id, 0.0)

    async def set_downed_until(self, user_id: int, guild_id: int, timestamp: float) -> None:
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            await self.conn.execute(
                """
                UPDATE users
                SET downed_until = ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (timestamp, user_id, guild_id),
            )
            await self.conn.commit()

    async def is_arrested(self, user_id: int, guild_id: int, at: float | None = None) -> bool:
        row = await self.get_user(user_id, guild_id)
        return float(row["arrested_until"]) > (time.time() if at is None else at)

    async def is_downed(self, user_id: int, guild_id: int, at: float | None = None) -> bool:
        row = await self.get_user(user_id, guild_id)
        return float(row["downed_until"]) > (time.time() if at is None else at)

    async def list_downed_users(self, guild_id: int, at: float | None = None) -> list[int]:
        now = time.time() if at is None else at
        cursor = await self.conn.execute(
            """
            SELECT user_id FROM users
            WHERE guild_id = ? AND downed_until > ?
            ORDER BY downed_until DESC
            """,
            (guild_id, now),
        )
        rows = await cursor.fetchall()
        return [int(row["user_id"]) for row in rows]

    async def is_restricted(self, user_id: int, guild_id: int, at: float | None = None) -> bool:
        now = time.time() if at is None else at
        row = await self.get_user(user_id, guild_id)
        return float(row["arrested_until"]) > now or float(row["downed_until"]) > now

    async def get_bodyguards(self, user_id: int, guild_id: int) -> dict[int, int]:
        cursor = await self.conn.execute(
            """
            SELECT tier, quantity FROM user_bodyguards
            WHERE guild_id = ? AND user_id = ? AND quantity > 0
            """,
            (guild_id, user_id),
        )
        rows = await cursor.fetchall()
        return {int(row["tier"]): int(row["quantity"]) for row in rows}

    async def hire_bodyguard(self, user_id: int, guild_id: int, tier: int) -> str | None:
        import config

        spec = config.BODYGUARD_TIERS.get(tier)
        if spec is None:
            return "invalid_tier"
        cost = float(spec["cost"])
        guards = await self.get_bodyguards(user_id, guild_id)
        total = sum(guards.values())
        if total >= config.BODYGUARD_MAX_TOTAL:
            return "max_guards"
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            cursor = await self.conn.execute(
                "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            row = await cursor.fetchone()
            if row is None or float(row["wallet"]) < cost:
                await self.conn.commit()
                return "insufficient_funds"
            await self.conn.execute(
                """
                UPDATE users
                SET wallet = wallet - ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (cost, user_id, guild_id),
            )
            qty = guards.get(tier, 0) + 1
            await self.conn.execute(
                """
                INSERT INTO user_bodyguards (guild_id, user_id, tier, quantity)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id, tier) DO UPDATE SET
                    quantity = excluded.quantity
                """,
                (guild_id, user_id, tier, qty),
            )
            await self.conn.commit()
        return None

    async def get_house_pot(self, guild_id: int) -> float:
        cursor = await self.conn.execute(
            "SELECT balance FROM guild_house_pot WHERE guild_id = ?",
            (guild_id,),
        )
        row = await cursor.fetchone()
        return float(row["balance"]) if row is not None else 0.0

    async def credit_house_pot(self, guild_id: int, amount: float) -> None:
        if amount <= 0:
            return
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT INTO guild_house_pot (guild_id, balance)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    balance = guild_house_pot.balance + excluded.balance
                """,
                (guild_id, amount),
            )
            await self.conn.commit()

    async def debit_house_pot(self, guild_id: int, amount: float) -> float:
        if amount <= 0:
            return 0.0
        async with self._write_lock:
            cursor = await self.conn.execute(
                "SELECT balance FROM guild_house_pot WHERE guild_id = ?",
                (guild_id,),
            )
            row = await cursor.fetchone()
            available = float(row["balance"]) if row is not None else 0.0
            taken = min(amount, available)
            if taken <= 0:
                return 0.0
            new_balance = available - taken
            if row is None:
                return 0.0
            await self.conn.execute(
                "UPDATE guild_house_pot SET balance = ? WHERE guild_id = ?",
                (new_balance, guild_id),
            )
            await self.conn.commit()
            return taken

    async def leaderboard(self, guild_id: int, limit: int = 10) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT user_id, wallet, bank, (wallet + bank) AS net
            FROM users
            WHERE guild_id = ?
            ORDER BY net DESC, user_id ASC
            LIMIT ?
            """,
            (guild_id, limit),
        )
        return list(await cursor.fetchall())

    async def total_circulation(self, guild_id: int) -> float:
        cursor = await self.conn.execute(
            "SELECT COALESCE(SUM(wallet + bank), 0) AS total FROM users WHERE guild_id = ?",
            (guild_id,),
        )
        row = await cursor.fetchone()
        return float(row["total"])

    async def economy_stats(self, guild_id: int) -> aiosqlite.Row:
        cursor = await self.conn.execute(
            """
            SELECT
                COUNT(*) AS users,
                COALESCE(SUM(wallet), 0) AS total_wallet,
                COALESCE(SUM(bank), 0) AS total_bank,
                COALESCE(SUM(wallet + bank), 0) AS total_wealth,
                COALESCE(SUM(total_earned), 0) AS total_earned,
                COALESCE(SUM(messages_sent), 0) AS messages_sent
            FROM users
            WHERE guild_id = ?
            """,
            (guild_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            msg = "Expected aggregate row"
            raise RuntimeError(msg)
        return row

    async def count_bounties(self, guild_id: int) -> int:
        value = await self.fetch_value(
            "SELECT COUNT(*) FROM bounties WHERE guild_id = ?", (guild_id,)
        )
        return int(value or 0)

    async def create_bounty_with_payment(
        self,
        guild_id: int,
        placer_id: int,
        target_id: int,
        amount: float,
        tax: float,
        trigger_word: str,
    ) -> int | None:
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                await self._ensure_user_no_lock(placer_id, guild_id)
                cursor = await self.conn.execute(
                    "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                    (placer_id, guild_id),
                )
                row = await cursor.fetchone()
                if row is None or float(row["wallet"]) < amount + tax:
                    await self.conn.rollback()
                    return None
                await self.conn.execute(
                    """
                    UPDATE users
                    SET wallet = wallet - ?
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (amount + tax, placer_id, guild_id),
                )
                cursor = await self.conn.execute(
                    """
                    INSERT INTO bounties (
                        guild_id, placer_id, target_id, amount, trigger_word, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    RETURNING id
                    """,
                    (guild_id, placer_id, target_id, amount, trigger_word, time.time()),
                )
                row = await cursor.fetchone()
                bounty_id = int(row["id"]) if row is not None else int(cursor.lastrowid)
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
            return bounty_id

    async def list_bounties(self, guild_id: int) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT *
            FROM bounties
            WHERE guild_id = ?
            ORDER BY created_at ASC
            """,
            (guild_id,),
        )
        return list(await cursor.fetchall())

    async def delete_bounty(self, bounty_id: int, guild_id: int) -> None:
        async with self._write_lock:
            await self.conn.execute(
                "DELETE FROM bounties WHERE id = ? AND guild_id = ?",
                (bounty_id, guild_id),
            )
            await self.conn.commit()

    async def get_hacker_pot(self, guild_id: int) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(
            "SELECT * FROM hacker_pots WHERE guild_id = ?",
            (guild_id,),
        )
        return await cursor.fetchone()

    async def claim_hack_start(
        self,
        guild_id: int,
        user_id: int,
        cooldown_seconds: float,
        timestamp: float,
    ) -> float | None:
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.conn.execute(
                    """
                    SELECT last_hack
                    FROM hacker_cooldowns
                    WHERE guild_id = ? AND user_id = ?
                    """,
                    (guild_id, user_id),
                )
                row = await cursor.fetchone()
                last_hack = float(row["last_hack"]) if row is not None else 0.0
                remaining = (last_hack + cooldown_seconds) - timestamp if last_hack > 0 else -1
                if remaining > 0:
                    await self.conn.rollback()
                    return remaining
                await self.conn.execute(
                    """
                    INSERT INTO hacker_cooldowns (guild_id, user_id, last_hack)
                    VALUES (?, ?, ?)
                    ON CONFLICT(guild_id, user_id) DO UPDATE SET
                        last_hack = excluded.last_hack
                    """,
                    (guild_id, user_id, timestamp),
                )
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
            return None

    async def set_hacker_pot(
        self,
        guild_id: int,
        holder_id: int,
        pass_count: int,
        started_at: float,
        expires_at: float,
    ) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT INTO hacker_pots (guild_id, holder_id, pass_count, started_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    holder_id = excluded.holder_id,
                    pass_count = excluded.pass_count,
                    started_at = excluded.started_at,
                    expires_at = excluded.expires_at
                """,
                (guild_id, holder_id, pass_count, started_at, expires_at),
            )
            await self.conn.commit()

    async def clear_hacker_pot(self, guild_id: int) -> None:
        async with self._write_lock:
            await self.conn.execute("DELETE FROM hacker_pots WHERE guild_id = ?", (guild_id,))
            await self.conn.commit()


    async def debit_bank_up_to(
        self,
        user_id: int,
        guild_id: int,
        amount: float,
    ) -> float:
        if amount <= 0:
            return 0.0
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            cursor = await self.conn.execute(
                "SELECT bank FROM users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            row = await cursor.fetchone()
            bank = float(row["bank"]) if row is not None else 0.0
            removed = min(bank, amount)
            if removed > 0:
                await self.conn.execute(
                    "UPDATE users SET bank = bank - ? WHERE user_id = ? AND guild_id = ?",
                    (removed, user_id, guild_id),
                )
            await self.conn.commit()
            return removed

    async def get_scourge_pot(self, guild_id: int) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(
            "SELECT * FROM scourge_pots WHERE guild_id = ?", (guild_id,),
        )
        return await cursor.fetchone()

    async def set_scourge_pot(
        self, guild_id: int, holder_id: int, pass_count: int,
        started_at: float, expires_at: float, penalty_amount: float,
    ) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT INTO scourge_pots (
                    guild_id, holder_id, pass_count, started_at, expires_at, penalty_amount
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    holder_id=excluded.holder_id, pass_count=excluded.pass_count,
                    started_at=excluded.started_at, expires_at=excluded.expires_at,
                    penalty_amount=excluded.penalty_amount
                """,
                (guild_id, holder_id, pass_count, started_at, expires_at, penalty_amount),
            )
            await self.conn.commit()

    async def clear_scourge_pot(self, guild_id: int) -> None:
        async with self._write_lock:
            await self.conn.execute("DELETE FROM scourge_pots WHERE guild_id = ?", (guild_id,))
            await self.conn.commit()

    async def clear_scourge_event(self, guild_id: int) -> None:
        async with self._write_lock:
            await self.conn.execute(
                "DELETE FROM scourge_events WHERE guild_id = ?",
                (guild_id,),
            )
            await self.conn.commit()

    async def get_scourge_event(self, guild_id: int) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(
            "SELECT * FROM scourge_events WHERE guild_id = ?", (guild_id,),
        )
        return await cursor.fetchone()

    async def upsert_scourge_event(
        self, guild_id: int, channel_id: int, *, phase: str, phase_ends_at: float,
        next_hourly_roll_at: float, infections_done: int = 0, next_infection_at: float = 0.0,
    ) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT INTO scourge_events (
                    guild_id, channel_id, phase, phase_ends_at,
                    next_hourly_roll_at, infections_done, next_infection_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    channel_id=excluded.channel_id, phase=excluded.phase,
                    phase_ends_at=excluded.phase_ends_at,
                    next_hourly_roll_at=excluded.next_hourly_roll_at,
                    infections_done=excluded.infections_done,
                    next_infection_at=excluded.next_infection_at
                """,
                (guild_id, channel_id, phase, phase_ends_at, next_hourly_roll_at,
                 infections_done, next_infection_at),
            )
            await self.conn.commit()

    async def get_active_boss(self, guild_id: int) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(
            "SELECT * FROM boss_sessions WHERE guild_id = ?",
            (guild_id,),
        )
        return await cursor.fetchone()

    async def list_active_boss_guild_ids(self) -> list[int]:
        cursor = await self.conn.execute("SELECT guild_id FROM boss_sessions")
        rows = await cursor.fetchall()
        return [int(row[0]) for row in rows]

    async def _apply_passive_decay_to_row_unlocked(
        self,
        guild_id: int,
        boss: Any,
    ) -> Any | None:
        """Advance passive boss decay within the current DB transaction."""
        now = time.time()
        max_hp = float(boss["max_hp"])
        hp = float(boss["hp"])
        spawned_at = float(boss["spawned_at"])
        try:
            pd_raw = boss["passive_decay_at"]
        except (KeyError, TypeError):
            pd_raw = None
        checkpoint = float(pd_raw) if pd_raw is not None else spawned_at
        elapsed = now - checkpoint
        whole_minutes = int(elapsed // 60)
        if whole_minutes <= 0:
            return boss
        from utils.boss_mechanics import passive_decay_rate_for_variant

        variant = str(boss["variant"])
        decay_rate = passive_decay_rate_for_variant(variant)
        decay_amount = whole_minutes * decay_rate * max_hp
        new_hp = max(0.0, hp - decay_amount)
        new_checkpoint = checkpoint + whole_minutes * 60.0
        await self.conn.execute(
            """
            UPDATE boss_sessions
            SET hp = ?, passive_decay_at = ?
            WHERE guild_id = ?
            """,
            (new_hp, new_checkpoint, guild_id),
        )
        cursor = await self.conn.execute(
            "SELECT * FROM boss_sessions WHERE guild_id = ?",
            (guild_id,),
        )
        return await cursor.fetchone()

    async def apply_boss_passive_decay(self, guild_id: int) -> Any | None:
        """Apply accumulated passive HP decay (commit). Returns boss row or None."""
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.conn.execute(
                    "SELECT * FROM boss_sessions WHERE guild_id = ?",
                    (guild_id,),
                )
                boss = await cursor.fetchone()
                if boss is None:
                    await self.conn.rollback()
                    return None
                boss = await self._apply_passive_decay_to_row_unlocked(guild_id, boss)
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
            return boss

    async def replace_boss(
        self,
        guild_id: int,
        name: str,
        variant: str,
        hp: float,
        spawned_at: float | None = None,
        *,
        summoner_id: int | None = None,
        element: str | None = None,
        mirrored_variant: str | None = None,
    ) -> None:
        import random

        from utils.boss_mechanics import boss_expires_at

        elem = element or random.choice(config.BOSS_ELEMENTS)
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                await self.conn.execute("DELETE FROM boss_sessions WHERE guild_id = ?", (guild_id,))
                await self.conn.execute(
                    "DELETE FROM boss_raider_status WHERE guild_id = ?",
                    (guild_id,),
                )
                await self.conn.execute(
                    "DELETE FROM boss_attack_cooldowns WHERE guild_id = ?",
                    (guild_id,),
                )
                spawn_ts = time.time() if spawned_at is None else spawned_at
                expires_at = boss_expires_at(spawn_ts, variant)
                await self.conn.execute(
                    """
                    INSERT INTO boss_sessions (
                        guild_id, name, variant, hp, max_hp, spawned_at, passive_decay_at,
                        phases_announced, summoned, summoner_id,
                        element, attack_count, mirrored_variant,
                        expires_at, solo_attack_streak
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, 0, ?, ?, 0)
                    """,
                    (
                        guild_id,
                        name,
                        variant,
                        hp,
                        hp,
                        spawn_ts,
                        spawn_ts,
                        summoner_id,
                        elem,
                        mirrored_variant,
                        expires_at,
                    ),
                )
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()

    async def increment_boss_attack_count(self, guild_id: int) -> tuple[int, float]:
        """Returns (attack_count, heal_applied)."""
        async with self._write_lock:
            cursor = await self.conn.execute(
                "SELECT * FROM boss_sessions WHERE guild_id = ?",
                (guild_id,),
            )
            boss = await cursor.fetchone()
            if boss is None:
                return 0, 0.0
            count = int(boss["attack_count"] or 0) + 1
            heal_applied = 0.0
            variant = str(boss["variant"])
            variant_cfg = config.BOSS_VARIANTS.get(variant, {})
            every = int(variant_cfg.get("heal_every_attacks", 0))
            cap = float(variant_cfg.get("heal_amount_cap", 0))
            hp = float(boss["hp"])
            max_hp = float(boss["max_hp"])
            if every > 0 and count % every == 0 and hp < max_hp and cap > 0:
                heal_applied = min(cap, max_hp - hp)
                hp += heal_applied
            await self.conn.execute(
                """
                UPDATE boss_sessions
                SET attack_count = ?, hp = ?
                WHERE guild_id = ?
                """,
                (count, hp, guild_id),
            )
            await self.conn.commit()
            return count, heal_applied

    async def damage_boss(
        self,
        guild_id: int,
        user_id: int,
        damage: float,
    ) -> aiosqlite.Row | None:
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.conn.execute(
                    "SELECT * FROM boss_sessions WHERE guild_id = ?",
                    (guild_id,),
                )
                boss = await cursor.fetchone()
                if boss is None:
                    await self.conn.rollback()
                    return None
                boss = await self._apply_passive_decay_to_row_unlocked(guild_id, boss)
                if boss is None:
                    await self.conn.rollback()
                    return None
                if float(boss["hp"]) <= 0:
                    await self.conn.commit()
                    return boss
                applied = min(float(boss["hp"]), damage)
                await self.conn.execute(
                    """
                    UPDATE boss_sessions
                    SET hp = MAX(hp - ?, 0)
                    WHERE guild_id = ?
                    """,
                    (applied, guild_id),
                )
                await self.conn.execute(
                    """
                    INSERT INTO boss_damage (guild_id, user_id, damage)
                    VALUES (?, ?, ?)
                    ON CONFLICT(guild_id, user_id) DO UPDATE SET
                        damage = boss_damage.damage + excluded.damage
                    """,
                    (guild_id, user_id, applied),
                )
                cursor = await self.conn.execute(
                    "SELECT * FROM boss_sessions WHERE guild_id = ?",
                    (guild_id,),
                )
                updated = await cursor.fetchone()
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
            return updated

    async def list_boss_damage(self, guild_id: int) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT *
            FROM boss_damage
            WHERE guild_id = ?
            ORDER BY damage DESC
            """,
            (guild_id,),
        )
        return list(await cursor.fetchall())

    async def count_distinct_boss_raiders(self, guild_id: int) -> int:
        value = await self.fetch_value(
            """
            SELECT COUNT(DISTINCT user_id)
            FROM boss_damage
            WHERE guild_id = ? AND damage > 0
            """,
            (guild_id,),
        )
        return int(value or 0)

    async def boss_attack_cooldown_remaining(
        self,
        guild_id: int,
        user_id: int,
        *,
        at: float | None = None,
    ) -> float | None:
        from utils.boss_element_effects import attack_cooldown_while_debuffed

        now = time.time() if at is None else at
        cursor = await self.conn.execute(
            """
            SELECT last_attack, cooldown_seconds
            FROM boss_attack_cooldowns
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        last_attack = float(row["last_attack"])
        if last_attack <= 0:
            return None

        base_cooldown = float(row["cooldown_seconds"])
        if base_cooldown <= 0:
            base_cooldown = float(config.BOSS_ATTACK_COOLDOWN_SECONDS)

        cooldown_seconds = base_cooldown
        status = await self.get_boss_raider_status(guild_id, user_id)
        if status is not None:
            debuff_cd = attack_cooldown_while_debuffed(
                float(status["attack_slow_until"]),
                float(status["verdant_root_until"]),
                float(status["debuff_attack_cooldown"]),
                now=now,
            )
            if debuff_cd is not None:
                cooldown_seconds = debuff_cd

        remaining = (last_attack + cooldown_seconds) - now
        return remaining if remaining > 0 else None

    async def record_boss_attack_time(
        self,
        guild_id: int,
        user_id: int,
        timestamp: float | None = None,
    ) -> None:
        import random

        ts = time.time() if timestamp is None else timestamp
        cooldown_seconds = float(
            random.randint(
                config.BOSS_ATTACK_COOLDOWN_MIN_SECONDS,
                config.BOSS_ATTACK_COOLDOWN_MAX_SECONDS,
            ),
        )
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT INTO boss_attack_cooldowns (guild_id, user_id, last_attack, cooldown_seconds)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    last_attack = excluded.last_attack,
                    cooldown_seconds = excluded.cooldown_seconds
                """,
                (guild_id, user_id, ts, cooldown_seconds),
            )
            await self.conn.commit()

    async def increment_boss_solo_streak(self, guild_id: int) -> int:
        async with self._write_lock:
            await self.conn.execute(
                """
                UPDATE boss_sessions
                SET solo_attack_streak = solo_attack_streak + 1
                WHERE guild_id = ?
                """,
                (guild_id,),
            )
            cursor = await self.conn.execute(
                "SELECT solo_attack_streak FROM boss_sessions WHERE guild_id = ?",
                (guild_id,),
            )
            row = await cursor.fetchone()
            await self.conn.commit()
            return int(row["solo_attack_streak"]) if row is not None else 0

    async def reset_boss_solo_streak(self, guild_id: int) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                UPDATE boss_sessions
                SET solo_attack_streak = 0
                WHERE guild_id = ?
                """,
                (guild_id,),
            )
            await self.conn.commit()

    def boss_has_expired(self, boss: Any, *, at: float | None = None) -> bool:
        now = time.time() if at is None else at
        try:
            raw = boss["expires_at"]
        except (KeyError, TypeError):
            return False
        if raw is None:
            return False
        return now >= float(raw)

    async def get_auto_potion_settings(
        self,
        user_id: int,
        guild_id: int,
    ) -> tuple[str | None, int]:
        async with self._write_lock:
            await self._ensure_progress_no_lock(user_id, guild_id)
            await self.conn.commit()
        cursor = await self.conn.execute(
            """
            SELECT auto_potion_item_id, auto_potion_threshold_pct
            FROM user_progress
            WHERE user_id = ? AND guild_id = ?
            """,
            (user_id, guild_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None, 0
        item_id = row["auto_potion_item_id"]
        return (
            str(item_id) if item_id is not None else None,
            int(row["auto_potion_threshold_pct"] or 0),
        )

    async def set_auto_potion_settings(
        self,
        user_id: int,
        guild_id: int,
        item_id: str | None,
        threshold_pct: int,
    ) -> None:
        async with self._write_lock:
            await self._ensure_progress_no_lock(user_id, guild_id)
            await self.conn.execute(
                """
                UPDATE user_progress
                SET auto_potion_item_id = ?, auto_potion_threshold_pct = ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (item_id, max(0, int(threshold_pct)), user_id, guild_id),
            )
            await self.conn.commit()

    async def try_auto_potion_heal(
        self,
        user_id: int,
        guild_id: int,
        *,
        current_hp: float,
        max_hp: float,
    ) -> tuple[float, str | None]:
        from items import HP_POTION_HEAL, HP_POTION_IDS, get_item

        item_id, threshold_pct = await self.get_auto_potion_settings(user_id, guild_id)
        if not item_id or threshold_pct <= 0 or item_id not in HP_POTION_IDS:
            return current_hp, None
        potion = get_item(item_id)
        if potion is None:
            return current_hp, None
        if max_hp <= 0:
            return current_hp, None
        if int((current_hp / max_hp) * 100) > threshold_pct:
            return current_hp, None
        if await self.get_inventory_quantity(user_id, guild_id, item_id) <= 0:
            return current_hp, None
        if not await self.consume_inventory_item(user_id, guild_id, item_id):
            return current_hp, None
        heal_amount = float(HP_POTION_HEAL[item_id])
        new_hp, _ = await self.heal_player(user_id, guild_id, heal_amount, max_hp)
        note = f"🧪 **{potion.name}** auto-healed **{int(heal_amount)}** HP."
        return new_hp, note

    async def record_heal(self, guild_id: int, healer_id: int, target_id: int) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT INTO boss_heals (guild_id, healer_id, target_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (guild_id, healer_id, target_id, time.time()),
            )
            await self.conn.commit()

    async def clear_boss_raider_cc_debuffs(self, guild_id: int, user_id: int) -> None:
        """Clear chill/root attack pacing while opioid CC immunity is active."""
        async with self._write_lock:
            await self.conn.execute(
                """
                UPDATE boss_raider_status
                SET attack_slow_until = 0,
                    verdant_root_until = 0,
                    debuff_attack_cooldown = 0
                WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, user_id),
            )
            await self.conn.commit()

    async def get_boss_raider_status(
        self,
        guild_id: int,
        user_id: int,
    ) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(
            """
            SELECT attack_slow_until, verdant_root_until,
                   dot_ticks_remaining, dot_damage, dot_next_tick_at,
                   debuff_attack_cooldown
            FROM boss_raider_status
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        )
        return await cursor.fetchone()

    async def apply_boss_element_status(
        self,
        guild_id: int,
        user_id: int,
        *,
        frost_slow_until: float | None = None,
        verdant_root_until: float | None = None,
        fire_burn: tuple[float, int, float] | None = None,
        debuff_attack_cooldown: float | None = None,
    ) -> None:
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                SELECT attack_slow_until, verdant_root_until,
                       dot_ticks_remaining, dot_damage, dot_next_tick_at,
                       debuff_attack_cooldown
                FROM boss_raider_status
                WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, user_id),
            )
            row = await cursor.fetchone()
            slow_until = float(row["attack_slow_until"]) if row is not None else 0.0
            root_until = float(row["verdant_root_until"]) if row is not None else 0.0
            dot_ticks = int(row["dot_ticks_remaining"]) if row is not None else 0
            dot_damage = float(row["dot_damage"]) if row is not None else 0.0
            dot_next = float(row["dot_next_tick_at"]) if row is not None else 0.0
            debuff_cd = float(row["debuff_attack_cooldown"]) if row is not None else 0.0

            if frost_slow_until is not None:
                slow_until = max(slow_until, frost_slow_until)
            if verdant_root_until is not None:
                root_until = max(root_until, verdant_root_until)
            if debuff_attack_cooldown is not None:
                debuff_cd = max(debuff_cd, debuff_attack_cooldown)
            if fire_burn is not None:
                tick_damage, ticks, first_tick = fire_burn
                dot_damage = tick_damage
                dot_ticks = max(dot_ticks, ticks)
                dot_next = min(dot_next, first_tick) if dot_next > 0 else first_tick

            await self.conn.execute(
                """
                INSERT INTO boss_raider_status (
                    guild_id, user_id, attack_slow_until, verdant_root_until,
                    dot_ticks_remaining, dot_damage, dot_next_tick_at,
                    debuff_attack_cooldown
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    attack_slow_until = excluded.attack_slow_until,
                    verdant_root_until = excluded.verdant_root_until,
                    dot_ticks_remaining = excluded.dot_ticks_remaining,
                    dot_damage = excluded.dot_damage,
                    dot_next_tick_at = excluded.dot_next_tick_at,
                    debuff_attack_cooldown = excluded.debuff_attack_cooldown
                """,
                (
                    guild_id,
                    user_id,
                    slow_until,
                    root_until,
                    dot_ticks,
                    dot_damage,
                    dot_next,
                    debuff_cd,
                ),
            )
            await self.conn.commit()

    async def drain_mana(self, user_id: int, guild_id: int, amount: int) -> int:
        """Remove up to `amount` mana; returns mana actually drained."""
        if amount <= 0:
            return 0
        async with self._write_lock:
            row = await self._ensure_user_no_lock(user_id, guild_id)
            row = await self._refresh_mana_unlocked(user_id, guild_id, row)
            current = int(row["mana"])
            drained = min(current, amount)
            if drained <= 0:
                return 0
            await self.conn.execute(
                """
                UPDATE users
                SET mana = mana - ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (drained, user_id, guild_id),
            )
            await self.conn.commit()
            return drained

    async def process_boss_fire_dot(
        self,
        user_id: int,
        guild_id: int,
        max_hp: float,
        *,
        at: float | None = None,
    ) -> tuple[float, float, float, int] | None:
        """Apply a due burn tick. Returns (hp, max_hp, tick_damage, ticks_left) or None."""
        now = time.time() if at is None else at
        cursor = await self.conn.execute(
            """
            SELECT dot_ticks_remaining, dot_damage, dot_next_tick_at
            FROM boss_raider_status
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        ticks_left = int(row["dot_ticks_remaining"])
        tick_damage = float(row["dot_damage"])
        next_at = float(row["dot_next_tick_at"])
        if ticks_left <= 0 or tick_damage <= 0:
            return None
        if next_at > now:
            return None

        hp, max_hp = await self.damage_player(user_id, guild_id, tick_damage, max_hp)
        ticks_left -= 1
        next_tick = now + config.BOSS_FIRE_BURN_INTERVAL_SECONDS if ticks_left > 0 else 0.0

        async with self._write_lock:
            if ticks_left <= 0:
                await self.conn.execute(
                    """
                    UPDATE boss_raider_status
                    SET dot_ticks_remaining = 0, dot_damage = 0, dot_next_tick_at = 0
                    WHERE guild_id = ? AND user_id = ?
                    """,
                    (guild_id, user_id),
                )
            else:
                await self.conn.execute(
                    """
                    UPDATE boss_raider_status
                    SET dot_ticks_remaining = ?, dot_next_tick_at = ?
                    WHERE guild_id = ? AND user_id = ?
                    """,
                    (ticks_left, next_tick, guild_id, user_id),
                )
            await self.conn.commit()
        return hp, max_hp, tick_damage, ticks_left

    async def boss_raider_debuff_summary(
        self,
        guild_id: int,
        user_id: int,
        *,
        at: float | None = None,
    ) -> str | None:
        now = time.time() if at is None else at
        row = await self.get_boss_raider_status(guild_id, user_id)
        if row is None:
            return None
        parts: list[str] = []
        slow_until = float(row["attack_slow_until"])
        root_until = float(row["verdant_root_until"])
        dot_ticks = int(row["dot_ticks_remaining"])
        if slow_until > now:
            parts.append(f"❄️ Chilled ({int(slow_until - now)}s)")
        if root_until > now:
            parts.append(f"🌿 Rooted ({int(root_until - now)}s)")
        if dot_ticks > 0:
            parts.append(f"🔥 Burning ({dot_ticks} ticks)")
        return " · ".join(parts) if parts else None

    async def clear_boss(self, guild_id: int) -> None:
        async with self._write_lock:
            await self.conn.execute("DELETE FROM boss_sessions WHERE guild_id = ?", (guild_id,))
            await self.conn.execute(
                "DELETE FROM boss_raider_status WHERE guild_id = ?",
                (guild_id,),
            )
            await self.conn.execute(
                "DELETE FROM boss_attack_cooldowns WHERE guild_id = ?",
                (guild_id,),
            )
            await self.conn.execute("DELETE FROM boss_raid_adds WHERE guild_id = ?", (guild_id,))
            await self.conn.commit()

    async def clear_all_bosses(self) -> int:
        async with self._write_lock:
            cursor = await self.conn.execute("SELECT COUNT(*) FROM boss_sessions")
            row = await cursor.fetchone()
            count = int(row[0]) if row is not None else 0
            await self.conn.execute("DELETE FROM boss_sessions")
            await self.conn.commit()
            return count

    async def fetch_value(self, query: str, params: tuple[Any, ...] = ()) -> Any:
        cursor = await self.conn.execute(query, params)
        row = await cursor.fetchone()
        if row is None:
            return None
        return row[0]

    async def _ensure_progress_no_lock(self, user_id: int, guild_id: int) -> None:
        await self._ensure_user_no_lock(user_id, guild_id)
        await self.conn.execute(
            """
            INSERT INTO user_progress (user_id, guild_id)
            VALUES (?, ?)
            ON CONFLICT(user_id, guild_id) DO NOTHING
            """,
            (user_id, guild_id),
        )

    async def get_user_progress(self, user_id: int, guild_id: int) -> aiosqlite.Row:
        async with self._write_lock:
            await self._ensure_progress_no_lock(user_id, guild_id)
            await self.conn.commit()
        cursor = await self.conn.execute(
            "SELECT * FROM user_progress WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        row = await cursor.fetchone()
        if row is None:
            msg = "Expected user progress row"
            raise RuntimeError(msg)
        return row

    async def _ensure_character_no_lock(self, user_id: int, guild_id: int) -> None:
        from utils.energy import energy_cap_for_upgrades

        await self._ensure_user_no_lock(user_id, guild_id)
        cursor = await self.conn.execute(
            "SELECT user_id FROM user_character WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        if await cursor.fetchone() is not None:
            return
        cap = energy_cap_for_upgrades(0)
        now = time.time()
        await self.conn.execute(
            """
            INSERT INTO user_character (
                user_id, guild_id, energy, energy_cap, cap_upgrades, energy_updated_at
            )
            VALUES (?, ?, ?, ?, 0, ?)
            """,
            (user_id, guild_id, cap, cap, now),
        )

    async def _refresh_character_energy_unlocked(
        self,
        user_id: int,
        guild_id: int,
    ) -> aiosqlite.Row:
        from utils.energy import apply_energy_regen, energy_cap_for_upgrades

        await self._ensure_character_no_lock(user_id, guild_id)
        cursor = await self.conn.execute(
            "SELECT * FROM user_character WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        row = await cursor.fetchone()
        if row is None:
            msg = "Expected user_character row"
            raise RuntimeError(msg)

        regen_per_tick = int(
            await self.get_config_value(guild_id, "energy_regen_per_tick")
        )
        tick_seconds = float(
            await self.get_config_value(guild_id, "energy_regen_interval_seconds")
        )
        from utils.aspects import effective_energy_regen_per_tick

        aspect_bonuses = await self.get_equipped_aspect_bonuses(user_id, guild_id)
        regen_per_tick = effective_energy_regen_per_tick(regen_per_tick, aspect_bonuses)
        expected_cap = energy_cap_for_upgrades(int(row["cap_upgrades"]))
        current_cap = int(row["energy_cap"])
        if current_cap != expected_cap:
            current_cap = expected_cap

        refreshed, advanced_at = apply_energy_regen(
            int(row["energy"]),
            current_cap,
            float(row["energy_updated_at"]),
            regen_per_tick=regen_per_tick,
            tick_seconds=tick_seconds,
        )
        if refreshed != int(row["energy"]) or advanced_at != float(row["energy_updated_at"]):
            await self.conn.execute(
                """
                UPDATE user_character
                SET energy = ?, energy_cap = ?, energy_updated_at = ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (refreshed, current_cap, advanced_at, user_id, guild_id),
            )
        cursor = await self.conn.execute(
            "SELECT * FROM user_character WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        updated = await cursor.fetchone()
        if updated is None:
            msg = "Expected user_character row after refresh"
            raise RuntimeError(msg)
        return updated

    async def summoner_mana_regen_multiplier(self, user_id: int, guild_id: int) -> float:
        """While the active boss was summoned by this user, spell mana regen is reduced."""
        import config
        from utils.summoner_penalty import boss_summoner_id

        boss = await self.get_active_boss(guild_id)
        if boss is None:
            return 1.0
        summoner_id = boss_summoner_id(boss)
        if summoner_id is not None and summoner_id == user_id:
            return config.SUMMONER_DEBUFF_MANA_RETENTION
        return 1.0

    async def get_user_character(self, user_id: int, guild_id: int) -> aiosqlite.Row:
        async with self._write_lock:
            row = await self._refresh_character_energy_unlocked(user_id, guild_id)
            row = await self._refresh_mana_unlocked(user_id, guild_id, row)
            await self.conn.commit()
            return row

    async def _refresh_mana_unlocked(
        self,
        user_id: int,
        guild_id: int,
        row: aiosqlite.Row,
    ) -> aiosqlite.Row:
        from utils.classes import is_healer_class
        from utils.mana import _regen_params, mana_cap_for_class

        try:
            raw_mana = row["mana"]
        except (KeyError, IndexError):
            return row

        class_id = row["class_id"] if row["class_id"] else None
        healer = is_healer_class(str(class_id) if class_id else None)
        cap = mana_cap_for_class(str(class_id) if class_id else None)
        try:
            stored_cap = int(row["mana_cap"])
        except (KeyError, IndexError, TypeError):
            stored_cap = 0
        if stored_cap != cap:
            stored_cap = cap
        current = int(raw_mana or 0)
        last_at = float(row["mana_updated_at"] or 0)
        if last_at <= 0:
            last_at = time.time()
            current = cap
        mana_mult = await self.summoner_mana_regen_multiplier(user_id, guild_id)
        per_tick, interval = _regen_params(healer)
        per_tick = max(0, int(per_tick * mana_mult))

        ts = time.time()
        elapsed = max(0.0, ts - last_at)
        ticks = int(elapsed // interval) if interval > 0 else 0
        if ticks <= 0 or per_tick <= 0:
            refreshed, advanced = min(current, cap), last_at
        else:
            refreshed = min(cap, current + ticks * per_tick)
            advanced = last_at + ticks * interval
        if (
            refreshed != current
            or advanced != last_at
            or stored_cap != int(row["mana_cap"] or 0)
        ):
            await self.conn.execute(
                """
                UPDATE user_character
                SET mana = ?, mana_cap = ?, mana_updated_at = ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (refreshed, cap, advanced, user_id, guild_id),
            )
            cursor = await self.conn.execute(
                "SELECT * FROM user_character WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            updated = await cursor.fetchone()
            if updated is not None:
                return updated
        return row

    async def get_mana_snapshot(self, user_id: int, guild_id: int):
        from utils.classes import is_healer_class
        from utils.mana import mana_snapshot as snap

        row = await self.get_user_character(user_id, guild_id)
        class_id = str(row["class_id"]) if row["class_id"] else None
        return snap(
            int(row["mana"]),
            int(row["mana_cap"]),
            float(row["mana_updated_at"]),
            is_healer=is_healer_class(class_id),
        )

    async def spend_mana(self, user_id: int, guild_id: int, amount: int) -> tuple[bool, str]:
        if amount <= 0:
            return False, "invalid"
        async with self._write_lock:
            row = await self._refresh_character_energy_unlocked(user_id, guild_id)
            row = await self._refresh_mana_unlocked(user_id, guild_id, row)
            current = int(row["mana"])
            if current < amount:
                await self.conn.commit()
                return False, "mana"
            await self.conn.execute(
                """
                UPDATE user_character SET mana = mana - ? WHERE user_id = ? AND guild_id = ?
                """,
                (amount, user_id, guild_id),
            )
            await self.conn.commit()
            return True, "ok"

    async def restore_mana_from_damage(
        self,
        user_id: int,
        guild_id: int,
        damage: int,
    ) -> int:
        from utils.classes import is_healer_class
        from utils.mana import mana_from_damage

        async with self._write_lock:
            row = await self._refresh_character_energy_unlocked(user_id, guild_id)
            row = await self._refresh_mana_unlocked(user_id, guild_id, row)
            class_id = str(row["class_id"]) if row["class_id"] else None
            gain = mana_from_damage(damage, is_healer=is_healer_class(class_id))
            cap = int(row["mana_cap"])
            new_mana = min(cap, int(row["mana"]) + gain)
            await self.conn.execute(
                "UPDATE user_character SET mana = ? WHERE user_id = ? AND guild_id = ?",
                (new_mana, user_id, guild_id),
            )
            await self.conn.commit()
            return gain

    async def set_pending_spell(self, user_id: int, guild_id: int, skill_id: str) -> None:
        expires = time.time() + config.PENDING_SPELL_SECONDS
        async with self._write_lock:
            await self._ensure_character_no_lock(user_id, guild_id)
            await self.conn.execute(
                """
                UPDATE user_character
                SET pending_spell = ?, pending_spell_expires = ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (skill_id, expires, user_id, guild_id),
            )
            await self.conn.commit()

    async def consume_pending_spell(self, user_id: int, guild_id: int) -> str | None:
        async with self._write_lock:
            row = await self._refresh_character_energy_unlocked(user_id, guild_id)
            raw = row["pending_spell"]
            if raw is None:
                await self.conn.commit()
                return None
            expires = float(row["pending_spell_expires"] or 0)
            if time.time() > expires:
                await self.conn.execute(
                    """
                    UPDATE user_character
                    SET pending_spell = NULL, pending_spell_expires = NULL
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (user_id, guild_id),
                )
                await self.conn.commit()
                return None
            skill_id = str(raw)
            await self.conn.execute(
                """
                UPDATE user_character
                SET pending_spell = NULL, pending_spell_expires = NULL
                WHERE user_id = ? AND guild_id = ?
                """,
                (user_id, guild_id),
            )
            await self.conn.commit()
            return skill_id

    async def get_pending_spell_id(self, user_id: int, guild_id: int) -> str | None:
        row = await self.get_user_character(user_id, guild_id)
        raw = row["pending_spell"]
        if raw is None:
            return None
        if time.time() > float(row["pending_spell_expires"] or 0):
            return None
        return str(raw)

    async def add_heist_spell_bonus(self, user_id: int, guild_id: int, bonus: float) -> None:
        async with self._write_lock:
            await self._ensure_character_no_lock(user_id, guild_id)
            await self.conn.execute(
                """
                UPDATE user_character
                SET heist_spell_bonus = heist_spell_bonus + ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (bonus, user_id, guild_id),
            )
            await self.conn.commit()

    async def take_heist_spell_bonus(self, user_id: int, guild_id: int) -> float:
        async with self._write_lock:
            row = await self._refresh_character_energy_unlocked(user_id, guild_id)
            try:
                bonus = float(row["heist_spell_bonus"] or 0)
            except (KeyError, TypeError):
                bonus = 0.0
            if bonus > 0:
                await self.conn.execute(
                    """
                    UPDATE user_character SET heist_spell_bonus = 0
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (user_id, guild_id),
                )
            await self.conn.commit()
            return bonus

    async def _ensure_avatar_unlocks_unlocked(self, user_id: int, guild_id: int) -> None:
        from utils.avatars import AVATARS

        await self._ensure_character_no_lock(user_id, guild_id)
        now = time.time()
        for avatar in AVATARS:
            if avatar.price > 0:
                continue
            await self.conn.execute(
                """
                INSERT OR IGNORE INTO player_avatar_unlocks
                    (guild_id, user_id, avatar_id, unlocked_at)
                VALUES (?, ?, ?, ?)
                """,
                (guild_id, user_id, avatar.id, now),
            )
        await self._ensure_unique_default_avatar_unlocked(user_id, guild_id)

    async def ensure_avatar_unlocks(self, user_id: int, guild_id: int) -> None:
        async with self._write_lock:
            await self._ensure_avatar_unlocks_unlocked(user_id, guild_id)
            await self.conn.commit()

    async def _ensure_unique_default_avatar_unlocked(self, user_id: int, guild_id: int) -> str:
        from utils.avatar_generate import ensure_default_avatar_assets_async
        from utils.avatars import DEFAULT_AVATAR_ID, unique_default_avatar_id

        await self._ensure_character_no_lock(user_id, guild_id)
        aid = unique_default_avatar_id(user_id, guild_id)
        cursor = await self.conn.execute(
            """
            SELECT 1 FROM player_avatar_unlocks
            WHERE guild_id = ? AND user_id = ? AND avatar_id = ?
            """,
            (guild_id, user_id, aid),
        )
        if await cursor.fetchone() is not None:
            return aid

        await ensure_default_avatar_assets_async(user_id, guild_id)
        await self.conn.execute(
            """
            INSERT OR IGNORE INTO player_avatar_unlocks
                (guild_id, user_id, avatar_id, unlocked_at)
            VALUES (?, ?, ?, ?)
            """,
            (guild_id, user_id, aid, time.time()),
        )
        cursor = await self.conn.execute(
            """
            SELECT avatar_id FROM user_character
            WHERE user_id = ? AND guild_id = ?
            """,
            (user_id, guild_id),
        )
        row = await cursor.fetchone()
        stored = str(row["avatar_id"] or "").strip().lower() if row is not None else ""
        if stored in ("", DEFAULT_AVATAR_ID):
            await self.conn.execute(
                """
                UPDATE user_character SET avatar_id = ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (aid, user_id, guild_id),
            )
        return aid

    async def ensure_unique_default_avatar(self, user_id: int, guild_id: int) -> str:
        async with self._write_lock:
            aid = await self._ensure_unique_default_avatar_unlocked(user_id, guild_id)
            await self.conn.commit()
            return aid

    async def list_unlocked_avatar_ids(self, user_id: int, guild_id: int) -> set[str]:
        async with self._write_lock:
            await self._ensure_avatar_unlocks_unlocked(user_id, guild_id)
            cursor = await self.conn.execute(
                """
                SELECT avatar_id FROM player_avatar_unlocks
                WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, user_id),
            )
            rows = await cursor.fetchall()
            await self.conn.commit()
        return {str(row["avatar_id"]) for row in rows}

    async def get_equipped_avatar_id(self, user_id: int, guild_id: int) -> str:
        async with self._write_lock:
            await self._ensure_avatar_unlocks_unlocked(user_id, guild_id)
            cursor = await self.conn.execute(
                """
                SELECT avatar_id FROM user_character
                WHERE user_id = ? AND guild_id = ?
                """,
                (user_id, guild_id),
            )
            row = await cursor.fetchone()
            await self.conn.commit()
        stored = str(row["avatar_id"] or "") if row is not None else ""
        from utils.avatars import resolve_equipped_avatar_id

        return resolve_equipped_avatar_id(stored or None)

    async def unlock_avatar(self, user_id: int, guild_id: int, avatar_id: str) -> None:
        async with self._write_lock:
            await self._ensure_avatar_unlocks_unlocked(user_id, guild_id)
            await self.conn.execute(
                """
                INSERT OR IGNORE INTO player_avatar_unlocks
                    (guild_id, user_id, avatar_id, unlocked_at)
                VALUES (?, ?, ?, ?)
                """,
                (guild_id, user_id, avatar_id, time.time()),
            )
            await self.conn.commit()

    async def buy_avatar_unlock(
        self,
        user_id: int,
        guild_id: int,
        avatar_id: str,
        price: float,
    ) -> str | None:
        """Unlock paid avatar. Returns None on success, or error code string."""
        if price <= 0:
            await self.unlock_avatar(user_id, guild_id, avatar_id)
            return None
        async with self._write_lock:
            await self._ensure_avatar_unlocks_unlocked(user_id, guild_id)
            cursor = await self.conn.execute(
                """
                SELECT 1 FROM player_avatar_unlocks
                WHERE guild_id = ? AND user_id = ? AND avatar_id = ?
                """,
                (guild_id, user_id, avatar_id),
            )
            if await cursor.fetchone() is not None:
                return "already_owned"
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                await self._ensure_user_no_lock(user_id, guild_id)
                cursor = await self.conn.execute(
                    "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                    (user_id, guild_id),
                )
                wallet_row = await cursor.fetchone()
                if wallet_row is None or float(wallet_row["wallet"]) < price:
                    await self.conn.execute("ROLLBACK")
                    return "insufficient_funds"
                await self.conn.execute(
                    """
                    UPDATE users SET wallet = wallet - ?
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (price, user_id, guild_id),
                )
                await self.conn.execute(
                    """
                    INSERT OR IGNORE INTO player_avatar_unlocks
                        (guild_id, user_id, avatar_id, unlocked_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (guild_id, user_id, avatar_id, time.time()),
                )
                await self.conn.commit()
                return None
            except Exception:
                await self.conn.execute("ROLLBACK")
                raise

    async def set_equipped_avatar(
        self,
        user_id: int,
        guild_id: int,
        avatar_id: str,
    ) -> str | None:
        """Equip avatar. Returns None on success, or error code."""
        from utils.avatars import AVATAR_MAP, is_custom_avatar_id, is_unique_default_avatar_id

        if (
            not is_custom_avatar_id(avatar_id)
            and not is_unique_default_avatar_id(avatar_id)
            and avatar_id not in AVATAR_MAP
        ):
            return "unknown"
        unlocked = await self.list_unlocked_avatar_ids(user_id, guild_id)
        if avatar_id not in unlocked:
            return "locked"
        async with self._write_lock:
            await self._ensure_character_no_lock(user_id, guild_id)
            await self.conn.execute(
                """
                UPDATE user_character SET avatar_id = ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (avatar_id, user_id, guild_id),
            )
            await self.conn.commit()
        return None

    async def spend_job_energy(
        self,
        user_id: int,
        guild_id: int,
        energy_cost: int,
    ) -> tuple[bool, str | None]:
        """Spend energy after regen sync. Returns (ok, error_code)."""
        if energy_cost <= 0:
            return False, "invalid"

        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                row = await self._refresh_character_energy_unlocked(user_id, guild_id)
                current = int(row["energy"])
                if current < energy_cost:
                    await self.conn.rollback()
                    return False, "energy"

                await self.conn.execute(
                    """
                    UPDATE user_character
                    SET energy = energy - ?
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (energy_cost, user_id, guild_id),
                )
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
            return True, None

    async def upgrade_energy_cap(self, user_id: int, guild_id: int, cost: float) -> bool:
        from utils.energy import energy_cap_for_upgrades

        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                await self._ensure_user_no_lock(user_id, guild_id)
                cursor = await self.conn.execute(
                    "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                    (user_id, guild_id),
                )
                user_row = await cursor.fetchone()
                if user_row is None or float(user_row["wallet"]) < cost:
                    await self.conn.rollback()
                    return False

                row = await self._refresh_character_energy_unlocked(user_id, guild_id)
                upgrades = int(row["cap_upgrades"]) + 1
                new_cap = energy_cap_for_upgrades(upgrades)

                await self.conn.execute(
                    """
                    UPDATE users
                    SET wallet = wallet - ?
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (cost, user_id, guild_id),
                )
                await self.conn.execute(
                    """
                    UPDATE user_character
                    SET cap_upgrades = ?, energy_cap = ?
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (upgrades, new_cap, user_id, guild_id),
                )
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
            return True

    async def get_active_guild_event(self, guild_id: int) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(
            "SELECT * FROM guild_events WHERE guild_id = ?",
            (guild_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        if float(row["ends_at"]) <= time.time():
            await self.clear_guild_event(guild_id)
            return None
        return row

    async def set_guild_event(
        self,
        guild_id: int,
        event_type: str,
        multiplier: float,
        ends_at: float,
    ) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT INTO guild_events (guild_id, event_type, multiplier, ends_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    event_type = excluded.event_type,
                    multiplier = excluded.multiplier,
                    ends_at = excluded.ends_at
                """,
                (guild_id, event_type, multiplier, ends_at),
            )
            await self.conn.commit()

    async def clear_guild_event(self, guild_id: int) -> None:
        async with self._write_lock:
            await self.conn.execute("DELETE FROM guild_events WHERE guild_id = ?", (guild_id,))
            await self.conn.commit()

    async def get_income_multiplier(self, user_id: int, guild_id: int) -> float:
        from utils.classes import get_modifiers, is_jester_user

        progress = await self.get_user_progress(user_id, guild_id)
        prestige = int(progress["prestige_level"])
        mult = 1.0 + prestige * config.PRESTIGE_INCOME_BONUS_PER_LEVEL
        if is_jester_user(user_id):
            await self.ensure_jester_class(user_id, guild_id)
        char = await self.get_user_character(user_id, guild_id)
        class_id = char["class_id"] if char["class_id"] else None
        mult *= get_modifiers(class_id).income_mult
        event = await self.get_active_guild_event(guild_id)
        if event is not None and str(event["event_type"]) in ("bonus_income", "trivia_fiesta"):
            mult *= float(event["multiplier"])
        return mult

    async def ensure_jester_class(self, user_id: int, guild_id: int) -> None:
        if user_id != config.JESTER_EXCLUSIVE_USER_ID:
            return
        async with self._write_lock:
            await self._ensure_character_no_lock(user_id, guild_id)
            await self.conn.execute(
                """
                UPDATE user_character
                SET class_id = ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (config.JESTER_CLASS_ID, user_id, guild_id),
            )
            await self.conn.commit()

    async def get_class_id(self, user_id: int, guild_id: int) -> str | None:
        row = await self.get_user_character(user_id, guild_id)
        raw = row["class_id"]
        return str(raw) if raw else None

    async def get_master_roots(self, user_id: int, guild_id: int) -> set[str]:
        row = await self.get_user_character(user_id, guild_id)
        raw = str(row["master_roots"] or "")
        if not raw:
            return set()
        return {part for part in raw.split(",") if part}

    async def set_class_id(self, user_id: int, guild_id: int, class_id: str) -> tuple[bool, str]:
        from utils.classes import CLASS_MAP, is_jester_user

        if class_id == config.JESTER_CLASS_ID and not is_jester_user(user_id):
            return False, "forbidden"
        if class_id not in CLASS_MAP:
            return False, "unknown"
        async with self._write_lock:
            await self._ensure_character_no_lock(user_id, guild_id)
            await self.conn.execute(
                """
                UPDATE user_character SET class_id = ? WHERE user_id = ? AND guild_id = ?
                """,
                (class_id, user_id, guild_id),
            )
            await self.conn.commit()
        return True, "ok"

    async def add_class_xp(self, user_id: int, guild_id: int, amount: int) -> int:
        if amount <= 0:
            row = await self.get_user_character(user_id, guild_id)
            return int(row["class_xp"])
        async with self._write_lock:
            await self._ensure_character_no_lock(user_id, guild_id)
            await self.conn.execute(
                """
                UPDATE user_character
                SET class_xp = class_xp + ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (amount, user_id, guild_id),
            )
            await self.conn.commit()
        row = await self.get_user_character(user_id, guild_id)
        return int(row["class_xp"])

    async def get_character_attributes(self, user_id: int, guild_id: int):
        from utils.character_attributes import CharacterAttributes

        row = await self.get_user_character(user_id, guild_id)
        progress = await self.get_user_progress(user_id, guild_id)
        prestige_level = int(progress["prestige_level"])
        return CharacterAttributes.from_row(row, prestige_level=prestige_level)

    async def reset_guild_character_attributes(self, guild_id: int) -> int:
        """Zero all attribute stats for every character in a guild."""
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                UPDATE user_character
                SET stat_str = 0, stat_dex = 0, stat_agi = 0, stat_def = 0, stat_vit = 0
                WHERE guild_id = ?
                """,
                (guild_id,),
            )
            await self.conn.commit()
            return int(cursor.rowcount or 0)

    async def reset_user_character_attributes(self, user_id: int, guild_id: int) -> bool:
        """Zero one player's attribute stats."""
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                UPDATE user_character
                SET stat_str = 0, stat_dex = 0, stat_agi = 0, stat_def = 0, stat_vit = 0
                WHERE user_id = ? AND guild_id = ?
                """,
                (user_id, guild_id),
            )
            await self.conn.commit()
            return int(cursor.rowcount or 0) > 0

    async def allocate_attribute_points(
        self,
        user_id: int,
        guild_id: int,
        stat_name: str,
        points: int,
    ) -> tuple[bool, str]:
        from utils.character_attributes import (
            STAT_COLUMNS,
            CharacterAttributes,
            normalize_stat_name,
            stat_cap_for_prestige,
            unspent_attribute_points,
        )

        if points <= 0:
            return False, "Allocate at least **1** point."
        normalized = normalize_stat_name(stat_name)
        if normalized is None:
            return False, "Unknown stat. Use **strength**, **dexterity**, **agility**, **defense**, or **vitality**."
        async with self._write_lock:
            await self._ensure_character_no_lock(user_id, guild_id)
            await self._ensure_progress_no_lock(user_id, guild_id)
            prestige_cursor = await self.conn.execute(
                """
                SELECT prestige_level FROM user_progress
                WHERE user_id = ? AND guild_id = ?
                """,
                (user_id, guild_id),
            )
            prestige_row = await prestige_cursor.fetchone()
            prestige_level = int(prestige_row["prestige_level"]) if prestige_row else 0
            cursor = await self.conn.execute(
                "SELECT * FROM user_character WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            row = await cursor.fetchone()
            if row is None:
                return False, "Character not found."
            attrs = CharacterAttributes.from_row(row)
            class_xp = int(row["class_xp"] or 0)
            available = unspent_attribute_points(attrs, class_xp, prestige_level)
            if points > available:
                from utils.character_attributes import total_point_pool_cap

                stat_cap = stat_cap_for_prestige(prestige_level)
                pool_cap = total_point_pool_cap(prestige_level)
                if attrs.total_points() >= pool_cap:
                    hint = (
                        f"Prestige up for more points (pool **{pool_cap}**, "
                        f"**{stat_cap}**/stat)."
                    )
                else:
                    hint = "Earn more class XP from duels and boss raids."
                return (
                    False,
                    f"Only **{available}** unspent point{'s' if available != 1 else ''} — {hint}",
                )
            col = STAT_COLUMNS[normalized]
            current = attrs.value(normalized)
            stat_cap = stat_cap_for_prestige(prestige_level)
            if current + points > stat_cap:
                return (
                    False,
                    f"**{normalized.title()}** cannot exceed **{stat_cap}** at your prestige "
                    f"(currently **{current}**). Prestige up for +1 cap per stat.",
                )
            await self.conn.execute(
                f"""
                UPDATE user_character
                SET {col} = {col} + ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (points, user_id, guild_id),
            )
            await self.conn.commit()
        return True, f"+**{points}** {normalized.title()} (now **{current + points}**)."

    async def record_master_root(self, user_id: int, guild_id: int, starter_root: str) -> None:
        roots = await self.get_master_roots(user_id, guild_id)
        roots.add(starter_root)
        joined = ",".join(sorted(roots))
        async with self._write_lock:
            await self.conn.execute(
                """
                UPDATE user_character SET master_roots = ? WHERE user_id = ? AND guild_id = ?
                """,
                (joined, user_id, guild_id),
            )
            await self.conn.commit()

    async def jester_steal_wallet(
        self,
        victim_id: int,
        jester_id: int,
        guild_id: int,
    ) -> float:
        async with self._write_lock:
            await self._ensure_user_no_lock(victim_id, guild_id)
            await self._ensure_user_no_lock(jester_id, guild_id)
            cursor = await self.conn.execute(
                "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                (victim_id, guild_id),
            )
            row = await cursor.fetchone()
            if row is None:
                return 0.0
            wallet = float(row["wallet"])
            steal = max(0.0, min(wallet, wallet * config.JESTER_WALLET_STEAL_FRACTION))
            if steal <= 0:
                return 0.0
            await self.conn.execute(
                "UPDATE users SET wallet = wallet - ? WHERE user_id = ? AND guild_id = ?",
                (steal, victim_id, guild_id),
            )
            await self.conn.execute(
                "UPDATE users SET wallet = wallet + ? WHERE user_id = ? AND guild_id = ?",
                (steal, jester_id, guild_id),
            )
            await self.conn.commit()
            return steal

    async def get_drop_multiplier(self, guild_id: int) -> float:
        event = await self.get_active_guild_event(guild_id)
        if event is not None and str(event["event_type"]) == "double_drops":
            return float(event["multiplier"])
        return 1.0

    async def get_boss_hp_multiplier(self, guild_id: int) -> float:
        event = await self.get_active_guild_event(guild_id)
        if event is None:
            return 1.0
        event_type = str(event["event_type"])
        mult = float(event["multiplier"])
        if event_type == "festival_boss":
            return mult
        if event_type == "world_boss_week":
            return mult
        return 1.0

    async def _apply_income_bonuses(self, user_id: int, guild_id: int, amount: float) -> float:
        return amount * await self.get_income_multiplier(user_id, guild_id)

    async def list_achievements(self, user_id: int, guild_id: int) -> set[str]:
        cursor = await self.conn.execute(
            """
            SELECT achievement_id
            FROM achievements
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        )
        return {str(row["achievement_id"]) for row in await cursor.fetchall()}

    async def unlock_achievement(self, user_id: int, guild_id: int, achievement_id: str) -> bool:
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            cursor = await self.conn.execute(
                """
                INSERT INTO achievements (guild_id, user_id, achievement_id, unlocked_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id, achievement_id) DO NOTHING
                """,
                (guild_id, user_id, achievement_id, time.time()),
            )
            await self.conn.commit()
            return bool(getattr(cursor, "rowcount", 0))

    async def increment_progress(
        self,
        user_id: int,
        guild_id: int,
        *,
        bosses_killed: int = 0,
        heists_won: int = 0,
        heals_given: int = 0,
        mythic_kills: int = 0,
        ultra_kills: int = 0,
        crafts_done: int = 0,
        duel_wins: int = 0,
        gambles_won: int = 0,
        dungeons_cleared: int = 0,
        territories_claimed: int = 0,
        sieges_won: int = 0,
    ) -> None:
        if not any(
            (
                bosses_killed,
                heists_won,
                heals_given,
                mythic_kills,
                ultra_kills,
                crafts_done,
                duel_wins,
                gambles_won,
                dungeons_cleared,
                territories_claimed,
                sieges_won,
            )
        ):
            return
        async with self._write_lock:
            await self._ensure_progress_no_lock(user_id, guild_id)
            await self.conn.execute(
                """
                UPDATE user_progress
                SET bosses_killed = bosses_killed + ?,
                    heists_won = heists_won + ?,
                    heals_given = heals_given + ?,
                    mythic_kills = mythic_kills + ?,
                    ultra_kills = ultra_kills + ?,
                    crafts_done = crafts_done + ?,
                    duel_wins = duel_wins + ?,
                    gambles_won = gambles_won + ?,
                    dungeons_cleared = dungeons_cleared + ?,
                    territories_claimed = territories_claimed + ?,
                    sieges_won = sieges_won + ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (
                    bosses_killed,
                    heists_won,
                    heals_given,
                    mythic_kills,
                    ultra_kills,
                    crafts_done,
                    duel_wins,
                    gambles_won,
                    dungeons_cleared,
                    territories_claimed,
                    sieges_won,
                    user_id,
                    guild_id,
                ),
            )
            await self.conn.commit()

    async def increment_boss_kills_for_raid(
        self,
        guild_id: int,
        user_ids: Iterable[int],
        *,
        mythic: bool = False,
        ultra: bool = False,
    ) -> None:
        mythic_inc = 1 if mythic else 0
        ultra_inc = 1 if ultra else 0
        for user_id in set(user_ids):
            await self.increment_progress(
                user_id,
                guild_id,
                bosses_killed=1,
                mythic_kills=mythic_inc,
                ultra_kills=ultra_inc,
            )

    async def prestige_user(self, user_id: int, guild_id: int) -> int:
        async with self._write_lock:
            await self._ensure_progress_no_lock(user_id, guild_id)
            cursor = await self.conn.execute(
                """
                SELECT prestige_level FROM user_progress
                WHERE user_id = ? AND guild_id = ?
                """,
                (user_id, guild_id),
            )
            row = await cursor.fetchone()
            current = int(row["prestige_level"]) if row is not None else 0
            new_level = current + 1
            await self.conn.execute(
                """
                UPDATE user_progress
                SET prestige_level = ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (new_level, user_id, guild_id),
            )
            import config

            if new_level >= config.PRESTIGE_MAX_LEVEL:
                await self.conn.execute(
                    """
                    UPDATE users
                    SET wallet = 0, bank = 0, bank_expansions = 0
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (user_id, guild_id),
                )
            else:
                await self.conn.execute(
                    """
                    UPDATE users
                    SET wallet = 0
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (user_id, guild_id),
                )
            await self.conn.commit()
            return new_level

    async def consume_inventory_item(self, user_id: int, guild_id: int, item_id: str) -> bool:
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.conn.execute(
                    """
                    SELECT quantity FROM inventory
                    WHERE guild_id = ? AND user_id = ? AND item_id = ?
                    """,
                    (guild_id, user_id, item_id),
                )
                row = await cursor.fetchone()
                if row is None or int(row["quantity"]) <= 0:
                    await self.conn.rollback()
                    return False
                qty = int(row["quantity"]) - 1
                if qty <= 0:
                    await self.conn.execute(
                        """
                        DELETE FROM inventory
                        WHERE guild_id = ? AND user_id = ? AND item_id = ?
                        """,
                        (guild_id, user_id, item_id),
                    )
                else:
                    await self.conn.execute(
                        """
                        UPDATE inventory
                        SET quantity = ?
                        WHERE guild_id = ? AND user_id = ? AND item_id = ?
                        """,
                        (qty, guild_id, user_id, item_id),
                    )
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
            return True

    async def gift_inventory_item(
        self,
        sender_id: int,
        receiver_id: int,
        guild_id: int,
        item_id: str,
        quantity: int = 1,
    ) -> str | None:
        """Move stackable items from sender to receiver. Returns error code or None."""
        if sender_id == receiver_id:
            return "self_gift"
        qty = max(1, min(int(quantity), config.SHOP_MAX_BUY_QUANTITY))
        async with self._write_lock:
            await self._ensure_user_no_lock(sender_id, guild_id)
            await self._ensure_user_no_lock(receiver_id, guild_id)
            cursor = await self.conn.execute(
                """
                SELECT quantity FROM inventory
                WHERE guild_id = ? AND user_id = ? AND item_id = ?
                """,
                (guild_id, sender_id, item_id),
            )
            row = await cursor.fetchone()
            if row is None or int(row["quantity"]) < qty:
                await self.conn.commit()
                return "insufficient_items"
            remaining = int(row["quantity"]) - qty
            if remaining <= 0:
                await self.conn.execute(
                    """
                    DELETE FROM inventory
                    WHERE guild_id = ? AND user_id = ? AND item_id = ?
                    """,
                    (guild_id, sender_id, item_id),
                )
            else:
                await self.conn.execute(
                    """
                    UPDATE inventory SET quantity = ?
                    WHERE guild_id = ? AND user_id = ? AND item_id = ?
                    """,
                    (remaining, guild_id, sender_id, item_id),
                )
            await self.conn.execute(
                """
                INSERT INTO inventory (guild_id, user_id, item_id, quantity)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id, item_id) DO UPDATE SET
                    quantity = inventory.quantity + excluded.quantity
                """,
                (guild_id, receiver_id, item_id, qty),
            )
            from items import get_item, is_gear_instance_item

            item = get_item(item_id)
            if item is not None and is_gear_instance_item(item):
                import time

                now = time.time()
                for _ in range(qty):
                    await self.conn.execute(
                        """
                        INSERT INTO gear_instances (
                            guild_id, user_id, item_id, enhancement_level, is_broken, created_at
                        )
                        VALUES (?, ?, ?, 0, 0, ?)
                        """,
                        (guild_id, receiver_id, item_id, now),
                    )
                records = await self.get_equipment_records(sender_id, guild_id)
                equipped_ids = {
                    int(rec["gear_instance_id"])
                    for rec in records.values()
                    if rec.get("gear_instance_id") is not None
                }
                inst_cursor = await self.conn.execute(
                    """
                    SELECT instance_id
                    FROM gear_instances
                    WHERE guild_id = ? AND user_id = ? AND item_id = ?
                    ORDER BY enhancement_level ASC, instance_id ASC
                    """,
                    (guild_id, sender_id, item_id),
                )
                removable = [
                    int(r["instance_id"])
                    for r in await inst_cursor.fetchall()
                    if int(r["instance_id"]) not in equipped_ids
                ][:qty]
                for instance_id in removable:
                    await self.conn.execute(
                        "DELETE FROM gear_instances WHERE instance_id = ? AND guild_id = ?",
                        (instance_id, guild_id),
                    )
            await self.conn.commit()
        return None

    async def get_inventory_quantity(
        self, user_id: int, guild_id: int, item_id: str
    ) -> int:
        cursor = await self.conn.execute(
            """
            SELECT quantity FROM inventory
            WHERE guild_id = ? AND user_id = ? AND item_id = ?
            """,
            (guild_id, user_id, item_id),
        )
        row = await cursor.fetchone()
        return int(row["quantity"]) if row is not None else 0

    async def create_aspect_instance(
        self,
        user_id: int,
        guild_id: int,
        aspect_id: str,
        roll_pct: float,
    ) -> int:
        now = time.time()
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                INSERT INTO aspect_instances (guild_id, user_id, aspect_id, roll_pct, created_at)
                VALUES (?, ?, ?, ?, ?)
                RETURNING instance_id
                """,
                (guild_id, user_id, aspect_id, roll_pct, now),
            )
            row = await cursor.fetchone()
            await self.conn.commit()
            if row is None:
                msg = "aspect_instances insert did not return instance_id"
                raise RuntimeError(msg)
            return int(row["instance_id"])

    async def list_aspect_instances(self, user_id: int, guild_id: int) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT instance_id, aspect_id, roll_pct, created_at
            FROM aspect_instances
            WHERE guild_id = ? AND user_id = ?
            ORDER BY roll_pct DESC, instance_id DESC
            """,
            (guild_id, user_id),
        )
        return await cursor.fetchall()

    async def get_aspect_instance(
        self, user_id: int, guild_id: int, instance_id: int
    ) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(
            """
            SELECT instance_id, aspect_id, roll_pct
            FROM aspect_instances
            WHERE guild_id = ? AND user_id = ? AND instance_id = ?
            """,
            (guild_id, user_id, instance_id),
        )
        return await cursor.fetchone()

    async def list_equipped_aspect_slots(
        self, user_id: int, guild_id: int
    ) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT slot, instance_id FROM equipped_aspect
            WHERE guild_id = ? AND user_id = ?
            ORDER BY slot ASC
            """,
            (guild_id, user_id),
        )
        return await cursor.fetchall()

    async def get_equipped_aspect_instance_ids(self, user_id: int, guild_id: int) -> set[int]:
        rows = await self.list_equipped_aspect_slots(user_id, guild_id)
        return {int(row["instance_id"]) for row in rows}

    async def get_equipped_aspect_instance_id(self, user_id: int, guild_id: int) -> int | None:
        """First equipped slot (legacy helper)."""
        rows = await self.list_equipped_aspect_slots(user_id, guild_id)
        return int(rows[0]["instance_id"]) if rows else None

    async def list_equipped_aspect_rows(self, user_id: int, guild_id: int) -> list[aiosqlite.Row]:
        slots = await self.list_equipped_aspect_slots(user_id, guild_id)
        rows: list[aiosqlite.Row] = []
        for slot_row in slots:
            inst = await self.get_aspect_instance(
                user_id,
                guild_id,
                int(slot_row["instance_id"]),
            )
            if inst is not None:
                rows.append(inst)
        return rows

    async def get_equipped_aspect_row(self, user_id: int, guild_id: int) -> aiosqlite.Row | None:
        rows = await self.list_equipped_aspect_rows(user_id, guild_id)
        return rows[0] if rows else None

    async def get_equipped_aspect_bonuses(self, user_id: int, guild_id: int):
        from utils.aspects import (
            AspectBonuses,
            bonuses_from_instance,
            instance_from_row,
            merge_aspect_bonuses,
        )

        rows = await self.list_equipped_aspect_rows(user_id, guild_id)
        if not rows:
            return AspectBonuses()
        return merge_aspect_bonuses(
            [bonuses_from_instance(instance_from_row(row)) for row in rows],
        )

    async def equip_aspect_instance(
        self,
        user_id: int,
        guild_id: int,
        instance_id: int,
        slot: int | None = None,
    ) -> tuple[bool, str | None]:
        """Equip to slot 1–3. Returns (ok, equipped_slot or error)."""
        import config

        row = await self.get_aspect_instance(user_id, guild_id, instance_id)
        if row is None:
            return False, None

        max_slots = config.ASPECT_MAX_EQUIP_SLOTS
        equipped = await self.list_equipped_aspect_slots(user_id, guild_id)
        by_slot = {int(r["slot"]): int(r["instance_id"]) for r in equipped}
        by_instance = {int(r["instance_id"]): int(r["slot"]) for r in equipped}

        if instance_id in by_instance:
            target_slot = by_instance[instance_id]
        elif slot is not None:
            if slot < 1 or slot > max_slots:
                return False, "invalid_slot"
            if slot in by_slot and by_slot[slot] != instance_id:
                pass
            target_slot = slot
        else:
            free = [s for s in range(1, max_slots + 1) if s not in by_slot]
            if not free:
                return False, "full"
            target_slot = free[0]

        if len(by_slot) >= max_slots and target_slot not in by_slot and instance_id not in by_instance:
            return False, "full"

        async with self._write_lock:
            if instance_id in by_instance and by_instance[instance_id] != target_slot:
                await self.conn.execute(
                    """
                    DELETE FROM equipped_aspect
                    WHERE guild_id = ? AND user_id = ? AND instance_id = ?
                    """,
                    (guild_id, user_id, instance_id),
                )
            await self.conn.execute(
                """
                INSERT INTO equipped_aspect (guild_id, user_id, slot, instance_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id, slot) DO UPDATE SET
                    instance_id = excluded.instance_id
                """,
                (guild_id, user_id, target_slot, instance_id),
            )
            await self.conn.commit()
        return True, str(target_slot)

    async def unequip_aspect_slot(self, user_id: int, guild_id: int, slot: int) -> bool:
        import config

        if slot < 1 or slot > config.ASPECT_MAX_EQUIP_SLOTS:
            return False
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                DELETE FROM equipped_aspect
                WHERE guild_id = ? AND user_id = ? AND slot = ?
                """,
                (guild_id, user_id, slot),
            )
            await self.conn.commit()
            return cursor.rowcount > 0

    async def buy_aspect_from_shop(
        self,
        user_id: int,
        guild_id: int,
        unit_price: float,
        quantity: int = 1,
    ) -> list[int] | None:
        """Debit wallet and grant shop-rolled aspects. Returns instance ids or None."""
        from utils.aspects import random_aspect_definition, roll_pct_shop

        qty = max(1, min(int(quantity), config.SHOP_MAX_BUY_QUANTITY))
        total_cents = _spendable_cents(unit_price) * qty
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.conn.execute(
                    "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                    (user_id, guild_id),
                )
                row = await cursor.fetchone()
                if row is None or _spendable_cents(row["wallet"]) < total_cents:
                    await self.conn.rollback()
                    return None
                total_price = total_cents / 100.0
                await self.conn.execute(
                    """
                    UPDATE users SET wallet = wallet - ?
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (total_price, user_id, guild_id),
                )
                instance_ids: list[int] = []
                now = time.time()
                for _ in range(qty):
                    defn = random_aspect_definition()
                    roll_pct = roll_pct_shop()
                    cursor = await self.conn.execute(
                        """
                        INSERT INTO aspect_instances
                            (guild_id, user_id, aspect_id, roll_pct, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        RETURNING instance_id
                        """,
                        (guild_id, user_id, defn.id, roll_pct, now),
                    )
                    ins_row = await cursor.fetchone()
                    if ins_row is None:
                        await self.conn.rollback()
                        return None
                    instance_ids.append(int(ins_row["instance_id"]))
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
            return instance_ids

    async def try_mark_boss_phase(self, guild_id: int, hp_ratio: float) -> int | None:
        """Return newly crossed phase threshold (75, 50, or 25) or None."""
        phase_bits = {75: 1, 50: 2, 25: 4}
        async with self._write_lock:
            cursor = await self.conn.execute(
                "SELECT phases_announced FROM boss_sessions WHERE guild_id = ?",
                (guild_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            mask = int(row["phases_announced"] or 0)
            hp_percent = hp_ratio * 100.0
            for threshold in config.BOSS_PHASE_THRESHOLDS:
                pct = int(threshold * 100)
                bit = phase_bits.get(pct)
                if bit is None:
                    continue
                if hp_percent > pct or mask & bit:
                    continue
                mask |= bit
                await self.conn.execute(
                    """
                    UPDATE boss_sessions
                    SET phases_announced = ?
                    WHERE guild_id = ?
                    """,
                    (mask, guild_id),
                )
                await self.conn.commit()
                return pct
        return None

    async def gear_distribution(self, guild_id: int, limit: int = 8) -> list[tuple[str, int]]:
        cursor = await self.conn.execute(
            """
            SELECT item_id, COUNT(*) AS equipped_count
            FROM equipment
            WHERE guild_id = ?
            GROUP BY item_id
            ORDER BY equipped_count DESC
            LIMIT ?
            """,
            (guild_id, limit),
        )
        return [(str(row["item_id"]), int(row["equipped_count"])) for row in await cursor.fetchall()]

    _PROGRESS_LEADERBOARD_COLUMNS = frozenset(
        {
            "bosses_killed",
            "heists_won",
            "heals_given",
            "mythic_kills",
            "crafts_done",
            "prestige_level",
            "duel_wins",
        }
    )

    async def progress_leaderboard(
        self,
        guild_id: int,
        column: str,
        *,
        limit: int = 10,
    ) -> list[aiosqlite.Row]:
        if column not in self._PROGRESS_LEADERBOARD_COLUMNS:
            msg = f"Invalid progress leaderboard column: {column}"
            raise ValueError(msg)
        cursor = await self.conn.execute(
            f"""
            SELECT user_id, {column} AS score
            FROM user_progress
            WHERE guild_id = ? AND {column} > 0
            ORDER BY {column} DESC, user_id ASC
            LIMIT ?
            """,
            (guild_id, limit),
        )
        return list(await cursor.fetchall())

    async def achievement_count_leaderboard(
        self,
        guild_id: int,
        *,
        limit: int = 10,
    ) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT user_id, COUNT(*) AS score
            FROM achievements
            WHERE guild_id = ?
            GROUP BY user_id
            HAVING COUNT(*) > 0
            ORDER BY score DESC, user_id ASC
            LIMIT ?
            """,
            (guild_id, limit),
        )
        return list(await cursor.fetchall())

    async def hall_of_fame_snapshot(self, guild_id: int, *, limit: int = 5) -> dict[str, list[aiosqlite.Row]]:
        return {
            "richest": await self.leaderboard(guild_id, limit=limit),
            "boss_kills": await self.progress_leaderboard(guild_id, "bosses_killed", limit=limit),
            "heals": await self.progress_leaderboard(guild_id, "heals_given", limit=limit),
            "achievements": await self.achievement_count_leaderboard(guild_id, limit=limit),
            "duel_wins": await self.progress_leaderboard(guild_id, "duel_wins", limit=limit),
            "duel_elo": await self.duel_elo_leaderboard(guild_id, limit=limit),
            "crews": await self.crew_leaderboard(guild_id, limit=limit),
            "business_prestige": await self.business_prestige_leaderboard(guild_id, limit=limit),
            "drug_sales": await self.drug_sales_leaderboard(guild_id, limit=limit),
            "corp_treasury": await self.corp_treasury_leaderboard(guild_id, limit=limit),
            "district_influence": await self.district_influence_leaderboard(guild_id, limit=limit),
        }

    async def business_prestige_leaderboard(
        self, guild_id: int, *, limit: int = 10,
    ) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT user_id, (tier * 10 + business_prestige) AS score
            FROM user_businesses
            WHERE guild_id = ?
            ORDER BY score DESC, user_id ASC
            LIMIT ?
            """,
            (guild_id, limit),
        )
        return list(await cursor.fetchall())

    async def drug_sales_leaderboard(
        self, guild_id: int, *, limit: int = 10,
    ) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT user_id, units_sold AS score
            FROM user_drug_stats
            WHERE guild_id = ? AND units_sold > 0
            ORDER BY score DESC, user_id ASC
            LIMIT ?
            """,
            (guild_id, limit),
        )
        return list(await cursor.fetchall())

    async def corp_treasury_leaderboard(
        self, guild_id: int, *, limit: int = 10,
    ) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT crew_name AS user_id, treasury AS score
            FROM crew_stats
            WHERE guild_id = ? AND treasury > 0
            ORDER BY score DESC, crew_name ASC
            LIMIT ?
            """,
            (guild_id, limit),
        )
        return list(await cursor.fetchall())

    async def district_influence_leaderboard(
        self, guild_id: int, *, limit: int = 10,
    ) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT entity_id AS user_id, SUM(influence) AS score
            FROM district_influence
            WHERE guild_id = ? AND entity_type = 'user'
            GROUP BY entity_id
            HAVING SUM(influence) > 0
            ORDER BY score DESC, entity_id ASC
            LIMIT ?
            """,
            (guild_id, limit),
        )
        return list(await cursor.fetchall())

    async def list_user_quests(
        self,
        guild_id: int,
        user_id: int,
        track: str,
    ) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT quest_id, progress, target, completed_at, assigned_at, reset_key
            FROM user_quests
            WHERE guild_id = ? AND user_id = ? AND track = ?
            ORDER BY assigned_at ASC, quest_id ASC
            """,
            (guild_id, user_id, track),
        )
        return list(await cursor.fetchall())

    async def upsert_user_quest(
        self,
        guild_id: int,
        user_id: int,
        track: str,
        quest_id: str,
        *,
        target: int,
        progress: int = 0,
        reset_key: str = "",
    ) -> None:
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            await self.conn.execute(
                """
                INSERT INTO user_quests (
                    guild_id, user_id, track, quest_id,
                    progress, target, completed_at, assigned_at, reset_key
                )
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
                ON CONFLICT(guild_id, user_id, track, quest_id) DO UPDATE SET
                    target = excluded.target,
                    reset_key = excluded.reset_key,
                    progress = CASE
                        WHEN user_quests.reset_key != excluded.reset_key THEN 0
                        ELSE user_quests.progress
                    END,
                    completed_at = CASE
                        WHEN user_quests.reset_key != excluded.reset_key THEN NULL
                        ELSE user_quests.completed_at
                    END,
                    assigned_at = CASE
                        WHEN user_quests.reset_key != excluded.reset_key THEN excluded.assigned_at
                        ELSE user_quests.assigned_at
                    END
                """,
                (
                    guild_id,
                    user_id,
                    track,
                    quest_id,
                    progress,
                    target,
                    time.time(),
                    reset_key,
                ),
            )
            await self.conn.commit()

    async def clear_user_quest_track(
        self,
        guild_id: int,
        user_id: int,
        track: str,
    ) -> None:
        async with self._write_lock:
            await self.conn.execute(
                "DELETE FROM user_quests WHERE guild_id = ? AND user_id = ? AND track = ?",
                (guild_id, user_id, track),
            )
            await self.conn.commit()

    async def advance_quest_progress(
        self,
        guild_id: int,
        user_id: int,
        track: str,
        quest_id: str,
        *,
        amount: int = 1,
    ) -> tuple[bool, int, int]:
        """Return (newly_completed, progress, target). No-op if quest row missing or already done."""
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            cursor = await self.conn.execute(
                """
                SELECT progress, target, completed_at
                FROM user_quests
                WHERE guild_id = ? AND user_id = ? AND track = ? AND quest_id = ?
                """,
                (guild_id, user_id, track, quest_id),
            )
            row = await cursor.fetchone()
            if row is None:
                return False, 0, 0
            if row["completed_at"] is not None:
                return False, int(row["progress"]), int(row["target"])
            progress = min(int(row["target"]), int(row["progress"]) + amount)
            target = int(row["target"])
            completed = progress >= target
            await self.conn.execute(
                """
                UPDATE user_quests
                SET progress = ?,
                    completed_at = CASE WHEN ? THEN ? ELSE completed_at END
                WHERE guild_id = ? AND user_id = ? AND track = ? AND quest_id = ?
                """,
                (
                    progress,
                    completed,
                    time.time() if completed else None,
                    guild_id,
                    user_id,
                    track,
                    quest_id,
                ),
            )
            await self.conn.commit()
            return completed and row["completed_at"] is None, progress, target

    async def count_completed_quests(
        self,
        guild_id: int,
        user_id: int,
        track: str,
    ) -> int:
        value = await self.fetch_value(
            """
            SELECT COUNT(*) FROM user_quests
            WHERE guild_id = ? AND user_id = ? AND track = ? AND completed_at IS NOT NULL
            """,
            (guild_id, user_id, track),
        )
        return int(value or 0)

    async def duel_same_target_cooldown_remaining(
        self,
        guild_id: int,
        attacker_id: int,
        defender_id: int,
        cooldown_seconds: float,
        *,
        at: float | None = None,
    ) -> float | None:
        now = time.time() if at is None else at
        cursor = await self.conn.execute(
            """
            SELECT MAX(created_at) AS last_at
            FROM duel_history
            WHERE guild_id = ? AND attacker_id = ? AND defender_id = ?
            """,
            (guild_id, attacker_id, defender_id),
        )
        row = await cursor.fetchone()
        if row is None or row["last_at"] is None:
            return None
        remaining = (float(row["last_at"]) + cooldown_seconds) - now
        return remaining if remaining > 0 else None

    async def duel_attacks_in_last_hour(
        self,
        guild_id: int,
        attacker_id: int,
        *,
        at: float | None = None,
    ) -> int:
        now = time.time() if at is None else at
        value = await self.fetch_value(
            """
            SELECT COUNT(*) FROM duel_history
            WHERE guild_id = ? AND attacker_id = ? AND created_at > ?
            """,
            (guild_id, attacker_id, now - 3600),
        )
        return int(value or 0)

    async def execute_duel(
        self,
        guild_id: int,
        attacker_id: int,
        defender_id: int,
        winner_id: int,
        *,
        loss_fraction: float,
        same_target_cooldown_seconds: float,
        max_attacks_per_hour: int,
        timestamp: float | None = None,
        skip_same_target_cooldown: bool = False,
    ) -> tuple[float, float] | None:
        """Record duel and transfer loot. Returns (loot, loser_wallet) or None if blocked."""
        if winner_id not in (attacker_id, defender_id):
            return None
        loser_id = defender_id if winner_id == attacker_id else attacker_id
        now = time.time() if timestamp is None else timestamp

        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                await self._ensure_user_no_lock(attacker_id, guild_id)
                await self._ensure_user_no_lock(defender_id, guild_id)

                cursor = await self.conn.execute(
                    """
                    SELECT MAX(created_at) AS last_at
                    FROM duel_history
                    WHERE guild_id = ? AND attacker_id = ? AND defender_id = ?
                    """,
                    (guild_id, attacker_id, defender_id),
                )
                row = await cursor.fetchone()
                if (
                    not skip_same_target_cooldown
                    and row is not None
                    and row["last_at"] is not None
                ):
                    remaining = (float(row["last_at"]) + same_target_cooldown_seconds) - now
                    if remaining > 0:
                        await self.conn.rollback()
                        return None

                cursor = await self.conn.execute(
                    """
                    SELECT COUNT(*) AS cnt FROM duel_history
                    WHERE guild_id = ? AND attacker_id = ? AND created_at > ?
                    """,
                    (guild_id, attacker_id, now - 3600),
                )
                count_row = await cursor.fetchone()
                if count_row is not None and int(count_row["cnt"]) >= max_attacks_per_hour:
                    await self.conn.rollback()
                    return None

                cursor = await self.conn.execute(
                    "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                    (loser_id, guild_id),
                )
                loser_row = await cursor.fetchone()
                if loser_row is None:
                    await self.conn.rollback()
                    return None
                loser_wallet = float(loser_row["wallet"])
                loot = min(loser_wallet, max(0.0, loser_wallet * loss_fraction))

                if loot > 0:
                    await self.conn.execute(
                        """
                        UPDATE users
                        SET wallet = wallet - ?
                        WHERE user_id = ? AND guild_id = ?
                        """,
                        (loot, loser_id, guild_id),
                    )
                    await self.conn.execute(
                        """
                        UPDATE users
                        SET wallet = wallet + ?,
                            total_earned = total_earned + ?
                        WHERE user_id = ? AND guild_id = ?
                        """,
                        (loot, loot, winner_id, guild_id),
                    )

                await self.conn.execute(
                    """
                    INSERT INTO duel_history (
                        guild_id, attacker_id, defender_id, winner_id, loot_amount, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (guild_id, attacker_id, defender_id, winner_id, loot, now),
                )
                await self._apply_duel_elo_no_lock(
                    guild_id, winner_id, loser_id,
                )
                await self._ensure_progress_no_lock(winner_id, guild_id)
                await self.conn.execute(
                    """
                    UPDATE user_progress SET duel_wins = duel_wins + 1
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (winner_id, guild_id),
                )
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
            cursor = await self.conn.execute(
                "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                (loser_id, guild_id),
            )
            final = await cursor.fetchone()
            final_wallet = float(final["wallet"]) if final is not None else 0.0
            return loot, final_wallet

    async def _apply_duel_elo_no_lock(
        self,
        guild_id: int,
        winner_id: int,
        loser_id: int,
    ) -> None:
        import config

        k = config.DUEL_ELO_K_FACTOR
        for user_id in (winner_id, loser_id):
            await self.conn.execute(
                """
                INSERT OR IGNORE INTO duel_elo (guild_id, user_id, rating, wins, losses)
                VALUES (?, ?, ?, 0, 0)
                """,
                (guild_id, user_id, config.DUEL_ELO_START),
            )
        cursor = await self.conn.execute(
            """
            SELECT user_id, rating FROM duel_elo
            WHERE guild_id = ? AND user_id IN (?, ?)
            """,
            (guild_id, winner_id, loser_id),
        )
        ratings = {int(row["user_id"]): int(row["rating"]) for row in await cursor.fetchall()}
        win_r = ratings.get(winner_id, config.DUEL_ELO_START)
        lose_r = ratings.get(loser_id, config.DUEL_ELO_START)
        expected_win = 1.0 / (1.0 + 10 ** ((lose_r - win_r) / 400.0))
        delta = round(k * (1.0 - expected_win))
        new_win = max(100, win_r + delta)
        new_lose = max(100, lose_r - delta)
        await self.conn.execute(
            """
            UPDATE duel_elo SET rating = ?, wins = wins + 1
            WHERE guild_id = ? AND user_id = ?
            """,
            (new_win, guild_id, winner_id),
        )
        await self.conn.execute(
            """
            UPDATE duel_elo SET rating = ?, losses = losses + 1
            WHERE guild_id = ? AND user_id = ?
            """,
            (new_lose, guild_id, loser_id),
        )

    async def get_duel_elo(self, user_id: int, guild_id: int) -> tuple[int, int, int]:
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            import config

            await self.conn.execute(
                """
                INSERT OR IGNORE INTO duel_elo (guild_id, user_id, rating, wins, losses)
                VALUES (?, ?, ?, 0, 0)
                """,
                (guild_id, user_id, config.DUEL_ELO_START),
            )
            cursor = await self.conn.execute(
                "SELECT rating, wins, losses FROM duel_elo WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            row = await cursor.fetchone()
            await self.conn.commit()
        if row is None:
            return config.DUEL_ELO_START, 0, 0
        return int(row["rating"]), int(row["wins"]), int(row["losses"])

    async def duel_elo_leaderboard(
        self, guild_id: int, *, limit: int = 10,
    ) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT user_id, rating AS score FROM duel_elo
            WHERE guild_id = ?
            ORDER BY rating DESC, wins DESC
            LIMIT ?
            """,
            (guild_id, limit),
        )
        return list(await cursor.fetchall())

    async def get_jackpot_pool(self, guild_id: int) -> float:
        cursor = await self.conn.execute(
            "SELECT pool FROM guild_jackpot WHERE guild_id = ?",
            (guild_id,),
        )
        row = await cursor.fetchone()
        return float(row["pool"]) if row is not None else 0.0

    async def add_jackpot_contribution(self, guild_id: int, amount: float) -> None:
        if amount <= 0:
            return
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT INTO guild_jackpot (guild_id, pool) VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET pool = pool + excluded.pool
                """,
                (guild_id, amount),
            )
            await self.conn.commit()

    async def try_win_jackpot(self, guild_id: int, user_id: int, chance: float) -> float:
        import random

        if random.random() >= chance:
            return 0.0
        async with self._write_lock:
            cursor = await self.conn.execute(
                "SELECT pool FROM guild_jackpot WHERE guild_id = ?",
                (guild_id,),
            )
            row = await cursor.fetchone()
            pool = float(row["pool"]) if row is not None else 0.0
            if pool < 1.0:
                await self.conn.commit()
                return 0.0
            await self._ensure_user_no_lock(user_id, guild_id)
            await self.conn.execute(
                "UPDATE guild_jackpot SET pool = 0 WHERE guild_id = ?",
                (guild_id,),
            )
            await self.conn.execute(
                """
                UPDATE users SET wallet = wallet + ?, total_earned = total_earned + ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (pool, pool, user_id, guild_id),
            )
            await self.conn.commit()
            return pool

    async def get_crew_membership(self, user_id: int, guild_id: int) -> str | None:
        cursor = await self.conn.execute(
            "SELECT crew_name FROM crew_members WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        row = await cursor.fetchone()
        return str(row["crew_name"]) if row is not None else None

    async def list_joinable_crews(
        self, guild_id: int, *, exclude_user_id: int | None = None,
    ) -> list[tuple[str, int]]:
        """Crews with fewer than 8 members. Optionally exclude crews the user is already in."""
        cursor = await self.conn.execute(
            """
            SELECT cs.crew_name, COUNT(cm.user_id) AS member_count
            FROM crew_stats cs
            LEFT JOIN crew_members cm
                ON cs.guild_id = cm.guild_id AND cs.crew_name = cm.crew_name
            WHERE cs.guild_id = ?
            GROUP BY cs.crew_name
            HAVING COUNT(cm.user_id) < 8
            ORDER BY cs.crew_name ASC
            """,
            (guild_id,),
        )
        rows = await cursor.fetchall()
        out: list[tuple[str, int]] = []
        for row in rows:
            name = str(row["crew_name"])
            count = int(row["member_count"])
            if exclude_user_id is not None:
                member_cursor = await self.conn.execute(
                    """
                    SELECT 1 FROM crew_members
                    WHERE guild_id = ? AND crew_name = ? AND user_id = ?
                    """,
                    (guild_id, name, exclude_user_id),
                )
                if await member_cursor.fetchone() is not None:
                    continue
            out.append((name, count))
        return out

    async def list_joinable_crew_names(self, guild_id: int) -> list[str]:
        return [name for name, _ in await self.list_joinable_crews(guild_id)]

    async def resolve_crew_name(self, guild_id: int, crew_name: str) -> str | None:
        """Match an existing crew name case-insensitively; return canonical spelling."""
        needle = crew_name.strip()
        if len(needle) < 2:
            return None
        cursor = await self.conn.execute(
            "SELECT crew_name FROM crew_stats WHERE guild_id = ?",
            (guild_id,),
        )
        lowered = needle.lower()
        for row in await cursor.fetchall():
            canonical = str(row["crew_name"])
            if canonical.lower() == lowered:
                return canonical
        return None

    async def join_crew(self, user_id: int, guild_id: int, crew_name: str) -> str | None:
        name = crew_name.strip()[:32]
        if len(name) < 2:
            return "invalid_name"
        existing = await self.resolve_crew_name(guild_id, name)
        if existing is not None:
            name = existing
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            cursor = await self.conn.execute(
                "SELECT crew_name FROM crew_members WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            if await cursor.fetchone() is not None:
                return "already_in_crew"
            count_cursor = await self.conn.execute(
                "SELECT COUNT(*) AS cnt FROM crew_members WHERE guild_id = ? AND crew_name = ?",
                (guild_id, name),
            )
            cnt_row = await count_cursor.fetchone()
            if cnt_row is not None and int(cnt_row["cnt"]) >= 8:
                return "crew_full"
            await self.conn.execute(
                """
                INSERT INTO crew_stats (guild_id, crew_name, treasury, level, xp)
                VALUES (?, ?, 0, 1, 0)
                ON CONFLICT(guild_id, crew_name) DO NOTHING
                """,
                (guild_id, name),
            )
            await self.conn.execute(
                """
                INSERT INTO crew_members (guild_id, user_id, crew_name, joined_at)
                VALUES (?, ?, ?, ?)
                """,
                (guild_id, user_id, name, time.time()),
            )
            await self.conn.commit()
        return None

    async def leave_crew(self, user_id: int, guild_id: int) -> bool | str:
        loan = await self.get_active_crew_loan(user_id, guild_id)
        if loan is not None and float(loan["remaining"]) > 0:
            return "active_loan"
        async with self._write_lock:
            cursor = await self.conn.execute(
                "DELETE FROM crew_members WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            await self.conn.commit()
            return bool(getattr(cursor, "rowcount", 0))

    async def deposit_crew_treasury(
        self, user_id: int, guild_id: int, amount: float,
    ) -> str | None:
        if amount <= 0:
            return "invalid_amount"
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            cursor = await self.conn.execute(
                "SELECT crew_name FROM crew_members WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            row = await cursor.fetchone()
            if row is None:
                await self.conn.commit()
                return "not_in_crew"
            crew_name = str(row["crew_name"])
            wallet_cursor = await self.conn.execute(
                "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            wallet_row = await wallet_cursor.fetchone()
            if wallet_row is None or float(wallet_row["wallet"]) < amount:
                await self.conn.commit()
                return "insufficient_funds"
            await self.conn.execute(
                "UPDATE users SET wallet = wallet - ? WHERE user_id = ? AND guild_id = ?",
                (amount, user_id, guild_id),
            )
            xp_gain = int(amount // 100)
            await self.conn.execute(
                """
                INSERT INTO crew_stats (guild_id, crew_name, treasury, level, xp)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(guild_id, crew_name) DO UPDATE SET
                    treasury = crew_stats.treasury + excluded.treasury,
                    xp = crew_stats.xp + excluded.xp
                """,
                (guild_id, crew_name, amount, xp_gain),
            )
            await self.conn.execute(
                """
                INSERT INTO crew_member_contributions
                    (guild_id, crew_name, user_id, contributed)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, crew_name, user_id) DO UPDATE SET
                    contributed = crew_member_contributions.contributed
                        + excluded.contributed
                """,
                (guild_id, crew_name, user_id, amount),
            )
            await self._recalc_crew_level_no_lock(guild_id, crew_name)
            await self.conn.commit()
        return None

    async def _recalc_crew_level_no_lock(self, guild_id: int, crew_name: str) -> None:
        from utils.crew_banking import crew_level_from_xp

        cursor = await self.conn.execute(
            "SELECT xp FROM crew_stats WHERE guild_id = ? AND crew_name = ?",
            (guild_id, crew_name),
        )
        row = await cursor.fetchone()
        if row is None:
            return
        level = crew_level_from_xp(int(row["xp"]))
        await self.conn.execute(
            """
            UPDATE crew_stats SET level = ?
            WHERE guild_id = ? AND crew_name = ?
            """,
            (level, guild_id, crew_name),
        )

    async def get_crew_contributed(
        self, guild_id: int, crew_name: str, user_id: int,
    ) -> float:
        cursor = await self.conn.execute(
            """
            SELECT contributed FROM crew_member_contributions
            WHERE guild_id = ? AND crew_name = ? AND user_id = ?
            """,
            (guild_id, crew_name, user_id),
        )
        row = await cursor.fetchone()
        return float(row["contributed"]) if row is not None else 0.0

    async def get_active_crew_loan(
        self, user_id: int, guild_id: int,
    ) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(
            """
            SELECT * FROM crew_loans
            WHERE guild_id = ? AND borrower_id = ? AND status = 'active'
            ORDER BY loan_id DESC
            LIMIT 1
            """,
            (guild_id, user_id),
        )
        return await cursor.fetchone()

    async def get_crew_banking_snapshot(
        self, user_id: int, guild_id: int,
    ) -> dict[str, object] | None:
        crew_name = await self.get_crew_membership(user_id, guild_id)
        if crew_name is None:
            return None
        stats = await self.get_crew_stats(guild_id, crew_name)
        if stats is None:
            return None
        contributed = await self.get_crew_contributed(guild_id, crew_name, user_id)
        loan = await self.get_active_crew_loan(user_id, guild_id)
        return {
            "crew_name": crew_name,
            "treasury": float(stats["treasury"]),
            "xp": int(stats["xp"]),
            "level": int(stats["level"]),
            "contributed": contributed,
            "loan": loan,
        }

    async def issue_crew_loan(
        self, user_id: int, guild_id: int, amount: float,
    ) -> str | None:
        import config
        from utils.crew_banking import effective_interest_rate, max_loan_amount

        if amount < config.CREW_LOAN_MIN_AMOUNT:
            return "amount_too_low"
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            cursor = await self.conn.execute(
                "SELECT crew_name FROM crew_members WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            row = await cursor.fetchone()
            if row is None:
                await self.conn.commit()
                return "not_in_crew"
            crew_name = str(row["crew_name"])
            if await self.get_active_crew_loan(user_id, guild_id) is not None:
                await self.conn.commit()
                return "active_loan"
            stats = await self.get_crew_stats(guild_id, crew_name)
            if stats is None:
                await self.conn.commit()
                return "no_treasury"
            treasury = float(stats["treasury"])
            level = int(stats["level"])
            cap = max_loan_amount(treasury, level)
            if amount > cap:
                await self.conn.commit()
                return "amount_too_high"
            if amount > treasury:
                await self.conn.commit()
                return "insufficient_treasury"
            now = time.time()
            rate = effective_interest_rate(level)
            await self.conn.execute(
                """
                UPDATE crew_stats SET treasury = treasury - ?
                WHERE guild_id = ? AND crew_name = ?
                """,
                (amount, guild_id, crew_name),
            )
            await self.conn.execute(
                """
                UPDATE users SET wallet = wallet + ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (amount, user_id, guild_id),
            )
            await self.conn.execute(
                """
                INSERT INTO crew_loans (
                    guild_id, crew_name, borrower_id, principal, remaining,
                    interest_rate, created_at, due_at, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active')
                """,
                (
                    guild_id,
                    crew_name,
                    user_id,
                    amount,
                    amount,
                    rate,
                    now,
                    now + config.CREW_LOAN_TERM_SECONDS,
                ),
            )
            await self.conn.commit()
        return None

    async def repay_crew_loan(
        self, user_id: int, guild_id: int, payment: float,
    ) -> str | None:
        if payment <= 0:
            return "invalid_amount"
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            loan = await self.get_active_crew_loan(user_id, guild_id)
            if loan is None:
                await self.conn.commit()
                return "no_loan"
            remaining = float(loan["remaining"])
            if remaining <= 0:
                await self.conn.commit()
                return "no_loan"
            rate = float(loan["interest_rate"])
            wallet_cursor = await self.conn.execute(
                "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            wallet_row = await wallet_cursor.fetchone()
            if wallet_row is None or float(wallet_row["wallet"]) < payment:
                await self.conn.commit()
                return "insufficient_funds"
            pay = min(payment, remaining * (1.0 + rate))
            interest_portion = pay * rate
            principal_portion = pay - interest_portion
            new_remaining = max(0.0, remaining - principal_portion)
            crew_name = str(loan["crew_name"])
            loan_id = int(loan["loan_id"])
            await self.conn.execute(
                "UPDATE users SET wallet = wallet - ? WHERE user_id = ? AND guild_id = ?",
                (pay, user_id, guild_id),
            )
            await self.conn.execute(
                """
                UPDATE crew_stats SET treasury = treasury + ?
                WHERE guild_id = ? AND crew_name = ?
                """,
                (pay, guild_id, crew_name),
            )
            status = "paid" if new_remaining <= 0.01 else "active"
            await self.conn.execute(
                """
                UPDATE crew_loans SET remaining = ?, status = ?
                WHERE loan_id = ?
                """,
                (new_remaining, status, loan_id),
            )
            await self.conn.commit()
        return None

    async def withdraw_crew_contribution(
        self, user_id: int, guild_id: int, amount: float,
    ) -> str | None:
        import config

        if amount < config.CREW_WITHDRAW_MIN:
            return "invalid_amount"
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            loan = await self.get_active_crew_loan(user_id, guild_id)
            if loan is not None and float(loan["remaining"]) > 0:
                await self.conn.commit()
                return "active_loan"
            cursor = await self.conn.execute(
                "SELECT crew_name FROM crew_members WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            row = await cursor.fetchone()
            if row is None:
                await self.conn.commit()
                return "not_in_crew"
            crew_name = str(row["crew_name"])
            contributed = await self.get_crew_contributed(guild_id, crew_name, user_id)
            if amount > contributed:
                await self.conn.commit()
                return "insufficient_contribution"
            stats = await self.get_crew_stats(guild_id, crew_name)
            if stats is None or float(stats["treasury"]) < amount:
                await self.conn.commit()
                return "insufficient_treasury"
            await self.conn.execute(
                """
                UPDATE crew_member_contributions SET contributed = contributed - ?
                WHERE guild_id = ? AND crew_name = ? AND user_id = ?
                """,
                (amount, guild_id, crew_name, user_id),
            )
            await self.conn.execute(
                """
                UPDATE crew_stats SET treasury = treasury - ?
                WHERE guild_id = ? AND crew_name = ?
                """,
                (amount, guild_id, crew_name),
            )
            await self.conn.execute(
                """
                UPDATE users SET wallet = wallet + ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (amount, user_id, guild_id),
            )
            await self.conn.commit()
        return None

    async def get_crew_stats(self, guild_id: int, crew_name: str) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(
            "SELECT * FROM crew_stats WHERE guild_id = ? AND crew_name = ?",
            (guild_id, crew_name),
        )
        return await cursor.fetchone()

    async def list_crew_members(self, guild_id: int, crew_name: str) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT user_id FROM crew_members
            WHERE guild_id = ? AND crew_name = ?
            ORDER BY joined_at ASC
            """,
            (guild_id, crew_name),
        )
        return list(await cursor.fetchall())

    async def crew_leaderboard(self, guild_id: int, *, limit: int = 5) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT crew_name, treasury AS score, level, xp
            FROM crew_stats
            WHERE guild_id = ?
            ORDER BY level DESC, xp DESC, treasury DESC
            LIMIT ?
            """,
            (guild_id, limit),
        )
        return list(await cursor.fetchall())

    async def list_crew_held_territories(
        self, guild_id: int, crew_name: str,
    ) -> list[tuple[str, int]]:
        await self.ensure_territories(guild_id)
        cursor = await self.conn.execute(
            """
            SELECT territory_id, guards FROM territory_control
            WHERE guild_id = ? AND owner_crew_name = ?
            ORDER BY territory_id ASC
            """,
            (guild_id, crew_name),
        )
        rows = await cursor.fetchall()
        return [(str(row["territory_id"]), int(row["guards"])) for row in rows]

    async def list_crew_member_user_ids(
        self, guild_id: int, crew_name: str,
    ) -> list[int]:
        cursor = await self.conn.execute(
            """
            SELECT user_id FROM crew_members
            WHERE guild_id = ? AND crew_name = ?
            ORDER BY joined_at ASC
            """,
            (guild_id, crew_name),
        )
        return [int(row["user_id"]) for row in await cursor.fetchall()]

    async def get_crew_territory_perk_ids(
        self, guild_id: int, crew_name: str | None,
    ) -> set[str]:
        if crew_name is None:
            return set()
        held = await self.list_crew_held_territories(guild_id, crew_name)
        return {tid for tid, _ in held}

    async def set_territory_siege_message(
        self,
        guild_id: int,
        territory_id: str,
        channel_id: int,
        message_id: int,
    ) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                UPDATE territory_control SET
                    siege_channel_id = ?,
                    siege_message_id = ?
                WHERE guild_id = ? AND territory_id = ?
                """,
                (channel_id, message_id, guild_id, territory_id),
            )
            await self.conn.commit()

    async def clear_territory_siege_message(
        self, guild_id: int, territory_id: str,
    ) -> tuple[int | None, int | None]:
        row = await self.get_territory_row(guild_id, territory_id)
        if row is None:
            return None, None
        channel_id = row["siege_channel_id"]
        message_id = row["siege_message_id"]
        ch = int(channel_id) if channel_id is not None else None
        msg = int(message_id) if message_id is not None else None
        async with self._write_lock:
            await self.conn.execute(
                """
                UPDATE territory_control SET
                    siege_channel_id = NULL,
                    siege_message_id = NULL
                WHERE guild_id = ? AND territory_id = ?
                """,
                (guild_id, territory_id),
            )
            await self.conn.commit()
        return ch, msg

    async def ensure_territories(self, guild_id: int) -> None:
        from utils.territories import TERRITORY_IDS

        now = time.time()
        for territory_id in TERRITORY_IDS:
            await self.conn.execute(
                """
                INSERT INTO territory_control (
                    guild_id, territory_id, owner_crew_name, guards,
                    last_income_at, siege_attacker_crew, siege_ends_at,
                    siege_started_at, last_siege_at
                )
                VALUES (?, ?, NULL, 0, ?, NULL, NULL, NULL, NULL)
                ON CONFLICT(guild_id, territory_id) DO NOTHING
                """,
                (guild_id, territory_id, now),
            )
        await self.conn.commit()

    async def list_territory_rows(self, guild_id: int) -> list[aiosqlite.Row]:
        await self.ensure_territories(guild_id)
        cursor = await self.conn.execute(
            """
            SELECT * FROM territory_control
            WHERE guild_id = ?
            ORDER BY territory_id ASC
            """,
            (guild_id,),
        )
        return list(await cursor.fetchall())

    async def get_territory_row(
        self, guild_id: int, territory_id: str,
    ) -> aiosqlite.Row | None:
        await self.ensure_territories(guild_id)
        cursor = await self.conn.execute(
            """
            SELECT * FROM territory_control
            WHERE guild_id = ? AND territory_id = ?
            """,
            (guild_id, territory_id),
        )
        return await cursor.fetchone()

    async def count_crew_territories(self, guild_id: int, crew_name: str) -> int:
        cursor = await self.conn.execute(
            """
            SELECT COUNT(*) AS cnt FROM territory_control
            WHERE guild_id = ? AND owner_crew_name = ?
            """,
            (guild_id, crew_name),
        )
        row = await cursor.fetchone()
        return int(row["cnt"]) if row is not None else 0

    async def count_crew_members(self, guild_id: int, crew_name: str) -> int:
        cursor = await self.conn.execute(
            """
            SELECT COUNT(*) AS cnt FROM crew_members
            WHERE guild_id = ? AND crew_name = ?
            """,
            (guild_id, crew_name),
        )
        row = await cursor.fetchone()
        return int(row["cnt"]) if row is not None else 0

    async def credit_crew_treasury_no_wallet(
        self, guild_id: int, crew_name: str, amount: float,
    ) -> None:
        """Add nuggets to crew treasury (territory income, etc.) without a member deposit."""
        if amount <= 0:
            return
        xp_gain = int(amount // 100)
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT INTO crew_stats (guild_id, crew_name, treasury, level, xp)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(guild_id, crew_name) DO UPDATE SET
                    treasury = crew_stats.treasury + excluded.treasury,
                    xp = crew_stats.xp + excluded.xp
                """,
                (guild_id, crew_name, amount, xp_gain),
            )
            await self._recalc_crew_level_no_lock(guild_id, crew_name)
            await self.conn.commit()

    async def process_territory_hourly_income(self, guild_id: int) -> float:
        """Pay hourly income to owning crews; returns total paid this tick."""
        import config
        from utils.territories import TERRITORY_MAP, income_multiplier_under_siege

        await self.ensure_territories(guild_id)
        now = time.time()
        total_paid = 0.0
        async with self._write_lock:
            cursor = await self.conn.execute(
                "SELECT * FROM territory_control WHERE guild_id = ?",
                (guild_id,),
            )
            rows = await cursor.fetchall()
            for row in rows:
                owner = row["owner_crew_name"]
                if owner is None:
                    continue
                territory_id = str(row["territory_id"])
                defn = TERRITORY_MAP.get(territory_id)
                if defn is None:
                    continue
                last_at = float(row["last_income_at"])
                elapsed = now - last_at
                if elapsed < config.TERRITORY_HOURLY_TICK_SECONDS:
                    continue
                hours = int(elapsed // config.TERRITORY_HOURLY_TICK_SECONDS)
                if hours < 1:
                    continue
                mult = 1.0
                if territory_id == "citadel":
                    mult = 1.0 + config.TERRITORY_PERK_CITADEL_INCOME_BONUS
                siege_end = row["siege_ends_at"]
                if siege_end is not None and float(siege_end) > now:
                    mult *= income_multiplier_under_siege()
                payout = defn.income_per_hour * hours * mult
                if payout <= 0:
                    await self.conn.execute(
                        """
                        UPDATE territory_control SET last_income_at = ?
                        WHERE guild_id = ? AND territory_id = ?
                        """,
                        (last_at + hours * config.TERRITORY_HOURLY_TICK_SECONDS, guild_id, territory_id),
                    )
                    continue
                await self.conn.execute(
                    """
                    INSERT INTO crew_stats (guild_id, crew_name, treasury, level, xp)
                    VALUES (?, ?, ?, 1, ?)
                    ON CONFLICT(guild_id, crew_name) DO UPDATE SET
                        treasury = crew_stats.treasury + excluded.treasury,
                        xp = crew_stats.xp + excluded.xp
                    """,
                    (guild_id, str(owner), payout, int(payout // 100)),
                )
                await self._recalc_crew_level_no_lock(guild_id, str(owner))
                await self.conn.execute(
                    """
                    UPDATE territory_control SET last_income_at = ?
                    WHERE guild_id = ? AND territory_id = ?
                    """,
                    (
                        last_at + hours * config.TERRITORY_HOURLY_TICK_SECONDS,
                        guild_id,
                        territory_id,
                    ),
                )
                total_paid += payout
            await self.conn.commit()
        return total_paid

    # --- Business Empire ----------------------------------------------------

    async def get_business(self, user_id: int, guild_id: int) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(
            "SELECT * FROM user_businesses WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        return await cursor.fetchone()

    @staticmethod
    def _business_hourly_from_row(
        row: aiosqlite.Row,
        *,
        district_mult: float | None = None,
        reputation_effectiveness: float = 1.0,
    ) -> float:
        from utils.businesses import hourly_income, row_income_kwargs

        kwargs = row_income_kwargs(row)
        if district_mult is not None:
            kwargs["district_mult"] = district_mult
        kwargs["reputation_effectiveness"] = reputation_effectiveness
        return hourly_income(**kwargs)

    async def get_business_income_breakdown(
        self,
        user_id: int,
        guild_id: int,
        row: aiosqlite.Row | None = None,
        *,
        now: float | None = None,
    ) -> "BusinessIncomeBreakdown | None":
        """Full hourly income: business stats plus corp, buffs, events, and mega projects."""
        from utils.businesses import BusinessIncomeBreakdown

        if row is None:
            row = await self.get_business(user_id, guild_id)
        if row is None:
            return None
        current = time.time() if now is None else now
        rep_eff = await self._reputation_effectiveness_no_lock(user_id, guild_id)
        base = self._business_hourly_from_row(row, reputation_effectiveness=rep_eff)
        corp_mult = await self._corporate_income_mult_no_lock(user_id, guild_id)
        buff_mult = await self._active_buff_multiplier_no_lock(user_id, guild_id, current)
        event_mult = await self._business_event_mult_no_lock(guild_id)
        mega_mult = await self._mega_income_mult_no_lock(user_id, guild_id)
        synergy_mult = await self._synergy_income_mult_no_lock(row, current)
        district_war_mult = await self._district_war_income_mult_no_lock(user_id, guild_id, row, current)
        effective = base * corp_mult * buff_mult * event_mult * mega_mult * synergy_mult * district_war_mult
        return BusinessIncomeBreakdown(
            base_hourly=base,
            effective_hourly=effective,
            corp_mult=corp_mult,
            buff_mult=buff_mult * synergy_mult * district_war_mult,
            event_mult=event_mult,
            mega_mult=mega_mult,
        )

    @staticmethod
    def _business_capacity_from_row(row: aiosqlite.Row) -> float:
        from utils.businesses import capacity_for_level

        return capacity_for_level(int(row["tier"]), int(row["capacity"]))

    async def _settle_business_income_no_lock(
        self, user_id: int, guild_id: int, now: float | None = None,
    ) -> aiosqlite.Row | None:
        """Accrue stored income up to ``now`` and persist. Returns the fresh row."""
        from utils.businesses import accrue_income

        cursor = await self.conn.execute(
            "SELECT * FROM user_businesses WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        current = time.time() if now is None else now
        await self._apply_satisfaction_decay_no_lock(row, user_id, guild_id, current)
        cursor = await self.conn.execute(
            "SELECT * FROM user_businesses WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        last_at = float(row["last_income_at"]) or current
        elapsed = max(0.0, current - last_at)
        buff_mult = await self._active_buff_multiplier_no_lock(user_id, guild_id, current)
        corp_mult = await self._corporate_income_mult_no_lock(user_id, guild_id)
        event_mult = await self._business_event_mult_no_lock(guild_id)
        mega_mult = await self._mega_income_mult_no_lock(user_id, guild_id)
        synergy_mult = await self._synergy_income_mult_no_lock(row, current)
        district_war_mult = await self._district_war_income_mult_no_lock(user_id, guild_id, row, current)
        rep_eff = await self._reputation_effectiveness_no_lock(user_id, guild_id)
        legacy = await self._list_legacy_perks_no_lock(user_id, guild_id)
        from utils.legacy_perks import offline_accrual_bonus_from_perks

        offline_bonus = offline_accrual_bonus_from_perks(legacy)
        hourly = (
            self._business_hourly_from_row(row, reputation_effectiveness=rep_eff)
            * buff_mult
            * corp_mult
            * event_mult
            * mega_mult
            * synergy_mult
            * district_war_mult
            * (1.0 + offline_bonus)
        )
        capacity = self._business_capacity_from_row(row)
        new_stored = accrue_income(
            stored=float(row["stored_income"]),
            capacity=capacity,
            hourly=hourly,
            elapsed_seconds=elapsed,
        )
        await self.conn.execute(
            """
            UPDATE user_businesses
            SET stored_income = ?, last_income_at = ?
            WHERE user_id = ? AND guild_id = ?
            """,
            (new_stored, current, user_id, guild_id),
        )
        cursor = await self.conn.execute(
            "SELECT * FROM user_businesses WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        return await cursor.fetchone()

    async def create_business(self, user_id: int, guild_id: int) -> str | None:
        """Create the player's tier-1 business, debiting the purchase cost.

        Returns ``None`` on success or an error code string.
        """
        from utils.businesses import tier_def

        defn = tier_def(1)
        if defn is None:
            return "invalid_tier"
        cost = defn.purchase_cost
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            cursor = await self.conn.execute(
                "SELECT 1 FROM user_businesses WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            if await cursor.fetchone() is not None:
                await self.conn.commit()
                return "already_owns"
            cursor = await self.conn.execute(
                "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            wallet_row = await cursor.fetchone()
            if wallet_row is None or float(wallet_row["wallet"]) < cost:
                await self.conn.commit()
                return "insufficient_funds"
            await self.conn.execute(
                "UPDATE users SET wallet = wallet - ? WHERE user_id = ? AND guild_id = ?",
                (cost, user_id, guild_id),
            )
            now = time.time()
            await self.conn.execute(
                """
                INSERT INTO user_businesses (
                    user_id, guild_id, tier, tier_id, employee_satisfaction,
                    stored_income, last_income_at, created_at
                ) VALUES (?, ?, 1, ?, ?, 0, ?, ?)
                """,
                (
                    user_id,
                    guild_id,
                    defn.tier_id,
                    config.BUSINESS_SATISFACTION_START,
                    now,
                    now,
                ),
            )
            await self.conn.commit()
        return None

    async def collect_business_income(
        self, user_id: int, guild_id: int,
    ) -> tuple[float, str | None]:
        """Settle accrued income, move it to the wallet, and reset the store."""
        async with self._write_lock:
            row = await self._settle_business_income_no_lock(user_id, guild_id)
            if row is None:
                await self.conn.commit()
                return 0.0, "no_business"
            amount = float(row["stored_income"])
            if amount <= 0:
                await self.conn.commit()
                return 0.0, "empty"
            await self.conn.execute(
                """
                UPDATE user_businesses SET stored_income = 0
                WHERE user_id = ? AND guild_id = ?
                """,
                (user_id, guild_id),
            )
            await self.conn.execute(
                """
                UPDATE users
                SET wallet = wallet + ?, total_earned = total_earned + ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (amount, amount, user_id, guild_id),
            )
            await self.conn.commit()
        return amount, None

    async def tier_up_business(
        self, user_id: int, guild_id: int,
    ) -> tuple[str | None, int]:
        """Purchase the next business tier. Returns (error, new_tier)."""
        from utils.businesses import next_tier_def

        async with self._write_lock:
            row = await self._settle_business_income_no_lock(user_id, guild_id)
            if row is None:
                await self.conn.commit()
                return "no_business", 0
            current_tier = int(row["tier"])
            nxt = next_tier_def(current_tier)
            if nxt is None:
                await self.conn.commit()
                return "max_tier", current_tier
            cost = nxt.purchase_cost
            cursor = await self.conn.execute(
                "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            wallet_row = await cursor.fetchone()
            if wallet_row is None or float(wallet_row["wallet"]) < cost:
                await self.conn.commit()
                return "insufficient_funds", current_tier
            await self.conn.execute(
                "UPDATE users SET wallet = wallet - ? WHERE user_id = ? AND guild_id = ?",
                (cost, user_id, guild_id),
            )
            await self.conn.execute(
                """
                UPDATE user_businesses SET tier = ?, tier_id = ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (nxt.tier, nxt.tier_id, user_id, guild_id),
            )
            await self.conn.commit()
        return None, nxt.tier

    async def upgrade_business_attribute(
        self, user_id: int, guild_id: int, attribute: str,
    ) -> tuple[float, str | None]:
        """Buy one level of an attribute or upgrade branch.

        ``attribute`` is one of: security, reputation, efficiency, capacity,
        branch_security, branch_growth, branch_production. Returns (cost, error).
        """
        from utils.businesses import upgrade_cost

        attribute_columns = {
            "security": ("security", config.BUSINESS_ATTRIBUTE_MAX),
            "reputation": ("reputation", config.BUSINESS_ATTRIBUTE_MAX),
            "efficiency": ("efficiency", config.BUSINESS_ATTRIBUTE_MAX),
            "capacity": ("capacity", config.BUSINESS_ATTRIBUTE_MAX),
            "branch_security": ("branch_security", config.BUSINESS_BRANCH_MAX),
            "branch_growth": ("branch_growth", config.BUSINESS_BRANCH_MAX),
            "branch_production": ("branch_production", config.BUSINESS_BRANCH_MAX),
        }
        spec = attribute_columns.get(attribute)
        if spec is None:
            return 0.0, "invalid_attribute"
        column, level_cap = spec
        async with self._write_lock:
            row = await self._settle_business_income_no_lock(user_id, guild_id)
            if row is None:
                await self.conn.commit()
                return 0.0, "no_business"
            current_level = int(row[column])
            if current_level >= level_cap:
                await self.conn.commit()
                return 0.0, "max_level"
            cost = upgrade_cost(int(row["tier"]), current_level)
            cursor = await self.conn.execute(
                "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            wallet_row = await cursor.fetchone()
            if wallet_row is None or float(wallet_row["wallet"]) < cost:
                await self.conn.commit()
                return cost, "insufficient_funds"
            await self.conn.execute(
                "UPDATE users SET wallet = wallet - ? WHERE user_id = ? AND guild_id = ?",
                (cost, user_id, guild_id),
            )
            await self.conn.execute(
                f"""
                UPDATE user_businesses SET {column} = {column} + 1
                WHERE user_id = ? AND guild_id = ?
                """,
                (user_id, guild_id),
            )
            await self.conn.commit()
        return cost, None

    async def _list_completed_acquisitions_no_lock(
        self, user_id: int, guild_id: int,
    ) -> set[str]:
        cursor = await self.conn.execute(
            "SELECT acquisition_id FROM user_empire_acquisitions WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        return {str(r["acquisition_id"]) for r in await cursor.fetchall()}

    async def _list_legacy_perks_no_lock(self, user_id: int, guild_id: int) -> set[str]:
        cursor = await self.conn.execute(
            "SELECT perk_id FROM user_legacy_perks WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        return {str(r["perk_id"]) for r in await cursor.fetchall()}

    async def _reputation_effectiveness_no_lock(self, user_id: int, guild_id: int) -> float:
        acquisitions = await self._list_completed_acquisitions_no_lock(user_id, guild_id)
        bonus = 0.0
        if "media_conglomerate" in acquisitions:
            bonus = float(config.EMPIRE_ACQUISITIONS["media_conglomerate"]["reputation_bonus_factor"])
        return 1.0 + bonus

    async def _security_acquisition_bonus_no_lock(self, user_id: int, guild_id: int) -> int:
        acquisitions = await self._list_completed_acquisitions_no_lock(user_id, guild_id)
        if "private_security" in acquisitions:
            return int(config.EMPIRE_ACQUISITIONS["private_security"]["security_bonus"])
        return 0

    async def _synergy_income_mult_no_lock(self, row: aiosqlite.Row, now: float) -> float:
        stacks = int(row["synergy_stacks"] or 0)
        expires = float(row["synergy_expires"] or 0)
        if stacks <= 0 or expires <= now:
            return 1.0
        bonus = stacks * config.DRUG_SYNERGY_BUFF_INCOME_BONUS
        return 1.0 + bonus

    async def _district_war_income_mult_no_lock(
        self, user_id: int, guild_id: int, row: aiosqlite.Row, now: float,
    ) -> float:
        district_id = row["district_id"]
        if not district_id:
            return 1.0
        crew = await self._crew_name_no_lock(user_id, guild_id)
        if crew is None:
            return 1.0
        cursor = await self.conn.execute(
            """
            SELECT crew_name, bonus_ends_at FROM district_war_control
            WHERE guild_id = ? AND district_id = ?
            """,
            (guild_id, str(district_id)),
        )
        control = await cursor.fetchone()
        if control is None:
            return 1.0
        if float(control["bonus_ends_at"]) <= now:
            return 1.0
        if str(control["crew_name"]).lower() != crew.lower():
            return 1.0
        return 1.0 + config.DISTRICT_WAR_CONTROL_BONUS

    async def _apply_satisfaction_decay_no_lock(
        self, row: aiosqlite.Row, user_id: int, guild_id: int, now: float,
    ) -> None:
        last_at = float(row["last_satisfaction_at"] or row["created_at"] or now)
        if last_at <= 0:
            last_at = now
        elapsed_hours = max(0.0, (now - last_at) / 3600.0)
        if elapsed_hours < 1.0:
            return
        days = elapsed_hours / 24.0
        decay = days * config.BUSINESS_SATISFACTION_DECAY_PER_DAY
        sat = int(row["employee_satisfaction"])
        neutral = config.BUSINESS_SATISFACTION_NEUTRAL
        if sat > neutral:
            new_sat = max(neutral, int(round(sat - decay)))
        elif sat < neutral:
            new_sat = min(neutral, int(round(sat + decay * 0.5)))
        else:
            new_sat = sat
        # Neglect penalty if no management in 24h+ and below neutral
        if elapsed_hours >= config.BUSINESS_MANAGE_NEGLECT_HOURS and sat <= neutral:
            new_sat = max(0, new_sat - config.BUSINESS_MANAGE_NEGLECT_PENALTY)
        if new_sat != sat:
            await self.conn.execute(
                """
                UPDATE user_businesses SET employee_satisfaction = ?, last_satisfaction_at = ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (new_sat, now, user_id, guild_id),
            )

    async def manage_business_wages(
        self, user_id: int, guild_id: int,
    ) -> dict[str, object]:
        async with self._write_lock:
            row = await self._settle_business_income_no_lock(user_id, guild_id)
            if row is None:
                await self.conn.commit()
                return {"error": "no_business"}
            breakdown = await self.get_business_income_breakdown(user_id, guild_id, row)
            hourly = breakdown.effective_hourly if breakdown else self._business_hourly_from_row(row)
            cost = round(hourly * config.BUSINESS_MANAGE_WAGE_COST_FRACTION, 2)
            cursor = await self.conn.execute(
                "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            wallet_row = await cursor.fetchone()
            if wallet_row is None or float(wallet_row["wallet"]) < cost:
                await self.conn.commit()
                return {"error": "insufficient_funds", "cost": cost}
            sat = min(100, int(row["employee_satisfaction"]) + config.BUSINESS_MANAGE_WAGE_SAT_GAIN)
            await self.conn.execute(
                "UPDATE users SET wallet = wallet - ? WHERE user_id = ? AND guild_id = ?",
                (cost, user_id, guild_id),
            )
            now = time.time()
            await self.conn.execute(
                """
                UPDATE user_businesses
                SET employee_satisfaction = ?, last_satisfaction_at = ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (sat, now, user_id, guild_id),
            )
            await self.conn.commit()
        return {"error": None, "cost": cost, "satisfaction": sat}

    async def manage_business_event(
        self, user_id: int, guild_id: int,
    ) -> dict[str, object]:
        async with self._write_lock:
            row = await self._settle_business_income_no_lock(user_id, guild_id)
            if row is None:
                await self.conn.commit()
                return {"error": "no_business"}
            now = time.time()
            last_event = float(row["last_team_event_at"] or 0)
            if last_event > 0 and (now - last_event) < config.BUSINESS_MANAGE_EVENT_COOLDOWN_SECONDS:
                await self.conn.commit()
                return {"error": "cooldown", "retry_after": config.BUSINESS_MANAGE_EVENT_COOLDOWN_SECONDS - (now - last_event)}
            tier = int(row["tier"])
            cost = config.BUSINESS_MANAGE_EVENT_BASE_COST + tier * config.BUSINESS_MANAGE_EVENT_COST_PER_TIER
            cursor = await self.conn.execute(
                "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            wallet_row = await cursor.fetchone()
            if wallet_row is None or float(wallet_row["wallet"]) < cost:
                await self.conn.commit()
                return {"error": "insufficient_funds", "cost": cost}
            sat = min(100, int(row["employee_satisfaction"]) + config.BUSINESS_MANAGE_EVENT_SAT_GAIN)
            await self.conn.execute(
                "UPDATE users SET wallet = wallet - ? WHERE user_id = ? AND guild_id = ?",
                (cost, user_id, guild_id),
            )
            await self.conn.execute(
                """
                UPDATE user_businesses
                SET employee_satisfaction = ?, last_satisfaction_at = ?, last_team_event_at = ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (sat, now, now, user_id, guild_id),
            )
            await self.conn.commit()
        return {"error": None, "cost": cost, "satisfaction": sat}

    async def set_supply_chain_drug(
        self, user_id: int, guild_id: int, drug_id: str | None,
    ) -> str | None:
        from utils.drugs import drug_by_id

        if drug_id is not None:
            if drug_by_id(drug_id) is None:
                return "invalid_drug"
            drug_id = drug_by_id(drug_id).drug_id
        async with self._write_lock:
            row = await self.get_business(user_id, guild_id)
            if row is None:
                await self.conn.commit()
                return "no_business"
            if int(row["tier"]) < config.DRUG_SUPPLY_CHAIN_TIER_MIN:
                await self.conn.commit()
                return "tier_too_low"
            await self.conn.execute(
                "UPDATE user_businesses SET supply_chain_drug_id = ? WHERE user_id = ? AND guild_id = ?",
                (drug_id, user_id, guild_id),
            )
            await self.conn.commit()
        return None

    async def apply_drug_synergy_buff(self, user_id: int, guild_id: int) -> None:
        async with self._write_lock:
            await self._apply_drug_synergy_buff_no_lock(user_id, guild_id)
            await self.conn.commit()

    async def _apply_drug_synergy_buff_no_lock(self, user_id: int, guild_id: int) -> None:
        row = await self.get_business(user_id, guild_id)
        if row is None:
            return
        now = time.time()
        expires = float(row["synergy_expires"] or 0)
        stacks = int(row["synergy_stacks"] or 0)
        if expires <= now:
            stacks = 0
        stacks = min(stacks + 1, config.DRUG_SYNERGY_BUFF_MAX_STACKS)
        await self.conn.execute(
            """
            UPDATE user_businesses
            SET synergy_stacks = ?, synergy_expires = ?
            WHERE user_id = ? AND guild_id = ?
            """,
            (stacks, now + config.DRUG_SYNERGY_BUFF_DURATION_SECONDS, user_id, guild_id),
        )

    async def get_drug_stats(self, user_id: int, guild_id: int) -> dict[str, int]:
        cursor = await self.conn.execute(
            "SELECT units_sold FROM user_drug_stats WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        row = await cursor.fetchone()
        return {"units_sold": int(row["units_sold"]) if row else 0}

    async def _increment_drug_units_sold_no_lock(
        self, user_id: int, guild_id: int, quantity: int,
    ) -> None:
        await self._ensure_user_no_lock(user_id, guild_id)
        await self.conn.execute(
            """
            INSERT INTO user_drug_stats (user_id, guild_id, units_sold)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, guild_id) DO UPDATE SET
                units_sold = user_drug_stats.units_sold + excluded.units_sold
            """,
            (user_id, guild_id, max(0, int(quantity))),
        )

    async def grant_legacy_perk(
        self, user_id: int, guild_id: int, perk_id: str,
    ) -> str | None:
        from utils.legacy_perks import legacy_perk_by_id

        if legacy_perk_by_id(perk_id) is None:
            return "invalid_perk"
        async with self._write_lock:
            cursor = await self.conn.execute(
                "SELECT 1 FROM user_legacy_perks WHERE user_id = ? AND guild_id = ? AND perk_id = ?",
                (user_id, guild_id, perk_id),
            )
            if await cursor.fetchone() is not None:
                await self.conn.commit()
                return "already_owned"
            await self.conn.execute(
                """
                INSERT INTO user_legacy_perks (user_id, guild_id, perk_id, granted_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, guild_id, perk_id, time.time()),
            )
            await self.conn.commit()
        return None

    async def list_legacy_perks(self, user_id: int, guild_id: int) -> set[str]:
        return await self._list_legacy_perks_no_lock(user_id, guild_id)

    async def list_empire_acquisitions(self, user_id: int, guild_id: int) -> set[str]:
        return await self._list_completed_acquisitions_no_lock(user_id, guild_id)

    async def purchase_empire_acquisition(
        self, user_id: int, guild_id: int, acquisition_id: str,
    ) -> dict[str, object]:
        from utils.empire_acquisitions import acquisition_by_id
        from utils.mega_projects import MEGA_PROJECTS

        acq = acquisition_by_id(acquisition_id)
        if acq is None:
            return {"error": "invalid_acquisition"}
        completed_megas = await self.list_user_mega_projects(user_id, guild_id)
        all_megas_done = all(
            pid in completed_megas and completed_megas[pid].get("completed_at")
            for pid in (p.project_id for p in MEGA_PROJECTS)
        )
        if not all_megas_done:
            return {"error": "megas_incomplete"}
        async with self._write_lock:
            existing = await self._list_completed_acquisitions_no_lock(user_id, guild_id)
            if acquisition_id in existing:
                await self.conn.commit()
                return {"error": "already_owned"}
            cursor = await self.conn.execute(
                "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            wallet_row = await cursor.fetchone()
            if wallet_row is None or float(wallet_row["wallet"]) < acq.cost:
                await self.conn.commit()
                return {"error": "insufficient_funds", "cost": acq.cost}
            await self.conn.execute(
                "UPDATE users SET wallet = wallet - ? WHERE user_id = ? AND guild_id = ?",
                (acq.cost, user_id, guild_id),
            )
            await self.conn.execute(
                """
                INSERT INTO user_empire_acquisitions (user_id, guild_id, acquisition_id, completed_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, guild_id, acquisition_id, time.time()),
            )
            await self.conn.commit()
        return {"error": None, "acquisition_id": acquisition_id, "cost": acq.cost}

    async def process_district_wars(self, guild_id: int) -> None:
        from utils.districts import DISTRICT_MAP

        now = time.time()
        ends_at = now + config.DISTRICT_WAR_TICK_SECONDS
        async with self._write_lock:
            for district_id in DISTRICT_MAP:
                cursor = await self.conn.execute(
                    """
                    SELECT cm.crew_name, SUM(di.influence) AS total
                    FROM district_influence di
                    JOIN crew_members cm
                      ON cm.guild_id = di.guild_id AND CAST(cm.user_id AS TEXT) = di.entity_id
                    WHERE di.guild_id = ? AND di.district_id = ? AND di.entity_type = 'user'
                    GROUP BY cm.crew_name
                    ORDER BY total DESC
                    LIMIT 1
                    """,
                    (guild_id, district_id),
                )
                top = await cursor.fetchone()
                if top is None or float(top["total"] or 0) <= 0:
                    continue
                await self.conn.execute(
                    """
                    INSERT INTO district_war_control (guild_id, district_id, crew_name, bonus_ends_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(guild_id, district_id) DO UPDATE SET
                        crew_name = excluded.crew_name,
                        bonus_ends_at = excluded.bonus_ends_at
                    """,
                    (guild_id, district_id, str(top["crew_name"]), ends_at),
                )
            await self.conn.commit()

    async def contest_district_war(
        self, user_id: int, guild_id: int, district_id: str,
    ) -> dict[str, object]:
        from utils.districts import district_by_id

        if district_by_id(district_id) is None:
            return {"error": "invalid_district"}
        crew = await self.get_crew_membership(user_id, guild_id)
        if crew is None:
            return {"error": "no_crew"}
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                SELECT influence FROM district_influence
                WHERE guild_id = ? AND district_id = ? AND entity_type = 'user' AND entity_id = ?
                """,
                (guild_id, district_id, str(user_id)),
            )
            row = await cursor.fetchone()
            influence = float(row["influence"]) if row else 0.0
            if influence < config.DISTRICT_WAR_CONTEST_COST:
                await self.conn.commit()
                return {"error": "insufficient_influence", "needed": config.DISTRICT_WAR_CONTEST_COST}
            new_inf = influence - config.DISTRICT_WAR_CONTEST_COST
            await self.conn.execute(
                """
                INSERT INTO district_influence (
                    guild_id, district_id, entity_type, entity_id, influence, updated_at
                ) VALUES (?, ?, 'user', ?, ?, ?)
                ON CONFLICT(guild_id, district_id, entity_type, entity_id) DO UPDATE SET
                    influence = excluded.influence,
                    updated_at = excluded.updated_at
                """,
                (guild_id, district_id, str(user_id), new_inf, time.time()),
            )
            await self.conn.commit()
        return {"error": None, "influence_spent": config.DISTRICT_WAR_CONTEST_COST, "crew": crew}

    async def process_business_income(self, guild_id: int) -> None:
        """Background tick: accrue stored income for every business in a guild."""
        async with self._write_lock:
            cursor = await self.conn.execute(
                "SELECT user_id FROM user_businesses WHERE guild_id = ?",
                (guild_id,),
            )
            user_ids = [int(r["user_id"]) for r in await cursor.fetchall()]
            now = time.time()
            for uid in user_ids:
                await self._settle_business_income_no_lock(uid, guild_id, now=now)
                await self._process_supply_chain_no_lock(uid, guild_id, now=now)
            await self.conn.commit()

    async def _process_supply_chain_no_lock(
        self, user_id: int, guild_id: int, now: float,
    ) -> None:
        """Auto-plant supply chain drug when a lab slot is free (T5+ businesses)."""
        from utils.drugs import drug_by_id

        cursor = await self.conn.execute(
            "SELECT tier, supply_chain_drug_id FROM user_businesses WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        biz = await cursor.fetchone()
        if biz is None or int(biz["tier"]) < config.DRUG_SUPPLY_CHAIN_TIER_MIN:
            return
        drug_id = biz["supply_chain_drug_id"]
        if not drug_id:
            return
        defn = drug_by_id(str(drug_id))
        if defn is None:
            return
        stats = await self.get_drug_stats(user_id, guild_id)
        from utils.dealer_ranks import dealer_rank, lab_slot_count
        from utils.legacy_perks import extra_lab_slots_from_perks

        rank = dealer_rank(stats["units_sold"])
        legacy = await self._list_legacy_perks_no_lock(user_id, guild_id)
        max_slots = lab_slot_count(rank=rank, legacy_extra=extra_lab_slots_from_perks(legacy))
        cursor = await self.conn.execute(
            "SELECT COUNT(*) AS c FROM drug_grows WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        used = int((await cursor.fetchone())["c"])
        if used >= max_slots:
            return
        cursor = await self.conn.execute(
            "SELECT stored_income FROM user_businesses WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        stored_row = await cursor.fetchone()
        stored = float(stored_row["stored_income"] or 0) if stored_row else 0.0
        if stored < defn.seed_cost:
            return
        grow_seconds = int(defn.grow_seconds * config.DRUG_SUPPLY_CHAIN_GROW_SLOWDOWN)
        await self.conn.execute(
            "UPDATE user_businesses SET stored_income = stored_income - ? WHERE user_id = ? AND guild_id = ?",
            (defn.seed_cost, user_id, guild_id),
        )
        await self.conn.execute(
            """
            INSERT INTO drug_grows (user_id, guild_id, drug_id, planted_at, ready_at, yield_mult)
            VALUES (?, ?, ?, ?, ?, 1.0)
            """,
            (user_id, guild_id, defn.drug_id, now, now + grow_seconds),
        )

    async def prestige_business(
        self, user_id: int, guild_id: int,
    ) -> tuple[str | None, int]:
        """Prestige a maxed business: reset to tier 1, bank a permanent income bonus."""
        from utils.businesses import MAX_TIER, tier_def

        async with self._write_lock:
            row = await self._settle_business_income_no_lock(user_id, guild_id)
            if row is None:
                await self.conn.commit()
                return "no_business", 0
            if int(row["tier"]) < MAX_TIER:
                await self.conn.commit()
                return "not_max_tier", int(row["business_prestige"])
            if int(row["business_prestige"]) >= config.BUSINESS_PRESTIGE_MAX_LEVEL:
                legacy = await self._list_legacy_perks_no_lock(user_id, guild_id)
                if len(legacy) >= len(config.BUSINESS_LEGACY_PERKS):
                    await self.conn.commit()
                    return "max_prestige", int(row["business_prestige"])
                new_prestige = int(row["business_prestige"])
            else:
                new_prestige = int(row["business_prestige"]) + 1
            tier1 = tier_def(1)
            await self.conn.execute(
                """
                UPDATE user_businesses
                SET tier = 1, tier_id = ?, stored_income = 0,
                    business_prestige = ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (tier1.tier_id if tier1 else "lemon_stand", new_prestige, user_id, guild_id),
            )
            await self.conn.commit()
        return None, new_prestige

    async def list_user_mega_projects(
        self, user_id: int, guild_id: int,
    ) -> dict[str, dict[str, object]]:
        cursor = await self.conn.execute(
            """
            SELECT project_id, funded_amount, completed_at FROM user_mega_projects
            WHERE user_id = ? AND guild_id = ?
            """,
            (user_id, guild_id),
        )
        return {
            str(r["project_id"]): {
                "funded_amount": float(r["funded_amount"]),
                "completed_at": r["completed_at"],
            }
            for r in await cursor.fetchall()
        }

    async def contribute_to_mega_project(
        self, user_id: int, guild_id: int, project_id: str, amount: float,
    ) -> dict[str, object]:
        """Fund a personal mega project from the wallet. Returns a result dict."""
        from utils.mega_projects import mega_project_by_id

        project = mega_project_by_id(project_id)
        if project is None:
            return {"error": "invalid_project"}
        if amount <= 0:
            return {"error": "invalid_amount"}
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                SELECT funded_amount, completed_at FROM user_mega_projects
                WHERE user_id = ? AND guild_id = ? AND project_id = ?
                """,
                (user_id, guild_id, project_id),
            )
            existing = await cursor.fetchone()
            if existing is not None and existing["completed_at"] is not None:
                await self.conn.commit()
                return {"error": "already_complete"}
            funded = float(existing["funded_amount"]) if existing is not None else 0.0
            remaining = max(0.0, project.cost - funded)
            contribution = min(amount, remaining)
            if contribution <= 0:
                await self.conn.commit()
                return {"error": "already_complete"}
            cursor = await self.conn.execute(
                "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            wallet_row = await cursor.fetchone()
            if wallet_row is None or float(wallet_row["wallet"]) < contribution:
                await self.conn.commit()
                return {"error": "insufficient_funds", "needed": contribution}
            await self.conn.execute(
                "UPDATE users SET wallet = wallet - ? WHERE user_id = ? AND guild_id = ?",
                (contribution, user_id, guild_id),
            )
            new_funded = funded + contribution
            completed = new_funded >= project.cost
            completed_at = time.time() if completed else None
            await self.conn.execute(
                """
                INSERT INTO user_mega_projects (user_id, guild_id, project_id, funded_amount, completed_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, guild_id, project_id) DO UPDATE SET
                    funded_amount = excluded.funded_amount,
                    completed_at = excluded.completed_at
                """,
                (user_id, guild_id, project_id, new_funded, completed_at),
            )
            await self.conn.commit()
        return {
            "error": None,
            "contribution": contribution,
            "funded": new_funded,
            "target": project.cost,
            "completed": completed,
            "income_bonus": project.income_bonus,
        }

    # --- Drug trade ---------------------------------------------------------

    async def get_drug_inventory(self, user_id: int, guild_id: int) -> dict[str, int]:
        from utils.drugs import drug_by_id

        cursor = await self.conn.execute(
            "SELECT drug_id, quantity FROM drug_inventory WHERE user_id = ? AND guild_id = ? AND quantity > 0",
            (user_id, guild_id),
        )
        merged: dict[str, int] = {}
        for r in await cursor.fetchall():
            drug_id = str(r["drug_id"])
            qty = int(r["quantity"])
            defn = drug_by_id(drug_id)
            key = defn.drug_id if defn is not None else drug_id
            merged[key] = merged.get(key, 0) + qty
        return merged

    async def _find_drug_inventory_qty(
        self, user_id: int, guild_id: int, drug_id: str,
    ) -> tuple[str | None, int]:
        from utils.drugs import drug_by_id, inventory_lookup_ids

        defn = drug_by_id(drug_id)
        if defn is None:
            return None, 0
        for lookup_id in inventory_lookup_ids(defn):
            cursor = await self.conn.execute(
                "SELECT quantity FROM drug_inventory WHERE user_id = ? AND guild_id = ? AND drug_id = ?",
                (user_id, guild_id, lookup_id),
            )
            row = await cursor.fetchone()
            if row is not None and int(row["quantity"]) > 0:
                return lookup_id, int(row["quantity"])
        return None, 0

    async def list_drug_grows(self, user_id: int, guild_id: int) -> list[dict[str, object]]:
        cursor = await self.conn.execute(
            """
            SELECT grow_id, drug_id, planted_at, ready_at, yield_mult FROM drug_grows
            WHERE user_id = ? AND guild_id = ?
            ORDER BY ready_at ASC
            """,
            (user_id, guild_id),
        )
        rows: list[dict[str, object]] = []
        for r in await cursor.fetchall():
            row = dict(r)
            try:
                row["yield_mult"] = float(row.get("yield_mult") or 1.0)
            except (TypeError, ValueError):
                row["yield_mult"] = 1.0
            rows.append(row)
        return rows

    async def plant_drug(
        self,
        user_id: int,
        guild_id: int,
        drug_id: str,
        *,
        fertilizer_id: str | None = None,
    ) -> tuple[float, str | None]:
        """Plant a strain in a free lab slot. Returns (seed_cost, error)."""
        from utils.drugs import drug_by_id
        from utils.fertilizer import fertilizer_by_id

        defn = drug_by_id(drug_id)
        if defn is None:
            return 0.0, "invalid_drug"
        fert = fertilizer_by_id(fertilizer_id) if fertilizer_id else None
        if fertilizer_id and fert is None:
            return 0.0, "invalid_fertilizer"
        grow_seconds = float(defn.grow_seconds)
        yield_mult = 1.0
        if fert is not None:
            grow_seconds *= fert.grow_time_mult
            yield_mult = fert.yield_mult
        now = time.time()
        async with self._write_lock:
            if fert is not None:
                qty = await self._inventory_qty_unlocked(user_id, guild_id, fert.item_id)
                if qty < 1:
                    await self.conn.commit()
                    return defn.seed_cost, "no_fertilizer"
            cursor = await self.conn.execute(
                "SELECT COUNT(*) AS c FROM drug_grows WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            row = await cursor.fetchone()
            stats = await self.get_drug_stats(user_id, guild_id)
            from utils.dealer_ranks import dealer_rank, lab_slot_count
            from utils.legacy_perks import extra_lab_slots_from_perks

            rank = dealer_rank(stats["units_sold"])
            legacy = await self._list_legacy_perks_no_lock(user_id, guild_id)
            max_slots = lab_slot_count(rank=rank, legacy_extra=extra_lab_slots_from_perks(legacy))
            if row is not None and int(row["c"]) >= max_slots:
                await self.conn.commit()
                return 0.0, "no_slots"
            acquisitions = await self._list_completed_acquisitions_no_lock(user_id, guild_id)
            if "pharma_lab" in acquisitions:
                grow_seconds *= 1.0 - float(config.EMPIRE_ACQUISITIONS["pharma_lab"]["drug_grow_time_reduction"])
            await self._ensure_user_no_lock(user_id, guild_id)
            cursor = await self.conn.execute(
                "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            wallet_row = await cursor.fetchone()
            if wallet_row is None or float(wallet_row["wallet"]) < defn.seed_cost:
                await self.conn.commit()
                return defn.seed_cost, "insufficient_funds"
            await self.conn.execute(
                "UPDATE users SET wallet = wallet - ? WHERE user_id = ? AND guild_id = ?",
                (defn.seed_cost, user_id, guild_id),
            )
            if fert is not None:
                await self.conn.execute(
                    """
                    UPDATE inventory
                    SET quantity = quantity - 1
                    WHERE user_id = ? AND guild_id = ? AND item_id = ? AND quantity > 0
                    """,
                    (user_id, guild_id, fert.item_id),
                )
            await self.conn.execute(
                """
                INSERT INTO drug_grows (user_id, guild_id, drug_id, planted_at, ready_at, yield_mult)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, guild_id, defn.drug_id, now, now + grow_seconds, yield_mult),
            )
            await self.conn.commit()
        return defn.seed_cost, None

    async def apply_fertilizer_to_grow(
        self,
        user_id: int,
        guild_id: int,
        grow_id: int,
        fertilizer_id: str,
    ) -> str | None:
        """Apply shop fertilizer to an in-progress crop. Returns error code or None."""
        from utils.fertilizer import fertilizer_by_id

        fert = fertilizer_by_id(fertilizer_id)
        if fert is None:
            return "invalid_fertilizer"
        now = time.time()
        async with self._write_lock:
            qty = await self._inventory_qty_unlocked(user_id, guild_id, fert.item_id)
            if qty < 1:
                await self.conn.commit()
                return "no_fertilizer"
            cursor = await self.conn.execute(
                """
                SELECT grow_id, ready_at, yield_mult FROM drug_grows
                WHERE grow_id = ? AND user_id = ? AND guild_id = ?
                """,
                (grow_id, user_id, guild_id),
            )
            row = await cursor.fetchone()
            if row is None:
                await self.conn.commit()
                return "invalid_grow"
            current_mult = float(row["yield_mult"] or 1.0)
            if current_mult > 1.0:
                await self.conn.commit()
                return "already_fertilized"
            ready_at = float(row["ready_at"])
            remaining = max(0.0, ready_at - now)
            new_ready = now + remaining * fert.grow_time_mult
            await self.conn.execute(
                """
                UPDATE inventory
                SET quantity = quantity - 1
                WHERE user_id = ? AND guild_id = ? AND item_id = ? AND quantity > 0
                """,
                (user_id, guild_id, fert.item_id),
            )
            await self.conn.execute(
                """
                UPDATE drug_grows
                SET ready_at = ?, yield_mult = ?
                WHERE grow_id = ? AND user_id = ? AND guild_id = ?
                """,
                (new_ready, fert.yield_mult, grow_id, user_id, guild_id),
            )
            await self.conn.commit()
        return None

    async def _inventory_qty_unlocked(self, user_id: int, guild_id: int, item_id: str) -> int:
        cursor = await self.conn.execute(
            "SELECT quantity FROM inventory WHERE user_id = ? AND guild_id = ? AND item_id = ?",
            (user_id, guild_id, item_id),
        )
        row = await cursor.fetchone()
        return int(row["quantity"]) if row is not None else 0

    async def harvest_drugs(self, user_id: int, guild_id: int) -> dict[str, int]:
        """Harvest all ready grows. Returns {drug_id: quantity_added}."""
        import random

        from utils.drugs import drug_by_id, roll_yield

        now = time.time()
        # Industrial district gives a yield bonus.
        yield_bonus = 0.0
        biz = await self.get_business(user_id, guild_id)
        if biz is not None and str(biz["district_id"] or "") == "industrial":
            yield_bonus = config.DRUG_INDUSTRIAL_YIELD_BONUS
        harvested: dict[str, int] = {}
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                SELECT grow_id, drug_id, yield_mult FROM drug_grows
                WHERE user_id = ? AND guild_id = ? AND ready_at <= ?
                """,
                (user_id, guild_id, now),
            )
            ready = [
                (int(r["grow_id"]), str(r["drug_id"]), float(r["yield_mult"] or 1.0))
                for r in await cursor.fetchall()
            ]
            for grow_id, drug_id, crop_yield_mult in ready:
                defn = drug_by_id(drug_id)
                if defn is None:
                    await self.conn.execute("DELETE FROM drug_grows WHERE grow_id = ?", (grow_id,))
                    continue
                amount = roll_yield(defn, yield_bonus=yield_bonus, rng=random)
                amount = max(1, int(round(amount * crop_yield_mult)))
                await self.conn.execute(
                    """
                    INSERT INTO drug_inventory (user_id, guild_id, drug_id, quantity)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id, guild_id, drug_id) DO UPDATE SET quantity = drug_inventory.quantity + excluded.quantity
                    """,
                    (user_id, guild_id, drug_id, amount),
                )
                await self.conn.execute("DELETE FROM drug_grows WHERE grow_id = ?", (grow_id,))
                harvested[drug_id] = harvested.get(drug_id, 0) + amount
            await self.conn.commit()
        return harvested

    async def sell_drugs_street(
        self, user_id: int, guild_id: int, drug_id: str, quantity: int,
    ) -> dict[str, object]:
        """Sell product on the street. Volatile price with a raid risk."""
        import random

        from utils.drugs import drug_by_id, sale_total

        defn = drug_by_id(drug_id)
        if defn is None:
            return {"error": "invalid_drug"}
        if quantity <= 0:
            return {"error": "invalid_amount"}
        canonical_id = defn.drug_id
        async with self._write_lock:
            stored_id, available = await self._find_drug_inventory_qty(user_id, guild_id, canonical_id)
            if stored_id is None or available < quantity:
                await self.conn.commit()
                return {"error": "insufficient_product"}
            raid_chance = config.DRUG_RAID_CHANCE
            cursor = await self.conn.execute(
                "SELECT tier, business_prestige FROM user_businesses WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            biz = await cursor.fetchone()
            if biz is not None and int(biz["tier"]) >= 7:
                reduction = min(
                    config.DRUG_DISTRIBUTION_RAID_REDUCTION_CAP,
                    int(biz["business_prestige"]) * config.DRUG_DISTRIBUTION_RAID_REDUCTION_PER_PRESTIGE,
                )
                raid_chance = max(0.0, raid_chance - reduction)
            raided = random.random() < raid_chance
            if raided:
                lost = max(1, int(quantity * config.DRUG_RAID_LOSS_FRACTION))
                await self.conn.execute(
                    "UPDATE drug_inventory SET quantity = quantity - ? WHERE user_id = ? AND guild_id = ? AND drug_id = ?",
                    (lost, user_id, guild_id, stored_id),
                )
                await self.conn.execute(
                    """
                    INSERT INTO drug_transactions (guild_id, user_id, drug_id, quantity, amount, txn_type, created_at)
                    VALUES (?, ?, ?, ?, 0, 'raid', ?)
                    """,
                    (guild_id, user_id, canonical_id, lost, time.time()),
                )
                await self.conn.commit()
                return {"error": None, "raided": True, "lost": lost}
            total = sale_total(defn, quantity, rng=random)
            await self.conn.execute(
                "UPDATE drug_inventory SET quantity = quantity - ? WHERE user_id = ? AND guild_id = ? AND drug_id = ?",
                (quantity, user_id, guild_id, stored_id),
            )
            await self.conn.execute(
                "UPDATE users SET wallet = wallet + ?, total_earned = total_earned + ? WHERE user_id = ? AND guild_id = ?",
                (total, total, user_id, guild_id),
            )
            await self.conn.execute(
                """
                INSERT INTO drug_transactions (guild_id, user_id, drug_id, quantity, amount, txn_type, created_at)
                VALUES (?, ?, ?, ?, ?, 'street_sell', ?)
                """,
                (guild_id, user_id, canonical_id, quantity, total, time.time()),
            )
            await self._increment_drug_units_sold_no_lock(user_id, guild_id, quantity)
            await self.conn.commit()
        return {"error": None, "raided": False, "total": total, "quantity": quantity}

    async def sell_drugs_wholesale(
        self, user_id: int, guild_id: int, drug_id: str, quantity: int,
    ) -> dict[str, object]:
        """Bulk NPC buyer: fixed price, no raid risk (Dealer Rank 7+)."""
        from utils.dealer_ranks import can_wholesale, dealer_rank
        from utils.drugs import drug_by_id

        defn = drug_by_id(drug_id)
        if defn is None:
            return {"error": "invalid_drug"}
        if quantity <= 0:
            return {"error": "invalid_amount"}
        stats = await self.get_drug_stats(user_id, guild_id)
        rank = dealer_rank(stats["units_sold"])
        if not can_wholesale(rank):
            return {"error": "rank_locked", "required_rank": config.DEALER_RANK_WHOLESALE_UNLOCK}
        canonical_id = defn.drug_id
        unit_price = defn.street_price * config.DRUG_WHOLESALE_PRICE_FACTOR
        total = round(unit_price * quantity, 2)
        async with self._write_lock:
            stored_id, available = await self._find_drug_inventory_qty(user_id, guild_id, canonical_id)
            if stored_id is None or available < quantity:
                await self.conn.commit()
                return {"error": "insufficient_product"}
            await self.conn.execute(
                "UPDATE drug_inventory SET quantity = quantity - ? WHERE user_id = ? AND guild_id = ? AND drug_id = ?",
                (quantity, user_id, guild_id, stored_id),
            )
            await self.conn.execute(
                "UPDATE users SET wallet = wallet + ?, total_earned = total_earned + ? WHERE user_id = ? AND guild_id = ?",
                (total, total, user_id, guild_id),
            )
            await self.conn.execute(
                """
                INSERT INTO drug_transactions (guild_id, user_id, drug_id, quantity, amount, txn_type, created_at)
                VALUES (?, ?, ?, ?, ?, 'wholesale', ?)
                """,
                (guild_id, user_id, canonical_id, quantity, total, time.time()),
            )
            await self._increment_drug_units_sold_no_lock(user_id, guild_id, quantity)
            await self.conn.commit()
        return {"error": None, "total": total, "quantity": quantity, "unit_price": unit_price}

    async def create_drug_listing(
        self, user_id: int, guild_id: int, drug_id: str, quantity: int, price_per_unit: float,
    ) -> str | None:
        from utils.drugs import drug_by_id

        defn = drug_by_id(drug_id)
        if defn is None:
            return "invalid_drug"
        if quantity <= 0 or quantity > config.DRUG_MAX_LISTING_QTY or price_per_unit <= 0:
            return "invalid_amount"
        from utils.dealer_ranks import can_list_on_market, dealer_rank

        stats = await self.get_drug_stats(user_id, guild_id)
        if not can_list_on_market(dealer_rank(stats["units_sold"])):
            return "rank_locked"
        canonical_id = defn.drug_id
        async with self._write_lock:
            stored_id, available = await self._find_drug_inventory_qty(user_id, guild_id, canonical_id)
            if stored_id is None or available < quantity:
                await self.conn.commit()
                return "insufficient_product"
            await self.conn.execute(
                "UPDATE drug_inventory SET quantity = quantity - ? WHERE user_id = ? AND guild_id = ? AND drug_id = ?",
                (quantity, user_id, guild_id, stored_id),
            )
            await self.conn.execute(
                """
                INSERT INTO drug_market_listings (guild_id, seller_id, drug_id, quantity, price_per_unit, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (guild_id, user_id, canonical_id, quantity, price_per_unit, time.time()),
            )
            await self.conn.commit()
        return None

    async def list_drug_market(self, guild_id: int, *, limit: int = 20) -> list[dict[str, object]]:
        cursor = await self.conn.execute(
            """
            SELECT listing_id, seller_id, drug_id, quantity, price_per_unit FROM drug_market_listings
            WHERE guild_id = ?
            ORDER BY price_per_unit ASC
            LIMIT ?
            """,
            (guild_id, limit),
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def list_user_drug_listings(
        self, user_id: int, guild_id: int, *, limit: int = 25,
    ) -> list[dict[str, object]]:
        cursor = await self.conn.execute(
            """
            SELECT listing_id, drug_id, quantity, price_per_unit
            FROM drug_market_listings
            WHERE guild_id = ? AND seller_id = ?
            ORDER BY listing_id DESC
            LIMIT ?
            """,
            (guild_id, user_id, limit),
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def cancel_drug_listing(self, user_id: int, guild_id: int, listing_id: int) -> str | None:
        async with self._write_lock:
            cursor = await self.conn.execute(
                "SELECT seller_id, drug_id, quantity FROM drug_market_listings WHERE listing_id = ? AND guild_id = ?",
                (listing_id, guild_id),
            )
            listing = await cursor.fetchone()
            if listing is None:
                await self.conn.commit()
                return "not_found"
            if int(listing["seller_id"]) != user_id:
                await self.conn.commit()
                return "not_owner"
            await self.conn.execute(
                """
                INSERT INTO drug_inventory (user_id, guild_id, drug_id, quantity)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, guild_id, drug_id) DO UPDATE SET quantity = drug_inventory.quantity + excluded.quantity
                """,
                (user_id, guild_id, str(listing["drug_id"]), int(listing["quantity"])),
            )
            await self.conn.execute("DELETE FROM drug_market_listings WHERE listing_id = ?", (listing_id,))
            await self.conn.commit()
        return None

    async def buy_drug_listing(
        self, user_id: int, guild_id: int, listing_id: int, quantity: int,
    ) -> dict[str, object]:
        """Buy from a market listing. Seller receives proceeds minus market tax."""
        if quantity <= 0:
            return {"error": "invalid_amount"}
        async with self._write_lock:
            cursor = await self.conn.execute(
                "SELECT seller_id, drug_id, quantity, price_per_unit FROM drug_market_listings WHERE listing_id = ? AND guild_id = ?",
                (listing_id, guild_id),
            )
            listing = await cursor.fetchone()
            if listing is None:
                await self.conn.commit()
                return {"error": "not_found"}
            seller_id = int(listing["seller_id"])
            if seller_id == user_id:
                await self.conn.commit()
                return {"error": "own_listing"}
            available = int(listing["quantity"])
            if quantity > available:
                await self.conn.commit()
                return {"error": "not_enough_listed"}
            price = float(listing["price_per_unit"])
            drug_id = str(listing["drug_id"])
            total = price * quantity
            cursor = await self.conn.execute(
                "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            wallet_row = await cursor.fetchone()
            if wallet_row is None or float(wallet_row["wallet"]) < total:
                await self.conn.commit()
                return {"error": "insufficient_funds", "total": total}
            await self.conn.execute(
                "UPDATE users SET wallet = wallet - ? WHERE user_id = ? AND guild_id = ?",
                (total, user_id, guild_id),
            )
            proceeds = total * (1.0 - config.DRUG_MARKET_TAX)
            await self._ensure_user_no_lock(seller_id, guild_id)
            await self.conn.execute(
                "UPDATE users SET wallet = wallet + ?, total_earned = total_earned + ? WHERE user_id = ? AND guild_id = ?",
                (proceeds, proceeds, seller_id, guild_id),
            )
            await self._increment_drug_units_sold_no_lock(seller_id, guild_id, quantity)
            await self.conn.execute(
                """
                INSERT INTO drug_inventory (user_id, guild_id, drug_id, quantity)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, guild_id, drug_id) DO UPDATE SET quantity = drug_inventory.quantity + excluded.quantity
                """,
                (user_id, guild_id, drug_id, quantity),
            )
            remaining = available - quantity
            if remaining > 0:
                await self.conn.execute(
                    "UPDATE drug_market_listings SET quantity = ? WHERE listing_id = ?",
                    (remaining, listing_id),
                )
            else:
                await self.conn.execute("DELETE FROM drug_market_listings WHERE listing_id = ?", (listing_id,))
            await self.conn.commit()
        return {"error": None, "total": total, "quantity": quantity, "drug_id": drug_id}

    async def consume_drug(
        self,
        user_id: int,
        guild_id: int,
        drug_id: str,
        *,
        max_hp: float,
    ) -> dict[str, object]:
        """Consume one unit from the stash and apply immediate effects."""
        import random

        from utils.drugs import drug_buff_key, drug_by_id, drug_effect_duration, drug_has_timed_effect

        defn = drug_by_id(drug_id)
        if defn is None:
            return {"error": "invalid_drug"}
        canonical_id = defn.drug_id
        overdosed = False
        heal_amount = 0.0
        damage_amount = 0.0
        energy_delta = 0
        boss_mult = defn.effect_boss_mult
        duel_mult = defn.effect_duel_mult
        buff_variant: str | None = None
        if defn.drug_id == "lsd":
            if random.random() < 0.5:
                buff_variant = "boss"
                duel_mult = 1.0
            else:
                buff_variant = "duel"
                boss_mult = 1.0
        async with self._write_lock:
            stored_id, available = await self._find_drug_inventory_qty(user_id, guild_id, canonical_id)
            if stored_id is None or available < 1:
                await self.conn.commit()
                return {"error": "insufficient_product"}
            await self.conn.execute(
                "UPDATE drug_inventory SET quantity = quantity - 1 WHERE user_id = ? AND guild_id = ? AND drug_id = ?",
                (user_id, guild_id, stored_id),
            )
            if defn.overdose_chance > 0 and random.random() < defn.overdose_chance:
                overdosed = True
                damage_amount = max(1.0, max_hp * defn.overdose_damage_pct)
            elif defn.effect_heal_pct > 0:
                heal_amount = max(1.0, max_hp * defn.effect_heal_pct)
            if defn.effect_damage_pct > 0 and not overdosed:
                damage_amount = max(1.0, max_hp * defn.effect_damage_pct)
            energy_delta = defn.effect_energy
            if energy_delta != 0:
                await self._ensure_character_no_lock(user_id, guild_id)
                row = await self._refresh_character_energy_unlocked(user_id, guild_id)
                cap = int(row["energy_cap"])
                new_energy = max(0, min(cap, int(row["energy"]) + energy_delta))
                await self.conn.execute(
                    "UPDATE user_character SET energy = ? WHERE user_id = ? AND guild_id = ?",
                    (new_energy, user_id, guild_id),
                )
            if boss_mult > 1.0 or duel_mult > 1.0 or defn.effect_cc_immunity:
                duration = drug_effect_duration(defn)
                expires = time.time() + duration
                await self._ensure_character_no_lock(user_id, guild_id)
                await self.conn.execute(
                    """
                    UPDATE user_character
                    SET active_drug_buff = ?, active_drug_buff_expires = ?
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (drug_buff_key(canonical_id, buff_variant), expires, user_id, guild_id),
                )
                if defn.effect_cc_immunity:
                    await self.conn.execute(
                        """
                        UPDATE boss_raider_status
                        SET attack_slow_until = 0,
                            verdant_root_until = 0,
                            debuff_attack_cooldown = 0
                        WHERE guild_id = ? AND user_id = ?
                        """,
                        (guild_id, user_id),
                    )
                    await self.conn.execute(
                        "UPDATE users SET downed_until = 0 WHERE user_id = ? AND guild_id = ?",
                        (user_id, guild_id),
                    )
            await self._apply_drug_synergy_buff_no_lock(user_id, guild_id)
            await self.conn.commit()
        buff_duration = drug_effect_duration(defn) if drug_has_timed_effect(defn) else None
        new_hp: float | None = None
        if heal_amount > 0:
            new_hp, _ = await self.heal_player(user_id, guild_id, heal_amount, max_hp)
        if damage_amount > 0:
            new_hp, _ = await self.damage_player(user_id, guild_id, damage_amount, max_hp)
        return {
            "error": None,
            "drug_id": canonical_id,
            "name": defn.name,
            "emoji": defn.emoji,
            "effect_summary": defn.effect_summary,
            "energy_delta": energy_delta,
            "heal_amount": heal_amount,
            "damage_amount": damage_amount,
            "new_hp": new_hp,
            "max_hp": max_hp,
            "overdosed": overdosed,
            "boss_buff": boss_mult if boss_mult > 1.0 else None,
            "duel_buff": duel_mult if duel_mult > 1.0 else None,
            "buff_duration": buff_duration,
            "cc_immunity": defn.effect_cc_immunity,
            "attack_hp_risk_chance": defn.effect_attack_hp_risk_chance,
            "attack_hp_risk_pct": defn.effect_attack_hp_risk_pct,
        }

    # --- Crew cartel drug operations ----------------------------------------

    async def get_cartel_stash(self, guild_id: int, crew_name: str) -> dict[str, int]:
        cursor = await self.conn.execute(
            """
            SELECT drug_id, quantity FROM crew_cartel_stash
            WHERE guild_id = ? AND crew_name = ? AND quantity > 0
            """,
            (guild_id, crew_name),
        )
        return {str(r["drug_id"]): int(r["quantity"]) for r in await cursor.fetchall()}

    async def plant_cartel_drug(
        self, user_id: int, guild_id: int, crew_name: str, drug_id: str,
    ) -> tuple[float, str | None]:
        from utils.drugs import drug_by_id

        membership = await self.get_crew_membership(user_id, guild_id)
        if membership is None or membership.lower() != crew_name.lower():
            return 0.0, "not_in_crew"
        defn = drug_by_id(drug_id)
        if defn is None:
            return 0.0, "invalid_drug"
        now = time.time()
        async with self._write_lock:
            cursor = await self.conn.execute(
                "SELECT COUNT(*) AS c FROM crew_cartel_grows WHERE guild_id = ? AND crew_name = ?",
                (guild_id, crew_name),
            )
            if int((await cursor.fetchone())["c"]) >= config.CARTEL_LAB_SLOTS:
                await self.conn.commit()
                return 0.0, "no_slots"
            cursor = await self.conn.execute(
                "SELECT treasury FROM crew_stats WHERE guild_id = ? AND crew_name = ?",
                (guild_id, crew_name),
            )
            crew_row = await cursor.fetchone()
            if crew_row is None or float(crew_row["treasury"]) < defn.seed_cost:
                await self.conn.commit()
                return defn.seed_cost, "insufficient_treasury"
            await self.conn.execute(
                "UPDATE crew_stats SET treasury = treasury - ? WHERE guild_id = ? AND crew_name = ?",
                (defn.seed_cost, guild_id, crew_name),
            )
            await self.conn.execute(
                """
                INSERT INTO crew_cartel_grows (guild_id, crew_name, drug_id, planted_at, ready_at, yield_mult)
                VALUES (?, ?, ?, ?, ?, 1.0)
                """,
                (guild_id, crew_name, defn.drug_id, now, now + defn.grow_seconds),
            )
            await self.conn.commit()
        return defn.seed_cost, None

    async def harvest_cartel(self, guild_id: int, crew_name: str) -> dict[str, int]:
        from utils.drugs import drug_by_id, roll_yield

        now = time.time()
        harvested: dict[str, int] = {}
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                SELECT grow_id, drug_id, yield_mult FROM crew_cartel_grows
                WHERE guild_id = ? AND crew_name = ? AND ready_at <= ?
                """,
                (guild_id, crew_name, now),
            )
            ready = await cursor.fetchall()
            for row in ready:
                grow_id = int(row["grow_id"])
                drug_id = str(row["drug_id"])
                defn = drug_by_id(drug_id)
                if defn is None:
                    await self.conn.execute("DELETE FROM crew_cartel_grows WHERE grow_id = ?", (grow_id,))
                    continue
                amount = roll_yield(defn, yield_mult=float(row["yield_mult"] or 1.0))
                await self.conn.execute(
                    """
                    INSERT INTO crew_cartel_stash (guild_id, crew_name, drug_id, quantity)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(guild_id, crew_name, drug_id) DO UPDATE SET
                        quantity = crew_cartel_stash.quantity + excluded.quantity
                    """,
                    (guild_id, crew_name, drug_id, amount),
                )
                await self.conn.execute("DELETE FROM crew_cartel_grows WHERE grow_id = ?", (grow_id,))
                harvested[drug_id] = harvested.get(drug_id, 0) + amount
            await self.conn.commit()
        return harvested

    async def cartel_street_sell(
        self, user_id: int, guild_id: int, crew_name: str, drug_id: str, quantity: int,
    ) -> dict[str, object]:
        from utils.drugs import drug_by_id

        membership = await self.get_crew_membership(user_id, guild_id)
        if membership is None or membership.lower() != crew_name.lower():
            return {"error": "not_in_crew"}
        defn = drug_by_id(drug_id)
        if defn is None or quantity <= 0:
            return {"error": "invalid_amount"}
        unit_price = defn.street_price * config.DRUG_WHOLESALE_PRICE_FACTOR
        total = round(unit_price * quantity, 2)
        crew_share = round(total * config.CARTEL_STREET_SELL_CREW_SHARE, 2)
        player_share = round(total * config.CARTEL_STREET_SELL_PLAYER_SHARE, 2)
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                SELECT quantity FROM crew_cartel_stash
                WHERE guild_id = ? AND crew_name = ? AND drug_id = ?
                """,
                (guild_id, crew_name, defn.drug_id),
            )
            stash = await cursor.fetchone()
            if stash is None or int(stash["quantity"]) < quantity:
                await self.conn.commit()
                return {"error": "insufficient_product"}
            await self.conn.execute(
                """
                UPDATE crew_cartel_stash SET quantity = quantity - ?
                WHERE guild_id = ? AND crew_name = ? AND drug_id = ?
                """,
                (quantity, guild_id, crew_name, defn.drug_id),
            )
            await self.conn.execute(
                "UPDATE crew_stats SET treasury = treasury + ? WHERE guild_id = ? AND crew_name = ?",
                (crew_share, guild_id, crew_name),
            )
            await self._ensure_user_no_lock(user_id, guild_id)
            await self.conn.execute(
                "UPDATE users SET wallet = wallet + ?, total_earned = total_earned + ? WHERE user_id = ? AND guild_id = ?",
                (player_share, player_share, user_id, guild_id),
            )
            await self._increment_drug_units_sold_no_lock(user_id, guild_id, quantity)
            await self.conn.commit()
        return {
            "error": None,
            "total": total,
            "crew_share": crew_share,
            "player_share": player_share,
            "quantity": quantity,
        }

    def _pending_drug_buff_payload(
        self,
        pending: str,
        expires: float,
        *,
        now: float,
    ) -> dict[str, object] | None:
        from utils.drugs import drug_by_id, parse_drug_buff_key

        drug_id = parse_drug_buff_key(pending)
        if drug_id is None:
            return None
        if expires < now:
            return None
        defn = drug_by_id(drug_id)
        if defn is None:
            return None
        pending_str = str(pending)
        variant = pending_str.split(":", 2)[2] if pending_str.count(":") >= 2 else None
        boss_mult = defn.effect_boss_mult
        duel_mult = defn.effect_duel_mult
        if defn.drug_id == "lsd" and variant == "boss":
            duel_mult = 1.0
        elif defn.drug_id == "lsd" and variant == "duel":
            boss_mult = 1.0
        return {
            "drug_id": defn.drug_id,
            "name": defn.name,
            "boss_mult": boss_mult,
            "duel_mult": duel_mult,
            "cc_immunity": defn.effect_cc_immunity,
            "attack_hp_risk_chance": defn.effect_attack_hp_risk_chance,
            "attack_hp_risk_pct": defn.effect_attack_hp_risk_pct,
            "expires": expires,
        }

    async def _clear_expired_active_drug_buff(
        self, user_id: int, guild_id: int, *, now: float | None = None,
    ) -> None:
        at = time.time() if now is None else now
        row = await self._refresh_character_energy_unlocked(user_id, guild_id)
        try:
            pending = row["active_drug_buff"]
            expires = float(row["active_drug_buff_expires"] or 0)
        except (KeyError, TypeError):
            return
        from utils.drugs import parse_drug_buff_key

        if parse_drug_buff_key(str(pending) if pending else None) is None:
            return
        if expires >= at:
            return
        await self.conn.execute(
            """
            UPDATE user_character
            SET active_drug_buff = NULL, active_drug_buff_expires = NULL
            WHERE user_id = ? AND guild_id = ?
            """,
            (user_id, guild_id),
        )
        await self.conn.commit()

    async def _active_drug_buff_row(
        self, user_id: int, guild_id: int, *, now: float | None = None,
    ) -> tuple[str, float] | None:
        at = time.time() if now is None else now
        row = await self._refresh_character_energy_unlocked(user_id, guild_id)
        try:
            pending = row["active_drug_buff"]
            expires = float(row["active_drug_buff_expires"] or 0)
        except (KeyError, TypeError):
            return None
        from utils.drugs import parse_drug_buff_key

        if parse_drug_buff_key(str(pending) if pending else None) is None:
            return None
        if expires < at:
            return None
        return str(pending), expires

    async def take_pending_drug_buff(self, user_id: int, guild_id: int) -> dict[str, object] | None:
        """Return the active drug combat buff without consuming it."""
        return await self.peek_pending_drug_buff(user_id, guild_id)

    async def peek_pending_drug_buff(self, user_id: int, guild_id: int) -> dict[str, object] | None:
        """Return active drug combat buff without consuming it."""
        async with self._write_lock:
            now = time.time()
            await self._clear_expired_active_drug_buff(user_id, guild_id, now=now)
            row = await self._active_drug_buff_row(user_id, guild_id, now=now)
            if row is None:
                return None
            pending, expires = row
            return self._pending_drug_buff_payload(pending, expires, now=now)

    async def has_active_drug_cc_immunity(self, user_id: int, guild_id: int) -> bool:
        buff = await self.peek_pending_drug_buff(user_id, guild_id)
        return bool(buff and buff.get("cc_immunity"))

    async def roll_drug_attack_hp_risk(
        self,
        user_id: int,
        guild_id: int,
        *,
        max_hp: float,
    ) -> tuple[float, str]:
        """During opioid highs, each attack may self-damage."""
        import random

        async with self._write_lock:
            now = time.time()
            await self._clear_expired_active_drug_buff(user_id, guild_id, now=now)
            row = await self._active_drug_buff_row(user_id, guild_id, now=now)
            if row is None:
                return 0.0, ""
            pending, expires = row
            buff = self._pending_drug_buff_payload(pending, expires, now=now)
            if buff is None:
                return 0.0, ""
            chance = float(buff.get("attack_hp_risk_chance") or 0.0)
            pct = float(buff.get("attack_hp_risk_pct") or 0.0)
            if chance <= 0.0 or pct <= 0.0 or random.random() >= chance:
                return 0.0, ""
            damage = max(1.0, max_hp * pct)
            cursor = await self.conn.execute(
                """
                SELECT hp, max_hp
                FROM combat_state
                WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, user_id),
            )
            hp_row = await cursor.fetchone()
            hp = max_hp if hp_row is None else min(max_hp, float(hp_row["hp"]))
            new_hp = max(0.0, hp - damage)
            await self.conn.execute(
                """
                INSERT INTO combat_state (guild_id, user_id, hp, max_hp)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    hp = excluded.hp,
                    max_hp = excluded.max_hp
                """,
                (guild_id, user_id, new_hp, max_hp),
            )
            await self.conn.commit()
        return damage, f" 💉 **Withdrawal hit!** Lost **{int(damage)}** HP."

    async def _active_buff_multiplier_no_lock(
        self, user_id: int, guild_id: int, now: float,
    ) -> float:
        cursor = await self.conn.execute(
            """
            SELECT multiplier FROM business_buffs
            WHERE guild_id = ? AND user_id = ? AND ends_at > ?
            """,
            (guild_id, user_id, now),
        )
        mult = 1.0
        for r in await cursor.fetchall():
            mult *= float(r["multiplier"])
        return max(0.0, mult)

    async def _business_event_mult_no_lock(self, guild_id: int) -> float:
        cursor = await self.conn.execute(
            "SELECT event_type, ends_at FROM guild_events WHERE guild_id = ?",
            (guild_id,),
        )
        row = await cursor.fetchone()
        if row is None or float(row["ends_at"]) <= time.time():
            return 1.0
        return config.BUSINESS_SEASONAL_EVENTS.get(str(row["event_type"]), 1.0)

    async def _mega_income_mult_no_lock(self, user_id: int, guild_id: int) -> float:
        from utils.mega_projects import income_bonus_from_completed

        cursor = await self.conn.execute(
            """
            SELECT project_id FROM user_mega_projects
            WHERE user_id = ? AND guild_id = ? AND completed_at IS NOT NULL
            """,
            (user_id, guild_id),
        )
        completed = {str(r["project_id"]) for r in await cursor.fetchall()}
        bonus = min(config.MEGA_PROJECT_INCOME_BONUS_CAP, income_bonus_from_completed(completed))
        return 1.0 + bonus

    async def list_active_business_buffs(
        self, user_id: int, guild_id: int,
    ) -> list[dict[str, object]]:
        now = time.time()
        cursor = await self.conn.execute(
            """
            SELECT buff_id, buff_type, multiplier, ends_at, source_attack_id
            FROM business_buffs
            WHERE guild_id = ? AND user_id = ? AND ends_at > ?
            ORDER BY ends_at ASC
            """,
            (guild_id, user_id, now),
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def prune_expired_business_buffs(self, guild_id: int) -> None:
        async with self._write_lock:
            await self.conn.execute(
                "DELETE FROM business_buffs WHERE guild_id = ? AND ends_at <= ?",
                (guild_id, time.time()),
            )
            await self.conn.commit()

    async def perform_business_action(
        self,
        attacker_id: int,
        guild_id: int,
        action_id: str,
        *,
        target_id: int | None = None,
    ) -> dict[str, object]:
        """Execute a competitive action. Returns a result dict with an 'error' key on failure."""
        from utils.business_competition import (
            action_by_id,
            bonus_to_multiplier,
            effective_penalty,
            penalty_to_multiplier,
        )
        from utils.businesses import security_rating

        action = action_by_id(action_id)
        if action is None:
            return {"error": "invalid_action"}
        now = time.time()
        async with self._write_lock:
            # Attacker must own a business.
            cursor = await self.conn.execute(
                "SELECT * FROM user_businesses WHERE user_id = ? AND guild_id = ?",
                (attacker_id, guild_id),
            )
            attacker_biz = await cursor.fetchone()
            if attacker_biz is None:
                await self.conn.commit()
                return {"error": "no_business"}

            # Cooldown per action.
            cursor = await self.conn.execute(
                """
                SELECT last_used_at FROM business_action_cooldowns
                WHERE guild_id = ? AND user_id = ? AND action_type = ?
                """,
                (guild_id, attacker_id, action.action_id),
            )
            cd_row = await cursor.fetchone()
            if cd_row is not None:
                remaining = config.BUSINESS_ACTION_COOLDOWN_SECONDS - (now - float(cd_row["last_used_at"]))
                if remaining > 0:
                    await self.conn.commit()
                    return {"error": "cooldown", "retry_after": remaining}

            defender_biz = None
            if action.target == "opponent":
                if target_id is None or target_id == attacker_id:
                    await self.conn.commit()
                    return {"error": "invalid_target"}
                cursor = await self.conn.execute(
                    "SELECT * FROM user_businesses WHERE user_id = ? AND guild_id = ?",
                    (target_id, guild_id),
                )
                defender_biz = await cursor.fetchone()
                if defender_biz is None:
                    await self.conn.commit()
                    return {"error": "target_no_business"}

            # Charge the attacker.
            cursor = await self.conn.execute(
                "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                (attacker_id, guild_id),
            )
            wallet_row = await cursor.fetchone()
            if wallet_row is None or float(wallet_row["wallet"]) < action.cost:
                await self.conn.commit()
                return {"error": "insufficient_funds", "cost": action.cost}
            await self.conn.execute(
                "UPDATE users SET wallet = wallet - ? WHERE user_id = ? AND guild_id = ?",
                (action.cost, attacker_id, guild_id),
            )

            # Record cooldown.
            await self.conn.execute(
                """
                INSERT INTO business_action_cooldowns (guild_id, user_id, action_type, last_used_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id, action_type) DO UPDATE SET last_used_at = excluded.last_used_at
                """,
                (guild_id, attacker_id, action.action_id, now),
            )

            result: dict[str, object] = {"error": None, "action": action.action_id, "cost": action.cost}

            from utils.legacy_perks import action_duration_bonus_seconds

            attacker_legacy = await self._list_legacy_perks_no_lock(attacker_id, guild_id)
            duration_seconds = action.duration_seconds + action_duration_bonus_seconds(attacker_legacy)

            if action.kind == "buff":
                ends_at = now + duration_seconds
                await self.conn.execute(
                    """
                    INSERT INTO business_buffs (guild_id, user_id, buff_type, multiplier, ends_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (guild_id, attacker_id, action.action_id, bonus_to_multiplier(action.magnitude), ends_at),
                )
                result.update({"kind": "buff", "ends_at": ends_at, "magnitude": action.magnitude})
            elif action.kind == "attack":
                from utils.corporations import defense_bonus

                defender_crew = await self._crew_name_no_lock(int(target_id), guild_id)
                corp_defense = 0
                if defender_crew is not None:
                    corp_defense = defense_bonus(
                        await self._corporate_upgrade_level_no_lock(
                            guild_id, defender_crew, "defense",
                        ),
                    )
                acq_bonus = await self._security_acquisition_bonus_no_lock(int(target_id), guild_id)
                rating = security_rating(
                    security_level=int(defender_biz["security"]),
                    branch_security_level=int(defender_biz["branch_security"]),
                    tier=int(defender_biz["tier"]),
                    bonus=acq_bonus,
                ) + corp_defense
                penalty = effective_penalty(action.magnitude, rating)
                attack_duration = duration_seconds
                defender_acq = await self._list_completed_acquisitions_no_lock(int(target_id), guild_id)
                if "private_security" in defender_acq:
                    attack_duration *= 1.0 - float(
                        config.EMPIRE_ACQUISITIONS["private_security"]["attack_duration_reduction"],
                    )
                ends_at = now + attack_duration
                notify_expires = now + config.BUSINESS_DEFENSE_WINDOW_SECONDS
                cursor = await self.conn.execute(
                    """
                    INSERT INTO business_attacks (
                        guild_id, attacker_id, defender_id, action_type, penalty,
                        started_at, ends_at, defended, notify_expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (guild_id, attacker_id, int(target_id), action.action_id, penalty, now, ends_at, notify_expires),
                )
                attack_id = await self._last_insert_id_no_lock("business_attacks", "attack_id")
                await self.conn.execute(
                    """
                    INSERT INTO business_buffs (guild_id, user_id, buff_type, multiplier, ends_at, source_attack_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (guild_id, int(target_id), action.action_id, penalty_to_multiplier(penalty), ends_at, attack_id),
                )
                result.update({
                    "kind": "attack",
                    "defender_id": int(target_id),
                    "attack_id": attack_id,
                    "penalty": penalty,
                    "base_penalty": action.magnitude,
                    "mitigated": action.magnitude - penalty,
                    "security_rating": rating,
                    "ends_at": ends_at,
                    "notify_expires_at": notify_expires,
                })
            else:  # influence
                district_id = attacker_biz["district_id"]
                if not district_id:
                    # Refund: no district to expand into.
                    await self.conn.execute(
                        "UPDATE users SET wallet = wallet + ? WHERE user_id = ? AND guild_id = ?",
                        (action.cost, attacker_id, guild_id),
                    )
                    await self.conn.commit()
                    return {"error": "no_district"}
                cursor = await self.conn.execute(
                    """
                    SELECT influence FROM district_influence
                    WHERE guild_id = ? AND district_id = ? AND entity_type = 'user' AND entity_id = ?
                    """,
                    (guild_id, str(district_id), str(attacker_id)),
                )
                existing = await cursor.fetchone()
                cap = float(config.BUSINESS_DISTRICT_INFLUENCE_MAX)
                current = float(existing["influence"]) if existing is not None else 0.0
                territory_mult = await self.get_corporate_territory_mult(attacker_id, guild_id)
                influence_gain = (
                    config.BUSINESS_ACTION_MARKET_EXPANSION_INFLUENCE * territory_mult
                )
                new_value = min(cap, current + influence_gain)
                await self.conn.execute(
                    """
                    INSERT INTO district_influence (
                        guild_id, district_id, entity_type, entity_id, influence, updated_at
                    ) VALUES (?, ?, 'user', ?, ?, ?)
                    ON CONFLICT(guild_id, district_id, entity_type, entity_id) DO UPDATE SET
                        influence = excluded.influence, updated_at = excluded.updated_at
                    """,
                    (guild_id, str(district_id), str(attacker_id), new_value, now),
                )
                result.update({
                    "kind": "influence",
                    "district_id": str(district_id),
                    "influence": new_value,
                    "influence_gain": influence_gain,
                })
            await self.conn.commit()
        return result

    async def _last_insert_id_no_lock(self, table: str, pk_column: str) -> int:
        if self.is_postgres:
            cursor = await self.conn.execute(f"SELECT MAX({pk_column}) AS id FROM {table}")
        else:
            cursor = await self.conn.execute("SELECT last_insert_rowid() AS id")
        row = await cursor.fetchone()
        return int(row["id"]) if row is not None and row["id"] is not None else 0

    async def defend_business(self, user_id: int, guild_id: int) -> dict[str, object]:
        """Respond to the most recent active attack, halving its remaining penalty."""
        from utils.business_competition import defended_penalty, penalty_to_multiplier

        now = time.time()
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                SELECT * FROM business_attacks
                WHERE guild_id = ? AND defender_id = ? AND defended = 0
                  AND notify_expires_at > ? AND ends_at > ?
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (guild_id, user_id, now, now),
            )
            attack = await cursor.fetchone()
            if attack is None:
                await self.conn.commit()
                return {"error": "no_attack"}
            attack_id = int(attack["attack_id"])
            new_penalty = defended_penalty(float(attack["penalty"]))
            await self.conn.execute(
                "UPDATE business_attacks SET defended = 1, penalty = ? WHERE attack_id = ?",
                (new_penalty, attack_id),
            )
            await self.conn.execute(
                "UPDATE business_buffs SET multiplier = ? WHERE source_attack_id = ?",
                (penalty_to_multiplier(new_penalty), attack_id),
            )
            await self.conn.commit()
        return {
            "error": None,
            "action_type": str(attack["action_type"]),
            "attacker_id": int(attack["attacker_id"]),
            "new_penalty": new_penalty,
        }

    async def _crew_name_no_lock(self, user_id: int, guild_id: int) -> str | None:
        cursor = await self.conn.execute(
            "SELECT crew_name FROM crew_members WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        row = await cursor.fetchone()
        return str(row["crew_name"]) if row is not None else None

    async def _corporate_upgrade_level_no_lock(
        self, guild_id: int, crew_name: str, upgrade_type: str,
    ) -> int:
        cursor = await self.conn.execute(
            """
            SELECT level FROM crew_corporate_upgrades
            WHERE guild_id = ? AND crew_name = ? AND upgrade_type = ?
            """,
            (guild_id, crew_name, upgrade_type),
        )
        row = await cursor.fetchone()
        return int(row["level"]) if row is not None else 0

    async def _corporate_income_mult_no_lock(self, user_id: int, guild_id: int) -> float:
        from utils.corporations import income_bonus_multiplier

        crew = await self._crew_name_no_lock(user_id, guild_id)
        if crew is None:
            return 1.0
        level = await self._corporate_upgrade_level_no_lock(guild_id, crew, "income")
        return income_bonus_multiplier(level)

    async def get_corporate_defense_bonus(self, user_id: int, guild_id: int) -> int:
        from utils.corporations import defense_bonus

        crew = await self._crew_name_no_lock(user_id, guild_id)
        if crew is None:
            return 0
        level = await self._corporate_upgrade_level_no_lock(guild_id, crew, "defense")
        return defense_bonus(level)

    async def get_corporate_territory_mult(self, user_id: int, guild_id: int) -> float:
        from utils.corporations import territory_bonus_multiplier

        crew = await self._crew_name_no_lock(user_id, guild_id)
        if crew is None:
            return 1.0
        level = await self._corporate_upgrade_level_no_lock(guild_id, crew, "territory")
        return territory_bonus_multiplier(level)

    async def get_corporate_upgrades(
        self, guild_id: int, crew_name: str,
    ) -> dict[str, int]:
        cursor = await self.conn.execute(
            """
            SELECT upgrade_type, level FROM crew_corporate_upgrades
            WHERE guild_id = ? AND crew_name = ?
            """,
            (guild_id, crew_name),
        )
        return {str(r["upgrade_type"]): int(r["level"]) for r in await cursor.fetchall()}

    async def buy_corporate_upgrade(
        self, user_id: int, guild_id: int, upgrade_type: str,
    ) -> tuple[float, str | None]:
        """Spend crew treasury to raise a corporate upgrade. Returns (cost, error)."""
        from utils.corporations import upgrade_by_id, upgrade_cost

        if upgrade_by_id(upgrade_type) is None:
            return 0.0, "invalid_upgrade"
        async with self._write_lock:
            crew = await self._crew_name_no_lock(user_id, guild_id)
            if crew is None:
                await self.conn.commit()
                return 0.0, "not_in_crew"
            level = await self._corporate_upgrade_level_no_lock(guild_id, crew, upgrade_type)
            if level >= config.CORP_UPGRADE_MAX_LEVEL:
                await self.conn.commit()
                return 0.0, "max_level"
            cost = upgrade_cost(level)
            cursor = await self.conn.execute(
                "SELECT treasury FROM crew_stats WHERE guild_id = ? AND crew_name = ?",
                (guild_id, crew),
            )
            stats = await cursor.fetchone()
            if stats is None or float(stats["treasury"]) < cost:
                await self.conn.commit()
                return cost, "insufficient_treasury"
            await self.conn.execute(
                "UPDATE crew_stats SET treasury = treasury - ? WHERE guild_id = ? AND crew_name = ?",
                (cost, guild_id, crew),
            )
            await self.conn.execute(
                """
                INSERT INTO crew_corporate_upgrades (guild_id, crew_name, upgrade_type, level)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(guild_id, crew_name, upgrade_type) DO UPDATE SET level = crew_corporate_upgrades.level + 1
                """,
                (guild_id, crew, upgrade_type),
            )
            await self.conn.commit()
        return cost, None

    async def list_corporate_projects(
        self, guild_id: int, crew_name: str,
    ) -> dict[str, dict[str, object]]:
        cursor = await self.conn.execute(
            """
            SELECT project_id, funded_amount, completed_at FROM crew_corporate_projects
            WHERE guild_id = ? AND crew_name = ?
            """,
            (guild_id, crew_name),
        )
        return {
            str(r["project_id"]): {
                "funded_amount": float(r["funded_amount"]),
                "completed_at": r["completed_at"],
            }
            for r in await cursor.fetchall()
        }

    async def contribute_to_corporate_project(
        self, user_id: int, guild_id: int, project_id: str, amount: float,
    ) -> dict[str, object]:
        """Fund a corporate project from a member's wallet. Returns a result dict."""
        from utils.corporations import project_by_id

        project = project_by_id(project_id)
        if project is None:
            return {"error": "invalid_project"}
        if amount <= 0:
            return {"error": "invalid_amount"}
        async with self._write_lock:
            crew = await self._crew_name_no_lock(user_id, guild_id)
            if crew is None:
                await self.conn.commit()
                return {"error": "not_in_crew"}
            cursor = await self.conn.execute(
                """
                SELECT funded_amount, completed_at FROM crew_corporate_projects
                WHERE guild_id = ? AND crew_name = ? AND project_id = ?
                """,
                (guild_id, crew, project_id),
            )
            existing = await cursor.fetchone()
            if existing is not None and existing["completed_at"] is not None:
                await self.conn.commit()
                return {"error": "already_complete"}
            funded = float(existing["funded_amount"]) if existing is not None else 0.0
            remaining = max(0.0, project.target_amount - funded)
            contribution = min(amount, remaining)
            if contribution <= 0:
                await self.conn.commit()
                return {"error": "already_complete"}
            cursor = await self.conn.execute(
                "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            wallet_row = await cursor.fetchone()
            if wallet_row is None or float(wallet_row["wallet"]) < contribution:
                await self.conn.commit()
                return {"error": "insufficient_funds", "needed": contribution}
            await self.conn.execute(
                "UPDATE users SET wallet = wallet - ? WHERE user_id = ? AND guild_id = ?",
                (contribution, user_id, guild_id),
            )
            new_funded = funded + contribution
            completed = new_funded >= project.target_amount
            completed_at = time.time() if completed else None
            await self.conn.execute(
                """
                INSERT INTO crew_corporate_projects (guild_id, crew_name, project_id, funded_amount, completed_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, crew_name, project_id) DO UPDATE SET
                    funded_amount = excluded.funded_amount,
                    completed_at = excluded.completed_at
                """,
                (guild_id, crew, project_id, new_funded, completed_at),
            )
            reward = 0.0
            if completed:
                reward = project.reward_treasury
                await self.conn.execute(
                    """
                    INSERT INTO crew_stats (guild_id, crew_name, treasury, level, xp)
                    VALUES (?, ?, ?, 1, 0)
                    ON CONFLICT(guild_id, crew_name) DO UPDATE SET treasury = crew_stats.treasury + excluded.treasury
                    """,
                    (guild_id, crew, reward),
                )
            await self.conn.commit()
        return {
            "error": None,
            "contribution": contribution,
            "funded": new_funded,
            "target": project.target_amount,
            "completed": completed,
            "reward": reward,
            "crew": crew,
        }

    async def record_corporate_war_tick(self, guild_id: int) -> dict[str, object] | None:
        """Weekly war scoring: snapshot scores, award the leader, advance the week.

        Returns a result dict when a war week resolved, else None (not due yet).
        """
        from utils.territories import TERRITORY_MAP

        now = time.time()
        async with self._write_lock:
            cursor = await self.conn.execute(
                "SELECT last_tick_at, current_week FROM corporate_war_state WHERE guild_id = ?",
                (guild_id,),
            )
            state = await cursor.fetchone()
            if state is None:
                await self.conn.execute(
                    "INSERT INTO corporate_war_state (guild_id, last_tick_at, current_week) VALUES (?, ?, 1)",
                    (guild_id, now),
                )
                await self.conn.commit()
                return None
            last_tick = float(state["last_tick_at"])
            week = int(state["current_week"]) or 1
            if now - last_tick < config.CORP_WAR_TICK_SECONDS:
                await self.conn.commit()
                return None
            # Score = treasury + territory holdings.
            cursor = await self.conn.execute(
                "SELECT crew_name, treasury FROM crew_stats WHERE guild_id = ?",
                (guild_id,),
            )
            crews = [(str(r["crew_name"]), float(r["treasury"])) for r in await cursor.fetchall()]
            scores: list[tuple[str, float]] = []
            for crew_name, treasury in crews:
                cursor = await self.conn.execute(
                    "SELECT COUNT(*) AS c FROM territory_control WHERE guild_id = ? AND owner_crew_name = ?",
                    (guild_id, crew_name),
                )
                trow = await cursor.fetchone()
                territory_count = int(trow["c"]) if trow is not None else 0
                _ = TERRITORY_MAP  # territory scoring uses the live map count
                score = treasury + territory_count * config.CORP_WAR_TERRITORY_SCORE
                scores.append((crew_name, score))
                await self.conn.execute(
                    """
                    INSERT INTO corporate_war_scores (guild_id, week_id, crew_name, total_score, recorded_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(guild_id, week_id, crew_name) DO UPDATE SET
                        total_score = excluded.total_score, recorded_at = excluded.recorded_at
                    """,
                    (guild_id, week, crew_name, score, now),
                )
            winner = max(scores, key=lambda s: s[1], default=None)
            if winner is not None and winner[1] > 0:
                await self.conn.execute(
                    "UPDATE crew_stats SET treasury = treasury + ? WHERE guild_id = ? AND crew_name = ?",
                    (config.CORP_WAR_WINNER_TREASURY_BONUS, guild_id, winner[0]),
                )
            await self.conn.execute(
                "UPDATE corporate_war_state SET last_tick_at = ?, current_week = ? WHERE guild_id = ?",
                (now, week + 1, guild_id),
            )
            await self.conn.commit()
        return {
            "week": week,
            "winner": winner[0] if winner else None,
            "winner_score": winner[1] if winner else 0.0,
            "reward": config.CORP_WAR_WINNER_TREASURY_BONUS if winner else 0.0,
        }

    async def get_corporate_war_standings(
        self, guild_id: int, *, limit: int = 10,
    ) -> list[tuple[str, float]]:
        """Live standings for the current week (treasury + territory)."""
        from utils.territories import TERRITORY_MAP

        _ = TERRITORY_MAP
        cursor = await self.conn.execute(
            "SELECT crew_name, treasury FROM crew_stats WHERE guild_id = ?",
            (guild_id,),
        )
        rows = [(str(r["crew_name"]), float(r["treasury"])) for r in await cursor.fetchall()]
        standings: list[tuple[str, float]] = []
        for crew_name, treasury in rows:
            cursor = await self.conn.execute(
                "SELECT COUNT(*) AS c FROM territory_control WHERE guild_id = ? AND owner_crew_name = ?",
                (guild_id, crew_name),
            )
            trow = await cursor.fetchone()
            territory_count = int(trow["c"]) if trow is not None else 0
            standings.append(
                (crew_name, treasury + territory_count * config.CORP_WAR_TERRITORY_SCORE),
            )
        standings.sort(key=lambda s: s[1], reverse=True)
        return standings[:limit]

    async def get_stock_market_event(self, guild_id: int) -> tuple[str | None, float]:
        cursor = await self.conn.execute(
            "SELECT event_type, multiplier, ends_at FROM stock_market_event WHERE guild_id = ?",
            (guild_id,),
        )
        row = await cursor.fetchone()
        if row is None or row["event_type"] is None or float(row["ends_at"]) <= time.time():
            return None, 1.0
        return str(row["event_type"]), float(row["multiplier"])

    async def set_stock_market_event(
        self, guild_id: int, event_type: str | None, multiplier: float, ends_at: float,
    ) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT INTO stock_market_event (guild_id, event_type, multiplier, ends_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    event_type = excluded.event_type,
                    multiplier = excluded.multiplier,
                    ends_at = excluded.ends_at
                """,
                (guild_id, event_type, multiplier, ends_at),
            )
            await self.conn.commit()

    async def get_share_price(self, guild_id: int, crew_name: str) -> float:
        from utils.stock_market import share_price

        cursor = await self.conn.execute(
            "SELECT treasury FROM crew_stats WHERE guild_id = ? AND crew_name = ?",
            (guild_id, crew_name),
        )
        stats = await cursor.fetchone()
        treasury = float(stats["treasury"]) if stats is not None else 0.0
        members = await self.count_crew_members(guild_id, crew_name)
        _, mult = await self.get_stock_market_event(guild_id)
        return share_price(treasury, members, event_mult=mult)

    async def list_stock_market(self, guild_id: int) -> list[dict[str, object]]:
        from utils.stock_market import share_price

        _, mult = await self.get_stock_market_event(guild_id)
        cursor = await self.conn.execute(
            "SELECT crew_name, treasury FROM crew_stats WHERE guild_id = ?",
            (guild_id,),
        )
        rows = await cursor.fetchall()
        out: list[dict[str, object]] = []
        for r in rows:
            crew = str(r["crew_name"])
            members = await self.count_crew_members(guild_id, crew)
            out.append({
                "crew_name": crew,
                "price": share_price(float(r["treasury"]), members, event_mult=mult),
                "members": members,
            })
        out.sort(key=lambda d: float(d["price"]), reverse=True)
        return out

    async def get_stock_holdings(
        self, user_id: int, guild_id: int,
    ) -> list[dict[str, object]]:
        cursor = await self.conn.execute(
            """
            SELECT crew_name, shares FROM stock_holdings
            WHERE guild_id = ? AND user_id = ? AND shares > 0
            ORDER BY shares DESC
            """,
            (guild_id, user_id),
        )
        out: list[dict[str, object]] = []
        for r in await cursor.fetchall():
            crew = str(r["crew_name"])
            price = await self.get_share_price(guild_id, crew)
            shares = int(r["shares"])
            out.append({
                "crew_name": crew,
                "shares": shares,
                "price": price,
                "value": price * shares,
            })
        return out

    async def buy_shares(
        self, user_id: int, guild_id: int, crew_name: str, shares: int,
    ) -> tuple[float, str | None]:
        """Buy shares at the current price. Returns (total_cost, error)."""
        from utils.stock_market import buy_total

        if shares <= 0 or shares > config.STOCK_MAX_SHARES_PER_TXN:
            return 0.0, "invalid_amount"
        async with self._write_lock:
            cursor = await self.conn.execute(
                "SELECT 1 FROM crew_stats WHERE guild_id = ? AND crew_name = ?",
                (guild_id, crew_name),
            )
            if await cursor.fetchone() is None:
                await self.conn.commit()
                return 0.0, "unknown_corp"
            price = await self.get_share_price(guild_id, crew_name)
            total = buy_total(price, shares)
            cursor = await self.conn.execute(
                "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            wallet_row = await cursor.fetchone()
            if wallet_row is None or float(wallet_row["wallet"]) < total:
                await self.conn.commit()
                return total, "insufficient_funds"
            await self.conn.execute(
                "UPDATE users SET wallet = wallet - ? WHERE user_id = ? AND guild_id = ?",
                (total, user_id, guild_id),
            )
            await self.conn.execute(
                """
                INSERT INTO stock_holdings (guild_id, user_id, crew_name, shares)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id, crew_name) DO UPDATE SET shares = stock_holdings.shares + excluded.shares
                """,
                (guild_id, user_id, crew_name, shares),
            )
            await self.conn.execute(
                """
                INSERT INTO stock_transactions (guild_id, user_id, crew_name, shares, price, txn_type, created_at)
                VALUES (?, ?, ?, ?, ?, 'buy', ?)
                """,
                (guild_id, user_id, crew_name, shares, price, time.time()),
            )
            await self.conn.commit()
        return total, None

    async def sell_shares(
        self, user_id: int, guild_id: int, crew_name: str, shares: int,
    ) -> tuple[float, str | None]:
        """Sell shares at the current price (minus tax). Returns (proceeds, error)."""
        from utils.stock_market import sell_proceeds

        if shares <= 0 or shares > config.STOCK_MAX_SHARES_PER_TXN:
            return 0.0, "invalid_amount"
        async with self._write_lock:
            cursor = await self.conn.execute(
                "SELECT shares FROM stock_holdings WHERE guild_id = ? AND user_id = ? AND crew_name = ?",
                (guild_id, user_id, crew_name),
            )
            holding = await cursor.fetchone()
            if holding is None or int(holding["shares"]) < shares:
                await self.conn.commit()
                return 0.0, "insufficient_shares"
            price = await self.get_share_price(guild_id, crew_name)
            proceeds = sell_proceeds(price, shares)
            await self.conn.execute(
                "UPDATE stock_holdings SET shares = shares - ? WHERE guild_id = ? AND user_id = ? AND crew_name = ?",
                (shares, guild_id, user_id, crew_name),
            )
            await self.conn.execute(
                """
                UPDATE users SET wallet = wallet + ?, total_earned = total_earned + ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (proceeds, proceeds, user_id, guild_id),
            )
            await self.conn.execute(
                """
                INSERT INTO stock_transactions (guild_id, user_id, crew_name, shares, price, txn_type, created_at)
                VALUES (?, ?, ?, ?, ?, 'sell', ?)
                """,
                (guild_id, user_id, crew_name, shares, price, time.time()),
            )
            await self.conn.commit()
        return proceeds, None

    async def process_stock_dividends(self, guild_id: int) -> float:
        """Pay hourly dividends from each corporation treasury to its shareholders."""
        from utils.stock_market import dividend_amount

        now = time.time()
        total_paid = 0.0
        async with self._write_lock:
            cursor = await self.conn.execute(
                "SELECT last_dividend_at FROM stock_market_event WHERE guild_id = ?",
                (guild_id,),
            )
            state = await cursor.fetchone()
            last_at = float(state["last_dividend_at"]) if state is not None else 0.0
            if state is None:
                await self.conn.execute(
                    "INSERT INTO stock_market_event (guild_id, last_dividend_at) VALUES (?, ?)",
                    (guild_id, now),
                )
                await self.conn.commit()
                return 0.0
            if now - last_at < config.STOCK_DIVIDEND_TICK_SECONDS:
                await self.conn.commit()
                return 0.0
            cursor = await self.conn.execute(
                "SELECT DISTINCT crew_name FROM stock_holdings WHERE guild_id = ? AND shares > 0",
                (guild_id,),
            )
            crews = [str(r["crew_name"]) for r in await cursor.fetchall()]
            for crew in crews:
                cursor = await self.conn.execute(
                    "SELECT treasury FROM crew_stats WHERE guild_id = ? AND crew_name = ?",
                    (guild_id, crew),
                )
                stats = await cursor.fetchone()
                if stats is None:
                    continue
                treasury = float(stats["treasury"])
                price = await self.get_share_price(guild_id, crew)
                cursor = await self.conn.execute(
                    "SELECT user_id, shares FROM stock_holdings WHERE guild_id = ? AND crew_name = ? AND shares > 0",
                    (guild_id, crew),
                )
                holders = [(int(r["user_id"]), int(r["shares"])) for r in await cursor.fetchall()]
                payouts = [(uid, dividend_amount(price, sh)) for uid, sh in holders]
                needed = sum(p for _, p in payouts)
                if needed <= 0:
                    continue
                # Scale down if the treasury cannot cover all dividends.
                scale = 1.0 if treasury >= needed else (treasury / needed if needed > 0 else 0.0)
                actually_paid = 0.0
                for uid, payout in payouts:
                    amount = payout * scale
                    if amount <= 0:
                        continue
                    await self.conn.execute(
                        "UPDATE users SET wallet = wallet + ?, total_earned = total_earned + ? WHERE user_id = ? AND guild_id = ?",
                        (amount, amount, uid, guild_id),
                    )
                    actually_paid += amount
                if actually_paid > 0:
                    await self.conn.execute(
                        "UPDATE crew_stats SET treasury = treasury - ? WHERE guild_id = ? AND crew_name = ?",
                        (min(actually_paid, treasury), guild_id, crew),
                    )
                total_paid += actually_paid
            await self.conn.execute(
                "UPDATE stock_market_event SET last_dividend_at = ? WHERE guild_id = ?",
                (now, guild_id),
            )
            await self.conn.commit()
        return total_paid

    async def maybe_roll_stock_event(self, guild_id: int) -> str | None:
        """Randomly start a market event if none is active. Returns the new event type."""
        import random

        current, _ = await self.get_stock_market_event(guild_id)
        if current is not None:
            return None
        if random.random() >= config.STOCK_EVENT_CHANCE_PER_TICK:
            return None
        event_type = random.choice(list(config.STOCK_MARKET_EVENTS.keys()))
        await self.set_stock_market_event(
            guild_id,
            event_type,
            config.STOCK_MARKET_EVENTS[event_type],
            time.time() + config.STOCK_EVENT_DURATION_SECONDS,
        )
        return event_type

    async def relocate_business(
        self, user_id: int, guild_id: int, district_id: str,
    ) -> tuple[float, str | None]:
        """Move a business to a district for a tier-scaled fee. Returns (cost, error)."""
        from utils.districts import district_by_id, relocate_cost

        defn = district_by_id(district_id)
        if defn is None:
            return 0.0, "invalid_district"
        async with self._write_lock:
            row = await self._settle_business_income_no_lock(user_id, guild_id)
            if row is None:
                await self.conn.commit()
                return 0.0, "no_business"
            if str(row["district_id"] or "") == defn.district_id:
                await self.conn.commit()
                return 0.0, "already_here"
            now = time.time()
            last_relocate = float(row["last_relocate_at"] or 0)
            if now - last_relocate < config.BUSINESS_DISTRICT_RELOCATE_COOLDOWN_SECONDS:
                await self.conn.commit()
                return 0.0, "cooldown"
            cost = relocate_cost(int(row["tier"]))
            cursor = await self.conn.execute(
                "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            wallet_row = await cursor.fetchone()
            if wallet_row is None or float(wallet_row["wallet"]) < cost:
                await self.conn.commit()
                return cost, "insufficient_funds"
            await self.conn.execute(
                "UPDATE users SET wallet = wallet - ? WHERE user_id = ? AND guild_id = ?",
                (cost, user_id, guild_id),
            )
            await self.conn.execute(
                """
                UPDATE user_businesses SET district_id = ?, last_relocate_at = ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (defn.district_id, now, user_id, guild_id),
            )
            await self.conn.commit()
        return cost, None

    async def add_district_influence(
        self,
        guild_id: int,
        district_id: str,
        entity_type: str,
        entity_id: str,
        points: float,
    ) -> float:
        """Add influence (capped at the configured max) and return the new value."""
        cap = float(config.BUSINESS_DISTRICT_INFLUENCE_MAX)
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                SELECT influence FROM district_influence
                WHERE guild_id = ? AND district_id = ? AND entity_type = ? AND entity_id = ?
                """,
                (guild_id, district_id, entity_type, entity_id),
            )
            existing = await cursor.fetchone()
            current = float(existing["influence"]) if existing is not None else 0.0
            new_value = min(cap, max(0.0, current + points))
            await self.conn.execute(
                """
                INSERT INTO district_influence (
                    guild_id, district_id, entity_type, entity_id, influence, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, district_id, entity_type, entity_id) DO UPDATE SET
                    influence = excluded.influence,
                    updated_at = excluded.updated_at
                """,
                (guild_id, district_id, entity_type, entity_id, new_value, time.time()),
            )
            await self.conn.commit()
        return new_value

    async def expand_district_influence(
        self, user_id: int, guild_id: int, district_id: str, points: int,
    ) -> tuple[float, float, str | None]:
        """Spend nuggets to gain influence. Returns (cost, new_influence, error)."""
        from utils.districts import district_by_id

        if district_by_id(district_id) is None:
            return 0.0, 0.0, "invalid_district"
        if points <= 0:
            return 0.0, 0.0, "invalid_amount"
        cost = points * config.BUSINESS_DISTRICT_INFLUENCE_COST_PER_POINT
        ok = await self.debit_wallet(user_id, guild_id, cost)
        if not ok:
            return cost, 0.0, "insufficient_funds"
        territory_mult = await self.get_corporate_territory_mult(user_id, guild_id)
        new_value = await self.add_district_influence(
            guild_id, district_id, "user", str(user_id), float(points) * territory_mult,
        )
        return cost, new_value, None

    async def get_user_district_influence(
        self, user_id: int, guild_id: int, district_id: str,
    ) -> float:
        cursor = await self.conn.execute(
            """
            SELECT influence FROM district_influence
            WHERE guild_id = ? AND district_id = ? AND entity_type = 'user' AND entity_id = ?
            """,
            (guild_id, district_id, str(user_id)),
        )
        row = await cursor.fetchone()
        return float(row["influence"]) if row is not None else 0.0

    async def list_district_influence(
        self, guild_id: int, district_id: str, *, limit: int = 5,
    ) -> list[tuple[str, str, float]]:
        cursor = await self.conn.execute(
            """
            SELECT entity_type, entity_id, influence FROM district_influence
            WHERE guild_id = ? AND district_id = ? AND influence > 0
            ORDER BY influence DESC
            LIMIT ?
            """,
            (guild_id, district_id, limit),
        )
        return [
            (str(r["entity_type"]), str(r["entity_id"]), float(r["influence"]))
            for r in await cursor.fetchall()
        ]

    async def buy_territory_guards(
        self,
        user_id: int,
        guild_id: int,
        territory_id: str,
        count: int,
        *,
        pay_from: str = "wallet",
    ) -> str | None:
        from utils.territories import guard_cost_per_unit, territory_by_id

        defn = territory_by_id(territory_id)
        if defn is None:
            return "invalid_territory"
        qty = max(1, min(int(count), 20))
        crew_name = await self.get_crew_membership(user_id, guild_id)
        if crew_name is None:
            return "not_in_crew"
        row = await self.get_territory_row(guild_id, defn.territory_id)
        if row is None:
            return "invalid_territory"
        if not row["owner_crew_name"] or str(row["owner_crew_name"]) != crew_name:
            return "not_owner"
        current_guards = int(row["guards"])
        if current_guards + qty > defn.max_guards:
            return "guard_cap"
        unit_cost = guard_cost_per_unit(defn)
        total_cost = unit_cost * qty
        use_treasury = pay_from == "treasury"
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            if use_treasury:
                stats = await self.get_crew_stats(guild_id, crew_name)
                if stats is None or float(stats["treasury"]) < total_cost:
                    await self.conn.commit()
                    return "insufficient_treasury"
                await self.conn.execute(
                    """
                    UPDATE crew_stats SET treasury = treasury - ?
                    WHERE guild_id = ? AND crew_name = ?
                    """,
                    (total_cost, guild_id, crew_name),
                )
            else:
                wallet_cursor = await self.conn.execute(
                    "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                    (user_id, guild_id),
                )
                wallet_row = await wallet_cursor.fetchone()
                if wallet_row is None or float(wallet_row["wallet"]) < total_cost:
                    await self.conn.commit()
                    return "insufficient_funds"
                await self.conn.execute(
                    "UPDATE users SET wallet = wallet - ? WHERE user_id = ? AND guild_id = ?",
                    (total_cost, user_id, guild_id),
                )
            await self.conn.execute(
                """
                UPDATE territory_control SET guards = guards + ?
                WHERE guild_id = ? AND territory_id = ?
                """,
                (qty, guild_id, defn.territory_id),
            )
            await self.conn.commit()
        return None

    async def start_territory_siege(
        self, user_id: int, guild_id: int, territory_id: str,
    ) -> str | None:
        import config
        from utils.territories import territory_by_id

        defn = territory_by_id(territory_id)
        if defn is None:
            return "invalid_territory"
        attacker_crew = await self.get_crew_membership(user_id, guild_id)
        if attacker_crew is None:
            return "not_in_crew"
        row = await self.get_territory_row(guild_id, defn.territory_id)
        if row is None:
            return "invalid_territory"
        owner = row["owner_crew_name"]
        if not owner:
            return await self._claim_neutral_territory(
                user_id, guild_id, defn.territory_id, attacker_crew,
            )
        members = await self.count_crew_members(guild_id, attacker_crew)
        if members < config.TERRITORY_MIN_CREW_MEMBERS_TO_ATTACK:
            return "crew_too_small"
        if str(owner) == attacker_crew:
            return "own_territory"
        now = time.time()
        if row["siege_ends_at"] is not None and float(row["siege_ends_at"]) > now:
            return "already_under_siege"
        last_siege = row["last_siege_at"]
        if (
            last_siege is not None
            and now - float(last_siege) < config.TERRITORY_SIEGE_COOLDOWN_SECONDS
        ):
            return "siege_cooldown"
        held = await self.count_crew_territories(guild_id, attacker_crew)
        if held >= config.TERRITORY_MAX_HELD_PER_CREW:
            return "max_territories"
        async with self._write_lock:
            await self.conn.execute(
                """
                UPDATE territory_control SET
                    siege_attacker_crew = ?,
                    siege_attacker_user_id = ?,
                    siege_started_at = ?,
                    siege_ends_at = ?,
                    last_siege_at = ?
                WHERE guild_id = ? AND territory_id = ?
                """,
                (
                    attacker_crew,
                    user_id,
                    now,
                    now + config.TERRITORY_SIEGE_DURATION_SECONDS,
                    now,
                    guild_id,
                    defn.territory_id,
                ),
            )
            await self.conn.commit()
        return None

    async def _claim_neutral_territory(
        self, user_id: int, guild_id: int, territory_id: str, crew_name: str,
    ) -> str | None:
        import config

        held = await self.count_crew_territories(guild_id, crew_name)
        if held >= config.TERRITORY_MAX_HELD_PER_CREW:
            return "max_territories"
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT INTO crew_stats (guild_id, crew_name, treasury, level, xp)
                VALUES (?, ?, 0, 1, 0)
                ON CONFLICT(guild_id, crew_name) DO NOTHING
                """,
                (guild_id, crew_name),
            )
            await self.conn.execute(
                """
                UPDATE territory_control SET
                    owner_crew_name = ?,
                    siege_attacker_crew = NULL,
                    siege_attacker_user_id = NULL,
                    siege_started_at = NULL,
                    siege_ends_at = NULL,
                    guards = 0
                WHERE guild_id = ? AND territory_id = ?
                """,
                (crew_name, guild_id, territory_id),
            )
            await self._ensure_progress_no_lock(user_id, guild_id)
            await self.conn.execute(
                """
                UPDATE user_progress SET territories_claimed = territories_claimed + 1
                WHERE user_id = ? AND guild_id = ?
                """,
                (user_id, guild_id),
            )
            await self.conn.commit()
        return "claimed_neutral"

    async def abandon_territory(
        self, user_id: int, guild_id: int, territory_id: str,
    ) -> str | None:
        from utils.territories import territory_by_id

        defn = territory_by_id(territory_id)
        if defn is None:
            return "invalid_territory"
        crew_name = await self.get_crew_membership(user_id, guild_id)
        if crew_name is None:
            return "not_in_crew"
        row = await self.get_territory_row(guild_id, defn.territory_id)
        if row is None or not row["owner_crew_name"] or str(row["owner_crew_name"]) != crew_name:
            return "not_owner"
        async with self._write_lock:
            await self.conn.execute(
                """
                UPDATE territory_control SET
                    owner_crew_name = NULL,
                    guards = 0,
                    siege_attacker_crew = NULL,
                    siege_started_at = NULL,
                    siege_ends_at = NULL
                WHERE guild_id = ? AND territory_id = ?
                """,
                (guild_id, defn.territory_id),
            )
            await self.conn.commit()
        return None

    async def resolve_territory_sieges(self, guild_id: int) -> list[dict[str, object]]:
        """Resolve expired sieges; returns summary dicts for announcements."""
        import random

        from utils.territories import TERRITORY_MAP, siege_success_chance

        now = time.time()
        results: list[dict[str, object]] = []
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                SELECT * FROM territory_control
                WHERE guild_id = ?
                  AND siege_ends_at IS NOT NULL
                  AND siege_ends_at <= ?
                """,
                (guild_id, now),
            )
            rows = await cursor.fetchall()
            for row in rows:
                territory_id = str(row["territory_id"])
                defn = TERRITORY_MAP.get(territory_id)
                if defn is None:
                    continue
                attacker = row["siege_attacker_crew"]
                if attacker is None:
                    await self.conn.execute(
                        """
                        UPDATE territory_control SET
                            siege_attacker_crew = NULL,
                            siege_started_at = NULL,
                            siege_ends_at = NULL
                        WHERE guild_id = ? AND territory_id = ?
                        """,
                        (guild_id, territory_id),
                    )
                    continue
                owner = row["owner_crew_name"]
                guards = int(row["guards"])
                attacker_user = row["siege_attacker_user_id"]
                channel_id = row["siege_channel_id"]
                message_id = row["siege_message_id"]
                members = await self.count_crew_members(guild_id, str(attacker))
                chance = siege_success_chance(members, guards, defn)
                won = random.random() < chance
                if won:
                    await self.conn.execute(
                        """
                        UPDATE territory_control SET
                            owner_crew_name = ?,
                            guards = 0,
                            siege_attacker_crew = NULL,
                            siege_attacker_user_id = NULL,
                            siege_started_at = NULL,
                            siege_ends_at = NULL,
                            siege_channel_id = NULL,
                            siege_message_id = NULL
                        WHERE guild_id = ? AND territory_id = ?
                        """,
                        (attacker, guild_id, territory_id),
                    )
                    if attacker_user is not None:
                        await self._ensure_progress_no_lock(int(attacker_user), guild_id)
                        await self.conn.execute(
                            """
                            UPDATE user_progress SET
                                sieges_won = sieges_won + 1,
                                territories_claimed = territories_claimed + 1
                            WHERE user_id = ? AND guild_id = ?
                            """,
                            (int(attacker_user), guild_id),
                        )
                    results.append({
                        "territory_id": territory_id,
                        "name": defn.name,
                        "won": True,
                        "attacker": str(attacker),
                        "defender": str(owner) if owner else None,
                        "chance": chance,
                        "attacker_user_id": int(attacker_user) if attacker_user else None,
                        "channel_id": int(channel_id) if channel_id else None,
                        "message_id": int(message_id) if message_id else None,
                    })
                else:
                    await self.conn.execute(
                        """
                        UPDATE territory_control SET
                            siege_attacker_crew = NULL,
                            siege_attacker_user_id = NULL,
                            siege_started_at = NULL,
                            siege_ends_at = NULL,
                            siege_channel_id = NULL,
                            siege_message_id = NULL
                        WHERE guild_id = ? AND territory_id = ?
                        """,
                        (guild_id, territory_id),
                    )
                    results.append({
                        "territory_id": territory_id,
                        "name": defn.name,
                        "won": False,
                        "attacker": str(attacker),
                        "defender": str(owner) if owner else None,
                        "chance": chance,
                        "attacker_user_id": int(attacker_user) if attacker_user else None,
                        "channel_id": int(channel_id) if channel_id else None,
                        "message_id": int(message_id) if message_id else None,
                    })
            await self.conn.commit()
        return results

    async def save_loadout_preset(
        self,
        user_id: int,
        guild_id: int,
        slot: int,
        name: str,
        weapon_id: str | None,
        off_hand_id: str | None,
        armor_id: str | None,
    ) -> None:
        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            await self.conn.execute(
                """
                INSERT INTO loadout_presets (
                    guild_id, user_id, slot, name, weapon_id, off_hand_id, armor_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id, slot) DO UPDATE SET
                    name = excluded.name,
                    weapon_id = excluded.weapon_id,
                    off_hand_id = excluded.off_hand_id,
                    armor_id = excluded.armor_id
                """,
                (guild_id, user_id, slot, name[:32], weapon_id, off_hand_id, armor_id),
            )
            await self.conn.commit()

    async def get_loadout_preset(
        self, user_id: int, guild_id: int, slot: int,
    ) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(
            """
            SELECT * FROM loadout_presets
            WHERE guild_id = ? AND user_id = ? AND slot = ?
            """,
            (guild_id, user_id, slot),
        )
        return await cursor.fetchone()

    async def list_loadout_presets(self, user_id: int, guild_id: int) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT * FROM loadout_presets
            WHERE guild_id = ? AND user_id = ?
            ORDER BY slot ASC
            """,
            (guild_id, user_id),
        )
        return list(await cursor.fetchall())

    async def sell_all_battle_worn(
        self, user_id: int, guild_id: int,
    ) -> tuple[int, float]:
        """Sell every boss_weak_* item. Returns (items_sold, nuggets gained)."""
        from items import get_item, sell_refund_for_item

        async with self._write_lock:
            await self._ensure_user_no_lock(user_id, guild_id)
            cursor = await self.conn.execute(
                """
                SELECT item_id, quantity FROM inventory
                WHERE guild_id = ? AND user_id = ? AND item_id LIKE 'boss_weak_%'
                """,
                (guild_id, user_id),
            )
            rows = await cursor.fetchall()
            total_sold = 0
            total_payout = 0.0
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                for row in rows:
                    item_id = str(row["item_id"])
                    qty = int(row["quantity"])
                    item = get_item(item_id)
                    if item is None:
                        continue
                    refund = sell_refund_for_item(item)
                    if refund is None:
                        continue
                    total_sold += qty
                    total_payout += refund * qty
                    await self.conn.execute(
                        """
                        DELETE FROM inventory
                        WHERE guild_id = ? AND user_id = ? AND item_id = ?
                        """,
                        (guild_id, user_id, item_id),
                    )
                if total_payout > 0:
                    await self.conn.execute(
                        """
                        UPDATE users SET wallet = wallet + ?, total_earned = total_earned + ?
                        WHERE user_id = ? AND guild_id = ?
                        """,
                        (total_payout, total_payout, user_id, guild_id),
                    )
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
        return total_sold, total_payout

    async def set_pending_consumable(
        self, user_id: int, guild_id: int, consumable_id: str, *, duration_seconds: float = 300.0,
    ) -> None:
        expires = time.time() + duration_seconds
        async with self._write_lock:
            await self._ensure_character_no_lock(user_id, guild_id)
            await self.conn.execute(
                """
                UPDATE user_character
                SET pending_consumable = ?, pending_consumable_expires = ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (consumable_id, expires, user_id, guild_id),
            )
            await self.conn.commit()

    async def take_pending_consumable(self, user_id: int, guild_id: int, expected: str) -> bool:
        """Consume pending buff if it matches expected id and is not expired."""
        async with self._write_lock:
            row = await self._refresh_character_energy_unlocked(user_id, guild_id)
            try:
                pending = row["pending_consumable"]
                expires = float(row["pending_consumable_expires"] or 0)
            except (KeyError, TypeError):
                return False
            if not pending or str(pending) != expected:
                return False
            if expires < time.time():
                await self.conn.execute(
                    """
                    UPDATE user_character
                    SET pending_consumable = NULL, pending_consumable_expires = NULL
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (user_id, guild_id),
                )
                await self.conn.commit()
                return False
            await self.conn.execute(
                """
                UPDATE user_character
                SET pending_consumable = NULL, pending_consumable_expires = NULL
                WHERE user_id = ? AND guild_id = ?
                """,
                (user_id, guild_id),
            )
            await self.conn.commit()
            return True

    async def add_energy(self, user_id: int, guild_id: int, amount: int) -> int:
        async with self._write_lock:
            row = await self._refresh_character_energy_unlocked(user_id, guild_id)
            cap = int(row["energy_cap"])
            new_energy = min(cap, int(row["energy"]) + amount)
            await self.conn.execute(
                "UPDATE user_character SET energy = ? WHERE user_id = ? AND guild_id = ?",
                (new_energy, user_id, guild_id),
            )
            await self.conn.commit()
            return new_energy

    async def get_dungeon_run(self, user_id: int, guild_id: int) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(
            "SELECT * FROM dungeon_runs WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        return await cursor.fetchone()

    async def has_vault_dungeon_unlocked(self, user_id: int, guild_id: int) -> bool:
        progress = await self.get_user_progress(user_id, guild_id)
        return int(progress["vault_dungeon_unlocked"]) != 0

    async def unlock_vault_dungeon(self, user_id: int, guild_id: int, price: float) -> str | None:
        """Unlock Gilded Vault access. Returns None on success or an error code."""
        if await self.has_vault_dungeon_unlocked(user_id, guild_id):
            return "already_unlocked"
        if not await self.debit_wallet(user_id, guild_id, price):
            return "insufficient_funds"
        async with self._write_lock:
            await self.conn.execute(
                """
                UPDATE user_progress SET vault_dungeon_unlocked = 1
                WHERE user_id = ? AND guild_id = ?
                """,
                (user_id, guild_id),
            )
            await self.conn.commit()
        return None

    async def start_dungeon_run(
        self,
        user_id: int,
        guild_id: int,
        player_hp: float,
        max_hp: float,
        enemy_hp: float,
        *,
        tier: str = "normal",
    ) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT INTO dungeon_runs (
                    guild_id, user_id, room, player_hp, max_hp, enemy_hp, started_at, tier
                )
                VALUES (?, ?, 1, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    room = 1,
                    player_hp = excluded.player_hp,
                    max_hp = excluded.max_hp,
                    enemy_hp = excluded.enemy_hp,
                    started_at = excluded.started_at,
                    tier = excluded.tier
                """,
                (guild_id, user_id, player_hp, max_hp, enemy_hp, time.time(), tier),
            )
            await self.conn.commit()

    async def update_dungeon_run(
        self,
        user_id: int,
        guild_id: int,
        *,
        room: int,
        player_hp: float,
        enemy_hp: float,
    ) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                UPDATE dungeon_runs
                SET room = ?, player_hp = ?, enemy_hp = ?
                WHERE guild_id = ? AND user_id = ?
                """,
                (room, player_hp, enemy_hp, guild_id, user_id),
            )
            await self.conn.commit()

    async def clear_dungeon_run(self, user_id: int, guild_id: int) -> None:
        async with self._write_lock:
            await self.conn.execute(
                "DELETE FROM dungeon_runs WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            await self.conn.commit()

    async def fuse_aspect_instances(
        self,
        user_id: int,
        guild_id: int,
        instance_ids: list[int],
    ) -> int | None:
        """Burn 3 aspects → one new rolled aspect. Returns new instance_id or None."""
        if len(instance_ids) != 3:
            return None
        from utils.aspects import random_aspect_definition, roll_pct_shop

        async with self._write_lock:
            ids_set = set(instance_ids)
            if len(ids_set) != 3:
                return None
            for inst_id in ids_set:
                cursor = await self.conn.execute(
                    """
                    SELECT instance_id FROM aspect_instances
                    WHERE guild_id = ? AND user_id = ? AND instance_id = ?
                    """,
                    (guild_id, user_id, inst_id),
                )
                if await cursor.fetchone() is None:
                    return None
                eq = await self.conn.execute(
                    """
                    SELECT 1 FROM equipped_aspect
                    WHERE guild_id = ? AND user_id = ? AND instance_id = ?
                    """,
                    (guild_id, user_id, inst_id),
                )
                if await eq.fetchone() is not None:
                    return None
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                for inst_id in ids_set:
                    await self.conn.execute(
                        "DELETE FROM aspect_instances WHERE instance_id = ?",
                        (inst_id,),
                    )
                defn = random_aspect_definition()
                roll_pct = min(40.0, roll_pct_shop() + 6.0)
                cursor = await self.conn.execute(
                    """
                    INSERT INTO aspect_instances
                        (guild_id, user_id, aspect_id, roll_pct, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    RETURNING instance_id
                    """,
                    (guild_id, user_id, defn.id, roll_pct, time.time()),
                )
                row = await cursor.fetchone()
                if row is None:
                    await self.conn.rollback()
                    return None
                new_id = int(row["instance_id"])
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
            return new_id

    async def get_elo_season(self, guild_id: int) -> tuple[int, float]:
        cursor = await self.conn.execute(
            "SELECT season_number, last_reset_at FROM guild_elo_season WHERE guild_id = ?",
            (guild_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return 1, 0.0
        return int(row["season_number"]), float(row["last_reset_at"])

    async def reset_elo_season(self, guild_id: int) -> int:
        """Reset all duel ELO to start rating; bump season. Returns new season number."""
        import config

        async with self._write_lock:
            cursor = await self.conn.execute(
                "SELECT season_number FROM guild_elo_season WHERE guild_id = ?",
                (guild_id,),
            )
            row = await cursor.fetchone()
            season = int(row["season_number"]) + 1 if row is not None else 1
            now = time.time()
            await self.conn.execute(
                """
                INSERT INTO guild_elo_season (guild_id, season_number, last_reset_at)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    season_number = excluded.season_number,
                    last_reset_at = excluded.last_reset_at
                """,
                (guild_id, season, now),
            )
            await self.conn.execute(
                """
                UPDATE duel_elo SET rating = ?
                WHERE guild_id = ?
                """,
                (config.DUEL_ELO_START, guild_id),
            )
            await self.conn.commit()
        return season

    async def get_party_leader_for_member(self, guild_id: int, user_id: int) -> int | None:
        cursor = await self.conn.execute(
            """
            SELECT leader_id FROM dungeon_party_members
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        )
        row = await cursor.fetchone()
        return int(row["leader_id"]) if row is not None else None

    async def get_dungeon_party(self, guild_id: int, leader_id: int) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(
            "SELECT * FROM dungeon_parties WHERE guild_id = ? AND leader_id = ?",
            (guild_id, leader_id),
        )
        return await cursor.fetchone()

    async def list_party_members(
        self, guild_id: int, leader_id: int,
    ) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT * FROM dungeon_party_members
            WHERE guild_id = ? AND leader_id = ?
            ORDER BY user_id ASC
            """,
            (guild_id, leader_id),
        )
        return list(await cursor.fetchall())

    async def create_dungeon_party(
        self,
        guild_id: int,
        leader_id: int,
        player_hp: float,
        max_hp: float,
        enemy_hp: float,
        *,
        tier: str = "normal",
    ) -> None:
        async with self._write_lock:
            await self.conn.execute(
                "DELETE FROM dungeon_party_members WHERE guild_id = ? AND leader_id = ?",
                (guild_id, leader_id),
            )
            await self.conn.execute(
                "DELETE FROM dungeon_parties WHERE guild_id = ? AND leader_id = ?",
                (guild_id, leader_id),
            )
            await self.conn.execute(
                """
                INSERT INTO dungeon_parties (guild_id, leader_id, room, enemy_hp, started_at, tier)
                VALUES (?, ?, 1, ?, ?, ?)
                """,
                (guild_id, leader_id, enemy_hp, time.time(), tier),
            )
            await self.conn.execute(
                """
                INSERT INTO dungeon_party_members
                    (guild_id, leader_id, user_id, player_hp, max_hp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (guild_id, leader_id, leader_id, player_hp, max_hp),
            )
            await self.conn.commit()

    async def join_dungeon_party(
        self,
        guild_id: int,
        leader_id: int,
        user_id: int,
        player_hp: float,
        max_hp: float,
    ) -> str | None:
        async with self._write_lock:
            party = await self.get_dungeon_party(guild_id, leader_id)
            if party is None:
                return "no_party"
            members = await self.list_party_members(guild_id, leader_id)
            if len(members) >= 4:
                return "full"
            if any(int(m["user_id"]) == user_id for m in members):
                return "already_in"
            other = await self.get_party_leader_for_member(guild_id, user_id)
            if other is not None and other != leader_id:
                return "in_other_party"
            await self.conn.execute(
                """
                INSERT INTO dungeon_party_members
                    (guild_id, leader_id, user_id, player_hp, max_hp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (guild_id, leader_id, user_id, player_hp, max_hp),
            )
            await self.conn.commit()
        return None

    async def leave_dungeon_party(self, guild_id: int, user_id: int) -> bool:
        async with self._write_lock:
            leader_id = await self.get_party_leader_for_member(guild_id, user_id)
            if leader_id is None:
                return False
            await self.conn.execute(
                """
                DELETE FROM dungeon_party_members
                WHERE guild_id = ? AND leader_id = ? AND user_id = ?
                """,
                (guild_id, leader_id, user_id),
            )
            if leader_id == user_id:
                await self.conn.execute(
                    "DELETE FROM dungeon_parties WHERE guild_id = ? AND leader_id = ?",
                    (guild_id, leader_id),
                )
                await self.conn.execute(
                    "DELETE FROM dungeon_party_members WHERE guild_id = ? AND leader_id = ?",
                    (guild_id, leader_id),
                )
            await self.conn.commit()
            return True

    async def update_party_member_hp(
        self, guild_id: int, leader_id: int, user_id: int, player_hp: float,
    ) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                UPDATE dungeon_party_members SET player_hp = ?
                WHERE guild_id = ? AND leader_id = ? AND user_id = ?
                """,
                (player_hp, guild_id, leader_id, user_id),
            )
            await self.conn.commit()

    async def update_dungeon_party_enemy(
        self, guild_id: int, leader_id: int, *, room: int, enemy_hp: float,
    ) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                UPDATE dungeon_parties SET room = ?, enemy_hp = ?
                WHERE guild_id = ? AND leader_id = ?
                """,
                (room, enemy_hp, guild_id, leader_id),
            )
            await self.conn.commit()

    async def clear_dungeon_party(self, guild_id: int, leader_id: int) -> None:
        async with self._write_lock:
            await self.conn.execute(
                "DELETE FROM dungeon_party_members WHERE guild_id = ? AND leader_id = ?",
                (guild_id, leader_id),
            )
            await self.conn.execute(
                "DELETE FROM dungeon_parties WHERE guild_id = ? AND leader_id = ?",
                (guild_id, leader_id),
            )
            await self.conn.commit()

    async def unlock_custom_avatar(self, user_id: int, guild_id: int, avatar_id: str) -> None:
        await self.unlock_avatar(user_id, guild_id, avatar_id)

    async def save_custom_avatar_assets(
        self,
        guild_id: int,
        user_id: int,
        image_data: bytes,
        file_ext: str,
    ) -> None:
        now = time.time()
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT INTO custom_avatar_assets
                    (guild_id, user_id, file_ext, portrait_data, victory_data, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    file_ext = excluded.file_ext,
                    portrait_data = excluded.portrait_data,
                    victory_data = excluded.victory_data,
                    updated_at = excluded.updated_at
                """,
                (guild_id, user_id, file_ext, image_data, image_data, now),
            )
            await self.conn.commit()

    async def get_custom_avatar_assets(
        self, guild_id: int, user_id: int,
    ) -> tuple[bytes, bytes, str] | None:
        try:
            cursor = await self.conn.execute(
                """
                SELECT portrait_data, victory_data, file_ext
                FROM custom_avatar_assets
                WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, user_id),
            )
            row = await cursor.fetchone()
        except Exception:
            logging.exception(
                "custom_avatar_assets lookup failed guild=%s user=%s",
                guild_id,
                user_id,
            )
            return None
        if row is None:
            return None
        portrait = row["portrait_data"]
        victory = row["victory_data"]
        if isinstance(portrait, memoryview):
            portrait = bytes(portrait)
        if isinstance(victory, memoryview):
            victory = bytes(victory)
        return bytes(portrait), bytes(victory), str(row["file_ext"])
