from __future__ import annotations

import contextlib
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from utils.crew_ui import build_crew_leaderboard_embed, send_crew_panel
from utils.crew_bank_raid_ui import send_crew_bank_raid_panel
from utils.crew_raid_ui import RaidKind, send_crew_raid_panel
from utils.helpers import (
    fmt_amount,
    guild_only_message,
    resolve_main_channel,
    send_error,
    valid_amount,
)

logger = logging.getLogger(__name__)


class Crews(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Guard so unit tests that construct the cog with a stub bot don't start loops.
        if hasattr(bot, "wait_until_ready"):
            self.corporate_war_tick.start()

    crew_group = app_commands.Group(
        name="crew",
        description="Crew bank, membership, and raids.",
        guild_only=True,
    )

    def cog_unload(self) -> None:
        self.corporate_war_tick.cancel()

    @tasks.loop(seconds=3600)
    async def corporate_war_tick(self) -> None:
        for guild in self.bot.guilds:
            try:
                result = await self.bot.db.record_corporate_war_tick(guild.id)
            except Exception:
                logger.exception("corporate war tick failed guild=%s", guild.id)
                continue
            if not result or not result.get("winner"):
                continue
            channel = await resolve_main_channel(guild, self.bot.db)
            if channel is None:
                continue
            embed = discord.Embed(
                title=f"⚔️ Corporate War — Week {result['week']} results",
                description=(
                    f"**{result['winner']}** wins with a score of "
                    f"**{fmt_amount(float(result['winner_score']))}** and earns a "
                    f"**{fmt_amount(float(result['reward']))}** treasury bonus!"
                ),
                color=discord.Color.gold(),
            )
            with contextlib.suppress(discord.HTTPException):
                await channel.send(embed=embed)

    @corporate_war_tick.before_loop
    async def before_corporate_war_tick(self) -> None:
        await self.bot.wait_until_ready()

    async def crew_name_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        action = getattr(interaction.namespace, "action", None)
        if action is not None and str(action) != "join":
            return []
        try:
            if await self.bot.db.get_crew_membership(
                interaction.user.id, interaction.guild_id,
            ):
                return []
            crews = await self.bot.db.list_joinable_crews(
                interaction.guild_id,
                exclude_user_id=interaction.user.id,
            )
        except Exception:
            logger.exception("crew_name_autocomplete failed")
            return []
        needle = (current or "").strip().lower()
        choices: list[app_commands.Choice[str]] = []
        for name, count in crews:
            if needle and needle not in name.lower():
                continue
            label = f"{name} ({count}/8)"
            if len(label) > 100:
                label = label[:97] + "..."
            choices.append(app_commands.Choice(name=label, value=name[:100]))
            if len(choices) >= 25:
                break
        return choices

    async def raid_target_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        try:
            crew = await self.bot.db.get_crew_membership(
                interaction.user.id, interaction.guild_id,
            )
            if not crew:
                return []
            targets = await self.bot.db.list_raidable_crews(
                interaction.guild_id, crew,
            )
        except Exception:
            logger.exception("raid_target_autocomplete failed")
            return []
        needle = (current or "").strip().lower()
        choices: list[app_commands.Choice[str]] = []
        for name, count, treasury in targets:
            if needle and needle not in name.lower():
                continue
            label = f"{name} ({count} members · {fmt_amount(treasury)})"
            if len(label) > 100:
                label = label[:97] + "..."
            choices.append(app_commands.Choice(name=label, value=name[:100]))
            if len(choices) >= 25:
                break
        return choices

    async def drug_raid_target_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        try:
            crew = await self.bot.db.get_crew_membership(
                interaction.user.id, interaction.guild_id,
            )
            if not crew:
                return []
            targets = await self.bot.db.list_raidable_drug_crews(
                interaction.guild_id, crew,
            )
        except Exception:
            logger.exception("drug_raid_target_autocomplete failed")
            return []
        needle = (current or "").strip().lower()
        choices: list[app_commands.Choice[str]] = []
        for name, count, stash_total in targets:
            if needle and needle not in name.lower():
                continue
            label = f"{name} ({count} members · {stash_total} units)"
            if len(label) > 100:
                label = label[:97] + "..."
            choices.append(app_commands.Choice(name=label, value=name[:100]))
            if len(choices) >= 25:
                break
        return choices

    async def business_raid_target_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        try:
            crew = await self.bot.db.get_crew_membership(
                interaction.user.id, interaction.guild_id,
            )
            if not crew:
                return []
            targets = await self.bot.db.list_raidable_business_crews(
                interaction.guild_id, crew,
            )
        except Exception:
            logger.exception("business_raid_target_autocomplete failed")
            return []
        needle = (current or "").strip().lower()
        choices: list[app_commands.Choice[str]] = []
        for name, count, stored in targets:
            if needle and needle not in name.lower():
                continue
            label = f"{name} ({count} members · {fmt_amount(stored)} uncollected)"
            if len(label) > 100:
                label = label[:97] + "..."
            choices.append(app_commands.Choice(name=label, value=name[:100]))
            if len(choices) >= 25:
                break
        return choices

    @crew_group.command(
        name="raid",
        description="Raid another crew's bank. Win duels to steal from their treasury.",
    )
    @app_commands.describe(target_crew="Crew whose bank you want to hit")
    @app_commands.autocomplete(target_crew=raid_target_autocomplete)
    async def crew_raid(
        self,
        interaction: discord.Interaction,
        target_crew: str,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        try:
            await send_crew_bank_raid_panel(self, interaction, target_crew)
        except Exception:
            logger.exception("crew-raid failed guild=%s user=%s", interaction.guild_id, interaction.user.id)
            await send_error(interaction, "Could not open the crew raid panel. Try again in a moment.")

    @crew_group.command(
        name="raid-drugs",
        description="Raid another crew's cartel drug stash. Win duels to steal 2–5 random units.",
    )
    @app_commands.describe(target_crew="Crew whose cartel lab you want to hit")
    @app_commands.autocomplete(target_crew=drug_raid_target_autocomplete)
    async def crew_raid_drugs(
        self,
        interaction: discord.Interaction,
        target_crew: str,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        try:
            await send_crew_raid_panel(self, interaction, target_crew, RaidKind.DRUGS)
        except Exception:
            logger.exception(
                "crew-raid-drugs failed guild=%s user=%s", interaction.guild_id, interaction.user.id,
            )
            await send_error(interaction, "Could not open the drug raid panel. Try again in a moment.")

    @crew_group.command(
        name="raid-business",
        description="Raid another crew's business vaults. Win duels to steal 10% of uncollected income.",
    )
    @app_commands.describe(target_crew="Crew whose businesses you want to hit")
    @app_commands.autocomplete(target_crew=business_raid_target_autocomplete)
    async def crew_raid_business(
        self,
        interaction: discord.Interaction,
        target_crew: str,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        try:
            await send_crew_raid_panel(self, interaction, target_crew, RaidKind.BUSINESS)
        except Exception:
            logger.exception(
                "crew-raid-business failed guild=%s user=%s", interaction.guild_id, interaction.user.id,
            )
            await send_error(
                interaction, "Could not open the business raid panel. Try again in a moment.",
            )

    @crew_group.command(
        name="panel",
        description="Crew bank panel: join, deposit, withdraw, loans, repay, leaderboard.",
    )
    @app_commands.describe(
        action="What to do",
        name="Crew name (Join — pick from autocomplete)",
        amount="Nuggets for deposit, withdraw, loan, or repay",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Bank / status", value="bank"),
            app_commands.Choice(name="Join crew", value="join"),
            app_commands.Choice(name="Leave crew", value="leave"),
            app_commands.Choice(name="Deposit", value="deposit"),
            app_commands.Choice(name="Withdraw", value="withdraw"),
            app_commands.Choice(name="Borrow loan", value="loan"),
            app_commands.Choice(name="Repay loan", value="repay"),
            app_commands.Choice(name="Leaderboard", value="leaderboard"),
        ],
    )
    @app_commands.autocomplete(name=crew_name_autocomplete)
    async def crew_panel(
        self,
        interaction: discord.Interaction,
        action: str,
        name: str | None = None,
        amount: float | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        guild_id = interaction.guild_id
        uid = interaction.user.id

        if action == "leaderboard":
            rows = await self.bot.db.crew_leaderboard(guild_id, limit=10)
            if not rows:
                await interaction.response.send_message(
                    "No crews yet. **Join crew** to found one.", ephemeral=True,
                )
                return
            await interaction.response.send_message(embed=build_crew_leaderboard_embed(rows))
            return

        if action == "join":
            if not name:
                await interaction.response.send_message(
                    "Pick an existing crew from autocomplete or type a **new crew name** (2–32 chars).",
                    ephemeral=True,
                )
                return
            if await self.bot.db.get_crew_membership(uid, guild_id):
                await interaction.response.send_message(
                    "Leave your current crew before joining another.", ephemeral=True,
                )
                return
            err = await self.bot.db.join_crew(uid, guild_id, name)
            messages = {
                "invalid_name": "Crew name must be 2–32 characters.",
                "already_in_crew": "Leave your current crew first.",
                "crew_full": "That crew already has 8 members.",
            }
            if err:
                await interaction.response.send_message(messages.get(err, err), ephemeral=True)
                return
            joined_name = await self.bot.db.resolve_crew_name(guild_id, name) or name.strip()[:32]
            await interaction.response.send_message(
                f"You joined crew **{joined_name}**!", ephemeral=True,
            )
            return

        if action == "leave":
            result = await self.bot.db.leave_crew(uid, guild_id)
            if result is True:
                await interaction.response.send_message("You left your crew.", ephemeral=True)
            elif result == "active_loan":
                await interaction.response.send_message(
                    "Repay your crew loan first (`/crew panel` → Repay loan).", ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "You are not in a crew.", ephemeral=True,
                )
            return

        banking_actions = {"bank", "deposit", "withdraw", "loan", "repay"}
        if action in banking_actions:
            if action == "bank":
                await send_crew_panel(interaction, self)
                return
            await interaction.response.defer(ephemeral=True)
            try:
                if amount is None:
                    await interaction.followup.send(
                        "Set an **amount** for this action.", ephemeral=True,
                    )
                    return
                min_amount = (
                    config.CREW_LOAN_MIN_AMOUNT
                    if action in {"loan", "repay"}
                    else config.CREW_WITHDRAW_MIN
                )
                if not valid_amount(amount, minimum=min_amount):
                    await interaction.followup.send(
                        "Enter a positive amount.", ephemeral=True,
                    )
                    return
                value = float(amount)
                if action == "deposit":
                    err = await self.bot.db.deposit_crew_treasury(uid, guild_id, value)
                    msgs = {
                        "not_in_crew": "Join a crew first.",
                        "insufficient_funds": "Not enough goonbux.",
                        "invalid_amount": "Enter a positive amount.",
                        "treasury_error": "Could not update crew treasury. Try again.",
                    }
                    if err:
                        await interaction.followup.send(msgs.get(err, err), ephemeral=True)
                        return
                    snap = await self.bot.db.get_crew_banking_snapshot(uid, guild_id)
                    treasury = float(snap["treasury"]) if snap else value
                    await interaction.followup.send(
                        f"Deposited **{fmt_amount(value)}** (treasury **{fmt_amount(treasury)}**).",
                        ephemeral=True,
                    )
                    return
                if action == "withdraw":
                    err = await self.bot.db.withdraw_crew_contribution(uid, guild_id, value)
                    msgs = {
                        "not_in_crew": "Join a crew first.",
                        "active_loan": "Repay your loan before withdrawing contributions.",
                        "insufficient_contribution": "You can only withdraw what you deposited.",
                        "insufficient_treasury": "Crew treasury is too low.",
                        "insufficient_funds": "Could not credit your wallet.",
                        "invalid_amount": "Enter a positive amount.",
                    }
                    if err:
                        await interaction.followup.send(msgs.get(err, err), ephemeral=True)
                        return
                    await interaction.followup.send(
                        f"Withdrew **{fmt_amount(value)}** to your wallet.", ephemeral=True,
                    )
                    return
                if action == "loan":
                    err = await self.bot.db.issue_crew_loan(uid, guild_id, value)
                    msgs = {
                        "not_in_crew": "Join a crew first.",
                        "active_loan": "You already have a crew loan. Repay it first.",
                        "amount_too_low": f"Minimum loan is {fmt_amount(config.CREW_LOAN_MIN_AMOUNT)}.",
                        "amount_too_high": "Loan exceeds your crew limit or treasury.",
                        "insufficient_treasury": "Crew treasury does not have enough goonbux.",
                        "no_treasury": "Crew treasury is missing — rejoin or ask an admin.",
                        "invalid_amount": "Enter a positive amount.",
                    }
                    if err:
                        await interaction.followup.send(msgs.get(err, err), ephemeral=True)
                        return
                    await interaction.followup.send(
                        f"Borrowed **{fmt_amount(value)}** from the crew treasury.", ephemeral=True,
                    )
                    return
                if action == "repay":
                    err = await self.bot.db.repay_crew_loan(uid, guild_id, value)
                    msgs = {
                        "no_loan": "You have no active crew loan.",
                        "insufficient_funds": "Not enough goonbux in your wallet.",
                        "invalid_amount": "Enter a positive amount.",
                    }
                    if err:
                        await interaction.followup.send(msgs.get(err, err), ephemeral=True)
                        return
                    loan = await self.bot.db.get_active_crew_loan(uid, guild_id)
                    if loan is not None and float(loan["remaining"]) > 0:
                        await interaction.followup.send(
                            f"Paid **{fmt_amount(value)}**. "
                            f"Remaining: **{fmt_amount(float(loan['remaining']))}**.",
                            ephemeral=True,
                        )
                    else:
                        await interaction.followup.send(
                            f"Loan paid off! (**{fmt_amount(value)}**)", ephemeral=True,
                        )
                    return
            except Exception:
                logger.exception("crew banking action=%s failed", action)
                await send_error(interaction, "Something went wrong. Try again in a moment.")
            return

        await interaction.response.send_message("Unknown action.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Crews(bot))
