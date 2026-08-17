"""Casino Hub — coinflip, slots, jackpot, and a blackjack pointer in one panel."""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

import config
from utils.goon_theme import FOOTER_BRAND, branded_embed, panel_title
from utils.helpers import fmt_amount, guild_only_message

if TYPE_CHECKING:
    from cogs.gambling import Gambling


def _gambling_cog(interaction: discord.Interaction) -> Gambling | None:
    """Resolve the Gambling cog dynamically so the hub works no matter who opened it."""
    return interaction.client.get_cog("Gambling")  # type: ignore[return-value]


def build_casino_hub_embed(member_name: str) -> discord.Embed:
    embed = branded_embed(
        panel_title("Casino Hub", member_name=member_name),
        description="The house always takes a cut. Pick your poison below.",
    )
    embed.add_field(
        name="🪙 Coinflip",
        value=(
            f"50/50 vs the house · min **{fmt_amount(config.GAMBLING_MIN_BET)}** · "
            f"max **{fmt_amount(config.GAMBLING_MAX_BET)}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="🎰 Slots",
        value=(
            f"3-reel spin · min **{fmt_amount(config.GAMBLING_MIN_BET)}** · "
            f"max **{fmt_amount(config.SLOTS_MAX_BET)}** · triple 7️⃣ pays **8×**"
        ),
        inline=False,
    )
    embed.add_field(
        name="💰 Jackpot",
        value="Check the server pool — a lucky **/slots** spin can win it all.",
        inline=False,
    )
    embed.add_field(
        name="🃏 Blackjack",
        value="Needs its own hand tracker — run `/blackjack <amount>` to deal in.",
        inline=False,
    )
    embed.set_footer(text=f"{FOOTER_BRAND} · house tax feeds the jackpot pool")
    return embed


class _AmountModal(discord.ui.Modal):
    amount = discord.ui.TextInput(
        label="Goonbux to wager",
        placeholder=f"e.g. {int(config.GAMBLING_MIN_BET) * 5}",
        required=True,
        max_length=14,
    )

    def __init__(self, title: str) -> None:
        super().__init__(title=title)

    def parsed_amount(self) -> float | None:
        try:
            return float(str(self.amount.value).replace(",", "").strip())
        except ValueError:
            return None


class CoinflipAmountModal(_AmountModal):
    def __init__(self) -> None:
        super().__init__(title="Coinflip vs the house")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        amount = self.parsed_amount()
        if amount is None:
            await interaction.response.send_message("Enter a valid number.", ephemeral=True)
            return
        cog = _gambling_cog(interaction)
        if cog is None:
            await interaction.response.send_message(
                "Casino is unavailable right now.", ephemeral=True,
            )
            return
        await cog.play_coinflip_vs_house(interaction, amount)


class SlotsAmountModal(_AmountModal):
    def __init__(self) -> None:
        super().__init__(title="Spin the slots")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        amount = self.parsed_amount()
        if amount is None:
            await interaction.response.send_message("Enter a valid number.", ephemeral=True)
            return
        cog = _gambling_cog(interaction)
        if cog is None:
            await interaction.response.send_message(
                "Casino is unavailable right now.", ephemeral=True,
            )
            return
        await cog.play_slots_vs_house(interaction, amount)


class CasinoHubView(discord.ui.View):
    def __init__(self, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Open your own casino hub with `/casino`.", ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="🪙 Coinflip", style=discord.ButtonStyle.primary, row=0)
    async def coinflip_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        await interaction.response.send_modal(CoinflipAmountModal())

    @discord.ui.button(label="🎰 Slots", style=discord.ButtonStyle.success, row=0)
    async def slots_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        await interaction.response.send_modal(SlotsAmountModal())

    @discord.ui.button(label="💰 Jackpot", style=discord.ButtonStyle.secondary, row=0)
    async def jackpot_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        cog = _gambling_cog(interaction)
        if cog is None:
            await interaction.response.send_message(
                "Casino is unavailable right now.", ephemeral=True,
            )
            return
        await interaction.response.send_message(
            await cog.jackpot_status_text(interaction.guild_id), ephemeral=True,
        )

    @discord.ui.button(label="🃏 Blackjack note", style=discord.ButtonStyle.secondary, row=1)
    async def blackjack_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        embed = branded_embed(
            "🃏 Blackjack",
            description=(
                "Blackjack keeps its own hand tracker so hit/stand can run across "
                "multiple messages — run **/blackjack <amount>** to deal yourself in.\n\n"
                "Natural blackjack pays **2.5×**, a push returns your bet, and the "
                "dealer stands on **17+**."
            ),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def send_casino_hub(cog: object, interaction: discord.Interaction) -> None:
    del cog  # kept for API symmetry with the other hub launchers
    if interaction.guild_id is None:
        await interaction.response.send_message(guild_only_message(), ephemeral=True)
        return
    embed = build_casino_hub_embed(interaction.user.display_name)
    view = CasinoHubView(interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
