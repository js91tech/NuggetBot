"""Street cop bust encounters — fight or flee when selling drugs."""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

import config
from utils.boss_art import boss_art_path
from utils.combat_engine import AttackContext, roll_player_damage
from utils.drugs import drug_by_id, format_street_sale_bonus
from utils.helpers import fmt_amount
from utils.police_bosses import (
    cop_display_name,
    roll_cop_counter_damage,
    roll_cop_crit,
    scaled_cop_stats,
)

if TYPE_CHECKING:
    from discord.ext import commands


@dataclass
class CopEncounterResult:
    embed: discord.Embed
    file: discord.File | None = None
    finished: bool = False
    lab_description: str | None = None
    record_sale_quest: bool = False
    error: str | None = None


def _cop_art_file(source_variant: str) -> discord.File | None:
    path = boss_art_path(source_variant)
    if path is None:
        return None
    return discord.File(str(path), filename="cop_boss.png")


async def build_cop_encounter_embed(encounter: dict[str, object]) -> tuple[discord.Embed, discord.File | None]:
    source_variant = str(encounter["source_variant"])
    cop_name = cop_display_name(source_variant)
    defn = drug_by_id(str(encounter["drug_id"]))
    product = defn.name if defn else str(encounter["drug_id"])
    qty = int(encounter["quantity"])
    cop_hp = float(encounter["cop_hp"])
    player_hp = float(encounter["player_hp"])
    player_max = float(encounter["player_max_hp"])
    stats = scaled_cop_stats(source_variant)

    embed = discord.Embed(
        title="🚨 Police bust!",
        description=(
            f"**{cop_name}** rolls up while you're moving **{qty} {product}**.\n"
            "Fight your way out or try to flee — losing means losing product, "
            f"and there's a **{int(config.DRUG_COP_ARREST_CHANCE * 100)}%** chance of "
            f"**{config.DRUG_COP_ARREST_SECONDS // 60} minutes** in lockup."
        ),
        color=discord.Color.dark_blue(),
    )
    embed.add_field(
        name="Officer",
        value=f"**{cop_name}** (`{source_variant}` tier)\nHP **{int(cop_hp)}**",
        inline=True,
    )
    embed.add_field(
        name="You",
        value=f"HP **{int(player_hp)}** / **{int(player_max)}**",
        inline=True,
    )
    embed.add_field(
        name="Heat",
        value=(
            f"Counter chance **{int(float(stats['counter_chance']) * 100)}%** · "
            f"Counter **{stats['counter_damage'][0]}–{stats['counter_damage'][1]}**"
        ),
        inline=False,
    )
    file = _cop_art_file(source_variant)
    if file is not None:
        embed.set_image(url="attachment://cop_boss.png")
    return embed, file


async def _maybe_instalock(db: object, user_id: int, guild_id: int) -> bool:
    if random.random() >= config.DRUG_COP_ARREST_CHANCE:
        return False
    until = time.time() + config.DRUG_COP_ARREST_SECONDS
    await db.set_arrested_until(user_id, guild_id, until)
    return True


async def execute_cop_flee(
    cog: commands.Cog,
    guild_id: int,
    user_id: int,
) -> CopEncounterResult:
    encounter = await cog.bot.db.get_drug_cop_encounter(user_id, guild_id)
    if encounter is None:
        return CopEncounterResult(
            embed=discord.Embed(description="No active bust."),
            error="No active bust.",
            finished=True,
        )
    source_variant = str(encounter["source_variant"])
    cop_name = cop_display_name(source_variant)
    defn = drug_by_id(str(encounter["drug_id"]))
    product = defn.name if defn else str(encounter["drug_id"])
    qty = int(encounter["quantity"])

    if random.random() < config.DRUG_COP_FLEE_SUCCESS_CHANCE:
        await cog.bot.db.clear_drug_cop_encounter(user_id, guild_id)
        embed, file = await build_cop_encounter_embed(encounter)
        embed.description = (
            f"You ditched **{cop_name}** and slipped away with your **{qty} {product}** intact."
        )
        embed.color = discord.Color.green()
        return CopEncounterResult(
            embed=embed,
            file=file,
            finished=True,
            lab_description=f"🏃 Escaped **{cop_name}** — stash still yours.",
        )

    arrested = False
    lost = 0
    async with cog.bot.db._write_lock:
        lost = await cog.bot.db._lose_drug_street_product(
            user_id,
            guild_id,
            str(encounter["drug_id"]),
            str(encounter["inventory_key"]),
            qty,
            lost_fraction=config.DRUG_RAID_LOSS_FRACTION,
            txn_type="cop_flee_fail",
        )
        await cog.bot.db.conn.execute(
            "DELETE FROM drug_cop_encounters WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        await cog.bot.db.conn.commit()
    arrested = await _maybe_instalock(cog.bot.db, user_id, guild_id)

    jail_note = (
        f" **Jailed for {config.DRUG_COP_ARREST_SECONDS // 60} minutes!**"
        if arrested
        else ""
    )
    embed, file = await build_cop_encounter_embed(encounter)
    embed.description = (
        f"**{cop_name}** caught you fleeing! Lost **{lost} {product}**.{jail_note}"
    )
    embed.color = discord.Color.red()
    return CopEncounterResult(
        embed=embed,
        file=file,
        finished=True,
        lab_description=embed.description,
    )


async def execute_cop_fight(
    cog: commands.Cog,
    guild_id: int,
    user_id: int,
) -> CopEncounterResult:
    encounter = await cog.bot.db.get_drug_cop_encounter(user_id, guild_id)
    if encounter is None:
        return CopEncounterResult(
            embed=discord.Embed(description="No active bust."),
            error="No active bust.",
            finished=True,
        )

    source_variant = str(encounter["source_variant"])
    cop_name = cop_display_name(source_variant)
    defn = drug_by_id(str(encounter["drug_id"]))
    product = defn.name if defn else str(encounter["drug_id"])
    qty = int(encounter["quantity"])
    cop_hp = float(encounter["cop_hp"])
    player_hp = float(encounter["player_hp"])
    player_max = float(encounter["player_max_hp"])
    sale_mult = float(encounter["sale_mult"])

    loadout = await cog.bot.db.get_combat_loadout(user_id, guild_id)
    progress = await cog.bot.db.get_user_progress(user_id, guild_id)
    drug_buff = await cog.bot.db.peek_pending_drug_buff(user_id, guild_id)
    ctx = AttackContext(prestige_level=int(progress["prestige_level"]))
    if drug_buff is not None and float(drug_buff["boss_mult"]) > 1.0:
        ctx = AttackContext(
            prestige_level=int(progress["prestige_level"]),
            damage_mult=float(drug_buff["boss_mult"]),
        )
    damage, critical, verb = roll_player_damage(
        loadout.primary,
        off_hand=loadout.off_hand,
        ctx=ctx,
        accessory_bonuses=loadout.accessory_bonuses,
    )
    cop_hp = max(0.0, cop_hp - damage)
    lines = [f"You **{verb}** **{cop_name}** for **{damage}** damage."]
    if critical:
        lines[-1] += " **CRIT!**"

    if cop_hp <= 0:
        async with cog.bot.db._write_lock:
            total = await cog.bot.db._finalize_drug_street_sale(
                user_id,
                guild_id,
                str(encounter["drug_id"]),
                str(encounter["inventory_key"]),
                qty,
                sale_mult=sale_mult,
            )
            await cog.bot.db.conn.execute(
                "DELETE FROM drug_cop_encounters WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            await cog.bot.db.conn.commit()
        bonus = format_street_sale_bonus(
            sale_mult,
            reputation_level=int(encounter["reputation_level"]),
            influence_pct=float(encounter["influence_pct"]),
        )
        embed, file = await build_cop_encounter_embed(encounter)
        embed.description = (
            f"**{cop_name}** goes down! You unload **{qty} {product}** for "
            f"**{fmt_amount(total)}**{bonus}."
        )
        embed.color = discord.Color.green()
        return CopEncounterResult(
            embed=embed,
            file=file,
            finished=True,
            lab_description=embed.description,
            record_sale_quest=True,
        )

    stats = scaled_cop_stats(source_variant)
    counter_lines: list[str] = []
    if random.random() < float(stats["counter_chance"]):
        hp_ratio = cop_hp / max(1.0, float(encounter["cop_hp"]))
        counter = roll_cop_counter_damage(source_variant, hp_ratio=hp_ratio)
        counter = max(1, int(round(counter * config.DUNGEON_PLAYER_DAMAGE_TAKEN_MULT)))
        if roll_cop_crit(source_variant):
            counter = int(round(counter * config.PLAYER_ATTACK_CRIT_MULTIPLIER))
            counter_lines.append(f"**{cop_name}** lands a **critical** hit for **{counter}**!")
        else:
            counter_lines.append(f"**{cop_name}** hits back for **{counter}**.")
        player_hp = max(0.0, player_hp - counter)
    else:
        counter_lines.append(f"**{cop_name}** misses the return fire.")

    if player_hp <= 0:
        lost = 0
        async with cog.bot.db._write_lock:
            lost = await cog.bot.db._lose_drug_street_product(
                user_id,
                guild_id,
                str(encounter["drug_id"]),
                str(encounter["inventory_key"]),
                qty,
                txn_type="cop_fight_loss",
            )
            await cog.bot.db.conn.execute(
                "DELETE FROM drug_cop_encounters WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            await cog.bot.db.conn.commit()
        arrested = await _maybe_instalock(cog.bot.db, user_id, guild_id)
        jail_note = (
            f" **Jailed for {config.DRUG_COP_ARREST_SECONDS // 60} minutes!**"
            if arrested
            else ""
        )
        embed, file = await build_cop_encounter_embed(encounter)
        embed.description = "\n".join(lines + counter_lines) + (
            f"\nYou go down! Lost **{lost} {product}**.{jail_note}"
        )
        embed.color = discord.Color.red()
        return CopEncounterResult(
            embed=embed,
            file=file,
            finished=True,
            lab_description=embed.description,
        )

    await cog.bot.db.update_drug_cop_encounter_hp(
        user_id, guild_id, cop_hp=cop_hp, player_hp=player_hp,
    )
    encounter["cop_hp"] = cop_hp
    encounter["player_hp"] = player_hp
    embed, file = await build_cop_encounter_embed(encounter)
    embed.description = "\n".join(lines + counter_lines)
    return CopEncounterResult(embed=embed, file=file, finished=False)
