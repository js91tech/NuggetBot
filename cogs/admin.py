from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.classes import is_silent_power_user
from utils.helpers import fmt_amount, guild_only_message, valid_amount

CONFIG_CHOICES = list(config.LIVE_SETTINGS)
MAX_CURRENCY_AMOUNT = 1_000_000_000_000.0
ADMIN_GRANT_DENIED = "You don't have permission to use this command."


def _format_config_value(value: float) -> str:
    return str(int(value)) if value == int(value) else f"{value:g}"


def _custom_marker(setting: str, custom_settings: set[str]) -> str:
    return "custom" if setting in custom_settings else "default"


def can_use_discord_admin_grants(user_id: int) -> bool:
    """Discord free-grant admin commands are limited to the silent-power user.

    Dashboard inventory grants stay available to anyone with dashboard access.
    """
    return is_silent_power_user(user_id)


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    admin_group = app_commands.Group(
        name="admin",
        description="Server administration tools.",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    async def _setting_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        del interaction
        current_lower = current.lower()
        matches = [
            setting
            for setting in CONFIG_CHOICES
            if current_lower in setting.lower()
        ][:25]
        return [app_commands.Choice(name=setting, value=setting) for setting in matches]

    @staticmethod
    def _valid_currency_amount(amount: float, *, allow_zero: bool = False) -> bool:
        minimum = 0.0 if allow_zero else 0.01
        return valid_amount(amount, minimum=minimum) and amount <= MAX_CURRENCY_AMOUNT

    async def _require_discord_admin_grants(
        self, interaction: discord.Interaction,
    ) -> bool:
        if can_use_discord_admin_grants(interaction.user.id):
            return True
        await interaction.response.send_message(ADMIN_GRANT_DENIED, ephemeral=True)
        return False

    async def _human_members(self, guild: discord.Guild) -> list[discord.Member]:
        try:
            members = [member async for member in guild.fetch_members(limit=None)]
        except discord.HTTPException:
            members = list(guild.members)
        return [member for member in members if not member.bot]

    async def _send_config_overview(self, interaction: discord.Interaction) -> None:
        assert interaction.guild_id is not None
        values = await self.bot.db.get_config_values(interaction.guild_id)
        custom_settings = await self.bot.db.custom_config_names(interaction.guild_id)
        lines = [
            f"`{setting}` = `{_format_config_value(values[setting])}` "
            f"({_custom_marker(setting, custom_settings)}) - {spec.description}"
            for setting, spec in config.LIVE_SETTINGS.items()
        ]
        embed = discord.Embed(
            title="Live Config",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(
            text="Use /admin config setting value to change one, or /admin config-reset setting.",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @admin_group.command(name="gift", description="Give nuggets to a user.")
    @app_commands.describe(user="User to receive nuggets", amount="Amount to create")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def gift(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        amount: float,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if not await self._require_discord_admin_grants(interaction):
            return
        if user.bot and not config.ALLOW_BOT_PLAYERS:
            await interaction.response.send_message("Choose a human user.", ephemeral=True)
            return
        if not self._valid_currency_amount(amount):
            await interaction.response.send_message("Enter a positive, reasonable amount.", ephemeral=True)
            return

        await self.bot.db.credit_wallet(user.id, interaction.guild_id, amount)
        await interaction.response.send_message(
            f"Gifted {fmt_amount(amount)} to {user.mention}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @admin_group.command(name="gift-all", description="Give nuggets to every human.")
    @app_commands.describe(amount="Amount each human receives")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def gift_all(self, interaction: discord.Interaction, amount: float) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if not await self._require_discord_admin_grants(interaction):
            return
        if not self._valid_currency_amount(amount):
            await interaction.response.send_message("Enter a positive, reasonable amount.", ephemeral=True)
            return

        await interaction.response.defer()
        members = await self._human_members(interaction.guild)
        count = await self.bot.db.credit_wallets((member.id for member in members), interaction.guild.id, amount)
        await interaction.followup.send(
            f"Gifted {fmt_amount(amount)} to {count} human member(s)."
        )

    @admin_group.command(name="set-currency", description="Set a user's wallet exactly.")
    @app_commands.describe(user="User to edit", amount="Exact new wallet amount")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def set_currency(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        amount: float,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if not await self._require_discord_admin_grants(interaction):
            return
        if user.bot and not config.ALLOW_BOT_PLAYERS:
            await interaction.response.send_message("Choose a human user.", ephemeral=True)
            return
        if not self._valid_currency_amount(amount, allow_zero=True):
            await interaction.response.send_message("Enter a non-negative, reasonable amount.", ephemeral=True)
            return

        await self.bot.db.set_wallet(user.id, interaction.guild_id, amount)
        await interaction.response.send_message(
            f"Set {user.mention}'s wallet to {fmt_amount(amount)}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @admin_group.command(name="reset-user", description="Wipe a user's wallet and stats.")
    @app_commands.describe(user="User to reset")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def reset_user(self, interaction: discord.Interaction, user: discord.Member) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if user.bot and not config.ALLOW_BOT_PLAYERS:
            await interaction.response.send_message("Choose a human user.", ephemeral=True)
            return

        await self.bot.db.reset_user(user.id, interaction.guild_id)
        await interaction.response.send_message(
            f"Reset wallet and stats for {user.mention}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @admin_group.command(name="config", description="View or change live settings.")
    @app_commands.describe(setting="Setting to change", value="New numeric value")
    @app_commands.autocomplete(setting=_setting_autocomplete)
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def config_command(
        self,
        interaction: discord.Interaction,
        setting: str | None = None,
        value: float | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if setting is None and value is None:
            await self._send_config_overview(interaction)
            return
        if setting is None or value is None:
            await interaction.response.send_message(
                "Use `/admin config` to view settings or `/admin config setting value` to change one.",
                ephemeral=True,
            )
            return
        if setting not in config.LIVE_SETTINGS:
            await interaction.response.send_message("Unknown setting. Use autocomplete.", ephemeral=True)
            return

        try:
            stored = await self.bot.db.set_config_value(interaction.guild_id, setting, value)
        except ValueError as exc:
            await interaction.response.send_message(
                f"`{setting}` {exc}.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Set `{setting}` to `{_format_config_value(stored)}`.",
            ephemeral=True,
        )

    @admin_group.command(name="config-reset", description="Reset one live setting.")
    @app_commands.describe(setting="Setting to reset")
    @app_commands.autocomplete(setting=_setting_autocomplete)
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def config_reset(self, interaction: discord.Interaction, setting: str) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if setting not in config.LIVE_SETTINGS:
            await interaction.response.send_message("Unknown setting. Use autocomplete.", ephemeral=True)
            return

        await self.bot.db.reset_config_value(interaction.guild_id, setting)
        default = config.live_setting_default(setting)
        await interaction.response.send_message(
            f"Reset `{setting}` to `{_format_config_value(default)}`.",
            ephemeral=True,
        )

    @admin_group.command(name="bot-status", description="Show economy and game status.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def status_dashboard(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        stats = await self.bot.db.economy_stats(interaction.guild.id)
        bounty_count = await self.bot.db.count_bounties(interaction.guild.id)
        boss = await self.bot.db.get_active_boss(interaction.guild.id)
        virus = await self.bot.db.get_hacker_pot(interaction.guild.id)
        custom_settings = await self.bot.db.custom_config_names(interaction.guild.id)
        channel_settings = await self.bot.db.get_guild_channel_settings(interaction.guild.id)
        main_channel_id = channel_settings["main_channel_id"]
        designated_channel_id = channel_settings["designated_channel_id"]
        split_enabled = channel_settings["split_announcement_channels"]
        main_channel = (
            f"<#{main_channel_id}>"
            if main_channel_id is not None
            else "not set (using fallback)"
        )
        designated_channel = (
            f"<#{designated_channel_id}>"
            if designated_channel_id is not None
            else "not set (falls back to main)"
        )
        split_status = (
            "enabled — boss posts in designated, coin drops in main"
            if split_enabled
            else "disabled — boss and coin drops share main channel"
        )

        embed = discord.Embed(
            title=f"{interaction.guild.name} Bot Status",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Economy",
            value=(
                f"Tracked users: `{int(stats['users'])}`\n"
                f"Wallet total: `{fmt_amount(float(stats['total_wallet']))}`\n"
                f"Bank total: `{fmt_amount(float(stats['total_bank']))}`\n"
                f"Combined wealth: `{fmt_amount(float(stats['total_wealth']))}`\n"
                f"Lifetime earned: `{fmt_amount(float(stats['total_earned']))}`\n"
                f"Messages rewarded: `{int(stats['messages_sent'])}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Active Games",
            value=(
                f"Bounties: `{bounty_count}`\n"
                f"Boss: `{self._boss_status(boss)}`\n"
                f"Virus: `{self._virus_status(virus)}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Custom Settings",
            value=self._custom_settings_status(custom_settings),
            inline=False,
        )
        embed.add_field(
            name="Channels",
            value=(
                f"**Main** (coin drops): {main_channel}\n"
                f"**Designated** (bot posts): {designated_channel}\n"
                f"**Split mode:** {split_status}"
            ),
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @admin_group.command(
        name="set-main-channel",
        description="Set the channel for random coin drops and gifts.",
    )
    @app_commands.describe(channel="Text channel for server-wide announcements")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def set_main_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        if interaction.guild_id is None or interaction.guild is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if not channel.permissions_for(interaction.guild.me).send_messages:
            await interaction.response.send_message(
                "I cannot send messages in that channel.",
                ephemeral=True,
            )
            return

        await self.bot.db.set_main_channel_id(interaction.guild_id, channel.id)
        await interaction.response.send_message(
            f"Main channel set to {channel.mention}. "
            "Random coin drops will post there. "
            "Use `/admin set-designated-channel` and `/admin toggle-split-channels` to route boss posts elsewhere.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @admin_group.command(
        name="clear-main-channel",
        description="Clear the main channel (coin drops use fallback).",
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def clear_main_channel(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        await self.bot.db.set_main_channel_id(interaction.guild_id, None)
        await interaction.response.send_message(
            "Main channel cleared. Coin drops will use the system channel or first writable channel.",
            ephemeral=True,
        )

    @admin_group.command(
        name="set-designated-channel",
        description="Set the channel for boss spawns and bot announcements.",
    )
    @app_commands.describe(channel="Text channel where the bot posts boss and system messages")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def set_designated_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        if interaction.guild_id is None or interaction.guild is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if not channel.permissions_for(interaction.guild.me).send_messages:
            await interaction.response.send_message(
                "I cannot send messages in that channel.",
                ephemeral=True,
            )
            return

        await self.bot.db.set_designated_channel_id(interaction.guild_id, channel.id)
        await interaction.response.send_message(
            f"Designated bot channel set to {channel.mention}. "
            "Enable `/admin toggle-split-channels` so boss posts go here while coin drops stay in main.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @admin_group.command(
        name="clear-designated-channel",
        description="Clear the designated bot channel.",
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def clear_designated_channel(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        await self.bot.db.set_designated_channel_id(interaction.guild_id, None)
        await interaction.response.send_message(
            "Designated channel cleared. With split mode on, boss posts fall back to the main channel.",
            ephemeral=True,
        )

    @admin_group.command(
        name="toggle-split-channels",
        description=(
            "When on, boss posts use designated channel; coin drops stay in main."
        ),
    )
    @app_commands.describe(
        enabled="True = split boss and coin-drop channels; False = everything uses main"
    )
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def toggle_split_channels(
        self,
        interaction: discord.Interaction,
        enabled: bool,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        await self.bot.db.set_split_announcement_channels(interaction.guild_id, enabled)
        if enabled:
            message = (
                "Split channel mode **enabled**. Boss spawns and defeats post in the "
                "**designated** channel; random coin drops stay in **main**."
            )
        else:
            message = (
                "Split channel mode **disabled**. Boss posts and coin drops both use the "
                "**main** channel (or fallback)."
            )
        await interaction.response.send_message(message, ephemeral=True)

    @admin_group.command(name="despawn-boss", description="Despawn this server's active boss.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def despawn_boss(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        boss = await self.bot.db.get_active_boss(interaction.guild_id)
        if boss is None:
            await interaction.response.send_message("No boss is active in this server.", ephemeral=True)
            return

        await self.bot.db.clear_boss(interaction.guild_id)
        await interaction.response.send_message(
            f"Despawned {boss['variant']} {boss['name']} in this server.",
            ephemeral=True,
        )

    @admin_group.command(name="despawn-all-bosses", description="Emergency clear all active bosses.")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def despawn_all_bosses(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        count = await self.bot.db.clear_all_bosses()
        await interaction.response.send_message(
            f"Despawned {count} active boss session(s).",
            ephemeral=True,
        )

    @staticmethod
    def _boss_status(boss: Any) -> str:
        if boss is None:
            return "none"
        return f"{boss['variant']} {boss['name']} ({fmt_amount(float(boss['hp']))} HP)"

    @staticmethod
    def _virus_status(virus: Any) -> str:
        if virus is None:
            return "none"
        seconds_left = int(max(0, float(virus["expires_at"]) - time.time()))
        return f"holder {int(virus['holder_id'])} ({seconds_left}s left)"

    @staticmethod
    def _custom_settings_status(custom_settings: Iterable[str]) -> str:
        settings = sorted(custom_settings)
        if not settings:
            return "`none`"
        return ", ".join(f"`{setting}`" for setting in settings)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))
