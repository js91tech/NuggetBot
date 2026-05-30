from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from dataclasses import replace
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from items import (
    BOSS_SLAYER_BLADE,
    BOSS_SLAYER_MAIL,
    BOSS_WEAK_ITEMS,
    MYTHIC_RAID_BLADE,
    MYTHIC_RAID_MAIL,
    ShopItem,
    armor_mitigation_percent,
    get_item,
)
from utils.achievements import evaluate_unlocks, format_unlock_message
from utils.aspects import (
    instance_from_row,
    random_aspect_definition,
    roll_pct_for_threat,
)
from utils.avatars import build_avatar_embed_files, get_avatar
from utils.combat_engine import (
    attack_context_for_class,
    roll_jester_reflect,
    roll_player_damage,
)
from utils.discord_api import safe_channel_send, safe_interaction_send
from utils.gear_sets import SetBonus, detect_set_bonus
from utils.helpers import fmt_amount, guild_only_message, resolve_bot_announcement_channel
from utils.loadout import PlayerLoadout, off_hand_crit_bonus, off_hand_power_bonus, parse_loadout
from utils.quests import record_quest_event
from utils.skills import get_skill, spell_buff_from_skill
from utils.spell_effects import combat_state_from_spell
from utils.stats import hp_bar
from utils.summoner_penalty import (
    apply_summoner_attack_debuff,
    apply_summoner_counter_damage,
    boss_summoner_id,
    is_summoner_debuffed,
    summoner_defense_retention,
    summoner_penalty_summary,
)
from utils.mana import mana_bar
from utils.boss_ui import BossAttackResult, build_boss_fight_view, send_boss_fight_panel

BOSS_NAME = "Hannah"
BOSS_NAME_TOMASS = config.BOSS_NAME_TOMASS
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
        hp = base_hp * float(config.BOSS_VARIANTS[variant]["multiplier"])
        hp *= await self.bot.db.get_boss_hp_multiplier(guild_id)
        return hp

    async def _tomass_hp(self, guild_id: int, mirrored_variant: str) -> float:
        circulation = await self.bot.db.total_circulation(guild_id)
        scale_factor = await self.bot.db.get_config_value(guild_id, "boss_health_scale_factor")
        scaled_hp = max(config.BOSS_MIN_HP, circulation * scale_factor)
        base_hp = min(config.BOSS_HP_CAP, scaled_hp)
        mirror_mult = float(config.BOSS_VARIANTS[mirrored_variant]["multiplier"])
        strength = float(config.BOSS_VARIANTS["tomass"]["mirrored_strength_mult"])
        hp = base_hp * mirror_mult * strength
        hp *= await self.bot.db.get_boss_hp_multiplier(guild_id)
        return hp

    async def _spawn_boss(
        self,
        guild_id: int,
        variant: str,
        *,
        summoner_id: int | None = None,
        boss_name: str | None = None,
        mirrored_variant: str | None = None,
    ) -> float:
        name = boss_name or BOSS_NAME
        if variant == "tomass":
            mirror = mirrored_variant or "enraged"
            hp = await self._tomass_hp(guild_id, mirror)
            await self.bot.db.replace_boss(
                guild_id,
                BOSS_NAME_TOMASS,
                variant,
                hp,
                summoner_id=summoner_id,
                mirrored_variant=mirror,
            )
        else:
            hp = await self._boss_hp(guild_id, variant)
            await self.bot.db.replace_boss(
                guild_id,
                name,
                variant,
                hp,
                summoner_id=summoner_id,
                mirrored_variant=mirrored_variant,
            )
        return hp

    async def dashboard_spawn_boss(
        self,
        guild: discord.Guild,
        variant: str,
    ) -> tuple[float, str] | None:
        """Free dashboard spawn — any variant, no summoner penalty."""
        normalized = variant.lower().strip()
        if normalized not in config.BOSS_VARIANTS:
            return None
        hp = await self._spawn_boss(guild.id, normalized, summoner_id=None)
        boss_row = await self.bot.db.get_active_boss(guild.id)
        elem = str(boss_row["element"]) if boss_row else None
        await self._send_boss_spawn_embed(
            guild,
            variant=normalized,
            hp=hp,
            summoner_id=None,
            element=elem,
        )
        return hp, normalized

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
        drop_mult = await self.bot.db.get_drop_multiplier(guild_id)
        granted: list[tuple[int, ShopItem]] = []
        inferior_chance = await self.bot.db.get_config_value(guild_id, "boss_inferior_drop_chance")
        epic_chance = await self.bot.db.get_config_value(guild_id, "boss_epic_drop_chance")
        if random.random() < inferior_chance * drop_mult:
            uid = Boss._weighted_random_damage_user(rows)
            if uid is not None:
                drop = random.choice(BOSS_WEAK_ITEMS)
                await self.bot.db.grant_item(uid, guild_id, drop.id)
                granted.append((uid, drop))
        if random.random() < epic_chance * drop_mult:
            uid = Boss._weighted_random_damage_user(rows)
            if uid is not None:
                epic = random.choice((BOSS_SLAYER_BLADE, BOSS_SLAYER_MAIL))
                await self.bot.db.grant_item(uid, guild_id, epic.id)
                granted.append((uid, epic))
        return granted

    async def _roll_mythic_loot(
        self,
        guild_id: int,
        rows: list[Any],
        variant: str,
    ) -> list[tuple[int, ShopItem]]:
        if variant not in ("celestial", "mythic") or not rows:
            return []
        drop_mult = await self.bot.db.get_drop_multiplier(guild_id)
        mythic_chance = await self.bot.db.get_config_value(guild_id, "boss_mythic_drop_chance")
        if random.random() >= mythic_chance * drop_mult:
            return []
        uid = Boss._weighted_random_damage_user(rows)
        if uid is None:
            return []
        mythic = random.choice((MYTHIC_RAID_BLADE, MYTHIC_RAID_MAIL))
        await self.bot.db.grant_item(uid, guild_id, mythic.id)
        return [(uid, mythic)]

    async def _roll_aspect_loot(
        self,
        guild_id: int,
        rows: list[Any],
        variant: str,
    ) -> list[tuple[int, str]]:
        if not rows:
            return []
        drop_mult = await self.bot.db.get_drop_multiplier(guild_id)
        aspect_chance = await self.bot.db.get_config_value(guild_id, "boss_aspect_drop_chance")
        if random.random() >= aspect_chance * drop_mult:
            return []
        uid = Boss._weighted_random_damage_user(rows)
        if uid is None:
            return []
        threat = config.BOSS_VARIANTS.get(variant, {}).get("threat", 1)
        defn = random_aspect_definition()
        roll_pct = roll_pct_for_threat(int(threat))
        await self.bot.db.create_aspect_instance(
            uid,
            guild_id,
            defn.id,
            roll_pct,
        )
        label = f"**{defn.name}** ({roll_pct:g}%)"
        return [(uid, label)]

    async def _send_boss_spawn_embed(
        self,
        guild: discord.Guild,
        *,
        variant: str,
        hp: float,
        summoner_id: int | None = None,
        element: str | None = None,
    ) -> None:
        channel = await resolve_bot_announcement_channel(guild, self.bot.db)
        if channel is None:
            logging.warning("Boss spawn embed skipped: no channel in guild %s", guild.id)
            return
        title = "Boss summoned!" if summoner_id is not None else "Boss raid incoming!"
        boss_label = BOSS_NAME_TOMASS if variant == "tomass" else BOSS_NAME
        desc = f"A **{variant}** **{boss_label}** crashes the party—time to rally the raid!"
        embed = discord.Embed(
            title=title,
            description=desc,
            color=discord.Color.dark_red(),
        )
        bar = hp_bar(hp, hp)
        embed.add_field(name="Health", value=f"`{bar}` **{fmt_amount(hp)}** HP", inline=True)
        embed.add_field(
            name="Battle room",
            value=channel.mention,
            inline=True,
        )
        if element:
            embed.add_field(name="Element", value=element.title(), inline=True)
        threat = config.BOSS_VARIANTS[variant]["threat"]
        embed.add_field(name="Threat tier", value=str(threat), inline=True)
        if summoner_id is not None:
            embed.add_field(
                name="Summoner penalty",
                value=f"<@{summoner_id}> — {summoner_penalty_summary()}",
                inline=False,
            )
        embed.add_field(
            name="Fight back",
            value="Use **`/boss`** for the raid fight panel · `/heal` for downed allies",
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
        killer_user_id: int | None = None,
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
        files: list[discord.File] = []
        if killer_user_id is not None:
            avatar_id = await self.bot.db.get_equipped_avatar_id(killer_user_id, guild.id)
            defn = get_avatar(avatar_id)
            killer_name = self._display_name(guild, killer_user_id)
            pose_label = defn.name if defn else "Raider"
            embed.add_field(
                name="Killing blow",
                value=f"**{killer_name}** — {pose_label} victory pose",
                inline=False,
            )
            avatar_files, victory_name, portrait_name = await build_avatar_embed_files(
                self.bot.db,
                avatar_id,
                guild_id=guild.id,
                user_id=killer_user_id,
            )
            files.extend(avatar_files)
            if victory_name:
                embed.set_image(url=f"attachment://{victory_name}")
            if portrait_name:
                embed.set_thumbnail(url=f"attachment://{portrait_name}")
        gate = getattr(self.bot, "outbound_gate", None)
        sent = await safe_channel_send(
            channel,
            embed=embed,
            files=files or None,
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
                killer_user_id=killer_user_id,
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
        loot_rows.extend(await self._roll_mythic_loot(guild_id, rows, variant))
        aspect_rows = await self._roll_aspect_loot(guild_id, rows, variant)
        gear_lines = [
            f"**{self._display_name(guild, uid)}** · **{item.name}** (`{item.id}`)"
            for uid, item in loot_rows
        ]
        gear_lines.extend(
            f"**{self._display_name(guild, uid)}** · Aspect {label}"
            for uid, label in aspect_rows
        )

        await self.bot.db.clear_boss(guild_id)

        contributor_ids = [int(row["user_id"]) for row in rows]
        mythic = variant == "mythic"
        await self.bot.db.increment_boss_kills_for_raid(
            guild_id,
            contributor_ids,
            mythic=mythic,
        )

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
            killer_user_id=killer_user_id,
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
            if killer_user_id is not None:
                unlocked = await evaluate_unlocks(
                    self.bot.db,
                    guild_id,
                    killer_user_id,
                )
                unlock_msg = format_unlock_message(unlocked)
                if unlock_msg:
                    await interaction.followup.send(unlock_msg, ephemeral=True)

    async def _loadout(self, user_id: int, guild_id: int) -> PlayerLoadout:
        return await self.bot.db.get_combat_loadout(user_id, guild_id)

    async def _max_hp(self, user_id: int, guild_id: int) -> float:
        from utils.classes import get_modifiers
        from utils.combat_engine import max_hp_from_armor

        loadout = await self._loadout(user_id, guild_id)
        class_id = await self.bot.db.get_class_id(user_id, guild_id)
        return float(max_hp_from_armor(loadout.armor, class_modifiers=get_modifiers(class_id)))

    @staticmethod
    def _attack_roll(
        weapon: ShopItem | None,
        *,
        off_hand: ShopItem | None = None,
        damage_mult: float = 1.0,
        extra_crit: float = 0.0,
        crit_chance_multiplier: float = 1.0,
    ) -> tuple[int, bool, str]:
        if weapon is None:
            low = int(config.BOSS_UNARMED_MIN * damage_mult)
            high = int(config.BOSS_UNARMED_MAX * damage_mult)
            damage = random.randint(low, max(low, high))
            verb = "hits"
            crit_chance = config.PLAYER_BASE_CRIT_CHANCE + extra_crit
        else:
            attack_power = weapon.power + off_hand_power_bonus(off_hand)
            low = int((attack_power + config.BOSS_ATTACK_BONUS_MIN) * damage_mult)
            high = int((attack_power + config.BOSS_ATTACK_BONUS_MAX) * damage_mult)
            damage = random.randint(low, max(low, high))
            verb = random.choice(weapon.verbs or ("strikes",))
            crit_chance = (
                config.PLAYER_BASE_CRIT_CHANCE
                + weapon.crit_chance
                + off_hand_crit_bonus(off_hand)
                + extra_crit
            )
        crit_chance = max(0.0, crit_chance * crit_chance_multiplier)
        critical = random.random() < crit_chance
        if critical:
            damage = int(damage * config.PLAYER_ATTACK_CRIT_MULTIPLIER)
        return damage, critical, verb

    @staticmethod
    def _apply_armor_mitigation(
        raw_damage: int,
        armor: ShopItem | None,
        *,
        set_bonus: SetBonus | None = None,
        defense_retention: float = 1.0,
    ) -> tuple[int, int]:
        if armor is None:
            return raw_damage, 0
        armor_power = armor.power * max(0.0, defense_retention)
        mitigated = int(raw_damage * armor_power / (armor_power + 100))
        if set_bonus is not None:
            mitigated += int(raw_damage * set_bonus.mitigation_bonus)
        mitigated = min(raw_damage - 1, mitigated)
        return max(1, raw_damage - mitigated), mitigated

    @staticmethod
    def _counter_roll(
        variant: str,
        armor: ShopItem | None,
        *,
        set_bonus: SetBonus | None = None,
        defense_retention: float = 1.0,
    ) -> tuple[int, int, bool, str]:
        variant_config = config.BOSS_VARIANTS[variant]
        low, high = variant_config["counter_damage"]
        raw_damage = random.randint(int(low), int(high))
        critical = random.random() < float(variant_config["crit_chance"])
        if critical:
            raw_damage = int(raw_damage * 1.75)
        damage, mitigated = Boss._apply_armor_mitigation(
            raw_damage,
            armor,
            set_bonus=set_bonus,
            defense_retention=defense_retention,
        )
        moves = {
            "normal": ("backhands", "shoulder-checks", "bonks"),
            "enraged": ("rage-smashes", "uppercuts", "body-slams"),
            "shadow": ("void-crushes", "shadow-rakes", "ambushes"),
            "celestial": ("meteor-crits", "starfalls onto", "supernovas"),
            "mythic": ("reality-tears", "cataclysm-strikes", "doom-crashes"),
            "tomass": ("ass-smacks", "cheek-claps", "thunder-cheeks"),
        }
        return damage, mitigated, critical, random.choice(moves.get(variant, moves["normal"]))

    @staticmethod
    def _counter_chance(variant: str, hp: float, max_hp: float) -> float:
        base = float(config.BOSS_VARIANTS[variant]["counter_chance"])
        if max_hp <= 0:
            return base
        desperation = 1.0 - (hp / max_hp)
        chance = min(0.95, base + desperation * COUNTER_HP_BONUS)
        if hp / max_hp <= config.BOSS_PHASE_THRESHOLDS[-1]:
            chance = min(0.95, chance + config.BOSS_PHASE_ENRAGE_BONUS)
        return chance

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
            if random.random() < config.BOSS_AUTO_SPAWN_TOMASS_CHANCE:
                mirror = random.choice(config.HANNAH_SPAWN_VARIANTS)
                hp = await self._spawn_boss(
                    guild.id,
                    "tomass",
                    mirrored_variant=mirror,
                )
                boss_row = await self.bot.db.get_active_boss(guild.id)
                elem = str(boss_row["element"]) if boss_row else None
                await self._send_boss_spawn_embed(
                    guild,
                    variant="tomass",
                    hp=hp,
                    element=elem,
                )
                continue
            variant = random.choice(config.HANNAH_SPAWN_VARIANTS)
            hp = await self._spawn_boss(guild.id, variant)
            boss_row = await self.bot.db.get_active_boss(guild.id)
            elem = str(boss_row["element"]) if boss_row else None
            await self._send_boss_spawn_embed(guild, variant=variant, hp=hp, element=elem)

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

    @app_commands.command(
        name="summon",
        description=(
            f"Admin only: spawn a boss for {int(config.SUMMON_COST):,} coins "
            "(summoner penalties apply)."
        ),
    )
    @app_commands.describe(boss="Boss to summon")
    @app_commands.choices(
        boss=[
            app_commands.Choice(name="Hannah (enraged)", value="hannah_enraged"),
            app_commands.Choice(name="TomAss (enraged mirror ×1.75)", value="tomass"),
        ],
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def summon(
        self,
        interaction: discord.Interaction,
        boss: str = "hannah_enraged",
    ) -> None:
        if interaction.guild_id is None or interaction.guild is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        if not await self.bot.db.debit_wallet(
            interaction.user.id,
            interaction.guild_id,
            config.SUMMON_COST,
        ):
            await interaction.response.send_message(
                f"Summoning costs **{fmt_amount(config.SUMMON_COST)}**. "
                "You do not have enough nuggets.",
                ephemeral=True,
            )
            return

        if boss == "tomass":
            mirror = "enraged"
            hp = await self._spawn_boss(
                interaction.guild_id,
                "tomass",
                summoner_id=interaction.user.id,
                mirrored_variant=mirror,
            )
            label = BOSS_NAME_TOMASS
            spawn_variant = "tomass"
        else:
            spawn_variant = "enraged"
            hp = await self._spawn_boss(
                interaction.guild_id,
                spawn_variant,
                summoner_id=interaction.user.id,
            )
            label = BOSS_NAME
        await interaction.response.send_message(
            f"Summoned **{spawn_variant}** {label} with {fmt_amount(hp)} HP "
            f"(-{fmt_amount(config.SUMMON_COST)}). Penalties: {summoner_penalty_summary()}"
        )
        boss_row = await self.bot.db.get_active_boss(interaction.guild_id)
        elem = str(boss_row["element"]) if boss_row else None
        await self._send_boss_spawn_embed(
            interaction.guild,
            variant=spawn_variant,
            hp=hp,
            summoner_id=interaction.user.id,
            element=elem,
        )

    async def build_boss_fight_embed(
        self,
        guild_id: int,
        *,
        boss_row: Any | None = None,
        member: discord.Member | None = None,
    ) -> tuple[discord.Embed | None, str | None]:
        if boss_row is None:
            boss_row = await self.bot.db.apply_boss_passive_decay(guild_id)
        if boss_row is None:
            return None, "No boss is active right now."
        hp = float(boss_row["hp"])
        if hp <= 0:
            return None, "The boss is already defeated."

        max_hp = float(boss_row["max_hp"])
        variant = str(boss_row["variant"])
        bar = hp_bar(hp, max_hp)
        pct = int(round(100 * hp / max_hp)) if max_hp > 0 else 0
        embed = discord.Embed(
            title=f"{variant.title()} {boss_row['name']}",
            description=f"`{bar}` **{pct}%**",
            color=discord.Color.dark_red(),
        )
        embed.add_field(name="HP", value=f"{fmt_amount(hp)} / {fmt_amount(max_hp)}", inline=True)
        threat = config.BOSS_VARIANTS[variant]["threat"]
        embed.add_field(name="Threat", value=str(threat), inline=True)
        element = boss_row["element"]
        if element:
            embed.add_field(name="Element", value=str(element).title(), inline=True)
        summoner_id = boss_summoner_id(boss_row)
        if summoner_id is not None:
            embed.add_field(
                name="Summoner penalty",
                value=f"<@{summoner_id}> — {summoner_penalty_summary()}",
                inline=False,
            )
        if member is not None:
            loadout = await self._loadout(member.id, guild_id)
            weapon = loadout.primary.name if loadout.primary else "bare hands"
            if loadout.off_hand is not None:
                weapon = f"{weapon} + {loadout.off_hand.name}"
            embed.add_field(name="Your loadout", value=weapon, inline=True)
            damage_rows = await self.bot.db.list_boss_damage(guild_id)
            your_damage = 0.0
            for row in damage_rows:
                if int(row["user_id"]) == member.id:
                    your_damage = float(row["damage"])
                    break
            embed.add_field(
                name="Your damage",
                value=fmt_amount(your_damage) if your_damage > 0 else "_None yet_",
                inline=True,
            )
            snap = await self.bot.db.get_mana_snapshot(member.id, guild_id)
            pending_spell = await self.bot.db.get_pending_spell_id(member.id, guild_id)
            pending_consumable = await self.bot.db.get_pending_consumable_id(member.id, guild_id)
            status_lines = [
                f"`{mana_bar(snap.current, snap.cap)}` **{snap.current}/{snap.cap}**",
            ]
            if pending_spell:
                skill = get_skill(pending_spell)
                label = skill.name if skill else pending_spell
                if skill and skill.effect == "heal_ally":
                    status_lines.append(f"⚠️ **{label}** — use **Heal ally**, not Attack")
                else:
                    status_lines.append(f"Ready: **{label}** on next attack")
            if pending_consumable:
                item = get_item(pending_consumable)
                name = item.name if item else pending_consumable
                status_lines.append(f"Item: **{name}** on next attack")
            embed.add_field(
                name="Your mana & buffs",
                value="\n".join(status_lines),
                inline=False,
            )
        embed.set_footer(text="⚔️ Attack · Cast skill · Use item · Heal ally · Refresh")
        return embed, None

    async def execute_boss_heal(
        self,
        healer: discord.Member,
        target: discord.Member,
        guild_id: int,
    ) -> tuple[discord.Embed | None, str | None]:
        if target.bot and not config.ALLOW_BOT_PLAYERS:
            return None, "Bots do not need healing."
        if not await self.bot.db.is_downed(target.id, guild_id):
            return None, "That user is not downed."

        await self.bot.db.set_downed_until(target.id, guild_id, 0)
        await self.bot.db.restore_player_hp(
            target.id,
            guild_id,
            await self._max_hp(target.id, guild_id),
        )
        await self.bot.db.record_heal(guild_id, healer.id, target.id)
        await self.bot.db.increment_progress(
            healer.id,
            guild_id,
            heals_given=1,
        )
        self_heal = target.id == healer.id
        heal_reward = config.HEALER_SELF_REWARD if self_heal else config.HEALER_ALLY_REWARD
        bless_id = await self.bot.db.consume_pending_spell(healer.id, guild_id)
        if bless_id:
            bless = get_skill(bless_id)
            if bless is not None and bless.effect == "heal_ally":
                heal_reward *= 1.0 + bless.magnitude
        await self.bot.db.credit_wallet(healer.id, guild_id, heal_reward)
        await record_quest_event(
            self.bot.db,
            guild_id,
            healer.id,
            "boss_heal",
        )
        if self_heal:
            description = f"{healer.display_name} got back up."
            title = "Self revive"
        else:
            description = f"{healer.display_name} revived {target.display_name}."
            title = "Field medic"
        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Reward",
            value=f"+{fmt_amount(heal_reward)}",
            inline=True,
        )
        return embed, None

    async def execute_boss_attack(
        self,
        member: discord.Member,
        guild: discord.Guild,
        *,
        interaction: discord.Interaction | None = None,
    ) -> BossAttackResult:
        guild_id = guild.id
        if await self.bot.db.is_restricted(member.id, guild_id):
            return BossAttackResult(error="You cannot attack right now.")

        boss = await self.bot.db.get_active_boss(guild_id)
        if boss is None:
            return BossAttackResult(error="No boss is active right now.")

        loadout = await self._loadout(member.id, guild_id)
        set_bonus = detect_set_bonus(loadout.primary, loadout.armor)
        progress = await self.bot.db.get_user_progress(member.id, guild_id)
        prestige = int(progress["prestige_level"])
        summoner_debuff = is_summoner_debuffed(boss, member.id)
        crit_mult = config.SUMMONER_DEBUFF_CRIT_RETENTION if summoner_debuff else 1.0
        await self.bot.db.ensure_jester_class(member.id, guild_id)
        class_id = await self.bot.db.get_class_id(member.id, guild_id)
        boss_element = None
        with contextlib.suppress(KeyError, TypeError):
            boss_element = str(boss["element"])
        ctx = attack_context_for_class(
            class_id,
            prestige_level=prestige,
            boss_element=boss_element,
            for_boss=True,
        )
        bonuses = await self.bot.db.get_equipped_aspect_bonuses(member.id, guild_id)
        aspect_note = ""
        rows = await self.bot.db.list_equipped_aspect_rows(member.id, guild_id)
        if rows:
            ctx = replace(
                ctx,
                damage_mult=ctx.damage_mult * bonuses.damage_mult * bonuses.boss_damage_mult,
                extra_crit=ctx.extra_crit + bonuses.extra_crit,
            )
            names = ", ".join(instance_from_row(r).name for r in rows[:3])
            aspect_note = f" · Aspects: **{names}**"
        spell_note = ""
        skill_id = await self.bot.db.consume_pending_spell(member.id, guild_id)
        if skill_id:
            skill = get_skill(skill_id)
            if skill is not None:
                spell_state = combat_state_from_spell(spell_buff_from_skill(skill))
                if spell_state.damage_mult > 1.0 or spell_state.extra_crit > 0:
                    ctx = replace(
                        ctx,
                        damage_mult=ctx.damage_mult * spell_state.damage_mult,
                        extra_crit=ctx.extra_crit + spell_state.extra_crit,
                    )
                    spell_note = f" via **{skill.name}**"
        if await self.bot.db.take_pending_consumable(member.id, guild_id, "raid_potion"):
            ctx = replace(ctx, damage_mult=ctx.damage_mult * 1.2)
            spell_note += " · **Raid Potion** +20%"
        damage, attack_critical, attack_verb = roll_player_damage(
            loadout.primary,
            off_hand=loadout.off_hand,
            ctx=ctx,
            set_bonus=set_bonus,
            crit_chance_multiplier=crit_mult,
        )
        if summoner_debuff:
            damage = apply_summoner_attack_debuff(damage)
        mana_gain = await self.bot.db.restore_mana_from_damage(member.id, guild_id, damage)
        heal_applied = 0.0
        updated = await self.bot.db.damage_boss(guild_id, member.id, damage)
        xp_gain = max(1, int(damage * config.CLASS_XP_PER_BOSS_DAMAGE))
        await self.bot.db.add_class_xp(member.id, guild_id, xp_gain)
        if updated is not None:
            _, heal_applied = await self.bot.db.increment_boss_attack_count(guild_id)
        if heal_applied > 0:
            updated = await self.bot.db.get_active_boss(guild_id)
        if updated is None:
            return BossAttackResult(error="No boss is active right now.")

        await record_quest_event(self.bot.db, guild_id, member.id, "boss_attack")

        if float(updated["hp"]) <= 0:
            await self._complete_boss_defeat(
                guild,
                interaction=interaction,
                killer_user_id=member.id,
            )
            defeat_embed = discord.Embed(
                title="Boss defeated!",
                description=f"**{member.display_name}** landed the final blow!",
                color=discord.Color.gold(),
            )
            return BossAttackResult(embed=defeat_embed, defeated=True)

        boss_hp = float(updated["hp"])
        boss_max = float(updated["max_hp"])
        phase_pct = await self.bot.db.try_mark_boss_phase(guild_id, boss_hp / boss_max)
        phase_note = ""
        if phase_pct is not None:
            phase_note = f"\n**Phase {phase_pct}%** — {BOSS_NAME} enrages!"

        counter_text = await self._maybe_counterattack(guild_id, updated)

        bar = hp_bar(boss_hp, boss_max)
        pct = int(round(100 * boss_hp / boss_max)) if boss_max > 0 else 0
        active_name = str(updated["name"])
        embed = discord.Embed(
            title=f"{member.display_name} → {active_name}",
            color=discord.Color.green() if attack_critical else discord.Color.blurple(),
        )
        weapon_text = loadout.primary.name if loadout.primary is not None else "bare hands"
        if loadout.off_hand is not None:
            weapon_text = f"{weapon_text} + {loadout.off_hand.name} (off-hand)"
        embed.add_field(
            name="Hit",
            value=f"{attack_verb} for **{damage}** with {weapon_text}{spell_note}{aspect_note}",
            inline=True,
        )
        embed.add_field(name="Mana", value=f"+{mana_gain} from damage", inline=True)
        if attack_critical:
            embed.add_field(name="Crit", value="**YES**", inline=True)
        if summoner_debuff:
            embed.add_field(
                name="Summoner penalty",
                value=summoner_penalty_summary(),
                inline=False,
            )
        embed.add_field(
            name=f"{active_name} HP",
            value=f"`{bar}` {fmt_amount(boss_hp)}/{fmt_amount(boss_max)} ({pct}%)",
            inline=False,
        )
        if heal_applied > 0:
            embed.add_field(
                name="TomAss regen",
                value=f"**{fmt_amount(heal_applied)}** HP restored!",
                inline=False,
            )
        if boss_element:
            embed.add_field(name="Element", value=boss_element.title(), inline=True)
            from utils.classes import get_class

            cls = get_class(class_id)
            if cls is not None and cls.element:
                strong_vs = config.BOSS_ELEMENT_BEATS.get(cls.element)
                weak_to = None
                for elem, beaten in config.BOSS_ELEMENT_BEATS.items():
                    if beaten == cls.element:
                        weak_to = elem
                        break
                if strong_vs == boss_element:
                    embed.add_field(
                        name="Element tip",
                        value=f"Your **{cls.element.title()}** is **strong** vs this boss.",
                        inline=False,
                    )
                elif weak_to == boss_element:
                    embed.add_field(
                        name="Element tip",
                        value=f"Boss **{boss_element.title()}** is **strong** vs your **{cls.element.title()}**.",
                        inline=False,
                    )
        if counter_text:
            embed.add_field(name="Counterattack", value=counter_text.strip(), inline=False)
        if phase_note:
            embed.add_field(name="Boss phase", value=phase_note.strip(), inline=False)
        if set_bonus is not None:
            embed.set_footer(text=f"{set_bonus.name} set bonus active · Press ⚔️ Attack again")
        else:
            embed.set_footer(text="Press ⚔️ Attack again · Refresh for live HP")
        return BossAttackResult(embed=embed)

    @app_commands.command(name="boss", description="Open the boss raid fight panel.")
    @app_commands.guild_only()
    async def boss(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or interaction.guild is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        await send_boss_fight_panel(interaction, self)

    @app_commands.command(name="attack", description="Attack the active boss.")
    @app_commands.guild_only()
    async def attack(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or interaction.guild is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Members only.", ephemeral=True)
            return

        result = await self.execute_boss_attack(
            interaction.user,
            interaction.guild,
            interaction=interaction,
        )
        if result.error:
            await interaction.response.send_message(result.error, ephemeral=True)
            return
        if result.defeated:
            if result.embed is not None and interaction.response.is_done():
                await interaction.followup.send(embed=result.embed, ephemeral=True)
            return
        view = await build_boss_fight_view(self, interaction.guild_id, interaction.user.id)
        await interaction.response.send_message(
            embed=result.embed,
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name="boss-status", description="Quick boss HP check (no buttons).")
    @app_commands.guild_only()
    async def boss_status(self, interaction: discord.Interaction) -> None:
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

        embed, err = await self.build_boss_fight_embed(
            interaction.guild_id,
            boss_row=boss_row,
        )
        if err or embed is None:
            await interaction.response.send_message(err or "No boss active.")
            return
        await interaction.response.send_message(embed=embed)

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
            await self._counterattack_text(guild_id, victim_id, variant, boss_row=boss_row)
            for victim_id in victims
        ]
        return "".join(parts)

    async def _counterattack_text(
        self,
        guild_id: int,
        victim_id: int,
        variant: str,
        *,
        boss_row: Any,
    ) -> str:
        await self.bot.db.ensure_jester_class(victim_id, guild_id)
        victim_class = await self.bot.db.get_class_id(victim_id, guild_id)
        reflect = roll_jester_reflect(victim_class)
        if reflect.proc:
            boss_name = str(boss_row["name"])
            damage_rows = await self.bot.db.list_boss_damage(guild_id)
            raiders = [int(r["user_id"]) for r in damage_rows if int(r["user_id"]) != victim_id]
            steal = 0.0
            downed_note = ""
            if raiders:
                unlucky = random.choice(raiders)
                steal = await self.bot.db.jester_steal_wallet(unlucky, victim_id, guild_id)
                downed_seconds = await self.bot.db.get_config_value(guild_id, "boss_downed_seconds")
                await self.bot.db.set_downed_until(
                    unlucky,
                    guild_id,
                    time.time() + downed_seconds,
                )
                downed_note = f" <@{unlucky}> is instantly downed!"
            steal_note = f" **{fmt_amount(steal)}** stolen!" if steal > 0 else ""
            return (
                f"\n**who me?** <@{victim_id}> deflects {boss_name}'s counter!{downed_note}{steal_note}"
            )
        loadout = await self._loadout(victim_id, guild_id)
        set_bonus = detect_set_bonus(loadout.primary, loadout.armor)
        max_hp = await self._max_hp(victim_id, guild_id)
        await self.bot.db.sync_combat_hp(victim_id, guild_id, max_hp)
        summoner_victim = is_summoner_debuffed(boss_row, victim_id)
        defense_retention = summoner_defense_retention() if summoner_victim else 1.0
        damage, mitigated, critical, move = self._counter_roll(
            variant,
            loadout.armor,
            set_bonus=set_bonus,
            defense_retention=defense_retention,
        )
        if summoner_victim:
            damage = apply_summoner_counter_damage(damage)
        hp, max_hp = await self.bot.db.damage_player(victim_id, guild_id, damage, max_hp)
        armor_text = ""
        if loadout.armor is not None and mitigated > 0:
            pct = armor_mitigation_percent(loadout.armor.power)
            armor_text = f" {loadout.armor.name} mitigates {mitigated} ({pct}%)."
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

    @app_commands.command(name="raid-leaderboard", description="Top damage dealers on the active boss.")
    @app_commands.guild_only()
    async def raid_leaderboard(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None or interaction.guild is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        boss = await self.bot.db.get_active_boss(interaction.guild_id)
        if boss is None:
            await interaction.response.send_message("No boss is active right now.", ephemeral=True)
            return

        rows = await self.bot.db.list_boss_damage(interaction.guild_id)
        if not rows:
            await interaction.response.send_message("Nobody has attacked yet.", ephemeral=True)
            return

        total = sum(float(row["damage"]) for row in rows)
        lines = []
        for index, row in enumerate(rows[:10], start=1):
            user_id = int(row["user_id"])
            dmg = float(row["damage"])
            share = int(round(100 * dmg / total)) if total > 0 else 0
            name = self._display_name(interaction.guild, user_id)
            lines.append(f"**{index}.** {name} — **{fmt_amount(dmg)}** ({share}%)")

        embed = discord.Embed(
            title=f"Raid damage — {boss['variant'].title()} {boss['name']}",
            description="\n".join(lines),
            color=discord.Color.orange(),
        )
        embed.set_footer(text="Rewards scale with your damage share when the boss falls")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="heal", description="Revive a downed teammate.")
    @app_commands.describe(target="Downed user to heal")
    @app_commands.guild_only()
    async def heal(self, interaction: discord.Interaction, target: discord.Member) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Members only.", ephemeral=True)
            return

        embed, err = await self.execute_boss_heal(
            interaction.user,
            target,
            interaction.guild_id,
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await interaction.response.send_message(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        unlocked = await evaluate_unlocks(
            self.bot.db,
            interaction.guild_id,
            interaction.user.id,
        )
        unlock_msg = format_unlock_message(unlocked)
        if unlock_msg:
            await interaction.followup.send(unlock_msg, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Boss(bot))
