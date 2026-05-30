from __future__ import annotations

import random

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.combat_engine import AttackContext, roll_player_damage
from utils.helpers import fmt_amount, guild_only_message
from utils.loadout import parse_loadout
from utils.quests import record_quest_event


class Dungeon(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _player_max_hp(self, user_id: int, guild_id: int) -> float:
        loadout = await self.bot.db.get_combat_loadout(user_id, guild_id)
        hp = float(config.PLAYER_BASE_HP)
        if loadout.armor:
            hp += float(loadout.armor.hp_bonus)
        return hp

    @app_commands.command(
        name="dungeon",
        description="Solo or party dungeon — 5 rooms, loot and alchemy scrap at the end.",
    )
    @app_commands.describe(
        action="What to do",
        leader="Party leader (for join)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Status (solo)", value="status"),
            app_commands.Choice(name="Start solo run", value="start"),
            app_commands.Choice(name="Fight room (solo)", value="fight"),
            app_commands.Choice(name="Flee (solo)", value="flee"),
            app_commands.Choice(name="Party — create", value="party-create"),
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
        action: str,
        leader: discord.Member | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        guild_id = interaction.guild_id
        uid = interaction.user.id

        run = await self.bot.db.get_dungeon_run(uid, guild_id)

        if action == "flee":
            if run is None:
                await interaction.response.send_message(
                    "No active dungeon.", ephemeral=True,
                )
                return
            await self.bot.db.clear_dungeon_run(uid, guild_id)
            await interaction.response.send_message(
                "You fled the dungeon empty-handed.", ephemeral=True,
            )
            return

        if action == "status":
            if run is None:
                await interaction.response.send_message(
                    "No active run. **Start run** costs "
                    f"{fmt_amount(config.DUNGEON_ENTRY_COST)}.",
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                f"Room **{int(run['room'])}/{config.DUNGEON_ROOMS}** · "
                f"Your HP **{int(float(run['player_hp']))}**/{int(float(run['max_hp']))} · "
                f"Enemy HP **{int(float(run['enemy_hp']))}**",
                ephemeral=True,
            )
            return

        if action == "start":
            if run is not None:
                await interaction.response.send_message(
                    "Finish or flee your current run first.", ephemeral=True,
                )
                return
            if await self.bot.db.is_restricted(uid, guild_id):
                await interaction.response.send_message(
                    "You cannot enter a dungeon while arrested or downed.",
                    ephemeral=True,
                )
                return
            cost = config.DUNGEON_ENTRY_COST
            if not await self.bot.db.debit_wallet(uid, guild_id, cost):
                await interaction.response.send_message(
                    f"Entry costs **{fmt_amount(cost)}**.", ephemeral=True,
                )
                return
            max_hp = await self._player_max_hp(uid, guild_id)
            enemy_hp = random.uniform(80, 140)
            await self.bot.db.start_dungeon_run(uid, guild_id, max_hp, max_hp, enemy_hp)
            await interaction.response.send_message(
                f"Entered the dungeon (-{fmt_amount(cost)}). "
                f"Room 1 — enemy HP **{int(enemy_hp)}**. Use **Fight room**.",
                ephemeral=True,
            )
            return

        if action == "fight":
            if run is None:
                await interaction.response.send_message(
                    "Start a run first.", ephemeral=True,
                )
                return
            loadout = await self.bot.db.get_combat_loadout(uid, guild_id)
            progress = await self.bot.db.get_user_progress(uid, guild_id)
            ctx = AttackContext(prestige_level=int(progress["prestige_level"]))
            damage, critical, verb = roll_player_damage(
                loadout.primary,
                off_hand=loadout.off_hand,
                ctx=ctx,
            )
            player_hp = float(run["player_hp"])
            enemy_hp = float(run["enemy_hp"]) - damage
            room = int(run["room"])
            crit_note = " **CRIT!**" if critical else ""
            lines = [f"You **{verb}** for **{damage}** damage.{crit_note}"]

            if enemy_hp > 0:
                counter = random.randint(12, 28)
                player_hp = max(0.0, player_hp - counter)
                lines.append(f"Enemy hits back for **{counter}**.")
                if player_hp <= 0:
                    await self.bot.db.clear_dungeon_run(uid, guild_id)
                    await interaction.response.send_message(
                        "\n".join(lines) + "\nYou were defeated.",
                        ephemeral=True,
                    )
                    return
                await self.bot.db.update_dungeon_run(
                    uid, guild_id, room=room, player_hp=player_hp, enemy_hp=enemy_hp,
                )
                await interaction.response.send_message("\n".join(lines), ephemeral=True)
                return

            reward = config.DUNGEON_ROOM_REWARD
            if room >= config.DUNGEON_ROOMS:
                reward += config.DUNGEON_CLEAR_BONUS
                await self.bot.db.clear_dungeon_run(uid, guild_id)
                await self.bot.db.credit_wallet(uid, guild_id, reward)
                await self.bot.db.increment_progress(
                    uid, guild_id, dungeons_cleared=1,
                )
                await record_quest_event(self.bot.db, guild_id, uid, "dungeon_clear")
                for _ in range(config.DUNGEON_SCRAP_PER_CLEAR):
                    await self.bot.db.grant_item(uid, guild_id, "alchemy_scrap")
                await interaction.response.send_message(
                    "\n".join(lines)
                    + f"\n**Dungeon cleared!** +{fmt_amount(reward)} · "
                    f"+{config.DUNGEON_SCRAP_PER_CLEAR} alchemy scrap",
                    ephemeral=True,
                )
                return

            next_room = room + 1
            next_enemy = random.uniform(90 + next_room * 15, 130 + next_room * 20)
            await self.bot.db.update_dungeon_run(
                uid,
                guild_id,
                room=next_room,
                player_hp=player_hp,
                enemy_hp=next_enemy,
            )
            await self.bot.db.credit_wallet(uid, guild_id, reward)
            await interaction.response.send_message(
                "\n".join(lines)
                + f"\nRoom cleared (+{fmt_amount(reward)}). "
                f"Room **{next_room}** — enemy HP **{int(next_enemy)}**.",
                ephemeral=True,
            )
            return

        if action == "party-create":
            if await self.bot.db.get_party_leader_for_member(guild_id, uid) is not None:
                await interaction.response.send_message(
                    "Leave your current party first.", ephemeral=True,
                )
                return
            cost = config.DUNGEON_PARTY_ENTRY_COST
            if not await self.bot.db.debit_wallet(uid, guild_id, cost):
                await interaction.response.send_message(
                    f"Party entry costs **{fmt_amount(cost)}**.", ephemeral=True,
                )
                return
            max_hp = await self._player_max_hp(uid, guild_id)
            enemy_hp = random.uniform(120, 200)
            await self.bot.db.create_dungeon_party(
                guild_id, uid, max_hp, max_hp, enemy_hp,
            )
            await interaction.response.send_message(
                f"Party created (-{fmt_amount(cost)}). "
                f"Others use **Party — join** with you as leader. "
                f"Enemy HP **{int(enemy_hp)}**.",
                ephemeral=True,
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
                f"Party leader <@{lid}> · Room **{int(party['room'])}/{config.DUNGEON_ROOMS}** · "
                f"Shared enemy **{int(float(party['enemy_hp']))}** HP\n"
                + "\n".join(names),
                ephemeral=True,
            )
            return

        if action == "party-fight":
            lid = await self.bot.db.get_party_leader_for_member(guild_id, uid)
            if lid is None:
                await interaction.response.send_message(
                    "Join a party first.", ephemeral=True,
                )
                return
            party = await self.bot.db.get_dungeon_party(guild_id, lid)
            if party is None:
                await interaction.response.send_message(
                    "No active party dungeon.", ephemeral=True,
                )
                return
            members = await self.bot.db.list_party_members(guild_id, lid)
            my_row = next((m for m in members if int(m["user_id"]) == uid), None)
            if my_row is None:
                await interaction.response.send_message(
                    "You are not in this party.", ephemeral=True,
                )
                return
            player_hp = float(my_row["player_hp"])
            if player_hp <= 0:
                await interaction.response.send_message(
                    "You are downed and cannot fight.", ephemeral=True,
                )
                return
            loadout = await self.bot.db.get_combat_loadout(uid, guild_id)
            progress = await self.bot.db.get_user_progress(uid, guild_id)
            ctx = AttackContext(prestige_level=int(progress["prestige_level"]))
            damage, critical, verb = roll_player_damage(
                loadout.primary, off_hand=loadout.off_hand, ctx=ctx,
            )
            enemy_hp = float(party["enemy_hp"]) - damage
            room = int(party["room"])
            crit = " **CRIT!**" if critical else ""
            lines = [
                f"**{interaction.user.display_name}** **{verb}** for **{damage}**.{crit}",
            ]
            if enemy_hp > 0:
                counter = random.randint(10, 24)
                player_hp = max(0.0, player_hp - counter)
                lines.append(f"Party takes **{counter}** splash damage on you.")
                await self.bot.db.update_party_member_hp(
                    guild_id, lid, uid, player_hp,
                )
                await self.bot.db.update_dungeon_party_enemy(
                    guild_id, lid, room=room, enemy_hp=enemy_hp,
                )
                if player_hp <= 0:
                    await interaction.response.send_message(
                        "\n".join(lines) + "\nYou were downed.",
                        ephemeral=True,
                    )
                    return
                await interaction.response.send_message(
                    "\n".join(lines) + f"\nEnemy **{int(enemy_hp)}** HP left.",
                    ephemeral=True,
                )
                return
            reward_each = config.DUNGEON_ROOM_REWARD / max(1, len(members))
            if room >= config.DUNGEON_ROOMS:
                reward_each += config.DUNGEON_CLEAR_BONUS / max(1, len(members))
                await self.bot.db.clear_dungeon_party(guild_id, lid)
                for m in members:
                    mid = int(m["user_id"])
                    if float(m["player_hp"]) > 0:
                        await self.bot.db.credit_wallet(
                            mid, guild_id, reward_each,
                        )
                        await self.bot.db.increment_progress(
                            mid, guild_id, dungeons_cleared=1,
                        )
                        for _ in range(config.DUNGEON_SCRAP_PER_CLEAR):
                            await self.bot.db.grant_item(
                                mid, guild_id, "alchemy_scrap",
                            )
                await interaction.response.send_message(
                    "\n".join(lines)
                    + f"\n**Party cleared the dungeon!** "
                    f"+{fmt_amount(reward_each)} each · scrap for survivors.",
                    ephemeral=True,
                )
                return
            next_room = room + 1
            next_enemy = random.uniform(100 + next_room * 20, 160 + next_room * 25)
            await self.bot.db.update_dungeon_party_enemy(
                guild_id, lid, room=next_room, enemy_hp=next_enemy,
            )
            for m in members:
                mid = int(m["user_id"])
                if float(m["player_hp"]) > 0:
                    await self.bot.db.credit_wallet(mid, guild_id, reward_each)
            await interaction.response.send_message(
                "\n".join(lines)
                + f"\nRoom cleared (+{fmt_amount(reward_each)} each). "
                f"Room **{next_room}** — enemy **{int(next_enemy)}** HP.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message("Unknown action.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Dungeon(bot))
