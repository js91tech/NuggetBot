from __future__ import annotations

import time
from typing import TYPE_CHECKING

import discord

import config
from utils.crew_banking import max_loan_amount, perks_summary
from utils.helpers import fmt_amount, valid_amount
from utils.territories import TERRITORY_MAP, format_crew_territory_summary, perks_from_held

if TYPE_CHECKING:
    from cogs.crews import Crews


async def build_crew_embed(
    cog: Crews,
    guild: discord.Guild,
    uid: int,
) -> tuple[discord.Embed | None, str | None]:
    snap = await cog.bot.db.get_crew_banking_snapshot(uid, guild.id)
    if snap is None:
        return None, "You are not in a crew."

    members = await cog.bot.db.list_crew_members(guild.id, snap["crew_name"])
    member_names: list[str] = []
    for row in members[:8]:
        member = guild.get_member(int(row["user_id"]))
        member_names.append(member.display_name if member else f"User {row['user_id']}")

    level = int(snap["level"])
    treasury = float(snap["treasury"])
    wallet = await cog.bot.db.get_balance(uid, guild.id)
    loan = snap["loan"]
    max_borrow = max_loan_amount(treasury, level)

    embed = discord.Embed(
        title=f"Crew {snap['crew_name']} ({len(members)}/8)",
        description="\n".join(member_names) or "_No members_",
        color=discord.Color.blue(),
    )
    embed.add_field(name="Treasury", value=fmt_amount(treasury), inline=True)
    embed.add_field(name="Your deposits", value=fmt_amount(float(snap["contributed"])), inline=True)
    embed.add_field(name="Your pocket", value=fmt_amount(wallet), inline=True)
    embed.add_field(name="Level / XP", value=f"{level} / {int(snap['xp'])}", inline=True)
    embed.add_field(name="Max borrow", value=fmt_amount(max_borrow), inline=True)
    embed.add_field(name="Perks", value=perks_summary(level), inline=False)

    held = await cog.bot.db.list_crew_held_territories(guild.id, snap["crew_name"])
    if held:
        income_total = sum(
            TERRITORY_MAP[t].income_per_hour for t, _ in held if t in TERRITORY_MAP
        )
        embed.add_field(
            name="Territories",
            value=format_crew_territory_summary(held, income_per_hour_total=income_total),
            inline=False,
        )
        perk_lines = perks_from_held({t for t, _ in held}).summary_lines()
        if perk_lines:
            embed.add_field(name="Zone perks", value="\n".join(perk_lines), inline=False)

    if loan is not None:
        remaining = float(loan["remaining"])
        due_at = float(loan["due_at"])
        overdue = time.time() > due_at
        embed.add_field(
            name="Active loan",
            value=(
                f"Remaining **{fmt_amount(remaining)}** of "
                f"{fmt_amount(float(loan['principal']))}"
                f"{' — **overdue**' if overdue else ''}"
            ),
            inline=False,
        )

    embed.set_footer(text="Deposit · Withdraw · Borrow · Repay · /territory for zones")
    return embed, None


def build_no_crew_embed() -> discord.Embed:
    return discord.Embed(
        title="Crew bank",
        description=(
            "Join an existing crew or create your own to share a treasury, "
            "earn crew XP, borrow loans, and stack `/heist` bonuses."
        ),
        color=discord.Color.blue(),
    )


def build_crew_leaderboard_embed(rows: list[object]) -> discord.Embed:
    lines = [
        f"**{i}. {row['crew_name']}** — Lv{int(row['level'])} · "
        f"{fmt_amount(float(row['score']))} treasury · {int(row['xp'])} XP"
        for i, row in enumerate(rows, 1)
    ]
    return discord.Embed(
        title="Crew leaderboard",
        description="\n".join(lines),
        color=discord.Color.dark_gold(),
    )


async def refresh_crew_message(
    interaction: discord.Interaction,
    cog: Crews,
    guild_id: int,
    user_id: int,
) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return

    snap = await cog.bot.db.get_crew_banking_snapshot(user_id, guild_id)
    if snap is None:
        embed = build_no_crew_embed()
        view = await CrewJoinView.build(cog, guild_id, user_id)
        await interaction.response.edit_message(embed=embed, view=view)
        return

    embed, _ = await build_crew_embed(cog, guild, user_id)
    view = CrewPanelView(cog, guild_id, user_id)
    await interaction.response.edit_message(embed=embed, view=view)


class _AmountModal(discord.ui.Modal):
    amount = discord.ui.TextInput(
        label="Amount",
        placeholder="How many nuggets?",
        required=True,
        max_length=16,
    )

    def __init__(
        self,
        cog: Crews,
        guild_id: int,
        user_id: int,
        *,
        title: str,
        minimum: float = 0.01,
    ) -> None:
        super().__init__(title=title)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.minimum = minimum

    def _parse_amount(self, raw: str) -> float | None:
        try:
            value = float(raw.replace(",", "").strip())
        except ValueError:
            return None
        if not valid_amount(value, minimum=self.minimum):
            return None
        return value


class DepositModal(_AmountModal):
    def __init__(self, cog: Crews, guild_id: int, user_id: int) -> None:
        super().__init__(cog, guild_id, user_id, title="Deposit to crew")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        value = self._parse_amount(str(self.amount.value))
        if value is None:
            await interaction.response.send_message("Enter a positive amount.", ephemeral=True)
            return
        err = await self.cog.bot.db.deposit_crew_treasury(self.user_id, self.guild_id, value)
        if err:
            msgs = {
                "not_in_crew": "Join a crew first.",
                "insufficient_funds": "Not enough nuggets in your pocket.",
                "invalid_amount": "Enter a positive amount.",
                "treasury_error": "Could not update crew treasury. Try again.",
            }
            await interaction.response.send_message(msgs.get(err, err), ephemeral=True)
            return
        await refresh_crew_message(interaction, self.cog, self.guild_id, self.user_id)


class WithdrawModal(_AmountModal):
    def __init__(self, cog: Crews, guild_id: int, user_id: int) -> None:
        super().__init__(cog, guild_id, user_id, title="Withdraw from crew")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        value = self._parse_amount(str(self.amount.value))
        if value is None:
            await interaction.response.send_message("Enter a positive amount.", ephemeral=True)
            return
        err = await self.cog.bot.db.withdraw_crew_contribution(
            self.user_id, self.guild_id, value,
        )
        if err:
            msgs = {
                "not_in_crew": "Join a crew first.",
                "active_loan": "Repay your loan before withdrawing contributions.",
                "insufficient_contribution": "You can only withdraw what you deposited.",
                "insufficient_treasury": "Crew treasury is too low.",
                "insufficient_funds": "Could not credit your wallet.",
                "invalid_amount": "Enter a positive amount.",
            }
            await interaction.response.send_message(msgs.get(err, err), ephemeral=True)
            return
        await refresh_crew_message(interaction, self.cog, self.guild_id, self.user_id)


class BorrowModal(_AmountModal):
    def __init__(self, cog: Crews, guild_id: int, user_id: int) -> None:
        super().__init__(
            cog,
            guild_id,
            user_id,
            title="Borrow from crew",
            minimum=config.CREW_LOAN_MIN_AMOUNT,
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        value = self._parse_amount(str(self.amount.value))
        if value is None:
            await interaction.response.send_message(
                f"Enter at least {fmt_amount(config.CREW_LOAN_MIN_AMOUNT)}.",
                ephemeral=True,
            )
            return
        err = await self.cog.bot.db.issue_crew_loan(self.user_id, self.guild_id, value)
        if err:
            msgs = {
                "not_in_crew": "Join a crew first.",
                "active_loan": "You already have a crew loan. Repay it first.",
                "amount_too_low": f"Minimum loan is {fmt_amount(config.CREW_LOAN_MIN_AMOUNT)}.",
                "amount_too_high": "Loan exceeds your crew limit or treasury.",
                "insufficient_treasury": "Crew treasury does not have enough nuggets.",
                "invalid_amount": "Enter a positive amount.",
            }
            await interaction.response.send_message(msgs.get(err, err), ephemeral=True)
            return
        await refresh_crew_message(interaction, self.cog, self.guild_id, self.user_id)


class RepayModal(_AmountModal):
    def __init__(self, cog: Crews, guild_id: int, user_id: int) -> None:
        super().__init__(cog, guild_id, user_id, title="Repay crew loan")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        value = self._parse_amount(str(self.amount.value))
        if value is None:
            await interaction.response.send_message("Enter a positive amount.", ephemeral=True)
            return
        err = await self.cog.bot.db.repay_crew_loan(self.user_id, self.guild_id, value)
        if err:
            msgs = {
                "no_loan": "You have no active crew loan.",
                "insufficient_funds": "Not enough nuggets in your wallet.",
                "invalid_amount": "Enter a positive amount.",
            }
            await interaction.response.send_message(msgs.get(err, err), ephemeral=True)
            return
        await refresh_crew_message(interaction, self.cog, self.guild_id, self.user_id)


class CreateCrewModal(discord.ui.Modal, title="Create crew"):
    name = discord.ui.TextInput(
        label="Crew name",
        placeholder="2–32 characters",
        required=True,
        max_length=32,
    )

    def __init__(self, cog: Crews, guild_id: int, user_id: int) -> None:
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        err = await self.cog.bot.db.join_crew(
            self.user_id,
            self.guild_id,
            str(self.name.value),
        )
        messages = {
            "invalid_name": "Crew name must be 2–32 characters.",
            "already_in_crew": "Leave your current crew first.",
            "crew_full": "That crew already has 8 members.",
        }
        if err:
            await interaction.response.send_message(messages.get(err, err), ephemeral=True)
            return
        await refresh_crew_message(interaction, self.cog, self.guild_id, self.user_id)


class JoinCrewSelect(discord.ui.Select):
    def __init__(self, cog: Crews, guild_id: int, user_id: int, crews: list[tuple[str, int]]) -> None:
        options = [
            discord.SelectOption(
                label=f"{name} ({count}/8)"[:100],
                value=name[:100],
            )
            for name, count in crews[:25]
        ]
        super().__init__(
            placeholder="Join an existing crew…",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        err = await self.cog.bot.db.join_crew(
            self.user_id,
            self.guild_id,
            self.values[0],
        )
        messages = {
            "invalid_name": "Crew name must be 2–32 characters.",
            "already_in_crew": "Leave your current crew first.",
            "crew_full": "That crew already has 8 members.",
        }
        if err:
            await interaction.response.send_message(messages.get(err, err), ephemeral=True)
            return
        await refresh_crew_message(interaction, self.cog, self.guild_id, self.user_id)


class CrewJoinView(discord.ui.View):
    def __init__(self, cog: Crews, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    @classmethod
    async def build(cls, cog: Crews, guild_id: int, user_id: int) -> CrewJoinView:
        view = cls(cog, guild_id, user_id)
        crews = await cog.bot.db.list_joinable_crews(guild_id, exclude_user_id=user_id)
        if crews:
            view.add_item(JoinCrewSelect(cog, guild_id, user_id, crews))
        return view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Open your own crew panel with `/crew`.", ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Create crew", style=discord.ButtonStyle.success, row=1)
    async def create_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await interaction.response.send_modal(
            CreateCrewModal(self.cog, self.guild_id, self.user_id),
        )

    @discord.ui.button(label="Leaderboard", style=discord.ButtonStyle.secondary, row=1)
    async def leaderboard_button(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        rows = await self.cog.bot.db.crew_leaderboard(self.guild_id, limit=10)
        if not rows:
            await interaction.response.send_message(
                "No crews yet. Create one to get started.", ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=build_crew_leaderboard_embed(rows),
            ephemeral=True,
        )


class CrewPanelView(discord.ui.View):
    def __init__(self, cog: Crews, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Open your own crew panel with `/crew`.", ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Deposit", style=discord.ButtonStyle.success, row=0)
    async def deposit_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await interaction.response.send_modal(
            DepositModal(self.cog, self.guild_id, self.user_id),
        )

    @discord.ui.button(label="Withdraw", style=discord.ButtonStyle.primary, row=0)
    async def withdraw_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        snap = await self.cog.bot.db.get_crew_banking_snapshot(self.user_id, self.guild_id)
        if snap is not None and snap["loan"] is not None:
            await interaction.response.send_message(
                "Repay your crew loan before withdrawing.", ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            WithdrawModal(self.cog, self.guild_id, self.user_id),
        )

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=0)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await refresh_crew_message(interaction, self.cog, self.guild_id, self.user_id)

    @discord.ui.button(label="Borrow", style=discord.ButtonStyle.primary, row=1)
    async def borrow_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        snap = await self.cog.bot.db.get_crew_banking_snapshot(self.user_id, self.guild_id)
        if snap is None:
            await interaction.response.send_message("Join a crew first.", ephemeral=True)
            return
        if snap["loan"] is not None:
            await interaction.response.send_message(
                "Repay your current loan before borrowing again.", ephemeral=True,
            )
            return
        if max_loan_amount(float(snap["treasury"]), int(snap["level"])) < config.CREW_LOAN_MIN_AMOUNT:
            await interaction.response.send_message(
                "Treasury is too low to borrow from.", ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            BorrowModal(self.cog, self.guild_id, self.user_id),
        )

    @discord.ui.button(label="Repay", style=discord.ButtonStyle.secondary, row=1)
    async def repay_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        loan = await self.cog.bot.db.get_active_crew_loan(self.user_id, self.guild_id)
        if loan is None:
            await interaction.response.send_message("You have no active crew loan.", ephemeral=True)
            return
        await interaction.response.send_modal(
            RepayModal(self.cog, self.guild_id, self.user_id),
        )

    @discord.ui.button(label="Repay all", style=discord.ButtonStyle.secondary, row=1)
    async def repay_all_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        loan = await self.cog.bot.db.get_active_crew_loan(self.user_id, self.guild_id)
        if loan is None:
            await interaction.response.send_message("You have no active crew loan.", ephemeral=True)
            return
        remaining = float(loan["remaining"])
        wallet = await self.cog.bot.db.get_balance(self.user_id, self.guild_id)
        pay = min(remaining, wallet)
        if pay <= 0:
            await interaction.response.send_message(
                "Not enough nuggets in your pocket to repay.", ephemeral=True,
            )
            return
        err = await self.cog.bot.db.repay_crew_loan(self.user_id, self.guild_id, pay)
        if err:
            await interaction.response.send_message(
                "Could not repay loan. Try again.", ephemeral=True,
            )
            return
        await refresh_crew_message(interaction, self.cog, self.guild_id, self.user_id)

    @discord.ui.button(label="Dep all", style=discord.ButtonStyle.secondary, row=2)
    async def deposit_all_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        wallet = await self.cog.bot.db.get_balance(self.user_id, self.guild_id)
        if wallet <= 0:
            await interaction.response.send_message("Nothing in your pocket to deposit.", ephemeral=True)
            return
        err = await self.cog.bot.db.deposit_crew_treasury(self.user_id, self.guild_id, wallet)
        if err:
            msgs = {
                "not_in_crew": "Join a crew first.",
                "insufficient_funds": "Not enough nuggets in your pocket.",
                "invalid_amount": "Enter a positive amount.",
                "treasury_error": "Could not update crew treasury. Try again.",
            }
            await interaction.response.send_message(msgs.get(err, err), ephemeral=True)
            return
        await refresh_crew_message(interaction, self.cog, self.guild_id, self.user_id)

    @discord.ui.button(label="With all", style=discord.ButtonStyle.secondary, row=2)
    async def withdraw_all_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        snap = await self.cog.bot.db.get_crew_banking_snapshot(self.user_id, self.guild_id)
        if snap is None:
            await interaction.response.send_message("Join a crew first.", ephemeral=True)
            return
        if snap["loan"] is not None:
            await interaction.response.send_message(
                "Repay your crew loan before withdrawing.", ephemeral=True,
            )
            return
        amount = float(snap["contributed"])
        if amount <= 0:
            await interaction.response.send_message("You have no crew deposits to withdraw.", ephemeral=True)
            return
        err = await self.cog.bot.db.withdraw_crew_contribution(
            self.user_id, self.guild_id, amount,
        )
        if err:
            msgs = {
                "insufficient_contribution": "You can only withdraw what you deposited.",
                "insufficient_treasury": "Crew treasury is too low.",
                "insufficient_funds": "Could not credit your wallet.",
            }
            await interaction.response.send_message(msgs.get(err, err), ephemeral=True)
            return
        await refresh_crew_message(interaction, self.cog, self.guild_id, self.user_id)

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.danger, row=2)
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        result = await self.cog.bot.db.leave_crew(self.user_id, self.guild_id)
        if result == "active_loan":
            await interaction.response.send_message(
                "Repay your crew loan before leaving.", ephemeral=True,
            )
            return
        if result is not True:
            await interaction.response.send_message("You are not in a crew.", ephemeral=True)
            return
        embed = build_no_crew_embed()
        view = await CrewJoinView.build(self.cog, self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Leaderboard", style=discord.ButtonStyle.secondary, row=3)
    async def leaderboard_button(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        rows = await self.cog.bot.db.crew_leaderboard(self.guild_id, limit=10)
        if not rows:
            await interaction.response.send_message("No crews yet.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=build_crew_leaderboard_embed(rows),
            ephemeral=True,
        )


async def send_crew_panel(interaction: discord.Interaction, cog: Crews) -> None:
    if interaction.guild_id is None or interaction.guild is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return

    snap = await cog.bot.db.get_crew_banking_snapshot(interaction.user.id, interaction.guild_id)
    if snap is None:
        embed = build_no_crew_embed()
        view = await CrewJoinView.build(cog, interaction.guild_id, interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        return

    embed, err = await build_crew_embed(cog, interaction.guild, interaction.user.id)
    if err or embed is None:
        await interaction.response.send_message(err or "Could not load crew panel.", ephemeral=True)
        return

    view = CrewPanelView(cog, interaction.guild_id, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
