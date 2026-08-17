"""Jobs hub — pick a hustle, work a shift, and manage your energy cap."""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

import config
from utils.energy import energy_bar, energy_snapshot
from utils.goon_theme import FOOTER_BRAND, branded_embed, panel_title
from utils.helpers import fmt_amount, guild_only_message
from utils.jobs import JOBS, JobDef, get_job, roll_job_payout
from utils.quests import record_quest_event

if TYPE_CHECKING:
    from discord.ext import commands


async def build_jobs_hub_embed(
    cog: commands.Cog,
    guild_id: int,
    user_id: int,
    *,
    selected_job: str | None = None,
    last_result: tuple[JobDef, float, float] | None = None,
) -> discord.Embed:
    row = await cog.bot.db.get_user_character(user_id, guild_id)
    regen_per_tick = int(
        await cog.bot.db.get_config_value(guild_id, "energy_regen_per_tick"),
    )
    tick_seconds = int(
        await cog.bot.db.get_config_value(guild_id, "energy_regen_interval_seconds"),
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
    energy_text = (
        f"`{energy_bar(snap.current, snap.cap)}` **{snap.current}/{snap.cap}** energy\n"
        f"+{snap.regen_per_tick} every {snap.tick_seconds // 60} min · {regen_note}"
    )

    lines = []
    for job in JOBS:
        marker = "➡️ " if job.job_id == selected_job else ""
        lines.append(
            f"{marker}{job.emoji} **{job.name}** (`{job.job_id}`) — "
            f"**{job.energy_cost}** energy → "
            f"**{fmt_amount(job.payout_min)}–{fmt_amount(job.payout_max)}**"
        )

    embed = branded_embed(
        panel_title("Jobs Hub"),
        description="Pick a hustle from the dropdown, then hit **Work / hustle** to run the shift.",
    )
    embed.add_field(name="Jobs", value="\n".join(lines), inline=False)
    embed.add_field(name="Your energy", value=energy_text, inline=False)
    embed.add_field(
        name="Upgrade cap",
        value=(
            f"**{fmt_amount(config.ENERGY_UPGRADE_COST)}** for "
            f"**+{config.ENERGY_CAP_PER_UPGRADE}** max energy "
            f"(current cap **{snap.cap}**)"
        ),
        inline=False,
    )
    if last_result is not None:
        job_def, payout, aspect_mult = last_result
        pay_note = f"**+{fmt_amount(payout)}**"
        if aspect_mult > 1.0:
            pay_note += f" (×{aspect_mult:.2f} aspect)"
        embed.add_field(
            name=f"{job_def.emoji} {job_def.name} shift complete",
            value=f"{job_def.description}\nPay: {pay_note}",
            inline=False,
        )
    embed.set_footer(
        text=f"{FOOTER_BRAND} · /work for instant shifts · /upgrade-energy for cap",
    )
    return embed


class JobSelect(discord.ui.Select):
    def __init__(self, view: JobsHubView) -> None:
        self._view = view
        options = [
            discord.SelectOption(
                label=job.name[:100],
                value=job.job_id,
                description=(
                    f"{job.energy_cost} energy · "
                    f"{fmt_amount(job.payout_min)}-{fmt_amount(job.payout_max)}"
                )[:100],
                emoji=job.emoji,
                default=job.job_id == view.selected_job,
            )
            for job in JOBS
        ]
        super().__init__(
            placeholder="Choose a job / hustle…",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self._view.selected_job = self.values[0]
        for option in self.options:
            option.default = option.value == self._view.selected_job
        embed = await build_jobs_hub_embed(
            self._view.cog,
            self._view.guild_id,
            self._view.user_id,
            selected_job=self._view.selected_job,
        )
        await interaction.response.edit_message(embed=embed, view=self._view)


class JobsHubView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.selected_job: str = JOBS[0].job_id
        self.add_item(JobSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your jobs hub.", ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Work / hustle", style=discord.ButtonStyle.success, row=1)
    async def work_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        if await self.cog.bot.db.is_restricted(self.user_id, self.guild_id):
            await interaction.response.send_message(
                "You cannot work while arrested or downed.", ephemeral=True,
            )
            return

        job_def = get_job(self.selected_job)
        if job_def is None:
            await interaction.response.send_message("Pick a job first.", ephemeral=True)
            return

        ok, err = await self.cog.bot.db.spend_job_energy(
            self.user_id, self.guild_id, job_def.energy_cost,
        )
        if not ok:
            if err == "energy":
                embed = await build_jobs_hub_embed(
                    self.cog, self.guild_id, self.user_id, selected_job=self.selected_job,
                )
                await interaction.response.send_message(
                    f"Not enough energy. Need **{job_def.energy_cost}**.",
                    embed=embed,
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                "Could not start that shift.", ephemeral=True,
            )
            return

        from utils.classes import get_modifiers

        class_id = await self.cog.bot.db.get_class_id(self.user_id, self.guild_id)
        job_mult = get_modifiers(class_id).job_payout_mult
        aspect_mult = (
            await self.cog.bot.db.get_equipped_aspect_bonuses(self.user_id, self.guild_id)
        ).work_income_mult
        payout = roll_job_payout(job_def, payout_mult=job_mult * aspect_mult)
        await self.cog.bot.db.credit_wallet(self.user_id, self.guild_id, payout)
        await record_quest_event(
            self.cog.bot.db, self.guild_id, self.user_id, "job_work",
        )

        embed = await build_jobs_hub_embed(
            self.cog,
            self.guild_id,
            self.user_id,
            selected_job=self.selected_job,
            last_result=(job_def, payout, aspect_mult),
        )
        await interaction.response.edit_message(content=None, embed=embed, view=self)

    @discord.ui.button(label="Upgrade energy", style=discord.ButtonStyle.primary, row=1)
    async def upgrade_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        cost = config.ENERGY_UPGRADE_COST
        wallet = await self.cog.bot.db.get_balance(self.user_id, self.guild_id)
        if wallet < cost:
            await interaction.response.send_message(
                f"You need **{fmt_amount(cost)}** in your wallet "
                f"(have **{fmt_amount(wallet)}**).",
                ephemeral=True,
            )
            return

        upgraded = await self.cog.bot.db.upgrade_energy_cap(
            self.user_id, self.guild_id, cost,
        )
        if not upgraded:
            await interaction.response.send_message(
                f"You need **{fmt_amount(cost)}** in your wallet.", ephemeral=True,
            )
            return

        embed = await build_jobs_hub_embed(
            self.cog, self.guild_id, self.user_id, selected_job=self.selected_job,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=self)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=1)
    async def refresh_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        embed = await build_jobs_hub_embed(
            self.cog, self.guild_id, self.user_id, selected_job=self.selected_job,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=self)


async def send_jobs_hub(cog: commands.Cog, interaction: discord.Interaction) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message(guild_only_message(), ephemeral=True)
        return
    view = JobsHubView(cog, interaction.guild_id, interaction.user.id)
    embed = await build_jobs_hub_embed(
        cog, interaction.guild_id, interaction.user.id, selected_job=view.selected_job,
    )
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
