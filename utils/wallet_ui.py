from __future__ import annotations

from typing import TYPE_CHECKING

import discord

import config
from utils.bank_expansion_ui import format_bank_expansion_roster
from utils.helpers import fmt_amount

if TYPE_CHECKING:
    from discord.ext import commands


def build_wallet_embed(
    member: discord.Member,
    *,
    wallet: float,
    bank: float,
    bank_capacity: float | None = None,
    bank_expansions: dict[int, int] | None = None,
) -> discord.Embed:
    net = wallet + bank
    embed = discord.Embed(
        title=f"{member.display_name}'s Vault",
        color=discord.Color.gold(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Pocket", value=fmt_amount(wallet), inline=True)
    if bank_capacity is not None:
        bank_label = f"{fmt_amount(bank)} / {fmt_amount(bank_capacity)}"
    else:
        bank_label = fmt_amount(bank)
    embed.add_field(name="Bank", value=bank_label, inline=True)
    embed.add_field(name="Net worth", value=fmt_amount(net), inline=True)
    if bank_expansions is not None:
        total = sum(bank_expansions.values())
        embed.add_field(
            name="Vault expansions",
            value=f"{format_bank_expansion_roster(bank_expansions)} · **{total}** total",
            inline=False,
        )
    footer = (
        f"Base bank cap {fmt_amount(config.BANK_BASE_CAPACITY)} · "
        f"Use Vault expansions for tiered upgrades · /bank-heist targets bank"
    )
    embed.set_footer(text=footer)
    return embed


async def build_wallet_embed_for_user(
    cog: commands.Cog,
    member: discord.Member,
    guild_id: int,
    user_id: int,
) -> discord.Embed:
    wallet = await cog.bot.db.get_balance(user_id, guild_id)
    bank = await cog.bot.db.get_bank(user_id, guild_id)
    capacity = await cog.bot.db.get_bank_capacity(user_id, guild_id)
    expansions = await cog.bot.db.get_bank_expansions(user_id, guild_id)
    return build_wallet_embed(
        member,
        wallet=wallet,
        bank=bank,
        bank_capacity=capacity,
        bank_expansions=expansions,
    )


class DepositModal(discord.ui.Modal, title="Deposit to bank"):
    amount = discord.ui.TextInput(
        label="Amount",
        placeholder="How many nuggets to deposit?",
        required=True,
        max_length=16,
    )

    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from utils.helpers import valid_amount

        try:
            value = float(str(self.amount.value).replace(",", "").strip())
        except ValueError:
            await interaction.response.send_message("Enter a valid number.", ephemeral=True)
            return
        if not valid_amount(value):
            await interaction.response.send_message("Enter a positive amount.", ephemeral=True)
            return
        wallet = await self.cog.bot.db.get_balance(self.user_id, self.guild_id)
        room = await self.cog.bot.db.get_bank_deposit_room(self.user_id, self.guild_id)
        if wallet < value:
            await interaction.response.send_message(
                "You do not have enough nuggets in your pocket.", ephemeral=True
            )
            return
        if room <= 0:
            await interaction.response.send_message(
                "Your bank is full. Buy a vault expansion with **/expand-bank** "
                "or use the **Vault expansions** button.",
                ephemeral=True,
            )
            return
        ok = await self.cog.bot.db.deposit_to_bank(self.user_id, self.guild_id, value)
        if not ok:
            await interaction.response.send_message(
                "Could not deposit — check pocket balance and bank capacity.",
                ephemeral=True,
            )
            return
        await self._refresh(interaction)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member):
            member = interaction.guild.get_member(self.user_id) if interaction.guild else None
        if member is None:
            await interaction.response.send_message("Deposit complete.", ephemeral=True)
            return
        view = WalletView(self.cog, self.guild_id, self.user_id)
        embed = await build_wallet_embed_for_user(self.cog, member, self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)


class WithdrawModal(discord.ui.Modal, title="Withdraw from bank"):
    amount = discord.ui.TextInput(
        label="Amount",
        placeholder="How many nuggets to withdraw?",
        required=True,
        max_length=16,
    )

    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from utils.helpers import valid_amount

        try:
            value = float(str(self.amount.value).replace(",", "").strip())
        except ValueError:
            await interaction.response.send_message("Enter a valid number.", ephemeral=True)
            return
        if not valid_amount(value):
            await interaction.response.send_message("Enter a positive amount.", ephemeral=True)
            return
        ok = await self.cog.bot.db.withdraw_from_bank(self.user_id, self.guild_id, value)
        if not ok:
            await interaction.response.send_message(
                "You do not have enough nuggets in your bank.", ephemeral=True
            )
            return
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message("Withdrawal complete.", ephemeral=True)
            return
        view = WalletView(self.cog, self.guild_id, self.user_id)
        embed = await build_wallet_embed_for_user(self.cog, member, self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)


class WalletView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=120.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your vault panel.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Deposit", style=discord.ButtonStyle.success)
    async def deposit_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await interaction.response.send_modal(
            DepositModal(self.cog, self.guild_id, self.user_id),
        )

    @discord.ui.button(label="Withdraw", style=discord.ButtonStyle.primary)
    async def withdraw_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await interaction.response.send_modal(
            WithdrawModal(self.cog, self.guild_id, self.user_id),
        )

    @discord.ui.button(label="Dep all", style=discord.ButtonStyle.secondary)
    async def deposit_all(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        wallet_before = await self.cog.bot.db.get_balance(self.user_id, self.guild_id)
        moved = await self.cog.bot.db.deposit_all_to_bank(self.user_id, self.guild_id)
        if moved <= 0:
            if wallet_before <= 0:
                await interaction.response.send_message(
                    "Nothing in your pocket to deposit.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "Bank is full. Use **/expand-bank** or the **Vault expansions** button.",
                    ephemeral=True,
                )
            return
        member = interaction.user
        assert isinstance(member, discord.Member)
        embed = await build_wallet_embed_for_user(self.cog, member, self.guild_id, self.user_id)
        wallet_after = await self.cog.bot.db.get_balance(self.user_id, self.guild_id)
        if wallet_after > 0:
            embed.description = (
                f"Deposited **{fmt_amount(moved)}** — "
                f"**{fmt_amount(wallet_after)}** left in pocket (bank full)."
            )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="With all", style=discord.ButtonStyle.secondary)
    async def withdraw_all(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        moved = await self.cog.bot.db.withdraw_all_from_bank(self.user_id, self.guild_id)
        if moved <= 0:
            await interaction.response.send_message("Your bank is empty.", ephemeral=True)
            return
        member = interaction.user
        assert isinstance(member, discord.Member)
        embed = await build_wallet_embed_for_user(self.cog, member, self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Vault expansions", style=discord.ButtonStyle.success, row=1)
    async def expand_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        from utils.bank_expansion_ui import send_bank_expansion_panel

        await send_bank_expansion_panel(interaction, self.cog)

    @discord.ui.button(label="Bodyguards", style=discord.ButtonStyle.secondary, row=1)
    async def bodyguards_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        from utils.bodyguard_ui import send_bodyguard_panel

        await send_bodyguard_panel(interaction, self.cog)
