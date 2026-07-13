"""Database mixin for gameplay expansion systems."""
from __future__ import annotations

import json
import time
from typing import Any

import aiosqlite

import config
from utils.affixes import roll_affix_ids, roll_affix_value
from utils.companions import COMPANION_DEFINITIONS
from utils.contracts import CONTRACT_MAP
from utils.relics import RELIC_DEFINITIONS


class DatabaseExpansionMixin:
    async def _migrate_gameplay_expansion(self) -> None:
        pk = "SERIAL PRIMARY KEY" if self.is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
        tables = [
            f"""
            CREATE TABLE IF NOT EXISTS blueprint_unlocks (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                blueprint_id TEXT NOT NULL,
                unlocked_at REAL NOT NULL,
                PRIMARY KEY (guild_id, user_id, blueprint_id)
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS relic_instances (
                instance_id {pk},
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                relic_id TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS equipped_relic (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                instance_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS gear_affixes (
                gear_instance_id INTEGER NOT NULL,
                affix_id TEXT NOT NULL,
                roll_value REAL NOT NULL,
                slot INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (gear_instance_id, slot)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS companion_collection (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                companion_id TEXT NOT NULL,
                obtained_at REAL NOT NULL,
                PRIMARY KEY (guild_id, user_id, companion_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS equipped_companion (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                companion_id TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS season_tokens (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                season_num INTEGER NOT NULL,
                tokens INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, season_num)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS season_redemptions (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                season_num INTEGER NOT NULL,
                reward_id TEXT NOT NULL,
                redeemed_at REAL NOT NULL,
                PRIMARY KEY (guild_id, user_id, season_num, reward_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS guild_contracts (
                guild_id BIGINT NOT NULL PRIMARY KEY,
                contract_ids TEXT NOT NULL,
                refresh_at REAL NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS user_contract_progress (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                contract_id TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                claimed INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, contract_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS guild_expeditions (
                guild_id BIGINT NOT NULL PRIMARY KEY,
                expedition_id TEXT NOT NULL,
                contributed_scrap INTEGER NOT NULL DEFAULT 0,
                contributed_nuggets REAL NOT NULL DEFAULT 0,
                ends_at REAL NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS expedition_contributors (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                expedition_id TEXT NOT NULL,
                scrap INTEGER NOT NULL DEFAULT 0,
                nuggets REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, expedition_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS museum_counts (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                category TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, category)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS drug_phenotypes (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                phenotype_id TEXT NOT NULL,
                discovered_at REAL NOT NULL,
                active INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, phenotype_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS crew_legacy (
                guild_id BIGINT NOT NULL,
                crew_id BIGINT NOT NULL,
                legacy_id TEXT NOT NULL,
                unlocked_at REAL NOT NULL,
                PRIMARY KEY (guild_id, crew_id, legacy_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS territory_cosmetics (
                guild_id BIGINT NOT NULL,
                crew_id BIGINT NOT NULL,
                zone_id TEXT NOT NULL,
                cosmetic_id TEXT NOT NULL,
                unlocked_at REAL NOT NULL,
                PRIMARY KEY (guild_id, crew_id, zone_id, cosmetic_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS guild_expedition_schedule (
                guild_id BIGINT NOT NULL PRIMARY KEY,
                next_spawn_at REAL NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS guild_income_buffs (
                guild_id BIGINT NOT NULL PRIMARY KEY,
                mult REAL NOT NULL DEFAULT 1.0,
                expires_at REAL NOT NULL DEFAULT 0
            )
            """,
        ]
        for sql in tables:
            await self.conn.execute(sql)
        await self.conn.commit()

    # --- Blueprints ---
    async def unlock_blueprint(self, user_id: int, guild_id: int, blueprint_id: str) -> bool:
        now = time.time()
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                INSERT OR IGNORE INTO blueprint_unlocks (guild_id, user_id, blueprint_id, unlocked_at)
                VALUES (?, ?, ?, ?)
                """,
                (guild_id, user_id, blueprint_id, now),
            )
            await self.conn.commit()
            return cursor.rowcount > 0

    async def list_blueprints(self, user_id: int, guild_id: int) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT blueprint_id, unlocked_at FROM blueprint_unlocks
            WHERE guild_id = ? AND user_id = ?
            ORDER BY unlocked_at DESC
            """,
            (guild_id, user_id),
        )
        return await cursor.fetchall()

    async def has_blueprint(self, user_id: int, guild_id: int, blueprint_id: str) -> bool:
        cursor = await self.conn.execute(
            """
            SELECT 1 FROM blueprint_unlocks
            WHERE guild_id = ? AND user_id = ? AND blueprint_id = ?
            """,
            (guild_id, user_id, blueprint_id),
        )
        return await cursor.fetchone() is not None

    # --- Relics ---
    async def create_relic_instance(self, user_id: int, guild_id: int, relic_id: str) -> int:
        if relic_id not in RELIC_DEFINITIONS:
            msg = f"Unknown relic: {relic_id}"
            raise ValueError(msg)
        now = time.time()
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                INSERT INTO relic_instances (guild_id, user_id, relic_id, created_at)
                VALUES (?, ?, ?, ?)
                RETURNING instance_id
                """,
                (guild_id, user_id, relic_id, now),
            )
            row = await cursor.fetchone()
            await self.conn.commit()
            if row is None:
                msg = "relic insert failed"
                raise RuntimeError(msg)
            return int(row["instance_id"])

    async def list_relic_instances(self, user_id: int, guild_id: int) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT instance_id, relic_id, created_at FROM relic_instances
            WHERE guild_id = ? AND user_id = ?
            ORDER BY created_at DESC
            """,
            (guild_id, user_id),
        )
        return await cursor.fetchall()

    async def get_equipped_relic_row(self, user_id: int, guild_id: int) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(
            """
            SELECT r.instance_id, r.relic_id
            FROM equipped_relic e
            JOIN relic_instances r ON r.instance_id = e.instance_id
            WHERE e.guild_id = ? AND e.user_id = ?
            """,
            (guild_id, user_id),
        )
        return await cursor.fetchone()

    async def equip_relic_instance(self, user_id: int, guild_id: int, instance_id: int) -> bool:
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                SELECT instance_id FROM relic_instances
                WHERE guild_id = ? AND user_id = ? AND instance_id = ?
                """,
                (guild_id, user_id, instance_id),
            )
            if await cursor.fetchone() is None:
                return False
            await self.conn.execute(
                """
                INSERT OR REPLACE INTO equipped_relic (guild_id, user_id, instance_id)
                VALUES (?, ?, ?)
                """,
                (guild_id, user_id, instance_id),
            )
            await self.conn.commit()
            return True

    async def unequip_relic(self, user_id: int, guild_id: int) -> bool:
        async with self._write_lock:
            cursor = await self.conn.execute(
                "DELETE FROM equipped_relic WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            await self.conn.commit()
            return cursor.rowcount > 0

    async def consume_enhance_safety_charge(self, user_id: int, guild_id: int) -> bool:
        row = await self.get_equipped_relic_row(user_id, guild_id)
        if row is None or row["relic_id"] != "relic_void_heart":
            return False
        return True

    # --- Gear affixes ---
    async def roll_gear_affixes(
        self, gear_instance_id: int, *, delve_bonus: bool = False,
    ) -> list[tuple[str, float]]:
        affix_ids = roll_affix_ids(delve_bonus=delve_bonus)
        created: list[tuple[str, float]] = []
        async with self._write_lock:
            for slot, affix_id in enumerate(affix_ids, start=1):
                roll = roll_affix_value()
                await self.conn.execute(
                    """
                    INSERT OR REPLACE INTO gear_affixes (gear_instance_id, affix_id, roll_value, slot)
                    VALUES (?, ?, ?, ?)
                    """,
                    (gear_instance_id, affix_id, roll, slot),
                )
                created.append((affix_id, roll))
            await self.conn.commit()
        return created

    async def list_gear_affixes(self, gear_instance_id: int) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT affix_id, roll_value, slot FROM gear_affixes
            WHERE gear_instance_id = ?
            ORDER BY slot ASC
            """,
            (gear_instance_id,),
        )
        return await cursor.fetchall()

    # --- Companions ---
    async def grant_companion(self, user_id: int, guild_id: int, companion_id: str) -> bool:
        if companion_id not in COMPANION_DEFINITIONS:
            return False
        now = time.time()
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                INSERT OR IGNORE INTO companion_collection
                    (guild_id, user_id, companion_id, obtained_at)
                VALUES (?, ?, ?, ?)
                """,
                (guild_id, user_id, companion_id, now),
            )
            await self.conn.commit()
            return cursor.rowcount > 0

    async def list_companions(self, user_id: int, guild_id: int) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT companion_id, obtained_at FROM companion_collection
            WHERE guild_id = ? AND user_id = ?
            ORDER BY obtained_at DESC
            """,
            (guild_id, user_id),
        )
        return await cursor.fetchall()

    async def get_equipped_companion_id(self, user_id: int, guild_id: int) -> str | None:
        cursor = await self.conn.execute(
            """
            SELECT companion_id FROM equipped_companion
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        )
        row = await cursor.fetchone()
        return str(row["companion_id"]) if row else None

    async def equip_companion(self, user_id: int, guild_id: int, companion_id: str) -> bool:
        cursor = await self.conn.execute(
            """
            SELECT 1 FROM companion_collection
            WHERE guild_id = ? AND user_id = ? AND companion_id = ?
            """,
            (guild_id, user_id, companion_id),
        )
        if await cursor.fetchone() is None:
            return False
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT OR REPLACE INTO equipped_companion (guild_id, user_id, companion_id)
                VALUES (?, ?, ?)
                """,
                (guild_id, user_id, companion_id),
            )
            await self.conn.commit()
            return True

    async def unequip_companion(self, user_id: int, guild_id: int) -> bool:
        async with self._write_lock:
            cursor = await self.conn.execute(
                "DELETE FROM equipped_companion WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            await self.conn.commit()
            return cursor.rowcount > 0

    # --- Season tokens ---
    async def add_season_tokens(
        self, user_id: int, guild_id: int, amount: int, season_num: int,
    ) -> int:
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT INTO season_tokens (guild_id, user_id, season_num, tokens)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (guild_id, user_id, season_num) DO UPDATE SET
                    tokens = season_tokens.tokens + excluded.tokens
                """,
                (guild_id, user_id, season_num, amount),
            )
            await self.conn.commit()
        return await self.get_season_tokens(user_id, guild_id, season_num)

    async def get_season_tokens(self, user_id: int, guild_id: int, season_num: int) -> int:
        cursor = await self.conn.execute(
            """
            SELECT tokens FROM season_tokens
            WHERE guild_id = ? AND user_id = ? AND season_num = ?
            """,
            (guild_id, user_id, season_num),
        )
        row = await cursor.fetchone()
        return int(row["tokens"]) if row else 0

    async def redeem_season_reward(
        self, user_id: int, guild_id: int, season_num: int, reward_id: str, cost: int,
    ) -> bool:
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                SELECT tokens FROM season_tokens
                WHERE guild_id = ? AND user_id = ? AND season_num = ?
                """,
                (guild_id, user_id, season_num),
            )
            row = await cursor.fetchone()
            if row is None or int(row["tokens"]) < cost:
                return False
            cursor = await self.conn.execute(
                """
                SELECT 1 FROM season_redemptions
                WHERE guild_id = ? AND user_id = ? AND season_num = ? AND reward_id = ?
                """,
                (guild_id, user_id, season_num, reward_id),
            )
            if await cursor.fetchone() is not None:
                return False
            await self.conn.execute(
                """
                UPDATE season_tokens SET tokens = tokens - ?
                WHERE guild_id = ? AND user_id = ? AND season_num = ?
                """,
                (cost, guild_id, user_id, season_num),
            )
            await self.conn.execute(
                """
                INSERT INTO season_redemptions
                    (guild_id, user_id, season_num, reward_id, redeemed_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (guild_id, user_id, season_num, reward_id, time.time()),
            )
            await self.conn.commit()
            return True

    async def has_season_redemption(
        self, user_id: int, guild_id: int, season_num: int, reward_id: str,
    ) -> bool:
        cursor = await self.conn.execute(
            """
            SELECT 1 FROM season_redemptions
            WHERE guild_id = ? AND user_id = ? AND season_num = ? AND reward_id = ?
            """,
            (guild_id, user_id, season_num, reward_id),
        )
        return await cursor.fetchone() is not None

    # --- Contracts ---
    async def get_contract_refresh_at(self, guild_id: int) -> float:
        cursor = await self.conn.execute(
            "SELECT refresh_at FROM guild_contracts WHERE guild_id = ?",
            (guild_id,),
        )
        row = await cursor.fetchone()
        return float(row["refresh_at"]) if row else 0.0

    async def set_guild_contracts(
        self, guild_id: int, contract_ids: list[str], refresh_at: float,
    ) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT OR REPLACE INTO guild_contracts (guild_id, contract_ids, refresh_at)
                VALUES (?, ?, ?)
                """,
                (guild_id, json.dumps(contract_ids), refresh_at),
            )
            await self.conn.execute(
                "DELETE FROM user_contract_progress WHERE guild_id = ?",
                (guild_id,),
            )
            await self.conn.commit()

    async def list_guild_contract_ids(self, guild_id: int) -> list[str]:
        cursor = await self.conn.execute(
            "SELECT contract_ids FROM guild_contracts WHERE guild_id = ?",
            (guild_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return []
        return list(json.loads(str(row["contract_ids"])))

    async def increment_contract_progress(
        self, guild_id: int, user_id: int, event: str, amount: int = 1,
    ) -> None:
        contract_ids = await self.list_guild_contract_ids(guild_id)
        if not contract_ids:
            return
        async with self._write_lock:
            for cid in contract_ids:
                defn = CONTRACT_MAP.get(cid)
                if defn is None or defn.event != event:
                    continue
                await self.conn.execute(
                    """
                    INSERT INTO user_contract_progress
                        (guild_id, user_id, contract_id, progress, claimed)
                    VALUES (?, ?, ?, ?, 0)
                    ON CONFLICT (guild_id, user_id, contract_id) DO UPDATE SET
                        progress = user_contract_progress.progress + excluded.progress
                    """,
                    (guild_id, user_id, cid, amount),
                )
            await self.conn.commit()

    async def get_contract_progress_rows(
        self, user_id: int, guild_id: int,
    ) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT contract_id, progress, claimed FROM user_contract_progress
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        )
        return await cursor.fetchall()

    async def claim_contract(
        self, user_id: int, guild_id: int, contract_id: str,
    ) -> dict[str, Any] | None:
        defn = CONTRACT_MAP.get(contract_id)
        if defn is None:
            return None
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                SELECT progress, claimed FROM user_contract_progress
                WHERE guild_id = ? AND user_id = ? AND contract_id = ?
                """,
                (guild_id, user_id, contract_id),
            )
            row = await cursor.fetchone()
            if row is None or int(row["claimed"]) or int(row["progress"]) < defn.target:
                return None
            await self.conn.execute(
                """
                UPDATE user_contract_progress SET claimed = 1
                WHERE guild_id = ? AND user_id = ? AND contract_id = ?
                """,
                (guild_id, user_id, contract_id),
            )
            await self.conn.commit()
        return {
            "nuggets": defn.reward_nuggets,
            "tokens": defn.reward_tokens,
            "item_id": defn.reward_item_id,
            "qty": defn.reward_qty,
        }

    # --- Museum ---
    async def increment_museum_category(
        self, guild_id: int, user_id: int, category: str, amount: int = 1,
    ) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT INTO museum_counts (guild_id, user_id, category, count)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (guild_id, user_id, category) DO UPDATE SET
                    count = museum_counts.count + excluded.count
                """,
                (guild_id, user_id, category, amount),
            )
            await self.conn.commit()

    async def get_museum_counts(self, user_id: int, guild_id: int) -> dict[str, int]:
        cursor = await self.conn.execute(
            """
            SELECT category, count FROM museum_counts
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        )
        return {str(r["category"]): int(r["count"]) for r in await cursor.fetchall()}

    # --- Expeditions ---
    async def get_active_expedition(self, guild_id: int) -> aiosqlite.Row | None:
        now = time.time()
        cursor = await self.conn.execute(
            """
            SELECT * FROM guild_expeditions
            WHERE guild_id = ? AND completed = 0 AND ends_at > ?
            """,
            (guild_id, now),
        )
        return await cursor.fetchone()

    async def start_expedition(
        self, guild_id: int, expedition_id: str, ends_at: float,
    ) -> None:
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT OR REPLACE INTO guild_expeditions
                    (guild_id, expedition_id, contributed_scrap, contributed_nuggets, ends_at, completed)
                VALUES (?, ?, 0, 0, ?, 0)
                """,
                (guild_id, expedition_id, ends_at),
            )
            await self.conn.commit()

    async def contribute_expedition(
        self,
        guild_id: int,
        user_id: int,
        *,
        scrap: int = 0,
        nuggets: float = 0.0,
    ) -> aiosqlite.Row | None:
        exp = await self.get_active_expedition(guild_id)
        if exp is None:
            return None
        if scrap > 0:
            for _ in range(scrap):
                if not await self.consume_inventory_item(user_id, guild_id, "alchemy_scrap"):
                    return None
        if nuggets > 0 and not await self.debit_wallet(user_id, guild_id, nuggets):
            if scrap > 0:
                await self.grant_item(user_id, guild_id, "alchemy_scrap", scrap)
            return None
        exp_id = str(exp["expedition_id"])
        async with self._write_lock:
            await self.conn.execute(
                """
                UPDATE guild_expeditions
                SET contributed_scrap = contributed_scrap + ?,
                    contributed_nuggets = contributed_nuggets + ?
                WHERE guild_id = ?
                """,
                (scrap, nuggets, guild_id),
            )
            await self.conn.execute(
                """
                INSERT INTO expedition_contributors
                    (guild_id, user_id, expedition_id, scrap, nuggets)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (guild_id, user_id, expedition_id) DO UPDATE SET
                    scrap = expedition_contributors.scrap + excluded.scrap,
                    nuggets = expedition_contributors.nuggets + excluded.nuggets
                """,
                (guild_id, user_id, exp_id, scrap, nuggets),
            )
            await self.conn.commit()
        return await self.get_active_expedition(guild_id)

    async def complete_expedition(self, guild_id: int) -> list[int]:
        exp = await self.get_active_expedition(guild_id)
        if exp is None:
            return []
        async with self._write_lock:
            await self.conn.execute(
                "UPDATE guild_expeditions SET completed = 1 WHERE guild_id = ?",
                (guild_id,),
            )
            cursor = await self.conn.execute(
                """
                SELECT user_id FROM expedition_contributors
                WHERE guild_id = ? AND expedition_id = ?
                """,
                (guild_id, str(exp["expedition_id"])),
            )
            contributors = [int(r["user_id"]) for r in await cursor.fetchall()]
            await self.conn.execute(
                """
                INSERT OR REPLACE INTO guild_income_buffs (guild_id, mult, expires_at)
                VALUES (?, ?, ?)
                """,
                (
                    guild_id,
                    config.EXPEDITION_INCOME_BUFF,
                    time.time() + config.EXPEDITION_INCOME_BUFF_HOURS * 3600,
                ),
            )
            await self.conn.commit()
        return contributors

    async def get_guild_income_mult(self, guild_id: int) -> float:
        now = time.time()
        cursor = await self.conn.execute(
            "SELECT mult, expires_at FROM guild_income_buffs WHERE guild_id = ?",
            (guild_id,),
        )
        row = await cursor.fetchone()
        if row is None or float(row["expires_at"]) <= now:
            return 1.0
        return float(row["mult"])

  # --- Phenotypes ---
    async def discover_phenotype(
        self, user_id: int, guild_id: int, phenotype_id: str,
    ) -> bool:
        now = time.time()
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                INSERT OR IGNORE INTO drug_phenotypes
                    (guild_id, user_id, phenotype_id, discovered_at, active)
                VALUES (?, ?, ?, ?, 0)
                """,
                (guild_id, user_id, phenotype_id, now),
            )
            await self.conn.commit()
            return cursor.rowcount > 0

    async def list_phenotypes(self, user_id: int, guild_id: int) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(
            """
            SELECT phenotype_id, discovered_at, active FROM drug_phenotypes
            WHERE guild_id = ? AND user_id = ?
            ORDER BY discovered_at DESC
            """,
            (guild_id, user_id),
        )
        return await cursor.fetchall()

    async def set_active_phenotype(
        self, user_id: int, guild_id: int, phenotype_id: str,
    ) -> bool:
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                SELECT 1 FROM drug_phenotypes
                WHERE guild_id = ? AND user_id = ? AND phenotype_id = ?
                """,
                (guild_id, user_id, phenotype_id),
            )
            if await cursor.fetchone() is None:
                return False
            await self.conn.execute(
                "UPDATE drug_phenotypes SET active = 0 WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            await self.conn.execute(
                """
                UPDATE drug_phenotypes SET active = 1
                WHERE guild_id = ? AND user_id = ? AND phenotype_id = ?
                """,
                (guild_id, user_id, phenotype_id),
            )
            await self.conn.commit()
            return True

    async def get_active_phenotype_id(self, user_id: int, guild_id: int) -> str | None:
        cursor = await self.conn.execute(
            """
            SELECT phenotype_id FROM drug_phenotypes
            WHERE guild_id = ? AND user_id = ? AND active = 1
            """,
            (guild_id, user_id),
        )
        row = await cursor.fetchone()
        return str(row["phenotype_id"]) if row else None

    # --- Crew legacy & territory cosmetics ---
    async def unlock_crew_legacy(
        self, guild_id: int, crew_id: int, legacy_id: str,
    ) -> bool:
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                INSERT OR IGNORE INTO crew_legacy (guild_id, crew_id, legacy_id, unlocked_at)
                VALUES (?, ?, ?, ?)
                """,
                (guild_id, crew_id, legacy_id, time.time()),
            )
            await self.conn.commit()
            return cursor.rowcount > 0

    async def list_crew_legacy(self, guild_id: int, crew_id: int) -> list[str]:
        cursor = await self.conn.execute(
            """
            SELECT legacy_id FROM crew_legacy
            WHERE guild_id = ? AND crew_id = ?
            """,
            (guild_id, crew_id),
        )
        return [str(r["legacy_id"]) for r in await cursor.fetchall()]

    async def unlock_territory_cosmetic(
        self, guild_id: int, crew_id: int, zone_id: str, cosmetic_id: str,
    ) -> bool:
        async with self._write_lock:
            cursor = await self.conn.execute(
                """
                INSERT OR IGNORE INTO territory_cosmetics
                    (guild_id, crew_id, zone_id, cosmetic_id, unlocked_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (guild_id, crew_id, zone_id, cosmetic_id, time.time()),
            )
            await self.conn.commit()
            return cursor.rowcount > 0

    async def get_expansion_bonuses_data(self, user_id: int, guild_id: int) -> dict[str, Any]:
        """Aggregate data for expansion bonus calculation."""
        relic_row = await self.get_equipped_relic_row(user_id, guild_id)
        companion_id = await self.get_equipped_companion_id(user_id, guild_id)
        museum = await self.get_museum_counts(user_id, guild_id)
        crew_legacy_bonus = 1.0
        crew_id = await self.get_user_crew_id(user_id, guild_id)
        if crew_id is not None:
            legacies = await self.list_crew_legacy(guild_id, crew_id)
            if legacies:
                crew_legacy_bonus = 1.0 + config.CREW_LEGACY_INCOME_BONUS * len(legacies)
        return {
            "relic_id": str(relic_row["relic_id"]) if relic_row else None,
            "companion_id": companion_id,
            "museum_counts": museum,
            "crew_legacy_bonus": crew_legacy_bonus,
        }

    async def get_user_crew_id(self, user_id: int, guild_id: int) -> int | None:
        cursor = await self.conn.execute(
            """
            SELECT crew_id FROM crew_members
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        )
        row = await cursor.fetchone()
        return int(row["crew_id"]) if row else None

    async def grant_drug_units(
        self, user_id: int, guild_id: int, drug_id: str, quantity: int,
    ) -> None:
        from utils.drugs import drug_by_id

        defn = drug_by_id(drug_id)
        if defn is None or quantity <= 0:
            return
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT INTO drug_inventory (user_id, guild_id, drug_id, quantity)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, guild_id, drug_id) DO UPDATE SET
                    quantity = drug_inventory.quantity + excluded.quantity
                """,
                (user_id, guild_id, defn.drug_id, quantity),
            )
            await self.conn.commit()

    async def spend_drug_units(
        self, user_id: int, guild_id: int, drug_id: str, quantity: int,
    ) -> bool:
        if quantity <= 0:
            return False
        from utils.drugs import drug_by_id

        defn = drug_by_id(drug_id)
        if defn is None:
            return False
        async with self._write_lock:
            stored_id, available = await self._find_drug_inventory_qty(
                user_id, guild_id, defn.drug_id,
            )
            if stored_id is None or available < quantity:
                return False
            new_qty = available - quantity
            if new_qty <= 0:
                await self.conn.execute(
                    """
                    DELETE FROM drug_inventory
                    WHERE guild_id = ? AND user_id = ? AND drug_id = ?
                    """,
                    (guild_id, user_id, stored_id),
                )
            else:
                await self.conn.execute(
                    """
                    UPDATE drug_inventory SET quantity = ?
                    WHERE guild_id = ? AND user_id = ? AND drug_id = ?
                    """,
                    (new_qty, guild_id, user_id, stored_id),
                )
            await self.conn.commit()
            return True
