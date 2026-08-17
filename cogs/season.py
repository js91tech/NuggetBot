from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.aspects import random_aspect_definition, roll_pct_shop
from utils.helpers import guild_only_message
from utils.meta_hub_ui import send_season_hub


class Season(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="season", description="Duel ELO season status, shop, or admin reset.")
    @app_commands.describe(action="Status, shop, or reset (admin)", reward="Season shop reward")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Status", value="status"),
            app_commands.Choice(name="Shop", value="shop"),
            app_commands.Choice(name="Redeem", value="redeem"),
            app_commands.Choice(name="Reset ELO (admin)", value="reset"),
        ],
        reward=[
            app_commands.Choice(name="Raider Title (50)", value="title_raider"),
            app_commands.Choice(name="Season Gold Avatar (120)", value="avatar_season_gold"),
            app_commands.Choice(name="Plunder Aspect (200)", value="aspect_season_plunder"),
            app_commands.Choice(name="Plunderer's Seal Relic (300)", value="relic_plunder_seal"),
        ],
    )
    @app_commands.guild_only()
    async def season(
        self,
        interaction: discord.Interaction,
        action: str,
        reward: str | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        guild_id = interaction.guild_id
        uid = interaction.user.id

        if action == "reset":
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("Admin only.", ephemeral=True)
                return
            new_season = await self.bot.db.reset_elo_season(guild_id)
            await interaction.response.send_message(
                f"**Season {new_season}** started. All duel ELO ratings reset to "
                f"**{config.DUEL_ELO_START}**.",
                ephemeral=True,
            )
            return

        if action == "shop":
            await send_season_hub(interaction, self, mode="shop")
            return

        season_num, _last_reset = await self.bot.db.get_elo_season(guild_id)

        if action == "redeem":
            if not reward or reward not in config.SEASON_TOKEN_SHOP:
                await interaction.response.send_message("Pick a reward.", ephemeral=True)
                return
            cost, kind = config.SEASON_TOKEN_SHOP[reward]
            if not await self.bot.db.redeem_season_reward(uid, guild_id, season_num, reward, cost):
                await interaction.response.send_message(
                    "Not enough tokens or already redeemed.", ephemeral=True,
                )
                return
            if kind == "aspect":
                defn = random_aspect_definition()
                roll = roll_pct_shop()
                await self.bot.db.create_aspect_instance(uid, guild_id, defn.id, roll)
                await interaction.response.send_message(
                    f"Redeemed **{defn.name}** aspect ({roll:.1f}%)!", ephemeral=True,
                )
            elif kind == "relic":
                await self.bot.db.create_relic_instance(uid, guild_id, "relic_plunder_seal")
                await interaction.response.send_message(
                    "Redeemed **Plunderer's Seal** relic!", ephemeral=True,
                )
            elif kind == "avatar":
                await self.bot.db.unlock_avatar(uid, guild_id, "season_gold")
                await interaction.response.send_message(
                    "Unlocked **Season Gold** avatar!", ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "Redeemed **Raider** title! Show it off in duels.", ephemeral=True,
                )
            return

        await send_season_hub(interaction, self, mode="status")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Season(bot))
