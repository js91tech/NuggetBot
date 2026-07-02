#!/usr/bin/env python3
"""Smoke-check: drug lab and business panels build without errors."""
from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _validate_view(view: object, label: str, errors: list[str]) -> None:
    import discord

    if not isinstance(view, discord.ui.View):
        errors.append(f"{label}: not a View")
        return
    try:
        rows = view.to_components()
    except Exception as exc:
        errors.append(f"{label}: to_components failed: {exc}")
        return
    if len(rows) > 5:
        errors.append(f"{label}: {len(rows)} rows exceeds Discord limit of 5")
    for row_index, row in enumerate(rows):
        if isinstance(row, dict):
            children = row.get("components", [])
            row_id = row.get("id", row_index)
        else:
            children = row.children
            row_id = row.id
        if len(children) > 5:
            errors.append(
                f"{label}: row {row_id} has {len(children)} components (max 5)",
            )


async def _main() -> int:
    import discord

    from database import Database
    from utils.business_ui import build_business_payload
    from utils.drug_ui import DrugLabView, build_lab_embed

    errors: list[str] = []
    db_path = Path(tempfile.mkdtemp()) / "panels.sqlite3"
    db = Database(str(db_path))
    await db.connect()
    await db.init_schema()

    guild_id = 42
    user_id = 7
    await db.ensure_user(user_id, guild_id)
    await db.credit_wallet(user_id, guild_id, 500_000.0, apply_bonuses=False)

    cog = MagicMock()
    cog.bot.db = db

    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.display_name = "PanelTester"
    member.guild.id = guild_id

    try:
        embed, banner = await build_lab_embed(cog, guild_id, user_id)
        if embed.image is None or not embed.image.url:
            errors.append("drug lab embed missing banner image")
        if banner.filename != "lab.png":
            errors.append(f"drug lab banner filename={banner.filename!r}")
        lab_view = await DrugLabView.build(cog, guild_id, user_id)
        _validate_view(lab_view, "drug_lab", errors)
    except Exception as exc:
        errors.append(f"drug lab panel: {exc}")

    err = await db.create_business(user_id, guild_id)
    if err is not None:
        errors.append(f"create_business: {err}")
    else:
        try:
            payload = await build_business_payload(cog, member, guild_id, user_id)
            if payload is None:
                errors.append("business payload is None after create")
            else:
                biz_embed, files, biz_view = payload
                if not files:
                    errors.append("business panel missing image files")
                _validate_view(biz_view, "business", errors)
        except Exception as exc:
            errors.append(f"business panel: {exc}")

        now = time.time()
        await db.conn.execute(
            """
            INSERT INTO business_attacks (
                guild_id, attacker_id, defender_id, action_type, penalty,
                started_at, ends_at, notify_expires_at, defended
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (guild_id, 99, user_id, "sabotage", 0.25, now, now + 3600, now + 1800),
        )
        await db.conn.commit()
        try:
            attack_payload = await build_business_payload(cog, member, guild_id, user_id)
            if attack_payload is None:
                errors.append("business under-attack payload is None")
            else:
                _validate_view(attack_payload[2], "business_under_attack", errors)
        except Exception as exc:
            errors.append(f"business under-attack panel: {exc}")

    from utils.drugs import DRUGS

    for drug in DRUGS:
        await db.conn.execute(
            """
            INSERT INTO drug_inventory (user_id, guild_id, drug_id, quantity)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, guild_id, drug_id) DO UPDATE SET quantity = excluded.quantity
            """,
            (user_id, guild_id, drug.drug_id, 50),
        )
    await db.conn.commit()
    try:
        large_embed, _ = await build_lab_embed(cog, guild_id, user_id)
        stash_field = next((f for f in large_embed.fields if f.name == "Stash"), None)
        if stash_field is None:
            errors.append("large stash embed missing Stash field")
        elif len(stash_field.value) > 1024:
            errors.append(f"stash field length {len(stash_field.value)} exceeds 1024")
        large_view = await DrugLabView.build(cog, guild_id, user_id)
        _validate_view(large_view, "drug_lab_large_stash", errors)
    except Exception as exc:
        errors.append(f"drug lab large stash: {exc}")

    await db.close()

    if errors:
        print("Panel verification failed:", file=sys.stderr)
        for line in errors:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print("OK: drug lab and business panels build cleanly (including under-attack + large stash)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
