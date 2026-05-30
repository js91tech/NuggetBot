from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from utils.helpers import fmt_amount

if TYPE_CHECKING:
    from discord.ext import commands


def build_wallet_embed(
    member: discord.Member,
    *,
    wallet: float,
    bank: float,
) -> discord.Embed:
    net = wallet + bank
    embed = discord.Embed(
        title=f"{member.display_name}'s Vault",
        color=discord.Color.gold(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Pocket", value=fmt_amount(wallet), inline=True)
    embed.add_field(name="Bank", value=fmt_amount(bank), inline=True)
    embed.add_field(name="Net worth", value=fmt_amount(net), inline=True)
    embed.set_footer(text="Bank can be hit by /bank-heist · Pocket is spendable & stealable")
    return embed


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
        ok = await self.cog.bot.db.deposit_to_bank(self.user_id, self.guild_id, value)
        if not ok:
            await interaction.response.send_message(
                "You do not have enough nuggets in your pocket.", ephemeral=True
            )
            return
        await self._refresh(interaction)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        wallet = await self.cog.bot.db.get_balance(self.user_id, self.guild_id)
        bank = await self.cog.bot.db.get_bank(self.user_id, self.guild_id)
        member = interaction.user
        if not isinstance(member, discord.Member):
            member = interaction.guild.get_member(self.user_id) if interaction.guild else None
        if member is None:
            await interaction.response.send_message("Deposit complete.", ephemeral=True)
            return
        view = WalletView(self.cog, self.guild_id, self.user_id)
        embed = build_wallet_embed(member, wallet=wallet, bank=bank)
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
        wallet = await self.cog.bot.db.get_balance(self.user_id, self.guild_id)
        bank = await self.cog.bot.db.get_bank(self.user_id, self.guild_id)
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message("Withdrawal complete.", ephemeral=True)
            return
        view = WalletView(self.cog, self.guild_id, self.user_id)
        embed = build_wallet_embed(member, wallet=wallet, bank=bank)
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
        moved = await self.cog.bot.db.deposit_all_to_bank(self.user_id, self.guild_id)
        if moved <= 0:
            await interaction.response.send_message("Nothing in your pocket to deposit.", ephemeral=True)
            return
        wallet = await self.cog.bot.db.get_balance(self.user_id, self.guild_id)
        bank = await self.cog.bot.db.get_bank(self.user_id, self.guild_id)
        member = interaction.user
        assert isinstance(member, discord.Member)
        embed = build_wallet_embed(member, wallet=wallet, bank=bank)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="With all", style=discord.ButtonStyle.secondary)
    async def withdraw_all(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        moved = await self.cog.bot.db.withdraw_all_from_bank(self.user_id, self.guild_id)
        if moved <= 0:
            await interaction.response.send_message("Your bank is empty.", ephemeral=True)
            return
        wallet = await self.cog.bot.db.get_balance(self.user_id, self.guild_id)
        bank = await self.cog.bot.db.get_bank(self.user_id, self.guild_id)
        member = interaction.user
        assert isinstance(member, discord.Member)
        embed = build_wallet_embed(member, wallet=wallet, bank=bank)
        await interaction.response.edit_message(embed=embed, view=self)
