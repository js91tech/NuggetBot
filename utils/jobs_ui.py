from __future__ import annotations

from typing import TYPE_CHECKING

import discord

import config
from utils.helpers import fmt_amount
from utils.jobs import JOBS, get_job, roll_job_payout
from utils.quests import record_quest_event

if TYPE_CHECKING:
    from cogs.jobs import Jobs


def energy_bar(current: int, cap: int, *, length: int = 10) -> str:
    if cap <= 0:
        return "░" * length
    filled = int(round((current / cap) * length))
    filled = max(0, min(length, filled))
    return "█" * filled + "░" * (length - filled)


async def build_jobs_embed(
    cog: Jobs,
    guild_id: int,
    user_id: int,
) -> discord.Embed:
    energy_text, _, cap = await cog._energy_display(user_id, guild_id)
    lines = [
        f"{job.emoji} **{job.name}** — **{job.energy_cost}** energy → "
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
            f"**+{config.ENERGY_CAP_PER_UPGRADE}** max for "
            f"**{fmt_amount(config.ENERGY_UPGRADE_COST)}** (cap **{cap}**)"
        ),
        inline=False,
    )
    embed.set_footer(text="Pick a job below · Instant shift")
    return embed


class JobsView(discord.ui.View):
    def __init__(self, cog: Jobs, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

        options = [
            discord.SelectOption(
                label=f"{job.name} ({job.energy_cost} energy)",
                value=job.job_id,
                emoji=job.emoji,
                description=job.description[:100],
            )
            for job in JOBS
        ]
        select = discord.ui.Select(
            placeholder="Work a shift…",
            options=options,
            row=0,
        )
        select.callback = self._work_callback
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your jobs panel.", ephemeral=True,
            )
            return False
        return True

    async def _work_callback(self, interaction: discord.Interaction) -> None:
        job_id = interaction.data["values"][0]  # type: ignore[index]
        result_embed, err = await execute_job_shift(
            self.cog,
            self.user_id,
            self.guild_id,
            job_id,
        )
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        jobs_embed = await build_jobs_embed(self.cog, self.guild_id, self.user_id)
        view = JobsView(self.cog, self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=jobs_embed, view=view)
        if result_embed is not None:
            await interaction.followup.send(embed=result_embed, ephemeral=True)

    @discord.ui.button(label="Upgrade cap", style=discord.ButtonStyle.primary, row=1)
    async def upgrade_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        upgraded = await self.cog.bot.db.upgrade_energy_cap(
            self.user_id,
            self.guild_id,
            config.ENERGY_UPGRADE_COST,
        )
        if not upgraded:
            await interaction.response.send_message(
                f"You need **{fmt_amount(config.ENERGY_UPGRADE_COST)}** in your wallet.",
                ephemeral=True,
            )
            return
        row = await self.cog.bot.db.get_user_character(self.user_id, self.guild_id)
        embed = await build_jobs_embed(self.cog, self.guild_id, self.user_id)
        view = JobsView(self.cog, self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)
        await interaction.followup.send(
            f"Max energy increased to **{int(row['energy_cap'])}**.",
            ephemeral=True,
        )

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=1)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        embed = await build_jobs_embed(self.cog, self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=self)


async def execute_job_shift(
    cog: Jobs,
    user_id: int,
    guild_id: int,
    job_id: str,
) -> tuple[discord.Embed | None, str | None]:
    if await cog.bot.db.is_restricted(user_id, guild_id):
        return None, "You cannot work while arrested or downed."

    job_def = get_job(job_id)
    if job_def is None:
        return None, "Unknown job."

    ok, err = await cog.bot.db.spend_job_energy(user_id, guild_id, job_def.energy_cost)
    if not ok:
        if err == "energy":
            energy_text, current, cap = await cog._energy_display(user_id, guild_id)
            return None, (
                f"Not enough energy. Need **{job_def.energy_cost}**, "
                f"you have **{current}/{cap}**.\n{energy_text}"
            )
        return None, "Could not start that shift."

    from utils.classes import get_modifiers

    class_id = await cog.bot.db.get_class_id(user_id, guild_id)
    job_mult = get_modifiers(class_id).job_payout_mult
    aspect_mult = (
        await cog.bot.db.get_equipped_aspect_bonuses(user_id, guild_id)
    ).work_income_mult
    payout = roll_job_payout(job_def, payout_mult=job_mult * aspect_mult)
    await cog.bot.db.credit_wallet(user_id, guild_id, payout)
    await record_quest_event(cog.bot.db, guild_id, user_id, "job_work")

    energy_text, current, cap = await cog._energy_display(user_id, guild_id)
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
    return embed, None


async def send_jobs_panel(interaction: discord.Interaction, cog: Jobs) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return
    embed = await build_jobs_embed(cog, interaction.guild_id, interaction.user.id)
    view = JobsView(cog, interaction.guild_id, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
