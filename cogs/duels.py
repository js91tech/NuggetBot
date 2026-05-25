from __future__ import annotations

import asyncio
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.duel_combat import (
    DuelFighter,
    fighter_from_equipment,
    format_strike_line,
    simulate_duel,
)
from utils.helpers import fmt_amount, guild_only_message
from utils.restrictions import restriction_detail
from utils.skills import get_skill, spell_buff_from_skill
from utils.spell_effects import combat_state_from_spell
from utils.trap_bombs import TRAP_BOMB_GIF_PATH, TRAP_BOMB_ITEM_ID

DUEL_CHALLENGE_SECONDS = 90.0


@dataclass(frozen=True)
class DuelChallenge:
    guild_id: int
    attacker_id: int
    opponent_id: int
    loss_fraction: float
    cooldown_seconds: int
    max_per_hour: int


class DuelAcceptView(discord.ui.View):
    def __init__(
        self,
        cog: Duels,
        challenge: DuelChallenge,
        *,
        attacker_name: str,
        opponent_name: str,
    ) -> None:
        super().__init__(timeout=DUEL_CHALLENGE_SECONDS)
        self.cog = cog
        self.challenge = challenge
        self.attacker_name = attacker_name
        self.opponent_name = opponent_name
        self._resolved = False
        self._lock = asyncio.Lock()

    def _key(self) -> tuple[int, int, int]:
        return (
            self.challenge.guild_id,
            self.challenge.attacker_id,
            self.challenge.opponent_id,
        )

    async def on_timeout(self) -> None:
        self.cog.pending_duels.pop(self._key(), None)
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(
                    content="Duel challenge **expired**.",
                    embed=None,
                    view=self,
                )
            except (discord.HTTPException, discord.NotFound):
                pass

    async def _finish(self, interaction: discord.Interaction, *, accepted: bool) -> None:
        async with self._lock:
            if self._resolved:
                await interaction.response.send_message(
                    "This challenge was already answered.",
                    ephemeral=True,
                )
                return
            self._resolved = True
            for item in self.children:
                item.disabled = True
            self.cog.pending_duels.pop(self._key(), None)

            if not accepted:
                if interaction.user.id != self.challenge.opponent_id:
                    await interaction.response.send_message(
                        "Only the challenged player can decline.",
                        ephemeral=True,
                    )
                    self._resolved = False
                    for item in self.children:
                        item.disabled = False
                    return
                await interaction.response.edit_message(
                    content=(
                        f"**{self.opponent_name}** declined the duel "
                        f"from **{self.attacker_name}**."
                    ),
                    embed=None,
                    view=self,
                )
                self.stop()
                return

            if interaction.user.id != self.challenge.opponent_id:
                await interaction.response.send_message(
                    "Only the challenged player can accept.",
                    ephemeral=True,
                )
                self._resolved = False
                for item in self.children:
                    item.disabled = False
                return

            guild = interaction.guild
            if guild is None or interaction.guild_id != self.challenge.guild_id:
                await interaction.response.send_message("Wrong server.", ephemeral=True)
                self._resolved = False
                return

            attacker = guild.get_member(self.challenge.attacker_id)
            opponent = guild.get_member(self.challenge.opponent_id)
            if attacker is None or opponent is None:
                await interaction.response.edit_message(
                    content="Duel cancelled — a player left the server.",
                    view=self,
                )
                self.stop()
                return

            blocked = await restriction_detail(
                self.cog.bot.db,
                attacker.id,
                self.challenge.guild_id,
            )
            if blocked is not None:
                await interaction.response.edit_message(
                    content=f"Duel cancelled — {blocked}",
                    view=self,
                )
                self.stop()
                return
            blocked = await restriction_detail(
                self.cog.bot.db,
                opponent.id,
                self.challenge.guild_id,
            )
            if blocked is not None:
                await interaction.response.edit_message(
                    content=f"Duel cancelled — opponent: {blocked}",
                    view=self,
                )
                self.stop()
                return

            await interaction.response.defer()
            await self.cog._execute_duel(
                interaction,
                attacker,
                opponent,
                self.challenge,
            )
            self.stop()

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button
        await self._finish(interaction, accepted=True)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def decline(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button
        await self._finish(interaction, accepted=False)


class Duels(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.pending_duels: dict[tuple[int, int, int], DuelChallenge] = {}

    async def _duel_settings(self, guild_id: int) -> tuple[float, float, int]:
        loss_fraction = await self.bot.db.get_config_value(guild_id, "duel_loss_fraction")
        cooldown = await self.bot.db.get_config_value(guild_id, "duel_same_target_cooldown_seconds")
        max_per_hour = int(
            await self.bot.db.get_config_value(guild_id, "duel_max_attacks_per_hour")
        )
        return loss_fraction, cooldown, max_per_hour

    @staticmethod
    def _stake_preview(
        attacker_name: str,
        opponent_name: str,
        *,
        attacker_balance: float,
        opponent_balance: float,
        loss_fraction: float,
    ) -> str:
        loss_pct = int(round(loss_fraction * 100))
        atk_risk = attacker_balance * loss_fraction
        def_risk = opponent_balance * loss_fraction
        return (
            f"**{attacker_name}** challenges **{opponent_name}** ({loss_pct}% wallet transfer)\n"
            f"Your wallet: **{fmt_amount(attacker_balance)}** — you risk up to **{fmt_amount(atk_risk)}**\n"
            f"Their wallet: **{fmt_amount(opponent_balance)}** — they risk up to **{fmt_amount(def_risk)}**"
        )

    async def _execute_duel(
        self,
        interaction: discord.Interaction,
        attacker: discord.Member,
        opponent: discord.Member,
        challenge: DuelChallenge,
    ) -> None:
        guild_id = challenge.guild_id
        loss_fraction = challenge.loss_fraction
        cooldown_seconds = challenge.cooldown_seconds
        max_per_hour = challenge.max_per_hour

        attacker_util = await self.bot.db.get_equipped_aspect_bonuses(attacker.id, guild_id)
        attacker_equipment = await self.bot.db.get_equipment(attacker.id, guild_id)
        defender_equipment = await self.bot.db.get_equipment(opponent.id, guild_id)
        attacker_progress = await self.bot.db.get_user_progress(attacker.id, guild_id)
        defender_progress = await self.bot.db.get_user_progress(opponent.id, guild_id)
        await self.bot.db.ensure_jester_class(attacker.id, guild_id)
        await self.bot.db.ensure_jester_class(opponent.id, guild_id)
        attacker_class = await self.bot.db.get_class_id(attacker.id, guild_id)
        defender_class = await self.bot.db.get_class_id(opponent.id, guild_id)

        attacker_bonuses = await self.bot.db.get_equipped_aspect_bonuses(attacker.id, guild_id)
        defender_bonuses = await self.bot.db.get_equipped_aspect_bonuses(opponent.id, guild_id)
        attacker_bombs = await self.bot.db.get_inventory_quantity(
            attacker.id, guild_id, TRAP_BOMB_ITEM_ID
        )
        defender_bombs = await self.bot.db.get_inventory_quantity(
            opponent.id, guild_id, TRAP_BOMB_ITEM_ID
        )
        initial_attacker_bombs = attacker_bombs
        initial_defender_bombs = defender_bombs

        attacker_fighter = fighter_from_equipment(
            attacker.id,
            attacker.display_name,
            attacker_equipment,
            prestige_level=int(attacker_progress["prestige_level"]),
            class_id=attacker_class,
            aspect_bonuses=attacker_bonuses,
            trap_bomb_count=attacker_bombs,
        )
        defender_fighter = fighter_from_equipment(
            opponent.id,
            opponent.display_name,
            defender_equipment,
            prestige_level=int(defender_progress["prestige_level"]),
            class_id=defender_class,
            aspect_bonuses=defender_bonuses,
            trap_bomb_count=defender_bombs,
        )
        for fighter, uid in (
            (attacker_fighter, attacker.id),
            (defender_fighter, opponent.id),
        ):
            skill_id = await self.bot.db.consume_pending_spell(uid, guild_id)
            if skill_id:
                skill = get_skill(skill_id)
                if skill is not None:
                    fighter.spell_state = combat_state_from_spell(spell_buff_from_skill(skill))

        result = simulate_duel(attacker_fighter, defender_fighter)
        fighters: dict[int, DuelFighter] = {
            attacker_fighter.user_id: attacker_fighter,
            defender_fighter.user_id: defender_fighter,
        }

        trap_procs = sum(1 for s in result.strikes if s.trap_proc is not None)
        for _ in range(max(0, initial_attacker_bombs - attacker_fighter.trap_bomb_count):
            await self.bot.db.consume_inventory_item(
                attacker.id, guild_id, TRAP_BOMB_ITEM_ID
            )
        for _ in range(max(0, initial_defender_bombs - defender_fighter.trap_bomb_count):
            await self.bot.db.consume_inventory_item(
                opponent.id, guild_id, TRAP_BOMB_ITEM_ID
            )

        settlement = await self.bot.db.execute_duel(
            guild_id,
            attacker.id,
            opponent.id,
            result.winner_id,
            loss_fraction=loss_fraction,
            same_target_cooldown_seconds=cooldown_seconds,
            max_attacks_per_hour=max_per_hour,
        )
        if settlement is None:
            await interaction.followup.send(
                "Duel blocked by cooldown limits. Please try again.",
                ephemeral=True,
            )
            return

        await self.bot.db.add_class_xp(result.winner_id, guild_id, config.CLASS_XP_DUEL_WIN)
        await self.bot.db.add_class_xp(result.loser_id, guild_id, config.CLASS_XP_DUEL_LOSS)

        jester_lines: list[str] = []
        for jester_id, victim_id, _ in result.jester_steals:
            steal = await self.bot.db.jester_steal_wallet(victim_id, jester_id, guild_id)
            if steal > 0:
                jester_lines.append(
                    f"**who me?** <@{jester_id}> pockets **{fmt_amount(steal)}** from <@{victim_id}>!"
                )

        loot, _ = settlement
        winner = attacker if result.winner_id == attacker.id else opponent
        loser = opponent if result.winner_id == attacker.id else attacker
        plunder_note = ""
        if result.winner_id == attacker.id and attacker_util.duel_loot_mult > 1.0:
            extra = loot * (attacker_util.duel_loot_mult - 1.0)
            if extra > 0:
                await self.bot.db.credit_wallet(attacker.id, guild_id, extra)
                loot += extra
                plunder_note = (
                    f"\n**Plunderer's Seal** — **+{fmt_amount(extra)}** bonus loot!"
                )
        elif result.winner_id == opponent.id:
            def_util = await self.bot.db.get_equipped_aspect_bonuses(opponent.id, guild_id)
            if def_util.duel_loot_mult > 1.0:
                extra = loot * (def_util.duel_loot_mult - 1.0)
                if extra > 0:
                    await self.bot.db.credit_wallet(opponent.id, guild_id, extra)
                    loot += extra
                    plunder_note = (
                        f"\n**Plunderer's Seal** — **+{fmt_amount(extra)}** bonus loot!"
                    )
        loss_pct = int(round(loss_fraction * 100))

        log_lines = [format_strike_line(s, fighters) for s in result.strikes[:12]]
        if len(result.strikes) > 12:
            log_lines.append(f"_…and {len(result.strikes) - 12} more exchanges_")

        embed = discord.Embed(
            title="Duel resolved",
            description=(
                f"**{winner.display_name}** defeats **{loser.display_name}**!\n"
                f"**{fmt_amount(loot)}** ({loss_pct}% of {loser.display_name}'s wallet) "
                f"transferred to the winner.{plunder_note}"
            ),
            color=discord.Color.red() if result.winner_id == attacker.id else discord.Color.blue(),
        )
        embed.add_field(
            name="Final HP",
            value=(
                f"{attacker_fighter.display_name}: **{attacker_fighter.hp}**/"
                f"{attacker_fighter.max_hp}\n"
                f"{defender_fighter.display_name}: **{defender_fighter.hp}**/"
                f"{defender_fighter.max_hp}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Battle log",
            value="\n".join(log_lines) if log_lines else "No strikes recorded.",
            inline=False,
        )
        base_max = int(
            await self.bot.db.get_config_value(guild_id, "duel_max_attacks_per_hour")
        )
        limit_note = f"{max_per_hour}/hr"
        if max_per_hour > base_max:
            limit_note = f"{max_per_hour}/hr (+{max_per_hour - base_max} from aspect)"
        footer_bits = [
            f"Limits: {limit_note} · {int(cooldown_seconds // 60)}m cooldown vs same player",
        ]
        if trap_procs > 0:
            footer_bits.append(f"{trap_procs} trap bomb(s) detonated")
        embed.set_footer(text=" · ".join(footer_bits))

        files: list[discord.File] = []
        if trap_procs > 0 and TRAP_BOMB_GIF_PATH.is_file():
            files.append(discord.File(str(TRAP_BOMB_GIF_PATH), filename="trap_bomb.gif"))
            embed.set_image(url="attachment://trap_bomb.gif")

        await interaction.followup.send(
            content=f"{attacker.mention} vs {opponent.mention}",
            embed=embed,
            files=files or None,
            allowed_mentions=discord.AllowedMentions(users=[attacker, opponent]),
        )
        for line in jester_lines:
            await interaction.followup.send(
                line,
                allowed_mentions=discord.AllowedMentions.users,
            )

    @app_commands.command(
        name="duel",
        description="Challenge a player to a gear-based fight (they must accept).",
    )
    @app_commands.describe(opponent="Player to challenge")
    @app_commands.guild_only()
    async def duel(self, interaction: discord.Interaction, opponent: discord.Member) -> None:
        if interaction.guild_id is None or interaction.guild is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        attacker = interaction.user
        if not isinstance(attacker, discord.Member):
            await interaction.response.send_message("Invalid attacker.", ephemeral=True)
            return
        if opponent.bot or opponent.id == attacker.id:
            await interaction.response.send_message("Pick another non-bot player.", ephemeral=True)
            return

        guild_id = interaction.guild_id
        blocked = await restriction_detail(self.bot.db, attacker.id, guild_id)
        if blocked is not None:
            await interaction.response.send_message(blocked, ephemeral=True)
            return
        blocked = await restriction_detail(self.bot.db, opponent.id, guild_id)
        if blocked is not None:
            await interaction.response.send_message(
                f"That player cannot duel right now. {blocked}",
                ephemeral=True,
            )
            return

        loss_fraction, cooldown_seconds, max_per_hour = await self._duel_settings(guild_id)
        attacker_util = await self.bot.db.get_equipped_aspect_bonuses(attacker.id, guild_id)
        max_per_hour += attacker_util.extra_duels_per_hour
        cooldown_seconds = int(round(cooldown_seconds * attacker_util.duel_cooldown_mult))

        remaining_target = await self.bot.db.duel_same_target_cooldown_remaining(
            guild_id,
            attacker.id,
            opponent.id,
            cooldown_seconds,
        )
        if remaining_target is not None:
            mins = int(remaining_target // 60)
            secs = int(remaining_target % 60)
            await interaction.response.send_message(
                f"You already dueled {opponent.display_name} recently. "
                f"Try again in **{mins}m {secs}s**.",
                ephemeral=True,
            )
            return

        attacks_last_hour = await self.bot.db.duel_attacks_in_last_hour(guild_id, attacker.id)
        if attacks_last_hour >= max_per_hour:
            await interaction.response.send_message(
                f"You can only start **{max_per_hour}** duels per hour. Try again later.",
                ephemeral=True,
            )
            return

        challenge_key = (guild_id, attacker.id, opponent.id)
        if challenge_key in self.pending_duels:
            await interaction.response.send_message(
                f"You already have a pending challenge vs {opponent.display_name}.",
                ephemeral=True,
            )
            return
        for key in self.pending_duels:
            if key[0] == guild_id and key[2] == opponent.id:
                await interaction.response.send_message(
                    f"{opponent.display_name} already has a pending duel challenge.",
                    ephemeral=True,
                )
                return

        attacker_balance = await self.bot.db.get_balance(attacker.id, guild_id)
        opponent_balance = await self.bot.db.get_balance(opponent.id, guild_id)
        preview = self._stake_preview(
            attacker.display_name,
            opponent.display_name,
            attacker_balance=attacker_balance,
            opponent_balance=opponent_balance,
            loss_fraction=loss_fraction,
        )

        challenge = DuelChallenge(
            guild_id=guild_id,
            attacker_id=attacker.id,
            opponent_id=opponent.id,
            loss_fraction=loss_fraction,
            cooldown_seconds=cooldown_seconds,
            max_per_hour=max_per_hour,
        )
        self.pending_duels[challenge_key] = challenge

        loss_pct = int(round(loss_fraction * 100))
        embed = discord.Embed(
            title="Duel challenge",
            description=(
                f"{attacker.mention} challenges {opponent.mention} to a gear duel.\n"
                f"Loser pays **{loss_pct}%** of their wallet to the winner."
            ),
            color=discord.Color.dark_red(),
        )
        embed.set_footer(text=f"Accept or decline within {int(DUEL_CHALLENGE_SECONDS)} seconds")

        view = DuelAcceptView(
            self,
            challenge,
            attacker_name=attacker.display_name,
            opponent_name=opponent.display_name,
        )
        await interaction.response.send_message(preview, ephemeral=True)
        message = await interaction.followup.send(
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions(users=[attacker, opponent]),
        )
        view.message = message


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Duels(bot))
