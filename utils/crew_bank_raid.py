"""Automated crew-vs-crew treasury raids resolved through duel combat."""
from __future__ import annotations

import dataclasses
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import discord

import config
from utils.character_attributes import combat_bonuses_from_attributes
from utils.duel_combat import DuelFighter, DuelStrike, fighter_from_loadout, format_strike_line, simulate_duel
from utils.trap_bombs import TRAP_BOMB_ITEM_ID

if TYPE_CHECKING:
    from database import Database


@dataclass(frozen=True)
class CrewBankRaidBout:
    attacker_id: int
    defender_id: int
    attacker_name: str
    defender_name: str
    winner_id: int
    attacker_slot: int
    defender_slot: int
    strikes: tuple[DuelStrike, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CrewBankRaidResult:
    attacker_won: bool
    bouts: tuple[CrewBankRaidBout, ...]
    defender_order: tuple[int, ...]
    attackers_used: int
    defenders_defeated: int


def build_defender_order(roster_ids: list[int], rng: random.Random | None = None) -> list[int]:
    """First defender is random; remaining roster follows join order."""
    if not roster_ids:
        return []
    source = rng or random.Random()
    first = source.choice(roster_ids)
    return [first, *[uid for uid in roster_ids if uid != first]]


def fresh_fighter(fighter: DuelFighter) -> DuelFighter:
    """Reset transient duel state so each raid bout starts clean."""
    return dataclasses.replace(
        fighter,
        hp=fighter.max_hp,
        spell_offense_used=False,
        spell_defense_used=False,
        consumable_boost_used=False,
    )


def simulate_crew_bank_raid(
    attackers: list[DuelFighter],
    defenders: list[DuelFighter],
) -> CrewBankRaidResult:
    """Chain 1v1 duels until all defenders fall or attackers are wiped."""
    if not attackers or not defenders:
        return CrewBankRaidResult(
            attacker_won=False,
            bouts=tuple(),
            defender_order=tuple(d.user_id for d in defenders),
            attackers_used=0,
            defenders_defeated=0,
        )

    bouts: list[CrewBankRaidBout] = []
    attacker_index = 0
    defender_index = 0

    while attacker_index < len(attackers) and defender_index < len(defenders):
        attacker = fresh_fighter(attackers[attacker_index])
        defender = fresh_fighter(defenders[defender_index])
        duel = simulate_duel(attacker, defender)
        bouts.append(
            CrewBankRaidBout(
                attacker_id=attacker.user_id,
                defender_id=defender.user_id,
                attacker_name=attacker.display_name,
                defender_name=defender.display_name,
                winner_id=duel.winner_id,
                attacker_slot=attacker_index,
                defender_slot=defender_index,
                strikes=tuple(duel.strikes),
            ),
        )
        if duel.winner_id == attacker.user_id:
            defender_index += 1
        else:
            attacker_index += 1

    return CrewBankRaidResult(
        attacker_won=defender_index >= len(defenders),
        bouts=tuple(bouts),
        defender_order=tuple(d.user_id for d in defenders),
        attackers_used=attacker_index + (1 if defender_index >= len(defenders) else 0),
        defenders_defeated=defender_index,
    )


async def load_duel_fighter(
    db: Database,
    guild: discord.Guild,
    user_id: int,
) -> DuelFighter:
    """Build a duel-ready fighter from persisted gear and progression."""
    member = guild.get_member(user_id)
    display_name = member.display_name if member is not None else f"User {user_id}"
    loadout = await db.get_combat_loadout(user_id, guild.id)
    progress = await db.get_user_progress(user_id, guild.id)
    await db.ensure_jester_class(user_id, guild.id)
    class_id = await db.get_class_id(user_id, guild.id)
    aspect_bonuses = await db.get_equipped_aspect_bonuses(user_id, guild.id)
    attrs = await db.get_character_attributes(user_id, guild.id)
    attr_bonuses = combat_bonuses_from_attributes(attrs)
    trap_bombs = await db.get_inventory_quantity(user_id, guild.id, TRAP_BOMB_ITEM_ID)
    return fighter_from_loadout(
        user_id,
        display_name,
        loadout,
        prestige_level=int(progress["prestige_level"]),
        class_id=class_id,
        aspect_bonuses=aspect_bonuses,
        attr_bonuses=attr_bonuses,
        trap_bomb_count=trap_bombs,
    )


def format_bout_summary(bout: CrewBankRaidBout, fighters: dict[int, DuelFighter]) -> str:
    winner = fighters[bout.winner_id]
    loser_id = bout.defender_id if bout.winner_id == bout.attacker_id else bout.attacker_id
    loser = fighters[loser_id]
    header = (
        f"**{bout.attacker_name}** vs **{bout.defender_name}** — "
        f"**{winner.display_name}** wins"
    )
    strike_lines = [format_strike_line(strike, fighters) for strike in bout.strikes[-3:]]
    if len(bout.strikes) > 3:
        strike_lines.insert(0, f"_…{len(bout.strikes) - 3} earlier strikes…_")
    footer = f"**{loser.display_name}** is down."
    return "\n".join([header, *strike_lines, footer])


def format_raid_embed(
    *,
    attacker_crew: str,
    defender_crew: str,
    result: CrewBankRaidResult,
    fighters: dict[int, DuelFighter],
    loot: float,
    defender_treasury_after: float,
) -> discord.Embed:
    if result.attacker_won:
        title = f"🏦 {attacker_crew} raided {defender_crew}'s bank!"
        color = discord.Color.green()
        summary = (
            f"**{attacker_crew}** cleared **{result.defenders_defeated}** defenders "
            f"and stole **{loot:,.2f}** nuggets (**{int(config.CREW_BANK_RAID_LOOT_FRACTION * 100)}%** "
            f"of the vault).\n"
            f"**{defender_crew}** treasury: **{defender_treasury_after:,.2f}** remaining."
        )
    else:
        title = f"🛡️ {defender_crew} repelled {attacker_crew}!"
        color = discord.Color.red()
        summary = (
            f"**{defender_crew}** held the line after **{result.defenders_defeated}** "
            f"defender{'s' if result.defenders_defeated != 1 else ''} fell.\n"
            f"**{result.attackers_used}** attacker{'s' if result.attackers_used != 1 else ''} "
            f"were stopped — the vault is safe."
        )

    embed = discord.Embed(title=title, description=summary, color=color)
    bout_text = "\n\n".join(format_bout_summary(bout, fighters) for bout in result.bouts)
    if len(bout_text) > 3900:
        bout_text = bout_text[:3897] + "..."
    embed.add_field(name="Battle log", value=bout_text or "_No fights recorded_", inline=False)
    return embed


# Back-compat alias — all crew raid types use the same duel chain.
simulate_crew_raid = simulate_crew_bank_raid


def format_drug_raid_embed(
    *,
    attacker_crew: str,
    defender_crew: str,
    result: CrewBankRaidResult,
    fighters: dict[int, DuelFighter],
    drug_id: str,
    drug_name: str,
    drug_emoji: str,
    loot_qty: int,
    defender_stash_after: int,
) -> discord.Embed:
    if result.attacker_won:
        title = f"🌿 {attacker_crew} hit {defender_crew}'s cartel stash!"
        color = discord.Color.green()
        summary = (
            f"**{attacker_crew}** cleared **{result.defenders_defeated}** defenders and stole "
            f"**{loot_qty}× {drug_emoji} {drug_name}**.\n"
            f"**{defender_crew}** stash remaining: **{defender_stash_after}** units of that product."
        )
    else:
        title = f"🛡️ {defender_crew} protected their cartel lab!"
        color = discord.Color.red()
        summary = (
            f"**{defender_crew}** held the line — their drug stash is safe."
        )

    embed = discord.Embed(title=title, description=summary, color=color)
    bout_text = "\n\n".join(format_bout_summary(bout, fighters) for bout in result.bouts)
    if len(bout_text) > 3900:
        bout_text = bout_text[:3897] + "..."
    embed.add_field(name="Battle log", value=bout_text or "_No fights recorded_", inline=False)
    return embed


def format_business_raid_embed(
    *,
    attacker_crew: str,
    defender_crew: str,
    result: CrewBankRaidResult,
    fighters: dict[int, DuelFighter],
    loot: float,
    defender_stored_after: float,
) -> discord.Embed:
    if result.attacker_won:
        title = f"🏢 {attacker_crew} raided {defender_crew}'s businesses!"
        color = discord.Color.green()
        summary = (
            f"**{attacker_crew}** cleared **{result.defenders_defeated}** defenders and stole "
            f"**{loot:,.2f}** nuggets (**{int(config.CREW_BUSINESS_RAID_LOOT_FRACTION * 100)}%** "
            f"of uncollected business income).\n"
            f"**{defender_crew}** vaults remaining: **{defender_stored_after:,.2f}** uncollected."
        )
    else:
        title = f"🛡️ {defender_crew} repelled {attacker_crew}!"
        color = discord.Color.red()
        summary = (
            f"**{defender_crew}** held the line — member business vaults are safe."
        )

    embed = discord.Embed(title=title, description=summary, color=color)
    bout_text = "\n\n".join(format_bout_summary(bout, fighters) for bout in result.bouts)
    if len(bout_text) > 3900:
        bout_text = bout_text[:3897] + "..."
    embed.add_field(name="Battle log", value=bout_text or "_No fights recorded_", inline=False)
    return embed
