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
        self, rows: list[asyncpg.Record] | None = None, *, lastrowid: int | None = None
    ) -> None:
        self._rows = rows or []
        self.lastrowid = lastrowid

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
        if (
            sql.lstrip().upper().startswith(("SELECT", "INSERT INTO BOUNTIES"))
            and "RETURNING" in sql.upper()
        ):
            rows = await self.conn.fetch(sql, *params)
            lastrowid = int(rows[0]["id"]) if rows and "id" in rows[0] else None
            return PostgresCursor(list(rows), lastrowid=lastrowid)
        if sql.lstrip().upper().startswith(("SELECT", "WITH")):
            return PostgresCursor(list(await self.conn.fetch(sql, *params)))
        await self.conn.execute(sql, *params)
        return PostgresCursor()

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

    @staticmethod
    def _normalize_query(query: str) -> str | None:
        stripped = query.strip()
        upper = stripped.upper()
        if upper.startswith("PRAGMA"):
            return None
        if upper == "BEGIN IMMEDIATE":
            return "BEGIN"
        sql = query
        sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        sql = sql.replace("INSERT OR IGNORE INTO users", "INSERT INTO users")
        if "INSERT INTO users" in sql and "ON CONFLICT" not in sql:
            sql = f"{sql} ON CONFLICT DO NOTHING"
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
                last_daily REAL NOT NULL DEFAULT 0,
                last_heist REAL NOT NULL DEFAULT 0,
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
                slot TEXT NOT NULL CHECK (slot IN ('weapon', 'armor')),
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
                    await self.conn.execute(
                        """
                        INSERT INTO equipment (guild_id, user_id, slot, item_id)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(guild_id, user_id, slot) DO UPDATE SET
                            item_id = excluded.item_id
                        """,
                        (guild_id, user_id, slot, item_id),
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
            SELECT main_channel_id, designated_channel_id, split_announcement_channels
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

    async def get_guild_channel_settings(self, guild_id: int) -> dict[str, int | bool | None]:
        row = await self._get_guild_channels_row(guild_id)
        if row is None:
            return {
                "main_channel_id": None,
                "designated_channel_id": None,
                "split_announcement_channels": False,
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

    async def _prune_guild_channels_row(self, guild_id: int) -> None:
        """Remove empty guild_channels rows after partial clears."""
        await self.conn.execute(
            """
            DELETE FROM guild_channels
            WHERE guild_id = ?
              AND main_channel_id IS NULL
              AND designated_channel_id IS NULL
              AND split_announcement_channels = 0
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
                "DELETE FROM achievements WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            await self.conn.commit()

    async def buy_item(self, user_id: int, guild_id: int, item_id: str, price: float) -> bool:
        if price <= 0:
            return False
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                await self._ensure_user_no_lock(user_id, guild_id)
                cursor = await self.conn.execute(
                    "SELECT wallet FROM users WHERE user_id = ? AND guild_id = ?",
                    (user_id, guild_id),
                )
                row = await cursor.fetchone()
                if row is None or _spendable_cents(row["wallet"]) < _spendable_cents(price):
                    await self.conn.rollback()
                    return False
                await self.conn.execute(
                    """
                    UPDATE users
                    SET wallet = wallet - ?
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (price, user_id, guild_id),
                )
                await self.conn.execute(
                    """
                    INSERT INTO inventory (guild_id, user_id, item_id, quantity)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT(guild_id, user_id, item_id) DO UPDATE SET
                        quantity = inventory.quantity + 1
                    """,
                    (guild_id, user_id, item_id),
                )
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
            return True

    async def sell_one_item(self, user_id: int, guild_id: int, item_id: str, refund: float) -> bool:
        if refund <= 0:
            return False
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
                    return False
                new_qty = int(row["quantity"]) - 1
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
                await self.conn.execute(
                    """
                    UPDATE users
                    SET wallet = wallet + ?
                    WHERE user_id = ? AND guild_id = ?
                    """,
                    (refund, user_id, guild_id),
                )
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
            return True

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

    async def equip_item(self, user_id: int, guild_id: int, slot: str, item_id: str) -> bool:
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
                    INSERT INTO equipment (guild_id, user_id, slot, item_id)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(guild_id, user_id, slot) DO UPDATE SET
                        item_id = excluded.item_id
                    """,
                    (guild_id, user_id, slot, item_id),
                )
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()
            return True

    async def grant_item(
        self, user_id: int, guild_id: int, item_id: str, *, equip_slot: str | None = None
    ) -> None:
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
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
                        INSERT INTO equipment (guild_id, user_id, slot, item_id)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(guild_id, user_id, slot) DO UPDATE SET
                            item_id = excluded.item_id
                        """,
                        (guild_id, user_id, equip_slot, item_id),
                    )
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()

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

    async def is_restricted(self, user_id: int, guild_id: int, at: float | None = None) -> bool:
        now = time.time() if at is None else at
        row = await self.get_user(user_id, guild_id)
        return float(row["arrested_until"]) > now or float(row["downed_until"]) > now

    async def leaderboard(self, guild_id: int, limit: int = 10) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT user_id, wallet
            FROM users
            WHERE guild_id = ?
            ORDER BY wallet DESC, user_id ASC
            LIMIT ?
            """,
            (guild_id, limit),
        )
        return list(await cursor.fetchall())

    async def total_circulation(self, guild_id: int) -> float:
        cursor = await self.conn.execute(
            "SELECT COALESCE(SUM(wallet), 0) AS total FROM users WHERE guild_id = ?",
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
        decay_amount = (
            whole_minutes * config.BOSS_PASSIVE_HP_DECAY_FRACTION_PER_MINUTE * max_hp
        )
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
    ) -> None:
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                await self.conn.execute("DELETE FROM boss_sessions WHERE guild_id = ?", (guild_id,))
                spawn_ts = time.time() if spawned_at is None else spawned_at
                await self.conn.execute(
                    """
                    INSERT INTO boss_sessions (
                        guild_id, name, variant, hp, max_hp, spawned_at, passive_decay_at, phases_announced
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        guild_id,
                        name,
                        variant,
                        hp,
                        hp,
                        spawn_ts,
                        spawn_ts,
                    ),
                )
            except Exception:
                await self.conn.rollback()
                raise
            await self.conn.commit()

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

    async def clear_boss(self, guild_id: int) -> None:
        async with self._write_lock:
            await self.conn.execute("DELETE FROM boss_sessions WHERE guild_id = ?", (guild_id,))
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
        progress = await self.get_user_progress(user_id, guild_id)
        prestige = int(progress["prestige_level"])
        mult = 1.0 + prestige * config.PRESTIGE_INCOME_BONUS_PER_LEVEL
        event = await self.get_active_guild_event(guild_id)
        if event is not None and str(event["event_type"]) in ("bonus_income", "trivia_fiesta"):
            mult *= float(event["multiplier"])
        return mult

    async def get_drop_multiplier(self, guild_id: int) -> float:
        event = await self.get_active_guild_event(guild_id)
        if event is not None and str(event["event_type"]) == "double_drops":
            return float(event["multiplier"])
        return 1.0

    async def get_boss_hp_multiplier(self, guild_id: int) -> float:
        event = await self.get_active_guild_event(guild_id)
        if event is not None and str(event["event_type"]) == "festival_boss":
            return float(event["multiplier"])
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
        crafts_done: int = 0,
    ) -> None:
        if not any((bosses_killed, heists_won, heals_given, mythic_kills, crafts_done)):
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
                    crafts_done = crafts_done + ?
                WHERE user_id = ? AND guild_id = ?
                """,
                (
                    bosses_killed,
                    heists_won,
                    heals_given,
                    mythic_kills,
                    crafts_done,
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
        mythic: bool,
    ) -> None:
        mythic_inc = 1 if mythic else 0
        for user_id in set(user_ids):
            await self.increment_progress(
                user_id,
                guild_id,
                bosses_killed=1,
                mythic_kills=mythic_inc,
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
        {"bosses_killed", "heists_won", "heals_given", "mythic_kills", "crafts_done", "prestige_level"}
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
        }

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
