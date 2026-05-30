from __future__ import annotations

import random
from typing import TYPE_CHECKING

import discord

import config
from utils.combat_engine import AttackContext, roll_player_damage
from utils.helpers import fmt_amount
from utils.quests import record_quest_event

if TYPE_CHECKING:
    from cogs.dungeon import Dungeon


async def build_dungeon_embed(
    cog: Dungeon,
    guild_id: int,
    user_id: int,
) -> discord.Embed:
    run = await cog.bot.db.get_dungeon_run(user_id, guild_id)
    party_lid = await cog.bot.db.get_party_leader_for_member(guild_id, user_id)

    embed = discord.Embed(
        title="Dungeon",
        color=discord.Color.dark_purple(),
    )

    if party_lid is not None:
        party = await cog.bot.db.get_dungeon_party(guild_id, party_lid)
        members = await cog.bot.db.list_party_members(guild_id, party_lid)
        if party is not None:
            embed.description = (
                f"Party run · Leader <@{party_lid}>\n"
                f"Room **{int(party['room'])}/{config.DUNGEON_ROOMS}** · "
                f"Enemy **{int(float(party['enemy_hp']))}** HP"
            )
            for m in members:
                embed.add_field(
                    name=f"Raider {m['user_id']}",
                    value=f"**{int(float(m['player_hp']))}/{int(float(m['max_hp']))}** HP",
                    inline=True,
                )
        else:
            embed.description = "Party run — status unavailable."
    elif run is not None:
        embed.description = (
            f"Solo run · Room **{int(run['room'])}/{config.DUNGEON_ROOMS}**\n"
            f"Your HP **{int(float(run['player_hp']))}/{int(float(run['max_hp']))}** · "
            f"Enemy **{int(float(run['enemy_hp']))}** HP"
        )
    else:
        embed.description = (
            f"No active run.\n"
            f"Solo entry: **{fmt_amount(config.DUNGEON_ENTRY_COST)}** · "
            f"Party entry: **{fmt_amount(config.DUNGEON_PARTY_ENTRY_COST)}**"
        )

    embed.set_footer(text="Solo: Start · Fight · Flee · Party: Create")
    return embed


class DungeonView(discord.ui.View):
    def __init__(self, cog: Dungeon, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your dungeon panel.", ephemeral=True,
            )
            return False
        return True

    async def _refresh(self, interaction: discord.Interaction) -> None:
        embed = await build_dungeon_embed(self.cog, self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Start solo", style=discord.ButtonStyle.success, row=0)
    async def start_solo(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        msg, err = await start_solo_run(self.cog, self.user_id, self.guild_id)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        embed = await build_dungeon_embed(self.cog, self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send(msg, ephemeral=True)

    @discord.ui.button(label="Fight", style=discord.ButtonStyle.danger, row=0)
    async def fight(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        msg, err = await fight_solo_room(self.cog, self.user_id, self.guild_id)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        embed = await build_dungeon_embed(self.cog, self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send(msg, ephemeral=True)

    @discord.ui.button(label="Flee", style=discord.ButtonStyle.secondary, row=0)
    async def flee(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        run = await self.cog.bot.db.get_dungeon_run(self.user_id, self.guild_id)
        if run is None:
            await interaction.response.send_message("No active solo run.", ephemeral=True)
            return
        await self.cog.bot.db.clear_dungeon_run(self.user_id, self.guild_id)
        embed = await build_dungeon_embed(self.cog, self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send("You fled the dungeon empty-handed.", ephemeral=True)

    @discord.ui.button(label="Party create", style=discord.ButtonStyle.primary, row=1)
    async def party_create(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        msg, err = await create_party_run(self.cog, self.user_id, self.guild_id)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        embed = await build_dungeon_embed(self.cog, self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send(msg, ephemeral=True)

    @discord.ui.button(label="Party fight", style=discord.ButtonStyle.danger, row=1)
    async def party_fight(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        msg, err = await fight_party_room(self.cog, self.user_id, self.guild_id)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        embed = await build_dungeon_embed(self.cog, self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send(msg, ephemeral=True)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._refresh(interaction)


async def start_solo_run(cog: Dungeon, user_id: int, guild_id: int) -> tuple[str, str | None]:
    run = await cog.bot.db.get_dungeon_run(user_id, guild_id)
    if run is not None:
        return "", "Finish or flee your current run first."
    if await cog.bot.db.is_restricted(user_id, guild_id):
        return "", "You cannot enter a dungeon while arrested or downed."
    cost = config.DUNGEON_ENTRY_COST
    if not await cog.bot.db.debit_wallet(user_id, guild_id, cost):
        return "", f"Entry costs **{fmt_amount(cost)}**."
    max_hp = await cog._player_max_hp(user_id, guild_id)
    enemy_hp = random.uniform(80, 140)
    await cog.bot.db.start_dungeon_run(user_id, guild_id, max_hp, max_hp, enemy_hp)
    return (
        f"Entered the dungeon (-{fmt_amount(cost)}). "
        f"Room 1 — enemy HP **{int(enemy_hp)}**. Press **Fight**.",
        None,
    )


async def fight_solo_room(cog: Dungeon, user_id: int, guild_id: int) -> tuple[str, str | None]:
    run = await cog.bot.db.get_dungeon_run(user_id, guild_id)
    if run is None:
        return "", "Start a solo run first."
    loadout = await cog.bot.db.get_combat_loadout(user_id, guild_id)
    progress = await cog.bot.db.get_user_progress(user_id, guild_id)
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
            await cog.bot.db.clear_dungeon_run(user_id, guild_id)
            return "\n".join(lines) + "\nYou were defeated.", None
        await cog.bot.db.update_dungeon_run(
            user_id, guild_id, room=room, player_hp=player_hp, enemy_hp=enemy_hp,
        )
        return "\n".join(lines), None

    reward = config.DUNGEON_ROOM_REWARD
    if room >= config.DUNGEON_ROOMS:
        reward += config.DUNGEON_CLEAR_BONUS
        await cog.bot.db.clear_dungeon_run(user_id, guild_id)
        await cog.bot.db.credit_wallet(user_id, guild_id, reward)
        await cog.bot.db.increment_progress(user_id, guild_id, dungeons_cleared=1)
        await record_quest_event(cog.bot.db, guild_id, user_id, "dungeon_clear")
        for _ in range(config.DUNGEON_SCRAP_PER_CLEAR):
            await cog.bot.db.grant_item(user_id, guild_id, "alchemy_scrap")
        lines.append(
            f"**Dungeon cleared!** +{fmt_amount(reward)} · "
            f"+{config.DUNGEON_SCRAP_PER_CLEAR} alchemy scrap"
        )
        return "\n".join(lines), None

    next_room = room + 1
    next_enemy = random.uniform(90 + next_room * 15, 130 + next_room * 20)
    await cog.bot.db.update_dungeon_run(
        user_id,
        guild_id,
        room=next_room,
        player_hp=player_hp,
        enemy_hp=next_enemy,
    )
    await cog.bot.db.credit_wallet(user_id, guild_id, reward)
    lines.append(
        f"Room cleared (+{fmt_amount(reward)}). "
        f"Room **{next_room}** — enemy HP **{int(next_enemy)}**."
    )
    return "\n".join(lines), None


async def create_party_run(cog: Dungeon, user_id: int, guild_id: int) -> tuple[str, str | None]:
    if await cog.bot.db.get_party_leader_for_member(guild_id, user_id) is not None:
        return "", "Leave your current party first."
    cost = config.DUNGEON_PARTY_ENTRY_COST
    if not await cog.bot.db.debit_wallet(user_id, guild_id, cost):
        return "", f"Party entry costs **{fmt_amount(cost)}**."
    max_hp = await cog._player_max_hp(user_id, guild_id)
    enemy_hp = random.uniform(120, 200)
    await cog.bot.db.create_dungeon_party(guild_id, user_id, max_hp, max_hp, enemy_hp)
    return (
        f"Party created (-{fmt_amount(cost)}). "
        f"Others use `/dungeon` party join. Enemy HP **{int(enemy_hp)}**.",
        None,
    )


async def fight_party_room(cog: Dungeon, user_id: int, guild_id: int) -> tuple[str, str | None]:
    lid = await cog.bot.db.get_party_leader_for_member(guild_id, user_id)
    if lid is None:
        return "", "Join a party first."
    party = await cog.bot.db.get_dungeon_party(guild_id, lid)
    if party is None:
        return "", "No active party dungeon."
    members = await cog.bot.db.list_party_members(guild_id, lid)
    my_row = next((m for m in members if int(m["user_id"]) == user_id), None)
    if my_row is None:
        return "", "You are not in this party."
    player_hp = float(my_row["player_hp"])
    if player_hp <= 0:
        return "", "You are downed and cannot fight."
    loadout = await cog.bot.db.get_combat_loadout(user_id, guild_id)
    progress = await cog.bot.db.get_user_progress(user_id, guild_id)
    ctx = AttackContext(prestige_level=int(progress["prestige_level"]))
    damage, critical, verb = roll_player_damage(
        loadout.primary, off_hand=loadout.off_hand, ctx=ctx,
    )
    enemy_hp = float(party["enemy_hp"]) - damage
    room = int(party["room"])
    crit = " **CRIT!**" if critical else ""
    lines = [f"You **{verb}** for **{damage}**.{crit}"]

    if enemy_hp > 0:
        counter = random.randint(10, 24)
        player_hp = max(0.0, player_hp - counter)
        lines.append(f"Party takes **{counter}** splash damage on you.")
        await cog.bot.db.update_party_member_hp(guild_id, lid, user_id, player_hp)
        await cog.bot.db.update_dungeon_party_enemy(
            guild_id, lid, room=room, enemy_hp=enemy_hp,
        )
        if player_hp <= 0:
            lines.append("You were downed.")
            return "\n".join(lines), None
        lines.append(f"Enemy **{int(enemy_hp)}** HP left.")
        return "\n".join(lines), None

    reward_each = config.DUNGEON_ROOM_REWARD / max(1, len(members))
    if room >= config.DUNGEON_ROOMS:
        reward_each += config.DUNGEON_CLEAR_BONUS / max(1, len(members))
        await cog.bot.db.clear_dungeon_party(guild_id, lid)
        for m in members:
            mid = int(m["user_id"])
            if float(m["player_hp"]) > 0:
                await cog.bot.db.credit_wallet(mid, guild_id, reward_each)
                await cog.bot.db.increment_progress(mid, guild_id, dungeons_cleared=1)
                for _ in range(config.DUNGEON_SCRAP_PER_CLEAR):
                    await cog.bot.db.grant_item(mid, guild_id, "alchemy_scrap")
        lines.append(
            f"**Party cleared the dungeon!** +{fmt_amount(reward_each)} each · scrap for survivors."
        )
        return "\n".join(lines), None

    next_room = room + 1
    next_enemy = random.uniform(100 + next_room * 20, 160 + next_room * 25)
    await cog.bot.db.update_dungeon_party_enemy(
        guild_id, lid, room=next_room, enemy_hp=next_enemy,
    )
    for m in members:
        mid = int(m["user_id"])
        if float(m["player_hp"]) > 0:
            await cog.bot.db.credit_wallet(mid, guild_id, reward_each)
    lines.append(
        f"Room cleared (+{fmt_amount(reward_each)} each). "
        f"Room **{next_room}** — enemy **{int(next_enemy)}** HP."
    )
    return "\n".join(lines), None


async def send_dungeon_panel(interaction: discord.Interaction, cog: Dungeon) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return
    embed = await build_dungeon_embed(cog, interaction.guild_id, interaction.user.id)
    view = DungeonView(cog, interaction.guild_id, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
