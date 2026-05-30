from __future__ import annotations

import random
import time
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.achievements import evaluate_unlocks, format_unlock_message
from utils.bank_heist_ui import send_bank_heist_panel
from utils.crew_banking import heist_same_crew_bonus
from utils.gear_sets import heist_intimidation_bonus
from utils.bot_players import pvp_target_error
from utils.helpers import fmt_amount, guild_only_message
from utils.jail import bail_cost_for_tier, execute_bail
from utils.jail_ui import send_jail_panel


@dataclass
class BankHeistResult:
    embed: discord.Embed | None = None
    message: str | None = None
    error: str | None = None


class Heist(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.pending_arrests: dict[tuple[int, int], tuple[int, float]] = {}

    @app_commands.command(name="heist", description="Attempt to rob a user.")
    @app_commands.describe(target="Robbery target", crew1="Optional crew member", crew2="Optional crew member")
    @app_commands.guild_only()
    async def heist(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
        crew1: discord.Member | None = None,
        crew2: discord.Member | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        participants = [interaction.user]
        for member in (crew1, crew2):
            if member is not None:
                participants.append(member)

        participant_ids = {member.id for member in participants}
        if len(participant_ids) != len(participants):
            await interaction.response.send_message("Crew members must be unique.", ephemeral=True)
            return
        target_err = pvp_target_error(target, interaction.user.id)
        if target_err:
            await interaction.response.send_message(target_err, ephemeral=True)
            return
        if target.id in participant_ids:
            await interaction.response.send_message(
                "Do not target yourself or a crew member.", ephemeral=True,
            )
            return

        current = time.time()
        thief_row = await self.bot.db.get_user(interaction.user.id, interaction.guild_id)
        heist_cooldown = await self.bot.db.get_config_value(
            interaction.guild_id,
            "heist_cooldown_seconds",
        )
        cooldown_remaining = (float(thief_row["last_heist"]) + heist_cooldown) - current
        if cooldown_remaining > 0:
            await interaction.response.send_message(
                f"Your crew needs {int(cooldown_remaining // 60) + 1} more minutes to regroup.",
                ephemeral=True,
            )
            return

        for member in participants:
            if await self.bot.db.is_restricted(member.id, interaction.guild_id, current):
                await interaction.response.send_message(
                    f"{member.display_name} cannot join a heist right now.",
                    ephemeral=True,
                )
                return

        target_balance = await self.bot.db.get_balance(target.id, interaction.guild_id)
        if target_balance <= 0:
            await interaction.response.send_message("That user has no nuggets to steal.", ephemeral=True)
            return

        await self.bot.db.set_last_heist(interaction.user.id, interaction.guild_id, current)
        base_success = await self.bot.db.get_config_value(interaction.guild_id, "heist_base_success")
        loadout = await self.bot.db.get_combat_loadout(interaction.user.id, interaction.guild_id)
        intimidation = heist_intimidation_bonus(loadout.primary, off_hand=loadout.off_hand)
        from utils.classes import get_modifiers

        class_id = await self.bot.db.get_class_id(interaction.user.id, interaction.guild_id)
        class_mod = get_modifiers(class_id)
        spell_heist = await self.bot.db.take_heist_spell_bonus(
            interaction.user.id,
            interaction.guild_id,
        )
        crew_by_user: dict[int, str | None] = {}
        for member in participants:
            crew_by_user[member.id] = await self.bot.db.get_crew_membership(
                member.id,
                interaction.guild_id,
            )
        persistent_crew_bonus = heist_same_crew_bonus(
            [member.id for member in participants],
            crew_by_user,
        )
        leader_crew = crew_by_user.get(participants[0].id)
        held = await self.bot.db.get_crew_territory_perk_ids(
            interaction.guild_id, leader_crew,
        )
        vault_bonus = (
            config.TERRITORY_PERK_VAULT_HEIST_SUCCESS if "vault" in held else 0.0
        )
        success_chance = min(
            config.HEIST_MAX_SUCCESS,
            base_success
            + (len(participants) - 1) * config.HEIST_CREW_BONUS
            + intimidation
            + class_mod.heist_success_bonus
            + spell_heist
            + persistent_crew_bonus
            + vault_bonus
            - class_mod.heist_success_penalty,
        )

        if random.random() > success_chance:
            self.pending_arrests[(interaction.guild_id, interaction.user.id)] = (
                target.id,
                current + config.HEIST_ARREST_WINDOW_SECONDS,
            )
            await interaction.response.send_message(
                f"The heist failed. {target.mention} can use `/arrest` for the next 5 minutes.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        loot_frac = config.HEIST_LOOT_FRACTION
        if "docks" in held:
            loot_frac *= 1.0 + config.TERRITORY_PERK_DOCKS_HEIST_LOOT
        stolen = await self.bot.db.remove_up_to_balance(
            target.id,
            interaction.guild_id,
            target_balance * loot_frac,
        )
        split = stolen / len(participants)
        for member in participants:
            await self.bot.db.credit_wallet(member.id, interaction.guild_id, split)
        await self.bot.db.increment_progress(
            interaction.user.id,
            interaction.guild_id,
            heists_won=1,
        )

        crew_names = ", ".join(member.mention for member in participants)
        await interaction.response.send_message(
            f"Heist success! {crew_names} stole {fmt_amount(stolen)} from {target.mention}.",
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

    async def execute_bank_heist(
        self,
        thief: discord.Member,
        target: discord.Member,
        guild: discord.Guild,
        *,
        tier: int,
    ) -> BankHeistResult:
        spec = config.BANK_HEIST_TIERS.get(tier)
        if spec is None:
            return BankHeistResult(error="Invalid heist tier.")

        guild_id = guild.id
        current = time.time()
        if await self.bot.db.is_restricted(thief.id, guild_id, current):
            return BankHeistResult(error="You cannot run a bank heist right now.")

        target_bank = await self.bot.db.get_bank(target.id, guild_id)
        if target_bank <= 0:
            return BankHeistResult(error=f"{target.display_name} has nothing in their bank.")

        thief_row = await self.bot.db.get_user(thief.id, guild_id)
        cooldown_left = (
            float(thief_row["last_bank_heist"]) + config.BANK_HEIST_COOLDOWN_SECONDS - current
        )
        if cooldown_left > 0:
            return BankHeistResult(
                error=f"Cooldown — try again in **{int(cooldown_left // 60) + 1}** minutes.",
            )

        await self.bot.db.set_last_bank_heist(thief.id, guild_id, current)
        success_chance = float(spec["success"])
        if random.random() > success_chance:
            jail_seconds = float(spec["jail_seconds"])
            await self.bot.db.set_arrested_until(
                thief.id,
                guild_id,
                current + jail_seconds,
                arrest_tier=str(tier),
            )
            unstable_note = ""
            if tier == 3:
                slot = await self.bot.db.mark_random_equipped_unstable(
                    thief.id,
                    guild_id,
                    chance=float(spec.get("unstable_chance", 0)),
                )
                if slot is not None:
                    equipment = await self.bot.db.get_equipment(thief.id, guild_id)
                    item_id = equipment.get(slot)
                    item = get_item(item_id) if item_id else None
                    name = item.name if item is not None else slot
                    unstable_note = f"\nYour **{name}** ({slot}) is **unstable** — use `/fix`."
            hours = jail_seconds / 3600
            jail_label = f"{int(hours)}h" if hours >= 1 else f"{int(jail_seconds // 60)}m"
            bail = fmt_amount(bail_cost_for_tier(str(tier)))
            embed = discord.Embed(
                title="Bank heist failed!",
                description=(
                    f"Security caught **{thief.display_name}** targeting **{target.display_name}**'s vault.\n"
                    f"Jail time: **{jail_label}** · Bail: **{bail}** (`/jail` or **Jail Key**)."
                    f"{unstable_note}"
                ),
                color=discord.Color.red(),
            )
            return BankHeistResult(embed=embed)

        loot_amount = target_bank * float(spec["loot_fraction"])
        stolen = await self.bot.db.steal_from_bank(
            target.id,
            thief.id,
            guild_id,
            loot_amount,
        )
        embed = discord.Embed(
            title="Bank heist success!",
            description=(
                f"**{thief.display_name}** drained **{fmt_amount(stolen)}** "
                f"from **{target.display_name}**'s bank (Tier {tier})."
            ),
            color=discord.Color.gold(),
        )
        return BankHeistResult(embed=embed)

    @app_commands.command(
        name="bank-heist",
        description="High-risk bank vault robbery — pick tier after choosing a target.",
    )
    @app_commands.describe(target="Whose bank vault to hit")
    @app_commands.guild_only()
    async def bank_heist(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        await send_bank_heist_panel(interaction, self, target)

    @app_commands.command(name="arrest", description="Arrest a thief after a failed heist.")
    @app_commands.describe(thief="The failed thief")
    @app_commands.guild_only()
    async def arrest(self, interaction: discord.Interaction, thief: discord.Member) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if thief.bot and not config.ALLOW_BOT_PLAYERS:
            await interaction.response.send_message("Bots cannot be arrested.", ephemeral=True)
            return

        key = (interaction.guild_id, thief.id)
        pending = self.pending_arrests.get(key)
        current = time.time()
        if pending is None or pending[1] <= current:
            self.pending_arrests.pop(key, None)
            await interaction.response.send_message("There is no arrest window for that thief.", ephemeral=True)
            return
        if pending[0] != interaction.user.id:
            await interaction.response.send_message("Only the heist target can arrest that thief.", ephemeral=True)
            return

        arrest_seconds = await self.bot.db.get_config_value(
            interaction.guild_id,
            "arrest_lockout_seconds",
        )
        await self.bot.db.set_arrested_until(
            thief.id,
            interaction.guild_id,
            current + arrest_seconds,
            arrest_tier="wallet",
        )
        self.pending_arrests.pop(key, None)
        minutes = int(arrest_seconds // 60)
        bail = fmt_amount(bail_cost_for_tier("wallet"))
        await interaction.response.send_message(
            f"{thief.mention} has been arrested for {minutes} minute(s). "
            f"Bail: **{bail}** (`/jail` or `/bail`) or a **Jail Key**.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="jail", description="Open the jail panel — bail, Jail Key, free allies.")
    @app_commands.guild_only()
    async def jail(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        await send_jail_panel(interaction, self)

    @app_commands.command(name="bail", description="Pay bail or open the jail panel.")
    @app_commands.describe(user="Arrested player to bail out (omit to open panel)")
    @app_commands.guild_only()
    async def bail(self, interaction: discord.Interaction, user: discord.Member | None = None) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if user is None:
            await send_jail_panel(interaction, self)
            return
        if user.bot and not config.ALLOW_BOT_PLAYERS:
            await interaction.response.send_message("Bots cannot be bailed out.", ephemeral=True)
            return
        target = user

        result = await execute_bail(
            self.bot.db,
            interaction.user.id,
            target.id,
            interaction.guild_id,
        )
        if not result.ok:
            await interaction.response.send_message(result.error or "Bail failed.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Bail posted",
            description=result.message,
            color=discord.Color.green(),
        )
        if target.id != interaction.user.id:
            embed.add_field(name="Released", value=target.display_name, inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Heist(bot))
