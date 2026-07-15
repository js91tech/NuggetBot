from __future__ import annotations

import random

import discord
from discord import app_commands
from discord.ext import commands

import config
from items import BOSS_ACCESSORY_POOL, get_item
from utils.combat_engine import AttackContext, max_hp_from_armor, roll_player_damage
from utils.dungeon_tiers import (
    DUNGEON_TIERS,
    NORMAL_TIER,
    VAULT_TIER,
    format_room_reward_range,
    get_dungeon_tier,
    next_enemy_hp,
    next_party_enemy_hp,
    roll_party_room_payouts,
    roll_room_reward,
)
from utils.dungeon_ui import DungeonActionResult, send_dungeon_panel
from utils.energy import format_energy_display
from utils.helpers import fmt_amount, guild_only_message
from utils.quests import record_quest_event
from utils.expansion_loot import on_dungeon_clear
from utils.stats import hp_bar


class Dungeon(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _player_max_hp(self, user_id: int, guild_id: int) -> float:
        loadout = await self.bot.db.get_combat_loadout(user_id, guild_id)
        return float(
            max_hp_from_armor(
                loadout.armor,
                accessory_bonuses=loadout.accessory_bonuses,
            )
        )

    async def _maybe_roll_accessory_drop(
        self,
        user_id: int,
        guild_id: int,
        *,
        tier_id: str,
    ) -> str | None:
        chance = (
            config.DUNGEON_VAULT_ACCESSORY_DROP_CHANCE
            if tier_id == VAULT_TIER.tier_id
            else config.DUNGEON_ACCESSORY_DROP_CHANCE
        )
        if random.random() >= chance:
            return None
        accessory_id = random.choice(BOSS_ACCESSORY_POOL)
        item = get_item(accessory_id)
        if item is None:
            return None
        instance_id = await self.bot.db.grant_item(user_id, guild_id, accessory_id)
        if instance_id is not None:
            from utils.affixes import current_delve_week_id

            week = current_delve_week_id()
            await self.bot.db.roll_gear_affixes(
                instance_id, delve_bonus=week in ("cursed_depths", "blood_pact"),
            )
        return item.name

    async def _maybe_roll_vault_hardener(self, user_id: int, guild_id: int, tier_id: str) -> str | None:
        if tier_id != VAULT_TIER.tier_id:
            return None
        if random.random() >= 0.35:
            return None
        await self.bot.db.grant_item(user_id, guild_id, "void_hardener")
        return "Void Hardener"

    async def _energy_display(self, user_id: int, guild_id: int) -> tuple[str, int, int]:
        row = await self.bot.db.get_user_character(user_id, guild_id)
        regen_per_tick = int(
            await self.bot.db.get_config_value(guild_id, "energy_regen_per_tick")
        )
        tick_seconds = int(
            await self.bot.db.get_config_value(guild_id, "energy_regen_interval_seconds")
        )
        return format_energy_display(
            int(row["energy"]),
            int(row["energy_cap"]),
            int(row["cap_upgrades"]),
            float(row["energy_updated_at"]),
            regen_per_tick=regen_per_tick,
            tick_seconds=tick_seconds,
        )

    async def _vault_unlocked(self, user_id: int, guild_id: int) -> bool:
        return await self.bot.db.has_vault_dungeon_unlocked(user_id, guild_id)

    def _tier_summary(self, tier_id: str, *, unlocked: bool) -> str:
        tier = get_dungeon_tier(tier_id)
        if tier.unlock_cost <= 0:
            access = "Free · solo"
        elif unlocked:
            access = f"Unlocked · party (**{tier.min_party_size}+** raiders)"
        else:
            access = (
                f"Unlock: **{fmt_amount(tier.unlock_cost)}** · "
                f"party (**{tier.min_party_size}+** raiders)"
            )
        mode = "Party raid" if tier.party_only else "Solo · tougher foes"
        room_pay = format_room_reward_range(tier, party_split=tier.party_only)
        return (
            f"{tier.emoji} **{tier.name}** — {mode}\n"
            f"Room {room_pay} · "
            f"Clear **{fmt_amount(tier.clear_bonus)}** + **{tier.scrap_per_clear}** scrap\n"
            f"{access}"
        )

    async def build_dungeon_embed(
        self,
        guild_id: int,
        user_id: int,
    ) -> tuple[discord.Embed, bool, bool]:
        run = await self.bot.db.get_dungeon_run(user_id, guild_id)
        energy_text, current_energy, cap = await self._energy_display(user_id, guild_id)
        vault_unlocked = await self._vault_unlocked(user_id, guild_id)

        if run is None:
            embed = discord.Embed(
                title="Dungeon — choose your depth",
                description=(
                    f"**{config.DUNGEON_ROOMS} rooms** per run · "
                    f"**{config.DUNGEON_ENERGY_COST}** energy to enter"
                ),
                color=discord.Color.dark_purple(),
            )
            embed.add_field(
                name="Standard",
                value=self._tier_summary(NORMAL_TIER.tier_id, unlocked=True),
                inline=False,
            )
            embed.add_field(
                name="Premium",
                value=self._tier_summary(VAULT_TIER.tier_id, unlocked=vault_unlocked),
                inline=False,
            )
            embed.add_field(name="Your energy", value=energy_text, inline=False)
            can_start = current_energy >= config.DUNGEON_ENERGY_COST
            embed.set_footer(
                text=(
                    "Solo standard below · unlock Vault for a party raid"
                    if can_start
                    else f"Need {config.DUNGEON_ENERGY_COST} energy ({current_energy}/{cap})"
                ),
            )
            return embed, False, vault_unlocked

        tier = get_dungeon_tier(str(run["tier"]) if run["tier"] is not None else "normal")
        room = int(run["room"])
        player_hp = float(run["player_hp"])
        max_hp = float(run["max_hp"])
        enemy_hp = float(run["enemy_hp"])
        embed = discord.Embed(
            title=f"{tier.emoji} {tier.name} — Room {room}/{config.DUNGEON_ROOMS}",
            description="Fight through the room or flee empty-handed.",
            color=discord.Color.gold() if tier.tier_id == "vault" else discord.Color.dark_purple(),
        )
        embed.add_field(
            name="Your HP",
            value=(
                f"`{hp_bar(player_hp, max_hp)}` "
                f"**{int(player_hp)}/{int(max_hp)}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Enemy HP",
            value=f"`{hp_bar(enemy_hp, max(enemy_hp, 1))}` **{int(enemy_hp)}**",
            inline=False,
        )
        embed.add_field(
            name="Rewards",
            value=(
                f"Room {format_room_reward_range(tier, party_split=False)} · "
                f"Clear **{fmt_amount(tier.clear_bonus)}** + **{tier.scrap_per_clear}** scrap"
            ),
            inline=False,
        )
        embed.add_field(name="Energy", value=energy_text, inline=False)
        embed.set_footer(text="⚔️ Fight · Flee · Refresh")
        return embed, True, vault_unlocked

    async def execute_dungeon_unlock_vault(
        self,
        guild_id: int,
        user_id: int,
    ) -> DungeonActionResult:
        err = await self.bot.db.unlock_vault_dungeon(
            user_id,
            guild_id,
            config.DUNGEON_VAULT_UNLOCK_COST,
        )
        if err == "already_unlocked":
            return DungeonActionResult(error="You already have Gilded Vault access.")
        if err == "insufficient_funds":
            return DungeonActionResult(
                error=(
                    f"Gilded Vault access costs **{fmt_amount(config.DUNGEON_VAULT_UNLOCK_COST)}**."
                ),
            )
        if err is not None:
            return DungeonActionResult(error="Could not unlock that tier.")
        embed, _, _ = await self.build_dungeon_embed(guild_id, user_id)
        return DungeonActionResult(
            embed=embed,
            message=(
                f"**Gilded Vault unlocked!** "
                f"(-{fmt_amount(config.DUNGEON_VAULT_UNLOCK_COST)}) "
                "Create a vault party for premium rewards."
            ),
        )

    async def execute_dungeon_start(
        self,
        guild_id: int,
        user_id: int,
        *,
        tier_id: str = "normal",
    ) -> DungeonActionResult:
        tier = get_dungeon_tier(tier_id)
        if tier.tier_id not in DUNGEON_TIERS:
            return DungeonActionResult(error="Unknown dungeon tier.")

        if tier.party_only:
            return DungeonActionResult(
                error=(
                    f"**{tier.name}** is a party raid. "
                    f"Create a vault party from the dungeon panel or "
                    f"`/dungeon` → **Party — create vault**."
                ),
            )

        run = await self.bot.db.get_dungeon_run(user_id, guild_id)
        if run is not None:
            return DungeonActionResult(error="Finish or flee your current run first.")

        if tier.tier_id == VAULT_TIER.tier_id and not await self._vault_unlocked(user_id, guild_id):
            return DungeonActionResult(
                error=(
                    f"Unlock **{VAULT_TIER.name}** first "
                    f"({fmt_amount(config.DUNGEON_VAULT_UNLOCK_COST)})."
                ),
            )

        if await self.bot.db.is_restricted(user_id, guild_id):
            return DungeonActionResult(
                error="You cannot enter a dungeon while arrested or downed.",
            )

        ok, err = await self.bot.db.spend_job_energy(
            user_id,
            guild_id,
            tier.energy_cost,
        )
        if not ok:
            if err == "energy":
                energy_text, current, cap = await self._energy_display(user_id, guild_id)
                return DungeonActionResult(
                    error=(
                        f"Not enough energy. Need **{tier.energy_cost}**, "
                        f"you have **{current}/{cap}**.\n{energy_text}"
                    ),
                )
            return DungeonActionResult(error="Could not start that run.")

        max_hp = await self._player_max_hp(user_id, guild_id)
        enemy_hp = next_enemy_hp(tier, 1)
        await self.bot.db.start_dungeon_run(
            user_id, guild_id, max_hp, max_hp, enemy_hp, tier=tier.tier_id,
        )
        embed, _, _ = await self.build_dungeon_embed(guild_id, user_id)
        return DungeonActionResult(
            embed=embed,
            message=(
                f"Entered **{tier.name}** (-**{tier.energy_cost}** energy). "
                f"Room 1 — enemy HP **{int(enemy_hp)}**."
            ),
        )

    async def execute_dungeon_flee(
        self,
        guild_id: int,
        user_id: int,
    ) -> DungeonActionResult:
        run = await self.bot.db.get_dungeon_run(user_id, guild_id)
        if run is None:
            return DungeonActionResult(error="No active dungeon run.")

        await self.bot.db.clear_dungeon_run(user_id, guild_id)
        embed, _, _ = await self.build_dungeon_embed(guild_id, user_id)
        return DungeonActionResult(
            embed=embed,
            message="You fled the dungeon empty-handed.",
            finished=True,
        )

    async def execute_dungeon_fight(
        self,
        guild_id: int,
        user_id: int,
    ) -> DungeonActionResult:
        run = await self.bot.db.get_dungeon_run(user_id, guild_id)
        if run is None:
            return DungeonActionResult(error="Start a run first.")

        tier = get_dungeon_tier(str(run["tier"]) if run["tier"] is not None else "normal")
        loadout = await self.bot.db.get_combat_loadout(user_id, guild_id)
        progress = await self.bot.db.get_user_progress(user_id, guild_id)
        ctx = AttackContext(prestige_level=int(progress["prestige_level"]))
        damage, critical, verb = roll_player_damage(
            loadout.primary,
            off_hand=loadout.off_hand,
            ctx=ctx,
            accessory_bonuses=loadout.accessory_bonuses,
            attacker_id=user_id,
        )
        player_hp = float(run["player_hp"])
        enemy_hp = float(run["enemy_hp"]) - damage
        room = int(run["room"])
        crit_note = " **CRIT!**" if critical else ""
        lines = [f"You **{verb}** for **{damage}** damage.{crit_note}"]

        if enemy_hp > 0:
            counter = random.randint(tier.counter_min, tier.counter_max)
            counter = max(
                1,
                int(round(counter * config.DUNGEON_PLAYER_DAMAGE_TAKEN_MULT)),
            )
            player_hp = max(0.0, player_hp - counter)
            lines.append(f"Enemy hits back for **{counter}**.")
            if player_hp <= 0:
                await self.bot.db.clear_dungeon_run(user_id, guild_id)
                embed, _, _ = await self.build_dungeon_embed(guild_id, user_id)
                return DungeonActionResult(
                    embed=embed,
                    message="\n".join(lines) + "\nYou were defeated.",
                    finished=True,
                )
            await self.bot.db.update_dungeon_run(
                user_id, guild_id, room=room, player_hp=player_hp, enemy_hp=enemy_hp,
            )
            embed, _, _ = await self.build_dungeon_embed(guild_id, user_id)
            return DungeonActionResult(embed=embed, message="\n".join(lines))

        reward = roll_room_reward(tier)
        if room >= config.DUNGEON_ROOMS:
            reward += tier.clear_bonus
            await self.bot.db.clear_dungeon_run(user_id, guild_id)
            await self.bot.db.credit_wallet(user_id, guild_id, reward)
            await self.bot.db.increment_progress(
                user_id, guild_id, dungeons_cleared=1,
            )
            await record_quest_event(self.bot.db, guild_id, user_id, "dungeon_clear")
            await on_dungeon_clear(
                self.bot.db, guild_id, user_id, tier_id=tier.tier_id,
            )
            for _ in range(tier.scrap_per_clear):
                await self.bot.db.grant_item(user_id, guild_id, "alchemy_scrap")
            accessory_name = await self._maybe_roll_accessory_drop(
                user_id, guild_id, tier_id=tier.tier_id,
            )
            hardener_name = await self._maybe_roll_vault_hardener(user_id, guild_id, tier.tier_id)
            bonus_loot = ""
            if accessory_name:
                bonus_loot += f" · **{accessory_name}**"
            if hardener_name:
                bonus_loot += f" · **{hardener_name}**"
            embed, _, _ = await self.build_dungeon_embed(guild_id, user_id)
            return DungeonActionResult(
                embed=embed,
                message=(
                    "\n".join(lines)
                    + f"\n**{tier.name} cleared!** +{fmt_amount(reward)} · "
                    f"+{tier.scrap_per_clear} alchemy scrap{bonus_loot}"
                ),
                finished=True,
            )

        next_room = room + 1
        next_enemy = next_enemy_hp(tier, next_room)
        await self.bot.db.update_dungeon_run(
            user_id,
            guild_id,
            room=next_room,
            player_hp=player_hp,
            enemy_hp=next_enemy,
        )
        await self.bot.db.credit_wallet(user_id, guild_id, reward)
        embed, _, _ = await self.build_dungeon_embed(guild_id, user_id)
        return DungeonActionResult(
            embed=embed,
            message=(
                "\n".join(lines)
                + f"\nRoom cleared (+{fmt_amount(reward)}). "
                f"Room **{next_room}** — enemy HP **{int(next_enemy)}**."
            ),
        )

    async def execute_party_create(
        self,
        guild_id: int,
        user_id: int,
        *,
        tier_id: str = "normal",
    ) -> DungeonActionResult:
        tier = get_dungeon_tier(tier_id)
        if tier.tier_id not in DUNGEON_TIERS:
            return DungeonActionResult(error="Unknown dungeon tier.")

        if tier.tier_id == VAULT_TIER.tier_id and not await self._vault_unlocked(user_id, guild_id):
            return DungeonActionResult(
                error=(
                    f"Unlock **{VAULT_TIER.name}** first "
                    f"({fmt_amount(config.DUNGEON_VAULT_UNLOCK_COST)})."
                ),
            )

        if await self.bot.db.get_party_leader_for_member(guild_id, user_id) is not None:
            return DungeonActionResult(error="Leave your current party first.")

        if await self.bot.db.is_restricted(user_id, guild_id):
            return DungeonActionResult(
                error="You cannot enter a dungeon while arrested or downed.",
            )

        ok, err = await self.bot.db.spend_job_energy(
            user_id,
            guild_id,
            tier.energy_cost,
        )
        if not ok:
            if err == "energy":
                energy_text, current, cap = await self._energy_display(user_id, guild_id)
                return DungeonActionResult(
                    error=(
                        f"Not enough energy. Need **{tier.energy_cost}**, "
                        f"you have **{current}/{cap}**.\n{energy_text}"
                    ),
                )
            return DungeonActionResult(error="Could not start that party.")

        max_hp = await self._player_max_hp(user_id, guild_id)
        enemy_hp = next_party_enemy_hp(tier, 1)
        await self.bot.db.create_dungeon_party(
            guild_id, user_id, max_hp, max_hp, enemy_hp, tier=tier.tier_id,
        )
        recruit = (
            f"Need **{tier.min_party_size}** raiders before fighting."
            if tier.min_party_size > 1
            else "Others can join before you fight."
        )
        return DungeonActionResult(
            message=(
                f"**{tier.name}** party created (-**{tier.energy_cost}** energy). "
                f"Others use **/dungeon** → **Party — join** with you as leader. "
                f"Enemy HP **{int(enemy_hp)}**. {recruit}"
            ),
        )

    async def execute_party_fight(
        self,
        guild_id: int,
        user_id: int,
        *,
        display_name: str,
    ) -> DungeonActionResult:
        lid = await self.bot.db.get_party_leader_for_member(guild_id, user_id)
        if lid is None:
            return DungeonActionResult(error="Join a party first.")

        party = await self.bot.db.get_dungeon_party(guild_id, lid)
        if party is None:
            return DungeonActionResult(error="No active party dungeon.")

        tier = get_dungeon_tier(str(party["tier"]) if party["tier"] is not None else "normal")
        members = await self.bot.db.list_party_members(guild_id, lid)
        if len(members) < tier.min_party_size:
            return DungeonActionResult(
                error=(
                    f"**{tier.name}** needs at least **{tier.min_party_size}** raiders "
                    f"(you have **{len(members)}**)."
                ),
            )

        my_row = next((m for m in members if int(m["user_id"]) == user_id), None)
        if my_row is None:
            return DungeonActionResult(error="You are not in this party.")

        player_hp = float(my_row["player_hp"])
        if player_hp <= 0:
            return DungeonActionResult(error="You are downed and cannot fight.")

        loadout = await self.bot.db.get_combat_loadout(user_id, guild_id)
        progress = await self.bot.db.get_user_progress(user_id, guild_id)
        ctx = AttackContext(prestige_level=int(progress["prestige_level"]))
        damage, critical, verb = roll_player_damage(
            loadout.primary,
            off_hand=loadout.off_hand,
            ctx=ctx,
            accessory_bonuses=loadout.accessory_bonuses,
            attacker_id=user_id,
        )
        enemy_hp = float(party["enemy_hp"]) - damage
        room = int(party["room"])
        crit = " **CRIT!**" if critical else ""
        lines = [f"**{display_name}** **{verb}** for **{damage}**.{crit}"]

        if enemy_hp > 0:
            counter = random.randint(tier.party_counter_min, tier.party_counter_max)
            player_hp = max(0.0, player_hp - counter)
            lines.append(f"Party takes **{counter}** splash damage on you.")
            await self.bot.db.update_party_member_hp(
                guild_id, lid, user_id, player_hp,
            )
            await self.bot.db.update_dungeon_party_enemy(
                guild_id, lid, room=room, enemy_hp=enemy_hp,
            )
            if player_hp <= 0:
                return DungeonActionResult(
                    message="\n".join(lines) + "\nYou were downed.",
                )
            return DungeonActionResult(
                message="\n".join(lines) + f"\nEnemy **{int(enemy_hp)}** HP left.",
            )

        room_total, reward_each = roll_party_room_payouts(tier, len(members))
        if room >= config.DUNGEON_ROOMS:
            reward_each += tier.clear_bonus / max(1, len(members))
            await self.bot.db.clear_dungeon_party(guild_id, lid)
            bonus_notes: list[str] = []
            for m in members:
                mid = int(m["user_id"])
                if float(m["player_hp"]) > 0:
                    await self.bot.db.credit_wallet(mid, guild_id, reward_each)
                    await self.bot.db.increment_progress(
                        mid, guild_id, dungeons_cleared=1,
                    )
                    for _ in range(tier.scrap_per_clear):
                        await self.bot.db.grant_item(mid, guild_id, "alchemy_scrap")
                    accessory_name = await self._maybe_roll_accessory_drop(
                        mid, guild_id, tier_id=tier.tier_id,
                    )
                    hardener_name = await self._maybe_roll_vault_hardener(
                        mid, guild_id, tier.tier_id,
                    )
                    if accessory_name:
                        bonus_notes.append(accessory_name)
                    if hardener_name:
                        bonus_notes.append(hardener_name)
            bonus_text = f" · {', '.join(bonus_notes)}" if bonus_notes else ""
            return DungeonActionResult(
                message=(
                    "\n".join(lines)
                    + f"\n**{tier.name} cleared!** "
                    f"+{fmt_amount(reward_each)} each "
                    f"({fmt_amount(room_total)} room pool split) · scrap for survivors{bonus_text}."
                ),
            )

        next_room = room + 1
        next_enemy = next_party_enemy_hp(tier, next_room)
        await self.bot.db.update_dungeon_party_enemy(
            guild_id, lid, room=next_room, enemy_hp=next_enemy,
        )
        for m in members:
            mid = int(m["user_id"])
            if float(m["player_hp"]) > 0:
                await self.bot.db.credit_wallet(mid, guild_id, reward_each)
        return DungeonActionResult(
            message=(
                "\n".join(lines)
                + f"\nRoom cleared (+{fmt_amount(reward_each)} each, "
                f"{fmt_amount(room_total)} split). "
                f"Room **{next_room}** — enemy **{int(next_enemy)}** HP."
            ),
        )

    @app_commands.command(
        name="dungeon",
        description="Solo dungeon panel — 5 rooms, loot and alchemy scrap at the end.",
    )
    @app_commands.describe(
        action="Party commands (solo uses the panel buttons)",
        leader="Party leader (for join)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Party — create", value="party-create"),
            app_commands.Choice(name="Party — create vault", value="party-create-vault"),
            app_commands.Choice(name="Party — join", value="party-join"),
            app_commands.Choice(name="Party — leave", value="party-leave"),
            app_commands.Choice(name="Party — status", value="party-status"),
            app_commands.Choice(name="Party — fight", value="party-fight"),
        ],
    )
    @app_commands.guild_only()
    async def dungeon(
        self,
        interaction: discord.Interaction,
        action: str | None = None,
        leader: discord.Member | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        guild_id = interaction.guild_id
        uid = interaction.user.id

        if action is None:
            await send_dungeon_panel(interaction, self)
            return

        if action == "party-create":
            result = await self.execute_party_create(
                guild_id, uid, tier_id=NORMAL_TIER.tier_id,
            )
            if result.error:
                await interaction.response.send_message(result.error, ephemeral=True)
                return
            await interaction.response.send_message(
                result.message or "Party created.", ephemeral=True,
            )
            return

        if action == "party-create-vault":
            result = await self.execute_party_create(
                guild_id, uid, tier_id=VAULT_TIER.tier_id,
            )
            if result.error:
                await interaction.response.send_message(result.error, ephemeral=True)
                return
            await interaction.response.send_message(
                result.message or "Vault party created.", ephemeral=True,
            )
            return

        if action == "party-join":
            if leader is None:
                await interaction.response.send_message(
                    "Pick the **leader** who started the party.", ephemeral=True,
                )
                return
            max_hp = await self._player_max_hp(uid, guild_id)
            err = await self.bot.db.join_dungeon_party(
                guild_id, leader.id, uid, max_hp, max_hp,
            )
            msgs = {
                "no_party": "That player has no active party run.",
                "full": "Party is full (4 raiders).",
                "already_in": "You are already in that party.",
                "in_other_party": "Leave your other party first.",
            }
            if err:
                await interaction.response.send_message(
                    msgs.get(err, err), ephemeral=True,
                )
                return
            await interaction.response.send_message(
                f"Joined **{leader.display_name}**'s dungeon party!",
                ephemeral=True,
            )
            return

        if action == "party-leave":
            if await self.bot.db.leave_dungeon_party(guild_id, uid):
                await interaction.response.send_message(
                    "Left the party.", ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "You are not in a party.", ephemeral=True,
                )
            return

        if action == "party-status":
            lid = await self.bot.db.get_party_leader_for_member(guild_id, uid)
            if lid is None:
                await interaction.response.send_message(
                    "You are not in a party.", ephemeral=True,
                )
                return
            party = await self.bot.db.get_dungeon_party(guild_id, lid)
            members = await self.bot.db.list_party_members(guild_id, lid)
            if party is None:
                await interaction.response.send_message(
                    "Party run not found.", ephemeral=True,
                )
                return
            tier = get_dungeon_tier(str(party["tier"]) if party["tier"] is not None else "normal")
            names = []
            if interaction.guild:
                for m in members:
                    mem = interaction.guild.get_member(int(m["user_id"]))
                    hp = int(float(m["player_hp"]))
                    maxh = int(float(m["max_hp"]))
                    names.append(
                        f"{mem.display_name if mem else m['user_id']}: **{hp}/{maxh}** HP"
                    )
            await interaction.response.send_message(
                f"{tier.emoji} **{tier.name}** · leader <@{lid}> · "
                f"Room **{int(party['room'])}/{config.DUNGEON_ROOMS}** · "
                f"Raiders **{len(members)}/{tier.min_party_size}+** · "
                f"Enemy **{int(float(party['enemy_hp']))}** HP\n"
                + "\n".join(names),
                ephemeral=True,
            )
            return

        if action == "party-fight":
            result = await self.execute_party_fight(
                guild_id,
                uid,
                display_name=interaction.user.display_name,
            )
            if result.error:
                await interaction.response.send_message(result.error, ephemeral=True)
                return
            await interaction.response.send_message(
                result.message or "Fight resolved.", ephemeral=True,
            )
            return

        await interaction.response.send_message("Unknown action.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Dungeon(bot))
