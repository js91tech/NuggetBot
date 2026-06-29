#!/usr/bin/env python3
"""Smoke-check: database migrations and all cogs load before deploy."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DISCORD_TOKEN", "x" * 50)
os.environ.setdefault("DASHBOARD_ENABLED", "false")


async def _main() -> int:
    import discord
    from discord.ext import commands

    from bot import COGS
    from database import Database

    errors: list[str] = []
    db_path = Path(tempfile.mkdtemp()) / "startup.sqlite3"
    db = Database(str(db_path))
    try:
        await db.connect()
    except Exception as exc:
        errors.append(f"database connect: {exc}")
        return _fail(errors)

    cursor = await db.conn.execute("PRAGMA table_info(user_drug_stats)")
    drug_stat_cols = {row[1] for row in await cursor.fetchall()}
    if "units_harvested" not in drug_stat_cols:
        errors.append("user_drug_stats.units_harvested missing after migrations")

    cursor = await db.conn.execute("PRAGMA table_info(crew_bank_raid_cooldowns)")
    raid_cols = {row[1] for row in await cursor.fetchall()}
    for col in (
        "last_drug_attack_at",
        "last_drug_defended_at",
        "last_business_attack_at",
        "last_business_defended_at",
    ):
        if col not in raid_cols:
            errors.append(f"crew_bank_raid_cooldowns.{col} missing after migrations")

    bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
    bot.db = db
    for ext in COGS:
        try:
            await bot.load_extension(ext)
        except Exception as exc:
            errors.append(f"{ext}: {exc}")

    top_level = len(list(bot.tree.get_commands()))
    if top_level > 100:
        errors.append(f"top-level slash commands={top_level} exceeds Discord limit of 100")

    await db.close()
    if errors:
        return _fail(errors)
    print(f"OK: database migrated, {len(COGS)} cogs loaded, {top_level} top-level slash commands")
    return 0


def _fail(errors: list[str]) -> int:
    print("Startup verification failed:", file=sys.stderr)
    for line in errors:
        print(f"  - {line}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
