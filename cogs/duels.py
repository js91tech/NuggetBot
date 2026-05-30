from __future__ import annotations

import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.achievements import evaluate_unlocks, format_unlock_message
from utils.avatars import build_avatar_embed_files, get_avatar
from utils.bot_players import pvp_target_error
from utils.duel_combat import (
    DuelFighter,
    fighter_from_equipment,
    format_strike_line,
    simulate_duel,
)
from utils.helpers import clip_embed_field, fmt_amount, guild_only_message, send_error
from utils.quests import record_quest_event
from utils.skills import get_skill, spell_buff_from_skill
from utils.spell_effects import combat_state_from_spell
from utils.trap_bombs import TRAP_BOMB_GIF_PATH, TRAP_BOMB_ITEM_ID

logger = logging.getLogger(__name__)


class RematchView(discord.ui.View):
    """Loser opens a 2-minute window to /duel the winner without same-target cooldown."""

    def __init__(self, cog: Duels, guild_id: int, winner_id: int, loser_id: int) -> None:  # noqa: F821
        super().__init__(timeout=120.0)
        self.cog = cog
        self.guild_id = guild_id
        self.winner_id = winner_id
        self.loser_id = loser_id

    @discord.ui.button(label="Rematch", style=discord.ButtonStyle.primary, emoji="🔄")
    async def rematch(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        if interaction.user.id != self.loser_id:
            await interaction.response.send_message(
                "Only the duel loser can request a rematch.", ephemeral=True,
            )
            return
        self.cog.register_rematch_window(
            self.guild_id, self.loser_id, self.winner_id,
        )
        await interaction.response.send_message(
            f"Rematch window open for **2 minutes**. Run `/duel` against "
            f"<@{self.winner_id}> — same-player cooldown waived once.",
            ephemeral=True,
        )


class Duels(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._rematch_windows: dict[tuple[int, int, int], float] = {}

    def register_rematch_window(
        self, guild_id: int, loser_id: int, winner_id: int, *, seconds: float = 120.0,
    ) -> None:
        expires = time.time() + seconds
        self._rematch_windows[(guild_id, loser_id, winner_id)] = expires
        self._rematch_windows[(guild_id, winner_id, loser_id)] = expires

    def consume_rematch_window(
        self, guild_id: int, attacker_id: int, defender_id: int,
    ) -> bool:
        now = time.time()
        for key in (
            (guild_id, attacker_id, defender_id),
            (guild_id, defender_id, attacker_id),
        ):
            exp = self._rematch_windows.get(key)
            if exp is not None and exp > now:
                del self._rematch_windows[key]
                return True
            if exp is not None:
                del self._rematch_windows[key]
        return False

    async def _duel_settings(self, guild_id: int) -> tuple[float, float, int]:
        loss_fraction = await self.bot.db.get_config_value(guild_id, "duel_loss_fraction")
        cooldown = await self.bot.db.get_config_value(guild_id, "duel_same_target_cooldown_seconds")
        max_per_hour = int(
            await self.bot.db.get_config_value(guild_id, "duel_max_attacks_per_hour")
        )
        return loss_fraction, cooldown, max_per_hour

    @app_commands.command(
        name="duel",
        description="Challenge a player to a gear-based fight. Loser pays a % of their wallet to the winner.",
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
        target_err = pvp_target_error(opponent, attacker.id)
        if target_err:
            await interaction.response.send_message(target_err, ephemeral=True)
            return

        guild_id = interaction.guild_id
        if await self.bot.db.is_restricted(attacker.id, guild_id):
            await interaction.response.send_message(
                "You cannot duel while arrested or downed.",
                ephemeral=True,
            )
            return
        if await self.bot.db.is_restricted(opponent.id, guild_id):
            await interaction.response.send_message(
                "That player cannot be dueled right now.",
                ephemeral=True,
            )
            return

        loss_fraction, cooldown_seconds, max_per_hour = await self._duel_settings(guild_id)
        attacker_util = await self.bot.db.get_equipped_aspect_bonuses(attacker.id, guild_id)
        max_per_hour += attacker_util.extra_duels_per_hour
        cooldown_seconds = int(
            round(cooldown_seconds * attacker_util.duel_cooldown_mult)
        )
        skip_target_cd = self.consume_rematch_window(
            guild_id, attacker.id, opponent.id,
        )
        remaining_target = None
        if not skip_target_cd:
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

        await interaction.response.defer()

        try:
            await self._resolve_duel(
                interaction,
                guild_id,
                attacker,
                opponent,
                loss_fraction=loss_fraction,
                cooldown_seconds=cooldown_seconds,
                max_per_hour=max_per_hour,
                skip_target_cd=skip_target_cd,
                attacker_util=attacker_util,
            )
        except Exception:
            logger.exception("duel failed guild=%s attacker=%s", guild_id, attacker.id)
            await send_error(
                interaction,
                "The duel could not be completed. Try again in a moment.",
            )

    async def _resolve_duel(
        self,
        interaction: discord.Interaction,
        guild_id: int,
        attacker: discord.Member,
        opponent: discord.Member,
        *,
        loss_fraction: float,
        cooldown_seconds: int,
        max_per_hour: int,
        skip_target_cd: bool,
        attacker_util: object,
    ) -> None:
        attacker_equipment = await self.bot.db.get_equipment(attacker.id, guild_id)
        defender_equipment = await self.bot.db.get_equipment(opponent.id, guild_id)
        attacker_unstable = await self.bot.db.list_unstable_slots(attacker.id, guild_id)
        defender_unstable = await self.bot.db.list_unstable_slots(opponent.id, guild_id)
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
            unstable_slots=attacker_unstable,
        )
        defender_fighter = fighter_from_equipment(
            opponent.id,
            opponent.display_name,
            defender_equipment,
            prestige_level=int(defender_progress["prestige_level"]),
            class_id=defender_class,
            aspect_bonuses=defender_bonuses,
            trap_bomb_count=defender_bombs,
            unstable_slots=defender_unstable,
        )
        if await self.bot.db.take_pending_consumable(
            attacker.id, guild_id, "duel_scroll",
        ):
            attacker_fighter.consumable_boost = 1.15
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
        for _ in range(
            max(0, initial_attacker_bombs - attacker_fighter.trap_bomb_count)
        ):
            await self.bot.db.consume_inventory_item(
                attacker.id, guild_id, TRAP_BOMB_ITEM_ID
            )
        for _ in range(
            max(0, initial_defender_bombs - defender_fighter.trap_bomb_count)
        ):
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
            skip_same_target_cooldown=skip_target_cd,
        )
        if settlement is None:
            await interaction.followup.send(
                "Duel blocked by cooldown limits. Please try again.",
                ephemeral=True,
            )
            return

        xp_win = config.CLASS_XP_DUEL_WIN
        xp_loss = config.CLASS_XP_DUEL_LOSS
        await self.bot.db.add_class_xp(
            result.winner_id,
            guild_id,
            xp_win,
        )
        await self.bot.db.add_class_xp(result.loser_id, guild_id, xp_loss)

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
                f"{attacker_fighter.display_name}: **{attacker_fighter.hp}**/{attacker_fighter.max_hp}\n"
                f"{defender_fighter.display_name}: **{defender_fighter.hp}**/{defender_fighter.max_hp}"
            ),
            inline=False,
        )
        battle_log = clip_embed_field(
            "\n".join(log_lines) if log_lines else "No strikes recorded.",
        )
        embed.add_field(
            name="Battle log",
            value=battle_log,
            inline=False,
        )
        base_max = int(
            await self.bot.db.get_config_value(guild_id, "duel_max_attacks_per_hour")
        )
        limit_note = f"{max_per_hour}/hr"
        if max_per_hour > base_max:
            limit_note = f"{max_per_hour}/hr (+{max_per_hour - base_max} from aspect)"
        winner_elo = await self.bot.db.get_duel_elo(result.winner_id, guild_id)
        footer_bits = [
            f"Limits: {limit_note} · {int(cooldown_seconds // 60)}m cooldown vs same player",
            f"Winner ELO: **{winner_elo[0]}**",
        ]
        if trap_procs > 0:
            footer_bits.append(f"{trap_procs} trap bomb(s) detonated")
        embed.set_footer(text=" · ".join(footer_bits))

        winner_avatar_id = await self.bot.db.get_equipped_avatar_id(
            result.winner_id, guild_id
        )
        winner_defn = get_avatar(winner_avatar_id)
        if winner_defn:
            embed.add_field(
                name="Victory pose",
                value=f"{winner_defn.emoji} **{winner_defn.name}**",
                inline=False,
            )

        files: list[discord.File] = []
        victory_name: str | None = None
        portrait_name: str | None = None
        try:
            files, victory_name, portrait_name = await build_avatar_embed_files(
                self.bot.db,
                winner_avatar_id,
                guild_id=guild_id,
                user_id=result.winner_id,
            )
            if victory_name:
                embed.set_image(url=f"attachment://{victory_name}")
            if portrait_name:
                embed.set_thumbnail(url=f"attachment://{portrait_name}")
        except Exception:
            logger.exception("Failed to attach winner avatar art for duel embed")
        if trap_procs > 0 and TRAP_BOMB_GIF_PATH.is_file() and not victory_name:
            files.append(discord.File(str(TRAP_BOMB_GIF_PATH), filename="trap_bomb.gif"))
            embed.set_image(url="attachment://trap_bomb.gif")

        rematch_view = RematchView(self, guild_id, winner.id, loser.id)
        await interaction.followup.send(
            content=f"{attacker.mention} vs {opponent.mention}",
            embed=embed,
            files=files or None,
            allowed_mentions=discord.AllowedMentions(users=[attacker, opponent]),
            view=rematch_view,
        )
        await record_quest_event(self.bot.db, guild_id, result.winner_id, "duel_win")
        unlocked = await evaluate_unlocks(self.bot.db, guild_id, result.winner_id)
        if unlocked:
            msg = format_unlock_message(unlocked)
            await interaction.followup.send(msg, ephemeral=True)
        for line in jester_lines:
            await interaction.followup.send(
                line,
                allowed_mentions=discord.AllowedMentions.users,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Duels(bot))
