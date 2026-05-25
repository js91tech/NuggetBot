from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.energy import energy_snapshot
from utils.helpers import fmt_amount, guild_only_message
from utils.jobs import JOBS, get_job, roll_job_payout
from utils.quests import record_quest_event


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

    @app_commands.command(name="jobs", description="Browse jobs and your energy.")
    @app_commands.guild_only()
    async def jobs(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        energy_text, _, cap = await self._energy_display(interaction.user.id, interaction.guild_id)
        lines = [
            f"{job.emoji} **{job.name}** (`{job.job_id}`) — "
            f"**{job.energy_cost}** energy → "
            f"**{fmt_amount(job.payout_min)}–{fmt_amount(job.payout_max)}**"
            for job in JOBS
        ]
        embed = discord.Embed(
            title="Jobs board",
            description="\n".join(lines),
            color=discord.Color.teal(),
        )
        embed.add_field(name="Your energy", value=energy_text, inline=False)
        embed.add_field(
            name="Expand cap",
            value=(
                f"`/upgrade-energy` — **{fmt_amount(config.ENERGY_UPGRADE_COST)}** "
                f"for **+{config.ENERGY_CAP_PER_UPGRADE}** max energy "
                f"(current cap **{cap}**)"
            ),
            inline=False,
        )
        embed.set_footer(text="Instant shifts: /work <job>")
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
        from utils.restrictions import restriction_detail

        blocked = await restriction_detail(
            self.bot.db,
            interaction.user.id,
            interaction.guild_id,
        )
        if blocked is not None:
            await interaction.response.send_message(blocked, ephemeral=True)
            return

        job_def = get_job(job)
        if job_def is None:
            await interaction.response.send_message("Unknown job.", ephemeral=True)
            return

        from utils.classes import get_modifiers
        from utils.stealth_buff import job_payout_multiplier

        class_id = await self.bot.db.get_class_id(interaction.user.id, interaction.guild_id)
        job_mult = get_modifiers(class_id).job_payout_mult
        aspect_mult = (
            await self.bot.db.get_equipped_aspect_bonuses(
                interaction.user.id,
                interaction.guild_id,
            )
        ).work_income_mult
        payout_mult = job_mult * aspect_mult * job_payout_multiplier(interaction.user.id)
        low = int(job_def.payout_min * config.JOB_PAYOUT_MULTIPLIER * payout_mult)
        high = int(job_def.payout_max * config.JOB_PAYOUT_MULTIPLIER * payout_mult)
        range_note = f"**{fmt_amount(low)}–{fmt_amount(high)}** per shift"

        ok, err = await self.bot.db.spend_job_energy(
            interaction.user.id,
            interaction.guild_id,
            job_def.energy_cost,
        )
        if not ok:
            if err == "energy":
                energy_text, current, cap = await self._energy_display(
                    interaction.user.id,
                    interaction.guild_id,
                )
                await interaction.response.send_message(
                    f"Not enough energy. Need **{job_def.energy_cost}**, "
                    f"you have **{current}/{cap}**.\n{energy_text}",
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                "Could not start that shift.",
                ephemeral=True,
            )
            return

        payout = roll_job_payout(job_def, payout_mult=payout_mult)
        await self.bot.db.credit_wallet(
            interaction.user.id,
            interaction.guild_id,
            payout,
        )
        await record_quest_event(
            self.bot.db,
            interaction.guild_id,
            interaction.user.id,
            "job_work",
        )

        energy_text, current, cap = await self._energy_display(
            interaction.user.id,
            interaction.guild_id,
        )
        embed = discord.Embed(
            title=f"{job_def.emoji} {job_def.name} shift complete",
            description=job_def.description,
            color=discord.Color.green(),
        )
        pay_note = f"**+{fmt_amount(payout)}**"
        if aspect_mult > 1.0:
            pay_note += f" (×{aspect_mult:.2f} aspect)"
        embed.add_field(name="Pay", value=pay_note, inline=True)
        embed.add_field(
            name="Energy spent",
            value=f"**-{job_def.energy_cost}** ({current}/{cap} left)",
            inline=True,
        )
        embed.add_field(name="Energy", value=energy_text, inline=False)
        embed.set_footer(text=f"Typical pay band: {range_note}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
