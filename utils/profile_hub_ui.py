"""Profile hub — the /profile launcher menu into the other game panels."""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from utils.energy import energy_bar, energy_snapshot
from utils.goon_theme import FOOTER_BRAND, branded_embed, panel_title
from utils.helpers import fmt_amount, guild_only_message

if TYPE_CHECKING:
    from discord.ext import commands

    from database import Database


async def _gather_profile_summary(db: Database, user_id: int, guild_id: int) -> dict:
    """Basics only — wallet, bank, energy, class. Detailed stats live in /stats."""
    wallet = await db.get_balance(user_id, guild_id)
    bank = await db.get_bank(user_id, guild_id)
    char_row = await db.get_user_character(user_id, guild_id)
    regen_per_tick = int(await db.get_config_value(guild_id, "energy_regen_per_tick"))
    tick_seconds = int(await db.get_config_value(guild_id, "energy_regen_interval_seconds"))
    snap = energy_snapshot(
        int(char_row["energy"]),
        int(char_row["energy_cap"]),
        int(char_row["cap_upgrades"]),
        float(char_row["energy_updated_at"]),
        regen_per_tick=regen_per_tick,
        tick_seconds=tick_seconds,
    )
    class_id = await db.get_class_id(user_id, guild_id)
    return {
        "wallet": wallet,
        "bank": bank,
        "energy_current": snap.current,
        "energy_cap": snap.cap,
        "class_id": class_id,
    }


def build_profile_hub_embed(member: discord.Member, summary: dict) -> discord.Embed:
    wallet = float(summary.get("wallet", 0.0))
    bank = float(summary.get("bank", 0.0))
    energy_current = int(summary.get("energy_current", 0))
    energy_cap = int(summary.get("energy_cap", 0))
    class_id = summary.get("class_id")

    embed = branded_embed(
        panel_title("Profile Hub", member_name=member.display_name),
        description=(
            "Your command center. Jump into your vault, gear, jobs, and more "
            "with the buttons below."
        ),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Pocket", value=fmt_amount(wallet), inline=True)
    embed.add_field(name="Bank", value=fmt_amount(bank), inline=True)
    embed.add_field(name="Net worth", value=fmt_amount(wallet + bank), inline=True)
    embed.add_field(
        name="Energy",
        value=f"`{energy_bar(energy_current, energy_cap)}` **{energy_current}/{energy_cap}**",
        inline=True,
    )
    class_text = class_id or "_No class — /class choose_"
    embed.add_field(name="Class", value=f"`{class_text}`", inline=True)
    embed.set_footer(text=f"{FOOTER_BRAND} · /stats for full combat breakdown")
    return embed


class ProfileHubView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your profile hub.", ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Vault", style=discord.ButtonStyle.success, row=0)
    async def vault_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message("Guild only.", ephemeral=True)
            return
        from utils.wallet_ui import WalletView, build_wallet_embed_for_user

        embed = await build_wallet_embed_for_user(
            self.cog, member, self.guild_id, self.user_id,
        )
        view = WalletView(self.cog, self.guild_id, self.user_id)
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    @discord.ui.button(label="Gear", style=discord.ButtonStyle.primary, row=0)
    async def gear_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message("Guild only.", ephemeral=True)
            return
        from utils.gear_hub_ui import GearHubView, build_gear_hub_embed

        embed = await build_gear_hub_embed(self.cog, member, self.guild_id, self.user_id)
        view = GearHubView(self.cog, self.guild_id, self.user_id)
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    @discord.ui.button(label="Jobs / hustle", style=discord.ButtonStyle.primary, row=0)
    async def jobs_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        from utils.jobs_hub_ui import JobsHubView, build_jobs_hub_embed

        view = JobsHubView(self.cog, self.guild_id, self.user_id)
        embed = await build_jobs_hub_embed(
            self.cog, self.guild_id, self.user_id, selected_job=view.selected_job,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    @discord.ui.button(label="Character", style=discord.ButtonStyle.secondary, row=0)
    async def character_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        try:
            from utils.character_hub_ui import CharacterHubView, build_character_embed
        except ImportError:
            from utils.classes import format_modifiers_summary, get_class

            class_id = await self.cog.bot.db.get_class_id(self.user_id, self.guild_id)
            row = await self.cog.bot.db.get_user_character(self.user_id, self.guild_id)
            cls = get_class(class_id)
            if cls is not None:
                description = f"**{cls.emoji} {cls.name}**\n{format_modifiers_summary(cls.modifiers)}"
            else:
                description = "No class yet. Use `/class choose` to pick a starter."
            embed = branded_embed(panel_title("Character"), description=description)
            embed.add_field(name="Class XP", value=str(int(row["class_xp"])), inline=True)
            embed.set_footer(
                text=f"{FOOTER_BRAND} · /class view for full details · /class choose to switch",
            )
            await interaction.response.edit_message(content=None, embed=embed, view=self)
            return

        embed, options, _roots = await build_character_embed(
            self.cog, self.guild_id, self.user_id, interaction.user.display_name,
        )
        view = CharacterHubView(self.cog, self.guild_id, self.user_id, options)
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    @discord.ui.button(label="Crime", style=discord.ButtonStyle.danger, row=1)
    async def crime_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        try:
            from utils.crime_hub_ui import CrimeHubView, build_crime_hub_embed
        except ImportError:
            embed = branded_embed(
                panel_title("Crime"),
                description=(
                    "Heists and bounties run through their own commands for now:\n\n"
                    "`/heist @user` — rob a wallet\n"
                    "`/bounty` — place a bounty on a trigger word\n"
                    "`/bounties` — see active bounties"
                ),
            )
            await interaction.response.edit_message(content=None, embed=embed, view=self)
            return

        embed = build_crime_hub_embed(interaction.user.display_name)
        view = CrimeHubView(self.cog, self.guild_id, self.user_id)
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    @discord.ui.button(label="Casino", style=discord.ButtonStyle.danger, row=1)
    async def casino_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        try:
            from utils.casino_hub_ui import CasinoHubView, build_casino_hub_embed
        except ImportError:
            embed = branded_embed(
                panel_title("Casino"),
                description=(
                    "Table games open with their own dedicated commands:\n\n"
                    "`/coinflip` · `/blackjack` · `/slots` · `/jackpot`"
                ),
            )
            await interaction.response.edit_message(content=None, embed=embed, view=self)
            return

        embed = build_casino_hub_embed(interaction.user.display_name)
        view = CasinoHubView(self.user_id)
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    @discord.ui.button(label="Quests", style=discord.ButtonStyle.secondary, row=1)
    async def quests_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        from utils.quest_ui import build_quest_embeds

        embeds, view = await build_quest_embeds(
            self.cog.bot, self.guild_id, self.user_id,
        )
        await interaction.response.edit_message(
            content=None, embeds=embeds, view=view or self,
        )

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=1)
    async def refresh_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message("Refreshed.", ephemeral=True)
            return
        summary = await _gather_profile_summary(self.cog.bot.db, self.user_id, self.guild_id)
        embed = build_profile_hub_embed(member, summary)
        await interaction.response.edit_message(content=None, embed=embed, view=self)

    @discord.ui.button(label="Chaos", style=discord.ButtonStyle.danger, row=2)
    async def chaos_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        from utils.meta_hub_ui import send_chaos_hub

        await send_chaos_hub(interaction, self.cog)


async def send_profile_hub(
    cog: commands.Cog,
    interaction: discord.Interaction,
    target_member: discord.Member,
) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message(guild_only_message(), ephemeral=True)
        return
    guild_id = interaction.guild_id
    summary = await _gather_profile_summary(cog.bot.db, target_member.id, guild_id)
    embed = build_profile_hub_embed(target_member, summary)

    view: ProfileHubView | None = None
    if target_member.id == interaction.user.id:
        view = ProfileHubView(cog, guild_id, interaction.user.id)
    else:
        embed.set_footer(
            text=f"{FOOTER_BRAND} · Viewing another player — actions are disabled here",
        )

    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
