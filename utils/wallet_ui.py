from __future__ import annotations

from typing import TYPE_CHECKING

import discord

import config
from utils.helpers import fmt_amount

if TYPE_CHECKING:
    from discord.ext import commands


def build_wallet_embed(
    member: discord.Member,
    *,
    wallet: float,
    bank: float,
    capacity: float,
    storage_tokens: int,
) -> discord.Embed:
    net = wallet + bank
    embed = discord.Embed(
        title=f"{member.display_name}'s Vault",
        color=discord.Color.gold(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Pocket", value=fmt_amount(wallet), inline=True)
    embed.add_field(name="Bank", value=f"{fmt_amount(bank)} / {fmt_amount(capacity)}", inline=True)
    embed.add_field(name="Net worth", value=fmt_amount(net), inline=True)
    embed.add_field(
        name="Storage tokens",
        value=f"**{storage_tokens}** (+{fmt_amount(config.BANK_STORAGE_PER_TOKEN)} each)",
        inline=True,
    )
    at_max = capacity >= config.BANK_MAX_CAPACITY
    upgrade_hint = (
        "Vault maxed at 500k"
        if at_max
        else f"Upgrade: +{fmt_amount(config.BANK_STORAGE_PER_TOKEN)} for "
        f"{fmt_amount(config.BANK_STORAGE_TOKEN_COST)}"
    )
    embed.set_footer(
        text=f"{upgrade_hint} · Bank can be hit by /bank-heist · Pocket is stealable",
    )
    return embed


async def wallet_panel_payload(
    cog: commands.Cog,
    member: discord.Member,
    guild_id: int,
    user_id: int,
) -> tuple[discord.Embed, WalletView]:
    wallet = await cog.bot.db.get_balance(user_id, guild_id)
    bank = await cog.bot.db.get_bank(user_id, guild_id)
    capacity = await cog.bot.db.get_bank_capacity(user_id, guild_id)
    tokens = await cog.bot.db.get_bank_storage_tokens(user_id, guild_id)
    embed = build_wallet_embed(
        member,
        wallet=wallet,
        bank=bank,
        capacity=capacity,
        storage_tokens=tokens,
    )
    return embed, WalletView(cog, guild_id, user_id)


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
        moved = await self.cog.bot.db.deposit_to_bank(self.user_id, self.guild_id, value)
        if moved <= 0:
            room = await self.cog.bot.db.get_bank_deposit_room(self.user_id, self.guild_id)
            if room <= 0:
                await interaction.response.send_message(
                    "Your bank vault is full. Buy a **storage token** in `/balance`.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "You do not have enough nuggets in your pocket.", ephemeral=True,
                )
            return
        await self._refresh(interaction, moved, requested=value)

    async def _refresh(
        self,
        interaction: discord.Interaction,
        moved: float,
        *,
        requested: float,
    ) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message("Deposit complete.", ephemeral=True)
            return
        embed, view = await wallet_panel_payload(
            self.cog, member, self.guild_id, self.user_id,
        )
        note = f"Deposited **{fmt_amount(moved)}**."
        if moved < requested:
            note += " Vault cap reached — upgrade storage for more room."
        await interaction.response.edit_message(content=note, embed=embed, view=view)


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
                "You do not have enough nuggets in your bank.", ephemeral=True,
            )
            return
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message("Withdrawal complete.", ephemeral=True)
            return
        embed, view = await wallet_panel_payload(
            self.cog, member, self.guild_id, self.user_id,
        )
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
                "This is not your vault panel.", ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Deposit", style=discord.ButtonStyle.success, row=0)
    async def deposit_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await interaction.response.send_modal(
            DepositModal(self.cog, self.guild_id, self.user_id),
        )

    @discord.ui.button(label="Withdraw", style=discord.ButtonStyle.primary, row=0)
    async def withdraw_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await interaction.response.send_modal(
            WithdrawModal(self.cog, self.guild_id, self.user_id),
        )

    @discord.ui.button(label="Dep all", style=discord.ButtonStyle.secondary, row=0)
    async def deposit_all(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        moved = await self.cog.bot.db.deposit_all_to_bank(self.user_id, self.guild_id)
        if moved <= 0:
            room = await self.cog.bot.db.get_bank_deposit_room(self.user_id, self.guild_id)
            if room <= 0:
                await interaction.response.send_message(
                    "Bank vault is full. Buy a **storage token** (+10k for 15k).",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "Nothing in your pocket to deposit.", ephemeral=True,
                )
            return
        member = interaction.user
        assert isinstance(member, discord.Member)
        embed, _ = await wallet_panel_payload(
            self.cog, member, self.guild_id, self.user_id,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="With all", style=discord.ButtonStyle.secondary, row=0)
    async def withdraw_all(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        moved = await self.cog.bot.db.withdraw_all_from_bank(self.user_id, self.guild_id)
        if moved <= 0:
            await interaction.response.send_message("Your bank is empty.", ephemeral=True)
            return
        member = interaction.user
        assert isinstance(member, discord.Member)
        embed, _ = await wallet_panel_payload(
            self.cog, member, self.guild_id, self.user_id,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="+10k storage", style=discord.ButtonStyle.primary, row=1)
    async def upgrade_storage(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        err = await self.cog.bot.db.buy_bank_storage_token(self.user_id, self.guild_id)
        if err == "at_max_capacity":
            await interaction.response.send_message(
                "Vault already at **500,000** max capacity.", ephemeral=True,
            )
            return
        if err == "insufficient_funds":
            await interaction.response.send_message(
                f"Need **{fmt_amount(config.BANK_STORAGE_TOKEN_COST)}** in your pocket.",
                ephemeral=True,
            )
            return
        if err:
            await interaction.response.send_message("Could not upgrade storage.", ephemeral=True)
            return
        member = interaction.user
        assert isinstance(member, discord.Member)
        embed, _ = await wallet_panel_payload(
            self.cog, member, self.guild_id, self.user_id,
        )
        await interaction.response.edit_message(
            content=(
                f"Storage upgraded! +**{fmt_amount(config.BANK_STORAGE_PER_TOKEN)}** "
                f"bank capacity (-{fmt_amount(config.BANK_STORAGE_TOKEN_COST)})."
            ),
            embed=embed,
            view=self,
        )
