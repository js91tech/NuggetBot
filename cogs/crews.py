from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.crew_ui import build_crew_leaderboard_embed, send_crew_panel
from utils.helpers import fmt_amount, guild_only_message, send_error, valid_amount

logger = logging.getLogger(__name__)


class Crews(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

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

    @app_commands.command(
        name="crew",
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
    @app_commands.guild_only()
    async def crew(
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
                    "Repay your crew loan first (`/crew` → Repay loan).", ephemeral=True,
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
                        "insufficient_funds": "Not enough nuggets.",
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
                        "insufficient_treasury": "Crew treasury does not have enough nuggets.",
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
                        "insufficient_funds": "Not enough nuggets in your wallet.",
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
