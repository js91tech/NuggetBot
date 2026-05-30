from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.energy import energy_snapshot
from utils.helpers import fmt_amount, guild_only_message
from utils.jobs import JOBS, get_job
from utils.jobs_ui import execute_job_shift, send_jobs_panel


def _energy_bar(current: int, cap: int, *, length: int = 10) -> str:
    if cap <= 0:
        return "░" * length
    filled = int(round((current / cap) * length))
    filled = max(0, min(length, filled))
    return "█" * filled + "░" * (length - filled)


class Jobs(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _energy_display(self, user_id: int, guild_id: int) -> tuple[str, int, int]:
        row = await self.bot.db.get_user_character(user_id, guild_id)
        regen_per_tick = int(
            await self.bot.db.get_config_value(guild_id, "energy_regen_per_tick")
        )
        tick_seconds = int(
            await self.bot.db.get_config_value(guild_id, "energy_regen_interval_seconds")
        )
        snap = energy_snapshot(
            int(row["energy"]),
            int(row["energy_cap"]),
            int(row["cap_upgrades"]),
            float(row["energy_updated_at"]),
            regen_per_tick=regen_per_tick,
            tick_seconds=tick_seconds,
        )
        mins = snap.seconds_until_tick // 60
        secs = snap.seconds_until_tick % 60
        regen_note = (
            f"Next **+{snap.regen_per_tick}** in **{mins}m {secs}s**"
            if snap.seconds_until_tick > 0
            else "Full — regen paused until you spend energy"
        )
        text = (
            f"`{_energy_bar(snap.current, snap.cap)}` **{snap.current}/{snap.cap}** energy\n"
            f"+{snap.regen_per_tick} every {snap.tick_seconds // 60} min · {regen_note}"
        )
        return text, snap.current, snap.cap

    @app_commands.command(name="jobs", description="Open the jobs board panel.")
    @app_commands.guild_only()
    async def jobs(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        await send_jobs_panel(interaction, self)

    @app_commands.command(name="work", description="Work a job instantly (costs energy).")
    @app_commands.describe(job="Job to work")
    @app_commands.choices(
        job=[app_commands.Choice(name=f"{j.emoji} {j.name}", value=j.job_id) for j in JOBS],
    )
    @app_commands.guild_only()
    async def work(self, interaction: discord.Interaction, job: str) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if await self.bot.db.is_restricted(interaction.user.id, interaction.guild_id):
            await interaction.response.send_message(
                "You cannot work while arrested or downed.",
                ephemeral=True,
            )
            return

        job_def = get_job(job)
        if job_def is None:
            await interaction.response.send_message("Unknown job.", ephemeral=True)
            return

        result_embed, err = await execute_job_shift(
            self,
            interaction.user.id,
            interaction.guild_id,
            job,
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await interaction.response.send_message(embed=result_embed, ephemeral=True)

    @app_commands.command(
        name="upgrade-energy",
        description=f"Raise max energy by {config.ENERGY_CAP_PER_UPGRADE} "
        f"for {int(config.ENERGY_UPGRADE_COST):,} coins.",
    )
    @app_commands.guild_only()
    async def upgrade_energy(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        upgraded = await self.bot.db.upgrade_energy_cap(
            interaction.user.id,
            interaction.guild_id,
            config.ENERGY_UPGRADE_COST,
        )
        if not upgraded:
            await interaction.response.send_message(
                f"You need **{fmt_amount(config.ENERGY_UPGRADE_COST)}** in your wallet.",
                ephemeral=True,
            )
            return

        row = await self.bot.db.get_user_character(interaction.user.id, interaction.guild_id)
        await interaction.response.send_message(
            f"Max energy increased to **{int(row['energy_cap'])}** "
            f"(**+{config.ENERGY_CAP_PER_UPGRADE}** per upgrade, "
            f"{int(row['cap_upgrades'])} upgrades bought).",
            ephemeral=True,
        )

    @app_commands.command(name="energy", description="Check your job energy.")
    @app_commands.guild_only()
    async def energy(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        energy_text, current, cap = await self._energy_display(
            interaction.user.id,
            interaction.guild_id,
        )
        row = await self.bot.db.get_user_character(interaction.user.id, interaction.guild_id)
        await interaction.response.send_message(
            f"{energy_text}\n"
            f"Cap upgrades: **{int(row['cap_upgrades'])}** · "
            f"Next cap upgrade: **{fmt_amount(config.ENERGY_UPGRADE_COST)}**",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Jobs(bot))
