from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from utils.expansion_events import ensure_guild_contracts
from utils.helpers import fmt_amount, guild_only_message
from utils.meta_hub_ui import send_contracts_hub


class Contracts(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="contracts", description="View and claim active contracts.")
    @app_commands.describe(action="List or claim", contract_id="Contract to claim")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="List", value="list"),
            app_commands.Choice(name="Claim", value="claim"),
        ],
    )
    @app_commands.guild_only()
    async def contracts(
        self,
        interaction: discord.Interaction,
        action: str,
        contract_id: str | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        gid = interaction.guild_id
        uid = interaction.user.id
        await ensure_guild_contracts(self.bot.db, gid)

        if action == "list":
            await send_contracts_hub(interaction, self)
            return

        if action == "claim":
            if not contract_id:
                await interaction.response.send_message("Provide a contract id.", ephemeral=True)
                return
            reward = await self.bot.db.claim_contract(uid, gid, contract_id)
            if reward is None:
                await interaction.response.send_message(
                    "Contract incomplete, already claimed, or invalid.", ephemeral=True,
                )
                return
            if reward["nuggets"] > 0:
                await self.bot.db.credit_wallet(uid, gid, float(reward["nuggets"]))
            if reward["tokens"] > 0:
                season, _ = await self.bot.db.get_elo_season(gid)
                await self.bot.db.add_season_tokens(uid, gid, int(reward["tokens"]), season)
            if reward["item_id"]:
                for _ in range(int(reward["qty"])):
                    await self.bot.db.grant_item(uid, gid, str(reward["item_id"]))
            parts = [fmt_amount(float(reward["nuggets"]))] if reward["nuggets"] else []
            if reward["tokens"]:
                parts.append(f"{reward['tokens']} season tokens")
            if reward["item_id"]:
                parts.append(f"`{reward['item_id']}` ×{reward['qty']}")
            await interaction.response.send_message(
                f"Contract claimed! Rewards: {' + '.join(parts)}", ephemeral=True,
            )
            return

        await interaction.response.send_message("Unknown action.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Contracts(bot))
