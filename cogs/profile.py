from __future__ import annotations

import time

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.helpers import fmt_amount, guild_only_message
from utils.mana import mana_bar
from utils.territories import TERRITORY_MAP, perks_from_held


class Profile(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="profile", description="Your wallet, class, quests, cooldowns, and ranked stats.")
    @app_commands.describe(user="Player to inspect (defaults to you)")
    @app_commands.guild_only()
    async def profile(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        target = user or interaction.user
        guild_id = interaction.guild_id
        uid = target.id

        wallet = await self.bot.db.get_balance(uid, guild_id)
        bank = await self.bot.db.get_bank(uid, guild_id)
        user_row = await self.bot.db.get_user(uid, guild_id)
        progress = await self.bot.db.get_user_progress(uid, guild_id)
        rating, elo_wins, elo_losses = await self.bot.db.get_duel_elo(uid, guild_id)
        crew = await self.bot.db.get_crew_membership(uid, guild_id)
        crew_loan = await self.bot.db.get_active_crew_loan(uid, guild_id)
        held_territories: list[tuple[str, int]] = []
        if crew:
            held_territories = await self.bot.db.list_crew_held_territories(
                guild_id, crew,
            )
        avatar_id = await self.bot.db.get_equipped_avatar_id(uid, guild_id)
        class_id = await self.bot.db.get_class_id(uid, guild_id)
        drug_stats = await self.bot.db.get_drug_stats(uid, guild_id)
        from utils.dealer_ranks import dealer_rank, rank_title

        dealer_rank_val = dealer_rank(
            units_sold=drug_stats["units_sold"],
            units_harvested=drug_stats["units_harvested"],
        )
        snap = await self.bot.db.get_mana_snapshot(uid, guild_id)
        jackpot = await self.bot.db.get_jackpot_pool(guild_id)

        now = time.time()
        cooldown_lines: list[str] = []
        daily_left = (float(user_row["last_daily"]) + config.DAILY_COOLDOWN_SECONDS) - now
        if daily_left > 0:
            cooldown_lines.append(f"Daily: **{int(daily_left // 60)}m**")
        heist_cd = await self.bot.db.get_config_value(guild_id, "heist_cooldown_seconds")
        heist_left = (float(user_row["last_heist"]) + heist_cd) - now
        if heist_left > 0:
            cooldown_lines.append(f"Heist: **{int(heist_left // 60)}m**")
        if float(user_row["arrested_until"]) > now:
            cooldown_lines.append("**Arrested** — use **Jail Key** or **Pick Key**")
        if float(user_row["downed_until"]) > now:
            cooldown_lines.append("**Downed**")

        embed = discord.Embed(
            title=f"{target.display_name}'s profile",
            color=discord.Color.teal(),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Pocket", value=fmt_amount(wallet), inline=True)
        embed.add_field(name="Bank", value=fmt_amount(bank), inline=True)
        embed.add_field(
            name="Net worth",
            value=fmt_amount(wallet + bank),
            inline=True,
        )
        embed.add_field(
            name="Prestige",
            value=str(int(progress["prestige_level"])),
            inline=True,
        )
        embed.add_field(
            name="Boss kills",
            value=str(int(progress["bosses_killed"])),
            inline=True,
        )
        embed.add_field(
            name="Duel record",
            value=f"**{int(progress['duel_wins'])}** wins · ELO **{rating}** ({elo_wins}W/{elo_losses}L)",
            inline=False,
        )
        embed.add_field(
            name="Mana",
            value=f"`{mana_bar(snap.current, snap.cap)}` {snap.current}/{snap.cap}",
            inline=False,
        )
        class_text = class_id or "_No class — /class-choose_"
        embed.add_field(name="Class", value=f"`{class_text}`", inline=True)
        embed.add_field(name="Avatar", value=f"`{avatar_id}`", inline=True)
        if dealer_rank_val >= config.DEALER_RANK_CARTEL_TITLE:
            embed.add_field(
                name="Dealer title",
                value=f"**{rank_title(dealer_rank_val)}**",
                inline=True,
            )
        elif dealer_rank_val > 1:
            embed.add_field(
                name="Dealer rank",
                value=f"**{rank_title(dealer_rank_val)}** (rank {dealer_rank_val})",
                inline=True,
            )
        crew_value = crew or "_None — /crew join_"
        if crew_loan is not None and float(crew_loan["remaining"]) > 0:
            crew_value += (
                f"\nLoan: **{fmt_amount(float(crew_loan['remaining']))}** remaining"
            )
        if held_territories:
            zone_names = [
                TERRITORY_MAP[tid].name
                for tid, _ in held_territories
                if tid in TERRITORY_MAP
            ]
            if zone_names:
                crew_value += f"\nZones: **{', '.join(zone_names)}**"
            perk_labels = [
                line.split(" — ", 1)[-1]
                for line in perks_from_held({t for t, _ in held_territories}).summary_lines()
            ]
            if perk_labels:
                crew_value += f"\nPerks: {' · '.join(perk_labels)}"
        embed.add_field(
            name="Crew",
            value=crew_value,
            inline=True,
        )
        embed.add_field(
            name="Server jackpot",
            value=fmt_amount(jackpot),
            inline=True,
        )
        if cooldown_lines:
            embed.add_field(
                name="Cooldowns / status",
                value=" · ".join(cooldown_lines),
                inline=False,
            )
        embed.set_footer(text="/stats for gear · /quests for goals · /help for commands")
        ephemeral = target.id != interaction.user.id
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Profile(bot))
