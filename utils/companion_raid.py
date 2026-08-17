"""Guild boss raid companion auto-attack execution."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord

import config
from items import get_item
from utils.boss_adds import ADD_DISPLAY_NAMES, roll_add_loot
from utils.companion_combat import (
    CompanionStrikeResult,
    owner_attack_power_from_loadout,
    pick_companion_target,
)
from utils.companions import companion_display_name, companion_emoji, roll_companion_damage
from utils.discord_api import safe_channel_send
from utils.helpers import resolve_bot_announcement_channel

if TYPE_CHECKING:
    from cogs.boss import Boss

logger = logging.getLogger(__name__)


async def _owner_loadout(boss_cog: Boss, user_id: int, guild_id: int) -> Any:
    return await boss_cog._loadout(user_id, guild_id)


async def _maybe_revive_owner(db: Any, user_id: int, guild_id: int) -> bool:
    if not await db.is_downed(user_id, guild_id):
        return False
    await db.set_downed_until(user_id, guild_id, 0.0)
    return True


async def execute_companion_boss_strike(
    boss_cog: Boss,
    guild: discord.Guild,
    *,
    user_id: int,
    companion_id: str,
    evolution_tier: int,
    custom_name: str | None,
) -> CompanionStrikeResult | None:
    guild_id = guild.id
    boss = await boss_cog.bot.db.get_active_boss(guild_id)
    if boss is None or boss_cog.bot.db.boss_has_expired(boss):
        return None

    loadout = await _owner_loadout(boss_cog, user_id, guild_id)
    attack_power = owner_attack_power_from_loadout(loadout)
    damage, critical, verb = roll_companion_damage(
        companion_id,
        evolution_tier=evolution_tier,
        owner_attack_power=attack_power,
    )

    revived = await _maybe_revive_owner(boss_cog.bot.db, user_id, guild_id)
    updated = await boss_cog.bot.db.damage_boss(guild_id, user_id, damage)
    if updated is None:
        return None

    display = companion_display_name(companion_id, custom_name)
    emoji = companion_emoji(companion_id)
    target_name = str(boss["name"])

    if float(updated["hp"]) <= 0:
        await boss_cog._complete_boss_defeat(
            guild,
            interaction=None,
            killer_user_id=user_id,
        )

    return CompanionStrikeResult(
        user_id=user_id,
        companion_id=companion_id,
        display_name=display,
        emoji=emoji,
        damage=damage,
        critical=critical,
        verb=verb,
        target_kind="boss",
        target_name=target_name,
        revived_owner=revived,
    )


async def execute_companion_add_strike(
    boss_cog: Boss,
    guild: discord.Guild,
    *,
    user_id: int,
    companion_id: str,
    evolution_tier: int,
    custom_name: str | None,
    add_row: Any,
) -> CompanionStrikeResult | None:
    guild_id = guild.id
    loadout = await _owner_loadout(boss_cog, user_id, guild_id)
    attack_power = owner_attack_power_from_loadout(loadout)
    damage, critical, verb = roll_companion_damage(
        companion_id,
        evolution_tier=evolution_tier,
        owner_attack_power=attack_power,
    )

    revived = await _maybe_revive_owner(boss_cog.bot.db, user_id, guild_id)
    add_type = str(add_row["add_type"])
    add_name = ADD_DISPLAY_NAMES.get(add_type, add_type)
    new_hp, killed = await boss_cog.bot.db.damage_raid_add(
        int(add_row["add_id"]), guild_id, float(damage),
    )

    loot_note = ""
    if killed:
        boss = await boss_cog.bot.db.get_active_boss(guild_id)
        variant = str(boss["variant"]) if boss is not None else "default"
        drops = roll_add_loot(add_type, variant)
        drop_parts: list[str] = []
        for item_id, qty in drops:
            item = get_item(item_id)
            label = item.name if item is not None else item_id
            for _ in range(qty):
                await boss_cog.bot.db.grant_item(user_id, guild_id, item_id)
            drop_parts.append(f"**{qty}×** {label}")
        if drop_parts:
            loot_note = ", ".join(drop_parts)

    return CompanionStrikeResult(
        user_id=user_id,
        companion_id=companion_id,
        display_name=companion_display_name(companion_id, custom_name),
        emoji=companion_emoji(companion_id),
        damage=damage,
        critical=critical,
        verb=verb,
        target_kind="add",
        target_name=add_name,
        killed=killed,
        loot_note=loot_note,
        revived_owner=revived,
    )


async def run_companion_raid_tick(boss_cog: Boss, guild: discord.Guild) -> list[CompanionStrikeResult]:
    guild_id = guild.id
    boss = await boss_cog.bot.db.get_active_boss(guild_id)
    if boss is None or boss_cog.bot.db.boss_has_expired(boss):
        return []

    equipped_rows = await boss_cog.bot.db.list_guild_equipped_companions(guild_id)
    if not equipped_rows:
        return []

    adds = await boss_cog.bot.db.list_raid_adds(guild_id)
    has_adds = bool(adds)
    results: list[CompanionStrikeResult] = []

    for row in equipped_rows:
        user_id = int(row["user_id"])
        companion_id = str(row["companion_id"])
        evolution_tier = int(row["evolution_tier"])
        custom_name = row["custom_name"]
        if custom_name is not None:
            custom_name = str(custom_name)

        refreshed = await boss_cog.bot.db.refresh_companion_stamina(user_id, guild_id, companion_id)
        if refreshed is None:
            continue
        if int(refreshed["stamina"]) < config.COMPANION_STAMINA_PER_STRIKE:
            continue
        if not await boss_cog.bot.db.spend_companion_stamina(
            user_id, guild_id, companion_id, config.COMPANION_STAMINA_PER_STRIKE,
        ):
            continue

        target_kind = pick_companion_target(has_adds)
        strike: CompanionStrikeResult | None
        if target_kind == "add" and adds:
            strike = await execute_companion_add_strike(
                boss_cog,
                guild,
                user_id=user_id,
                companion_id=companion_id,
                evolution_tier=evolution_tier,
                custom_name=custom_name,
                add_row=adds[0],
            )
        else:
            strike = await execute_companion_boss_strike(
                boss_cog,
                guild,
                user_id=user_id,
                companion_id=companion_id,
                evolution_tier=evolution_tier,
                custom_name=custom_name,
            )
        if strike is not None:
            results.append(strike)

        boss = await boss_cog.bot.db.get_active_boss(guild_id)
        if boss is None or float(boss["hp"]) <= 0:
            break
        adds = await boss_cog.bot.db.list_raid_adds(guild_id)
        has_adds = bool(adds)

    return results


def strike_embed(strike: CompanionStrikeResult, member_name: str) -> discord.Embed:
    crit = " **CRIT!**" if strike.critical else ""
    revive = "\n🩹 **Auto-revived** their owner!" if strike.revived_owner else ""
    if strike.target_kind == "add":
        if strike.killed:
            desc = f"**Killed {strike.target_name}!**{revive}"
            if strike.loot_note:
                desc += f"\nLoot for owner: {strike.loot_note}"
        else:
            desc = f"Hit **{strike.target_name}** for **{strike.damage}** damage.{crit}{revive}"
    else:
        desc = (
            f"{strike.verb} **{strike.target_name}** for **{strike.damage}** damage.{crit}{revive}"
        )
    embed = discord.Embed(
        title=f"{strike.emoji} {strike.display_name} ({member_name})",
        description=desc,
        color=discord.Color.green() if strike.critical or strike.killed else discord.Color.teal(),
    )
    embed.set_footer(text="Henchling auto-attack")
    return embed


async def announce_companion_strikes(
    boss_cog: Boss,
    guild: discord.Guild,
    strikes: list[CompanionStrikeResult],
) -> None:
    if not strikes:
        return
    channel = await resolve_bot_announcement_channel(guild, boss_cog.bot.db)
    if channel is None:
        return
    for strike in strikes:
        member = guild.get_member(strike.user_id)
        member_name = member.display_name if member is not None else f"User {strike.user_id}"
        embed = strike_embed(strike, member_name)
        await safe_channel_send(channel, embed=embed)
