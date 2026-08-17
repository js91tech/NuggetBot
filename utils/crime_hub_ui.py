"""Crime Hub — one panel to launch pocket heists, bank jobs, and bounty hunting."""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from utils.goon_theme import FOOTER_BRAND, brand_color, branded_embed, panel_title
from utils.helpers import fmt_amount, guild_only_message

if TYPE_CHECKING:
    from discord.ext import commands


def _heist_cog(interaction: discord.Interaction):
    """Resolve the Heist cog dynamically so the hub works no matter who opened it."""
    return interaction.client.get_cog("Heist")


def build_crime_hub_embed(member_name: str) -> discord.Embed:
    embed = branded_embed(
        panel_title("Crime Hub", member_name=member_name),
        description=(
            "Pick your hustle. Pocket jobs are quick and dirty — bank jobs are the "
            "big score if you can beat the vault guards. Snitches get bounties."
        ),
    )
    embed.add_field(
        name="🥷 Pocket Heist",
        value=(
            "Fast wallet grab on another player. Bring crew for better odds.\n"
            "Use `/heist target:@who [crew1] [crew2]`."
        ),
        inline=False,
    )
    embed.add_field(
        name="🏦 Bank Heist",
        value=(
            "High-risk vault break-in — steals from a target's **bank**, not wallet. "
            "Tiered risk/reward, bodyguards can stop you cold.\n"
            "Tap the button below and pick a target."
        ),
        inline=False,
    )
    embed.add_field(
        name="🎯 Bounty Board",
        value="See the fattest live bounties on this server and their trigger words.",
        inline=False,
    )
    embed.add_field(
        name="🚨 Arrests",
        value=(
            "Beat a heist attempt on you? You get a 5-minute window to `/arrest` the thief."
        ),
        inline=False,
    )
    embed.set_footer(text=f"{FOOTER_BRAND} · play dirty")
    return embed


async def build_bounty_board_embed(cog: commands.Cog, guild: discord.Guild) -> discord.Embed:
    rows = list(await cog.bot.db.list_bounties(guild.id))
    rows.sort(key=lambda row: float(row["amount"]), reverse=True)

    embed = branded_embed(
        panel_title("Bounty Board"),
        description="_Top live bounties by reward. Say the trigger word after the target slips up to claim._",
    )
    if not rows:
        embed.add_field(
            name="No active bounties",
            value="Be the first — place one with `/bounty`.",
            inline=False,
        )
        return embed

    lines: list[str] = []
    for index, row in enumerate(rows[:8], start=1):
        target = guild.get_member(int(row["target_id"]))
        target_name = target.display_name if target is not None else f"User {row['target_id']}"
        placer = guild.get_member(int(row["placer_id"]))
        placer_name = placer.display_name if placer is not None else "Unknown"
        lines.append(
            f"**{index}.** #{row['id']} · {target_name} — **{fmt_amount(float(row['amount']))}**\n"
            f"Trigger: `{row['trigger_word']}` · Posted by {placer_name}"
        )
    embed.add_field(name="Top bounties", value="\n\n".join(lines), inline=False)
    embed.set_footer(text=f"{FOOTER_BRAND} · Use /bounty to place your own")
    return embed


class BankHeistTargetSelect(discord.ui.UserSelect):
    def __init__(self, user_id: int) -> None:
        super().__init__(
            placeholder="Choose a bank heist target…",
            min_values=1,
            max_values=1,
            row=0,
        )
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        target = self.values[0]
        if not isinstance(target, discord.Member):
            await interaction.response.send_message(
                "That user is not in this server.", ephemeral=True,
            )
            return
        cog = _heist_cog(interaction)
        if cog is None:
            await interaction.response.send_message(
                "Bank heist is unavailable right now.", ephemeral=True,
            )
            return
        from utils.bank_heist_ui import send_bank_heist_panel

        await send_bank_heist_panel(interaction, cog, target)


class BankHeistTargetView(discord.ui.View):
    def __init__(self, user_id: int) -> None:
        super().__init__(timeout=120.0)
        self.user_id = user_id
        self.add_item(BankHeistTargetSelect(user_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your crime hub.", ephemeral=True)
            return False
        return True


class CrimeHubView(discord.ui.View):
    """Crime Hub controls.

    ``user_id`` locks the panel to a single member (used for the personal
    ``/heist`` shortcut). Leave it ``None`` for public posts like
    ``/bounty-board`` where anyone in the channel should be able to click.
    """

    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int | None = None) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.user_id is not None and interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Open your own crime hub — `/heist` or `/bounty-board`.", ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="🥷 Pocket Heist", style=discord.ButtonStyle.primary, row=0)
    async def pocket_heist_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        embed = branded_embed(
            "🥷 Pocket Heist — how it works",
            description=(
                "Run `/heist target:@who` to try to lift goonbux straight from their "
                "wallet. Bring up to 2 crew members (`crew1`, `crew2`) to boost your "
                "success odds and split the take.\n\n"
                "Fail and the target gets a 5-minute `/arrest` window on you — "
                "succeed and you split whatever you steal with your crew."
            ),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🏦 Bank Heist", style=discord.ButtonStyle.danger, row=0)
    async def bank_heist_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        cog = _heist_cog(interaction)
        if cog is None:
            await interaction.response.send_message(
                "Bank heist is unavailable right now.", ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "Pick a vault to hit:",
            view=BankHeistTargetView(interaction.user.id),
            ephemeral=True,
        )

    @discord.ui.button(label="🎯 Bounty Board", style=discord.ButtonStyle.secondary, row=1)
    async def bounty_board_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        if interaction.guild is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        embed = await build_bounty_board_embed(self.cog, interaction.guild)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🚨 Arrest rules", style=discord.ButtonStyle.secondary, row=1)
    async def arrest_note_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        embed = discord.Embed(
            title="🚨 Arrests",
            description=(
                "When someone's pocket heist against you **fails**, you get a "
                "**5-minute window** to run `/arrest thief:@them` and lock them up. "
                "Missed the window? The arrest option disappears."
            ),
            color=brand_color(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def send_crime_hub(cog: commands.Cog, interaction: discord.Interaction) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message(guild_only_message(), ephemeral=True)
        return
    embed = build_crime_hub_embed(interaction.user.display_name)
    view = CrimeHubView(cog, interaction.guild_id, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
