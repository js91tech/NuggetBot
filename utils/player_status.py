from __future__ import annotations

import time
from typing import TYPE_CHECKING

import config
from utils.energy import energy_snapshot
from utils.helpers import fmt_amount

if TYPE_CHECKING:
    from database import Database


def format_countdown(seconds: float) -> str:
    """Human-readable time until ready (or 'Ready now')."""
    if seconds <= 0:
        return "Ready now"
    total = int(seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def energy_bar(current: int, cap: int, *, length: int = 10) -> str:
    if cap <= 0:
        return "░" * length
    filled = int(round((current / cap) * length))
    filled = max(0, min(length, filled))
    return "█" * filled + "░" * (length - filled)


async def build_status_fields(
    db: Database,
    *,
    user_id: int,
    guild_id: int,
    guild: object | None = None,
    at: float | None = None,
) -> list[tuple[str, str, bool]]:
    """Return embed fields as (name, value, inline) tuples."""
    now = time.time() if at is None else at
    user_row = await db.get_user(user_id, guild_id)
    balance = float(user_row["wallet"])

    daily_remaining = await db.daily_cooldown_remaining(
        user_id,
        guild_id,
        config.DAILY_COOLDOWN_SECONDS,
        at=now,
    )
    daily_line = (
        "Ready — use `/daily`"
        if daily_remaining is None
        else f"Ready in {format_countdown(daily_remaining)}"
    )

    char_row = await db.get_user_character(user_id, guild_id)
    regen_per_tick = int(await db.get_config_value(guild_id, "energy_regen_per_tick"))
    tick_seconds = int(
        await db.get_config_value(guild_id, "energy_regen_interval_seconds")
    )
    snap = energy_snapshot(
        int(char_row["energy"]),
        int(char_row["energy_cap"]),
        int(char_row["cap_upgrades"]),
        float(char_row["energy_updated_at"]),
        regen_per_tick=regen_per_tick,
        tick_seconds=tick_seconds,
        now=now,
    )
    regen_note = (
        f" · +{snap.regen_per_tick} in {format_countdown(snap.seconds_until_tick)}"
        if snap.seconds_until_tick > 0 and snap.current < snap.cap
        else ""
    )
    energy_line = (
        f"`{energy_bar(snap.current, snap.cap)}` **{snap.current}/{snap.cap}**{regen_note}"
    )

    restriction_lines: list[str] = []
    arrested_until = float(user_row["arrested_until"])
    if arrested_until > now:
        restriction_lines.append(
            f"Arrested — {format_countdown(arrested_until - now)} left"
        )
    downed_until = float(user_row["downed_until"])
    if downed_until > now:
        restriction_lines.append(
            f"Downed — {format_countdown(downed_until - now)} left"
        )
    restriction_line = (
        "\n".join(restriction_lines) if restriction_lines else "Clear"
    )

    pot = await db.get_hacker_pot(guild_id)
    if pot is None:
        virus_line = "No active virus in this server"
    else:
        holder_id = int(pot["holder_id"])
        seconds_left = max(0.0, float(pot["expires_at"]) - now)
        if holder_id == user_id:
            virus_line = f"You hold the virus — **{int(seconds_left)}s** left"
        else:
            holder_name = f"<@{holder_id}>"
            if guild is not None:
                member = getattr(guild, "get_member", lambda _id: None)(holder_id)
                if member is not None:
                    holder_name = member.display_name
            virus_line = f"Holder: **{holder_name}** · **{int(seconds_left)}s** left"

    hack_cooldown = await db.get_config_value(guild_id, "hack_cooldown_seconds")
    hack_remaining = await db.hack_cooldown_remaining(
        guild_id,
        user_id,
        float(hack_cooldown),
        at=now,
    )
    hack_line = (
        "`/hack` ready"
        if hack_remaining is None
        else f"`/hack` in {format_countdown(hack_remaining)}"
    )
    virus_line = f"{virus_line}\n{hack_line}"

    heist_cooldown = float(
        await db.get_config_value(guild_id, "heist_cooldown_seconds")
    )
    last_heist = float(user_row["last_heist"])
    heist_remaining = (last_heist + heist_cooldown) - now if last_heist > 0 else -1.0
    heist_line = (
        "`/heist` ready"
        if heist_remaining <= 0
        else f"`/heist` in {format_countdown(heist_remaining)}"
    )

    loss_fraction = await db.get_config_value(guild_id, "duel_loss_fraction")
    duel_cooldown = int(
        await db.get_config_value(guild_id, "duel_same_target_cooldown_seconds")
    )
    max_duels = int(await db.get_config_value(guild_id, "duel_max_attacks_per_hour"))
    aspect = await db.get_equipped_aspect_bonuses(user_id, guild_id)
    max_duels += aspect.extra_duels_per_hour
    duel_cooldown = int(round(duel_cooldown * aspect.duel_cooldown_mult))
    attacks_hour = await db.duel_attacks_in_last_hour(guild_id, user_id, at=now)
    duel_line = (
        f"**{attacks_hour}/{max_duels}** duels started this hour · "
        f"lose **{int(loss_fraction * 100)}%** wallet on defeat · "
        f"**{duel_cooldown // 60}m** cooldown vs same player"
    )

    fields: list[tuple[str, str, bool]] = [
        ("Wallet", fmt_amount(balance), True),
        ("Daily", daily_line, True),
        ("Energy", energy_line, False),
        ("Restrictions", restriction_line, True),
        ("Heist", heist_line, True),
        ("Virus", virus_line, False),
        ("Duels", duel_line, False),
    ]
    return fields
