"""18+ age confirmation and NSFW channel gates for GoonBot."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import discord

from utils.goon_theme import brand_color, danger_color, branded_embed

if TYPE_CHECKING:
    from database import Database

AGE_GATE_TITLE = "GoonBot — 18+ confirmation"
AGE_GATE_BODY = (
    "**GoonBot is an explicit adult Discord economy RPG.**\n\n"
    "By continuing you confirm that you are **at least 18 years old** "
    "and consent to erotic / NSFW game content.\n\n"
    "Sexual content involving minors is never allowed. "
    "If you are under 18, press **I am under 18** and leave."
)
REFUSAL_UNDERAGE = (
    "Access denied. GoonBot is **18+ only**. "
    "If you are under 18, do not use this bot."
)
NSFW_CHANNEL_REQUIRED = (
    "This server requires GoonBot commands in a **Discord NSFW channel**. "
    "Ask an admin to mark a channel NSFW, or disable the NSFW-channel gate "
    "(`nsfw_channel_only` guild setting)."
)


class AgeGateView(discord.ui.View):
    def __init__(self, db: Database, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180)
        self.db = db
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This confirmation is only for you.", ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="I am 18+ — enter", style=discord.ButtonStyle.success, row=0)
    async def confirm_adult(
        self, interaction: discord.Interaction, _button: discord.ui.Button,
    ) -> None:
        await self.db.set_age_verified(self.user_id, self.guild_id, True)
        embed = branded_embed(
            "Welcome to GoonBot",
            description=(
                "Age confirmed. Use `/profile` for the launcher hub, "
                "or browse slash commands. Play in NSFW channels when your "
                "server requires it."
            ),
        )
        self.stop()
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="I am under 18", style=discord.ButtonStyle.danger, row=0)
    async def refuse_underage(
        self, interaction: discord.Interaction, _button: discord.ui.Button,
    ) -> None:
        await self.db.set_age_verified(self.user_id, self.guild_id, False)
        embed = discord.Embed(
            title="Access denied",
            description=REFUSAL_UNDERAGE,
            color=danger_color(),
        )
        self.stop()
        await interaction.response.edit_message(embed=embed, view=None)


def age_gate_embed() -> discord.Embed:
    return discord.Embed(
        title=AGE_GATE_TITLE,
        description=AGE_GATE_BODY,
        color=brand_color(),
    )


async def is_age_verified(db: Database, user_id: int, guild_id: int) -> bool:
    return await db.get_age_verified(user_id, guild_id)


async def nsfw_channel_required(db: Database, guild_id: int) -> bool:
    try:
        value = await db.get_config_value(guild_id, "nsfw_channel_only")
    except KeyError:
        return True
    return float(value) >= 1.0


def channel_is_nsfw(channel: Any) -> bool:
    return bool(getattr(channel, "nsfw", False) or getattr(channel, "is_nsfw", lambda: False)())


async def check_interaction(interaction: discord.Interaction, db: Database) -> bool:
    """Tree interaction_check: NSFW channel + age gate. Returns False to block."""
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "GoonBot only works inside a server.", ephemeral=True,
        )
        return False

    # Allow the age-gate button callbacks (component interactions on our view)
    # App commands still go through here.
    if interaction.type == discord.InteractionType.component:
        return True

    channel = interaction.channel
    if await nsfw_channel_required(db, interaction.guild_id):
        if channel is not None and not channel_is_nsfw(channel):
            # DMs / threads inherit; threads may not have nsfw — check parent
            parent = getattr(channel, "parent", None)
            if parent is not None and channel_is_nsfw(parent):
                pass
            elif getattr(interaction.user, "guild_permissions", None) and (
                interaction.user.guild_permissions.administrator
            ):
                # Admins may configure from any channel for setup convenience
                pass
            else:
                await interaction.response.send_message(
                    NSFW_CHANNEL_REQUIRED, ephemeral=True,
                )
                return False

    if await is_age_verified(db, interaction.user.id, interaction.guild_id):
        return True

    view = AgeGateView(db, interaction.guild_id, interaction.user.id)
    await interaction.response.send_message(
        embed=age_gate_embed(), view=view, ephemeral=True,
    )
    return False
