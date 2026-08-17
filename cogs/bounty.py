from __future__ import annotations

from contextlib import suppress

import discord
from discord import app_commands
from discord.ext import commands

from utils.bot_players import pvp_target_error, skip_gameplay_bot
from utils.crime_hub_ui import CrimeHubView
from utils.helpers import (
    contains_word,
    fmt_amount,
    guild_only_message,
    normalize_trigger_word,
    valid_amount,
)


class Bounty(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.triggered_bounties: set[int] = set()

    @app_commands.command(name="bounty", description="Place a trigger-word bounty on a user.")
    @app_commands.describe(target="Bounty target", amount="Reward amount", trigger_word="Single trigger word")
    @app_commands.guild_only()
    async def bounty(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
        amount: float,
        trigger_word: str,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        target_err = pvp_target_error(target, interaction.user.id)
        if target_err:
            await interaction.response.send_message(target_err, ephemeral=True)
            return
        minimum = await self.bot.db.get_config_value(interaction.guild_id, "bounty_min_amount")
        tax = await self.bot.db.get_config_value(interaction.guild_id, "bounty_bot_tax")
        if not valid_amount(amount, minimum=minimum):
            await interaction.response.send_message(
                f"Bounties must be at least {fmt_amount(minimum)}.",
                ephemeral=True,
            )
            return

        normalized = normalize_trigger_word(trigger_word)
        if normalized is None:
            await interaction.response.send_message(
                "Trigger words must be a single short word using letters, numbers, dashes, or underscores.",
                ephemeral=True,
            )
            return

        bounty_id = await self.bot.db.create_bounty_with_payment(
            interaction.guild_id,
            interaction.user.id,
            target.id,
            amount,
            tax,
            normalized,
        )
        if bounty_id is None:
            total = fmt_amount(amount + tax)
            await interaction.response.send_message(
                f"You need {total} to place that bounty.",
                ephemeral=True,
            )
            return

        with suppress(discord.HTTPException):
            await target.send(
                f"A bounty was placed on you in {interaction.guild.name}. "
                f"Avoid saying `{normalized}`."
            )

        await interaction.response.send_message(
            f"Bounty #{bounty_id} placed on {target.mention} for {fmt_amount(amount)}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="bounties", description="List active bounties.")
    @app_commands.guild_only()
    async def bounties(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        rows = await self.bot.db.list_bounties(interaction.guild.id)
        if not rows:
            await interaction.response.send_message("There are no active bounties.")
            return

        lines = []
        for row in rows[:10]:
            target = interaction.guild.get_member(int(row["target_id"]))
            target_name = target.display_name if target else f"User {row['target_id']}"
            state = "triggered" if int(row["id"]) in self.triggered_bounties else "active"
            lines.append(
                f"#{row['id']} - {target_name}: {fmt_amount(float(row['amount']))} "
                f"for `{row['trigger_word']}` ({state})"
            )

        await interaction.response.send_message("\n".join(lines))

    @app_commands.command(
        name="bounty-board",
        description="Post a public Crime Hub embed of all active bounties in this channel.",
    )
    @app_commands.guild_only()
    async def bounty_board(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        rows = await self.bot.db.list_bounties(interaction.guild.id)
        embed = discord.Embed(
            title=f"{interaction.guild.name} — Bounty board",
            color=discord.Color.dark_red(),
        )
        if not rows:
            embed.description = "_No active bounties. Use `/bounty` to post one._"
        else:
            lines = []
            for row in rows[:15]:
                target = interaction.guild.get_member(int(row["target_id"]))
                target_name = target.display_name if target else f"User {row['target_id']}"
                placer = interaction.guild.get_member(int(row["placer_id"]))
                placer_name = placer.display_name if placer else "Unknown"
                lines.append(
                    f"**#{row['id']}** · {target_name} — {fmt_amount(float(row['amount']))}\n"
                    f"Trigger: `{row['trigger_word']}` · Posted by {placer_name}"
                )
            embed.description = "\n\n".join(lines)
        embed.set_footer(text="Say the trigger word after the target slips up to claim")
        # Attach the full Crime Hub (pocket heist, bank heist, bounties, arrests) so
        # anyone in the channel can jump straight into a hustle from this post.
        view = CrimeHubView(self, interaction.guild.id)
        await interaction.response.send_message(embed=embed, view=view)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if skip_gameplay_bot(message.author) or message.guild is None:
            return

        rows = await self.bot.db.list_bounties(message.guild.id)
        if not rows:
            return

        for row in rows:
            bounty_id = int(row["id"])
            trigger_word = str(row["trigger_word"])
            if not contains_word(message.content, trigger_word):
                continue

            if message.author.id == int(row["target_id"]):
                if bounty_id in self.triggered_bounties:
                    return
                self.triggered_bounties.add(bounty_id)
                await message.channel.send(
                    f"Bounty #{bounty_id} has been triggered. Say `{trigger_word}` to claim it!"
                )
                return

            if bounty_id not in self.triggered_bounties:
                continue
            if message.author.id in {int(row["placer_id"]), int(row["target_id"])}:
                continue

            await self.bot.db.credit_wallet(message.author.id, message.guild.id, float(row["amount"]))
            await self.bot.db.delete_bounty(bounty_id, message.guild.id)
            self.triggered_bounties.discard(bounty_id)
            await message.channel.send(
                f"{message.author.mention} claimed bounty #{bounty_id} for "
                f"{fmt_amount(float(row['amount']))}!",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Bounty(bot))
