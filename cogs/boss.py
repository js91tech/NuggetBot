from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from items import (
    BOSS_SLAYER_BLADE,
    BOSS_SLAYER_MAIL,
    BOSS_WEAK_ITEMS,
    ShopItem,
    armor_mitigation_percent,
    get_item,
)
from utils.discord_api import safe_channel_send, safe_interaction_send
from utils.helpers import fmt_amount, guild_only_message, resolve_bot_announcement_channel

BOSS_NAME = "Hannah"
COUNTER_HP_BONUS = 0.30
COUNTER_MULTI_SECOND = 0.65
COUNTER_MULTI_THIRD = 0.55


class Boss(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._boss_finish_lock = asyncio.Lock()
        self.auto_spawn.start()
        self.passive_boss_decay_tick.start()

    def cog_unload(self) -> None:
        self.auto_spawn.cancel()
        self.passive_boss_decay_tick.cancel()

    async def _boss_hp(self, guild_id: int, variant: str) -> float:
        circulation = await self.bot.db.total_circulation(guild_id)
        scale_factor = await self.bot.db.get_config_value(guild_id, "boss_health_scale_factor")
        scaled_hp = max(config.BOSS_MIN_HP, circulation * scale_factor)
        base_hp = min(config.BOSS_HP_CAP, scaled_hp)
        return base_hp * float(config.BOSS_VARIANTS[variant]["multiplier"])

    async def _spawn_boss(self, guild_id: int, variant: str) -> float:
        hp = await self._boss_hp(guild_id, variant)
        await self.bot.db.replace_boss(guild_id, BOSS_NAME, variant, hp)
        return hp

    @staticmethod
    def _weighted_random_damage_user(rows: list[Any]) -> int | None:
        if not rows:
            return None
        total = sum(float(r["damage"]) for r in rows)
        if total <= 0:
            return int(rows[0]["user_id"])
        pick = random.uniform(0, total)
        acc = 0.0
        for row in rows:
            acc += float(row["damage"])
            if pick <= acc:
                return int(row["user_id"])
        return int(rows[-1]["user_id"])

    async def _roll_boss_loot(self, guild_id: int, rows: list[Any]) -> list[tuple[int, ShopItem]]:
        if not rows:
            return []
        granted: list[tuple[int, ShopItem]] = []
        if random.random() < config.BOSS_INFERIOR_DROP_CHANCE:
            uid = Boss._weighted_random_damage_user(rows)
            if uid is not None:
                drop = random.choice(BOSS_WEAK_ITEMS)
                await self.bot.db.grant_item(uid, guild_id, drop.id)
                granted.append((uid, drop))
        if random.random() < config.BOSS_EPIC_DROP_CHANCE:
            uid = Boss._weighted_random_damage_user(rows)
            if uid is not None:
                epic = random.choice((BOSS_SLAYER_BLADE, BOSS_SLAYER_MAIL))
                await self.bot.db.grant_item(uid, guild_id, epic.id)
                granted.append((uid, epic))
        return granted

    async def _send_boss_spawn_embed(
        self,
        guild: discord.Guild,
        *,
        variant: str,
        hp: float,
        summoned: bool,
    ) -> None:
        channel = await resolve_bot_announcement_channel(guild, self.bot.db)
        if channel is None:
            logging.warning("Boss spawn embed skipped: no channel in guild %s", guild.id)
            return
        title = "Boss summoned!" if summoned else "Boss raid incoming!"
        desc = f"A **{variant}** **{BOSS_NAME}** crashes the party—time to rally the raid!"
        embed = discord.Embed(
            title=title,
            description=desc,
            color=discord.Color.dark_red(),
        )
        embed.add_field(name="Health", value=f"**{fmt_amount(hp)}** HP", inline=True)
        embed.add_field(
            name="Battle room",
            value=channel.mention,
            inline=True,
        )
        threat = config.BOSS_VARIANTS[variant]["threat"]
        embed.add_field(name="Threat tier", value=str(threat), inline=True)
        embed.add_field(
            name="Fight back",
            value="`/attack` to deal damage · `/boss` for status · `/heal` for downed allies",
            inline=False,
        )
        decay_pct = int(round(config.BOSS_PASSIVE_HP_DECAY_FRACTION_PER_MINUTE * 100))
        embed.set_footer(
            text=(
                f"Bosses lose {decay_pct}% of their max HP each minute from battle fatigue—even "
                "if nobody is attacking."
            )
        )
        gate = getattr(self.bot, "outbound_gate", None)
        sent = await safe_channel_send(
            channel,
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
            gate=gate,
        )
        if sent is None:
            logging.warning("Boss spawn embed not sent in guild %s", guild.id)

    async def _send_boss_defeat_embed(
        self,
        guild: discord.Guild,
        *,
        variant: str,
        reward_lines: list[str],
        gear_lines: list[str],
        summary: str,
    ) -> None:
        channel = await resolve_bot_announcement_channel(guild, self.bot.db)
        if channel is None:
            logging.warning("Boss defeat embed skipped: no channel in guild %s", guild.id)
            return
        embed = discord.Embed(
            title=f"{variant.title()} {BOSS_NAME} is down!",
            description=summary,
            color=discord.Color.gold(),
        )
        if reward_lines:
            body = "\n".join(reward_lines[:12])
            if len(reward_lines) > 12:
                body += f"\n...+{len(reward_lines) - 12} more"
            embed.add_field(
                name=f"{config.CURRENCY_NAME.title()} split (by damage share)",
                value=body,
                inline=False,
            )
        if gear_lines:
            embed.add_field(
                name="Gear dropped",
                value="\n".join(gear_lines[:12]),
                inline=False,
            )
        gate = getattr(self.bot, "outbound_gate", None)
        sent = await safe_channel_send(
            channel,
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
            gate=gate,
        )
        if sent is None:
            logging.warning("Boss defeat embed not sent in guild %s", guild.id)

    def _display_name(self, guild: discord.Guild, user_id: int) -> str:
        member = guild.get_member(user_id)
        return member.display_name if member else f"User {user_id}"

    async def _complete_boss_defeat(
        self,
        guild: discord.Guild,
        *,
        interaction: discord.Interaction | None,
        killer_user_id: int | None,
    ) -> None:
        async with self._boss_finish_lock:
            await self._complete_boss_defeat_impl(
                guild,
                interaction=interaction,
                killer_user_id=killer_user_id,
            )

    async def _complete_boss_defeat_impl(
        self,
        guild: discord.Guild,
        *,
        interaction: discord.Interaction | None,
        killer_user_id: int | None,
    ) -> None:
        guild_id = guild.id
        boss = await self.bot.db.get_active_boss(guild_id)
        if boss is None:
            if interaction is not None and not interaction.response.is_done():
                await interaction.response.send_message(
                    "The boss was already defeated.",
                    ephemeral=True,
                )
            return
        if float(boss["hp"]) > 0:
            return

        variant = str(boss["variant"])
        rows = await self.bot.db.list_boss_damage(guild_id)
        total_damage = sum(float(row["damage"]) for row in rows)
        max_hp = float(boss["max_hp"])

        if total_damage <= 0:
            await self.bot.db.clear_boss(guild_id)
            summary = (
                f"{BOSS_NAME} collapsed from exhaustion with **no recorded strikes**. "
                "No nuggets or gear were awarded."
            )
            await self._send_boss_defeat_embed(
                guild,
                variant=variant,
                reward_lines=[],
                gear_lines=[],
                summary=summary,
            )
            if interaction is not None and not interaction.response.is_done():
                await interaction.response.send_message(
                    "The boss melted from passive fatigue before anyone attacked—no payouts.",
                    ephemeral=True,
                )
            return

        reward_lines = []
        for row in rows:
            user_id = int(row["user_id"])
            reward = max_hp * (float(row["damage"]) / total_damage)
            await self.bot.db.credit_wallet(user_id, guild_id, reward)
            name = self._display_name(guild, user_id)
            reward_lines.append(f"{name}: {fmt_amount(reward)}")

        loot_rows = await self._roll_boss_loot(guild_id, rows)
        gear_lines = [
            f"**{self._display_name(guild, uid)}** · **{item.name}** (`{item.id}`)"
            for uid, item in loot_rows
        ]

        await self.bot.db.clear_boss(guild_id)

        if killer_user_id is not None:
            summary = f"The killing blow goes to <@{killer_user_id}>."
        else:
            summary = (
                "**Battle fatigue** finished what the raid started—she could not stall forever."
            )

        await self._send_boss_defeat_embed(
            guild,
            variant=variant,
            reward_lines=reward_lines,
            gear_lines=gear_lines,
            summary=summary,
        )

        if interaction is not None and not interaction.response.is_done():
            channel = await resolve_bot_announcement_channel(guild, self.bot.db)
            place = channel.mention if channel is not None else "the bot channel"
            if killer_user_id is not None:
                msg = (
                    f"{interaction.user.mention} landed the final blow! "
                    f"Payouts and drops are in {place}."
                )
            else:
                msg = (
                    f"{BOSS_NAME} finally collapsed from battle fatigue. "
                    f"Payouts and drops are in {place}."
                )
            await safe_interaction_send(
                interaction,
                msg,
                allowed_mentions=discord.AllowedMentions.none(),
                gate=getattr(self.bot, "outbound_gate", None),
            )

    async def _gear(self, user_id: int, guild_id: int) -> tuple[ShopItem | None, ShopItem | None]:
        equipment = await self.bot.db.get_equipment(user_id, guild_id)
        weapon = get_item(equipment["weapon"]) if "weapon" in equipment else None
        armor = get_item(equipment["armor"]) if "armor" in equipment else None
        return weapon, armor

    async def _max_hp(self, user_id: int, guild_id: int) -> float:
        _, armor = await self._gear(user_id, guild_id)
        return float(config.PLAYER_BASE_HP + (armor.hp_bonus if armor is not None else 0))

    @staticmethod
    def _attack_roll(weapon: ShopItem | None) -> tuple[int, bool, str]:
        if weapon is None:
            damage = random.randint(config.BOSS_UNARMED_MIN, config.BOSS_UNARMED_MAX)
            verb = "hits"
            crit_chance = config.PLAYER_BASE_CRIT_CHANCE
        else:
            damage = random.randint(config.BOSS_ATTACK_BONUS_MIN, config.BOSS_ATTACK_BONUS_MAX) + weapon.power
            verb = random.choice(weapon.verbs or ("strikes",))
            crit_chance = config.PLAYER_BASE_CRIT_CHANCE + weapon.crit_chance
        critical = random.random() < crit_chance
        if critical:
            damage = int(damage * config.PLAYER_ATTACK_CRIT_MULTIPLIER)
        return damage, critical, verb

    @staticmethod
    def _apply_armor_mitigation(raw_damage: int, armor: ShopItem | None) -> tuple[int, int]:
        if armor is None:
            return raw_damage, 0
        mitigated = int(raw_damage * armor.power / (armor.power + 100))
        return max(1, raw_damage - mitigated), mitigated

    @staticmethod
    def _counter_roll(variant: str, armor: ShopItem | None) -> tuple[int, int, bool, str]:
        variant_config = config.BOSS_VARIANTS[variant]
        low, high = variant_config["counter_damage"]
        raw_damage = random.randint(int(low), int(high))
        critical = random.random() < float(variant_config["crit_chance"])
        if critical:
            raw_damage = int(raw_damage * 1.75)
        damage, mitigated = Boss._apply_armor_mitigation(raw_damage, armor)
        moves = {
            "normal": ("backhands", "shoulder-checks", "bonks"),
            "enraged": ("rage-smashes", "uppercuts", "body-slams"),
            "shadow": ("void-crushes", "shadow-rakes", "ambushes"),
            "celestial": ("meteor-crits", "starfalls onto", "supernovas"),
        }
        return damage, mitigated, critical, random.choice(moves[variant])

    @staticmethod
    def _counter_chance(variant: str, hp: float, max_hp: float) -> float:
        base = float(config.BOSS_VARIANTS[variant]["counter_chance"])
        if max_hp <= 0:
            return base
        desperation = 1.0 - (hp / max_hp)
        return min(0.95, base + desperation * COUNTER_HP_BONUS)

    @staticmethod
    def _counter_target_count(pool_size: int, hp: float, max_hp: float) -> int:
        if pool_size <= 1:
            return 1
        desperation = 0.0 if max_hp <= 0 else 1.0 - (hp / max_hp)
        count = 1
        if pool_size >= 2 and random.random() < desperation * COUNTER_MULTI_SECOND:
            count = 2
        if pool_size >= 3 and count >= 2 and random.random() < desperation * COUNTER_MULTI_THIRD:
            count = 3
        return min(count, pool_size)

    @tasks.loop(seconds=config.BOSS_AUTO_SPAWN_SECONDS)
    async def auto_spawn(self) -> None:
        for guild in self.bot.guilds:
            if await self.bot.db.get_active_boss(guild.id) is not None:
                continue
            variant = random.choice(tuple(config.BOSS_VARIANTS))
            hp = await self._spawn_boss(guild.id, variant)
            await self._send_boss_spawn_embed(guild, variant=variant, hp=hp, summoned=False)

    @auto_spawn.before_loop
    async def before_auto_spawn(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=config.BOSS_PASSIVE_DECAY_TICK_SECONDS)
    async def passive_boss_decay_tick(self) -> None:
        guild_ids = await self.bot.db.list_active_boss_guild_ids()
        if not guild_ids:
            return
        pause = config.BACKGROUND_GUILD_PAUSE_SECONDS
        for guild_id in guild_ids:
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            boss = await self.bot.db.apply_boss_passive_decay(guild_id)
            if boss is None:
                continue
            if float(boss["hp"]) > 0:
                continue
            await self._complete_boss_defeat(guild, interaction=None, killer_user_id=None)
            if pause > 0:
                await asyncio.sleep(pause)

    @passive_boss_decay_tick.before_loop
    async def before_passive_boss_decay_tick(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="summon", description="Admin only: force-spawn a boss.")
    @app_commands.describe(variant="Boss variant")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def summon(self, interaction: discord.Interaction, variant: str = "normal") -> None:
        if interaction.guild_id is None or interaction.guild is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        normalized = variant.lower().strip()
        if normalized not in config.BOSS_VARIANTS:
            choices = ", ".join(config.BOSS_VARIANTS)
            await interaction.response.send_message(
                f"Unknown variant. Choose one of: {choices}.",
                ephemeral=True,
            )
            return

        hp = await self._spawn_boss(interaction.guild_id, normalized)
        await interaction.response.send_message(
            f"Summoned a {normalized} {BOSS_NAME} with {fmt_amount(hp)} HP."
        )
        await self._send_boss_spawn_embed(
            interaction.guild,
            variant=normalized,
            hp=hp,
            summoned=True,
        )

    @app_commands.command(name="boss", description="Check boss status.")
    @app_commands.guild_only()
    async def boss(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or interaction.guild is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        boss_row = await self.bot.db.apply_boss_passive_decay(interaction.guild_id)
        if boss_row is None:
            await interaction.response.send_message("No boss is active right now.")
            return

        if float(boss_row["hp"]) <= 0:
            await self._complete_boss_defeat(
                interaction.guild,
                interaction=interaction,
                killer_user_id=None,
            )
            return

        await interaction.response.send_message(
            f"{boss_row['variant'].title()} {boss_row['name']}: "
            f"{fmt_amount(float(boss_row['hp']))}/{fmt_amount(float(boss_row['max_hp']))} HP"
        )

    @app_commands.command(name="attack", description="Attack the active boss.")
    @app_commands.guild_only()
    async def attack(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or interaction.guild is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if await self.bot.db.is_restricted(interaction.user.id, interaction.guild_id):
            await interaction.response.send_message("You cannot attack right now.", ephemeral=True)
            return

        boss = await self.bot.db.get_active_boss(interaction.guild_id)
        if boss is None:
            await interaction.response.send_message("No boss is active right now.", ephemeral=True)
            return

        weapon, _ = await self._gear(interaction.user.id, interaction.guild_id)
        damage, attack_critical, attack_verb = self._attack_roll(weapon)
        updated = await self.bot.db.damage_boss(interaction.guild_id, interaction.user.id, damage)
        if updated is None:
            await interaction.response.send_message("No boss is active right now.", ephemeral=True)
            return

        if float(updated["hp"]) <= 0:
            await self._complete_boss_defeat(
                interaction.guild,
                interaction=interaction,
                killer_user_id=interaction.user.id,
            )
            return

        counter_text = await self._maybe_counterattack(interaction.guild_id, updated)

        weapon_text = f" with **{weapon.name}**" if weapon is not None else ""
        crit_text = " **Critical hit!**" if attack_critical else ""
        await interaction.response.send_message(
            f"{interaction.user.mention} {attack_verb} {BOSS_NAME}{weapon_text} "
            f"for {damage} damage.{crit_text} "
            f"HP: {fmt_amount(float(updated['hp']))}.{counter_text}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _maybe_counterattack(self, guild_id: int, boss_row: Any) -> str:
        variant = str(boss_row["variant"])
        hp = float(boss_row["hp"])
        max_hp = float(boss_row["max_hp"])
        if random.random() >= self._counter_chance(variant, hp, max_hp):
            return ""

        damage_rows = await self.bot.db.list_boss_damage(guild_id)
        if not damage_rows:
            return ""

        attacker_ids = list({int(row["user_id"]) for row in damage_rows})
        target_count = self._counter_target_count(len(attacker_ids), hp, max_hp)
        victims = random.sample(attacker_ids, target_count)
        parts = [
            await self._counterattack_text(guild_id, victim_id, variant) for victim_id in victims
        ]
        return "".join(parts)

    async def _counterattack_text(self, guild_id: int, victim_id: int, variant: str) -> str:
        _, armor = await self._gear(victim_id, guild_id)
        max_hp = await self._max_hp(victim_id, guild_id)
        await self.bot.db.sync_combat_hp(victim_id, guild_id, max_hp)
        damage, mitigated, critical, move = self._counter_roll(variant, armor)
        hp, max_hp = await self.bot.db.damage_player(victim_id, guild_id, damage, max_hp)
        armor_text = ""
        if armor is not None and mitigated > 0:
            pct = armor_mitigation_percent(armor.power)
            armor_text = f" {armor.name} mitigates {mitigated} ({pct}%)."
        crit_text = " Critical blow!" if critical else ""
        threat = int(config.BOSS_VARIANTS[variant]["threat"])
        if hp <= 0:
            downed_seconds = await self.bot.db.get_config_value(guild_id, "boss_downed_seconds")
            await self.bot.db.set_downed_until(victim_id, guild_id, time.time() + downed_seconds)
            return (
                f"\nThreat {threat} {BOSS_NAME} {move} <@{victim_id}> for {damage} damage."
                f"{crit_text}{armor_text} They are downed!"
            )
        return (
            f"\nThreat {threat} {BOSS_NAME} {move} <@{victim_id}> for {damage} damage."
            f"{crit_text}{armor_text} HP: {int(hp)}/{int(max_hp)}."
        )

    @app_commands.command(name="heal", description="Revive a downed teammate.")
    @app_commands.describe(target="Downed user to heal")
    @app_commands.guild_only()
    async def heal(self, interaction: discord.Interaction, target: discord.Member) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if target.bot:
            await interaction.response.send_message("Bots do not need healing.", ephemeral=True)
            return
        if not await self.bot.db.is_downed(target.id, interaction.guild_id):
            await interaction.response.send_message("That user is not downed.", ephemeral=True)
            return

        await self.bot.db.set_downed_until(target.id, interaction.guild_id, 0)
        await self.bot.db.restore_player_hp(
            target.id,
            interaction.guild_id,
            await self._max_hp(target.id, interaction.guild_id),
        )
        await self.bot.db.record_heal(interaction.guild_id, interaction.user.id, target.id)
        await interaction.response.send_message(
            f"{interaction.user.mention} revived {target.mention}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Boss(bot))
