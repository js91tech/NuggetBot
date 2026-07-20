"""Pet vs pet duels between equipped henchlings."""
from __future__ import annotations

import random

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.bot_players import pvp_target_error
from utils.companion_combat import owner_attack_power_from_loadout
from utils.companions import companion_display_name, companion_emoji, roll_companion_damage
from utils.helpers import fmt_amount, guild_only_message


class PetDuels(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _fighter_power(
        self, user_id: int, guild_id: int, companion_id: str, row: object,
    ) -> tuple[int, str, str]:
        loadout = await self._loadout(user_id, guild_id)
        attack_power = owner_attack_power_from_loadout(loadout)
        evolution_tier = int(row["evolution_tier"])  # type: ignore[index]
        damage, _, _ = roll_companion_damage(
            companion_id,
            evolution_tier=evolution_tier,
            owner_attack_power=attack_power,
        )
        custom_name = row["custom_name"]  # type: ignore[index]
        name = companion_display_name(companion_id, str(custom_name) if custom_name else None)
        emoji = companion_emoji(companion_id)
        return damage, name, emoji

    async def _loadout(self, user_id: int, guild_id: int) -> object:
        from utils.loadout import parse_resolved_loadout

        equipment = await self.bot.db.get_equipment(user_id, guild_id)
        instances = await self.bot.db.get_gear_instances_map(user_id, guild_id)
        unstable = await self.bot.db.get_unstable_equipment_slots(user_id, guild_id)
        return parse_resolved_loadout(equipment, instances=instances, unstable_slots=unstable)

    @app_commands.command(
        name="pet-duel",
        description="Pit your active henchling against another player's pet for nuggets.",
    )
    @app_commands.describe(
        opponent="Player to challenge",
        stake="Nuggets to wager (winner takes all)",
    )
    @app_commands.guild_only()
    async def pet_duel(
        self,
        interaction: discord.Interaction,
        opponent: discord.Member,
        stake: app_commands.Range[float, 5_000.0, 250_000.0] | None = None,
    ) -> None:
        if interaction.guild_id is None or interaction.guild is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Members only.", ephemeral=True)
            return
        attacker = interaction.user
        guild_id = interaction.guild_id

        target_err = pvp_target_error(attacker, opponent)
        if target_err:
            await interaction.response.send_message(target_err, ephemeral=True)
            return

        wager = float(stake or config.COMPANION_PET_DUEL_MIN_STAKE)
        wager = max(config.COMPANION_PET_DUEL_MIN_STAKE, min(wager, config.COMPANION_PET_DUEL_MAX_STAKE))

        atk_ids = await self.bot.db.list_equipped_companion_ids(attacker.id, guild_id)
        def_ids = await self.bot.db.list_equipped_companion_ids(opponent.id, guild_id)
        if not atk_ids:
            await interaction.response.send_message(
                "Equip a henchling with `/companion equip` first.", ephemeral=True,
            )
            return
        if not def_ids:
            await interaction.response.send_message(
                f"{opponent.display_name} has no active henchling.", ephemeral=True,
            )
            return

        atk_cid = atk_ids[0]
        def_cid = def_ids[0]
        atk_row = await self.bot.db.get_companion_row(attacker.id, guild_id, atk_cid)
        def_row = await self.bot.db.get_companion_row(opponent.id, guild_id, def_cid)
        if atk_row is None or def_row is None:
            await interaction.response.send_message("Companion data missing.", ephemeral=True)
            return

        for uid, cid in ((attacker.id, atk_cid), (opponent.id, def_cid)):
            refreshed = await self.bot.db.refresh_companion_stamina(uid, guild_id, cid)
            if refreshed is None or int(refreshed["stamina"]) < config.COMPANION_PET_DUEL_STAMINA_COST:
                who = "You" if uid == attacker.id else opponent.display_name
                await interaction.response.send_message(
                    f"{who} need **{config.COMPANION_PET_DUEL_STAMINA_COST}** companion stamina.",
                    ephemeral=True,
                )
                return

        cd = await self.bot.db.get_pet_duel_cooldown_remaining(attacker.id, guild_id)
        if cd > 0:
            await interaction.response.send_message(
                f"Pet duel cooling down — **{cd:.0f}s** remaining.", ephemeral=True,
            )
            return

        if not await self.bot.db.debit_wallet(attacker.id, guild_id, wager):
            await interaction.response.send_message("Insufficient wallet for stake.", ephemeral=True)
            return
        if not await self.bot.db.debit_wallet(opponent.id, guild_id, wager):
            await self.bot.db.credit_wallet(attacker.id, guild_id, wager)
            await interaction.response.send_message(
                f"{opponent.display_name} cannot cover the **{fmt_amount(wager)}** stake.",
                ephemeral=True,
            )
            return

        for uid, cid in ((attacker.id, atk_cid), (opponent.id, def_cid)):
            if not await self.bot.db.spend_companion_stamina(
                uid, guild_id, cid, config.COMPANION_PET_DUEL_STAMINA_COST,
            ):
                await self.bot.db.credit_wallet(attacker.id, guild_id, wager)
                await self.bot.db.credit_wallet(opponent.id, guild_id, wager)
                await interaction.response.send_message("Stamina check failed.", ephemeral=True)
                return

        atk_power, atk_name, atk_emoji = await self._fighter_power(
            attacker.id, guild_id, atk_cid, atk_row,
        )
        def_power, def_name, def_emoji = await self._fighter_power(
            opponent.id, guild_id, def_cid, def_row,
        )

        rounds: list[str] = []
        atk_hp = 100 + atk_power // 2
        def_hp = 100 + def_power // 2
        winner_id = attacker.id
        for rnd in range(1, 6):
            if atk_hp <= 0 or def_hp <= 0:
                break
            a_hit = max(1, int(atk_power * random.uniform(0.7, 1.3)))
            d_hit = max(1, int(def_power * random.uniform(0.7, 1.3)))
            def_hp -= a_hit
            if def_hp > 0:
                atk_hp -= d_hit
            rounds.append(
                f"R{rnd}: {atk_emoji} **-{a_hit}** vs {def_emoji} **-{d_hit}** "
                f"(`{max(0, atk_hp)}` / `{max(0, def_hp)}` HP)",
            )
            if atk_hp <= def_hp:
                winner_id = opponent.id

        if atk_hp > def_hp:
            winner_id = attacker.id
        elif def_hp > atk_hp:
            winner_id = opponent.id
        else:
            winner_id = random.choice([attacker.id, opponent.id])

        pot = wager * 2
        await self.bot.db.credit_wallet(winner_id, guild_id, pot)
        await self.bot.db.record_pet_duel(attacker.id, guild_id)
        await self.bot.db.record_pet_duel(opponent.id, guild_id)

        winner = attacker if winner_id == attacker.id else opponent
        loser = opponent if winner_id == attacker.id else attacker
        w_cid = atk_cid if winner_id == attacker.id else def_cid
        w_name = atk_name if winner_id == attacker.id else def_name
        l_name = def_name if winner_id == attacker.id else atk_name

        embed = discord.Embed(
            title="🐾 Pet Duel",
            description=(
                f"**{atk_emoji} {atk_name}** ({attacker.display_name}) vs "
                f"**{def_emoji} {def_name}** ({opponent.display_name})\n\n"
                + "\n".join(rounds)
                + f"\n\n🏆 **{w_name}** wins! **{winner.display_name}** takes "
                f"**{fmt_amount(pot)}**."
            ),
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"{loser.display_name}'s pet limps home.")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PetDuels(bot))
