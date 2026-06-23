"""UI for competitive actions and active defense."""
from __future__ import annotations

import contextlib
import time
from typing import TYPE_CHECKING

import discord

import config
from utils.business_competition import COMPETITIVE_ACTIONS, action_by_id
from utils.helpers import fmt_amount
from utils.quests import record_quest_event

if TYPE_CHECKING:
    from discord.ext import commands


def build_action_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🏴 Corporate Competition",
        description=(
            "Pick a strategic action. Buffs boost your own revenue; attacks apply a "
            "temporary penalty to a rival (mitigated by their security). Nothing is "
            "ever permanently lost."
        ),
        color=discord.Color.dark_red(),
    )
    for action in COMPETITIVE_ACTIONS:
        embed.add_field(
            name=f"{action.emoji} {action.name} — {fmt_amount(action.cost)}",
            value=action.description,
            inline=False,
        )
    embed.set_footer(text="Attacks notify the target, who has 15 minutes to /business defend.")
    return embed


async def _apply_action_result(
    interaction: discord.Interaction,
    cog: commands.Cog,
    result: dict[str, object],
) -> None:
    """Send the public attack notification (with Defend button) when relevant."""
    if result.get("kind") != "attack":
        return
    guild = interaction.guild
    if guild is None:
        return
    defender_id = int(result["defender_id"])
    defender = guild.get_member(defender_id)
    action = action_by_id(str(result["action"]))
    action_name = action.name if action else "an attack"
    from utils.helpers import resolve_main_channel

    channel = await resolve_main_channel(guild, cog.bot.db)
    if channel is None:
        return
    penalty_pct = int(float(result["penalty"]) * 100)
    embed = discord.Embed(
        title="⚠ Your business is under attack!",
        description=(
            f"{interaction.user.mention} launched a **{action_name}** against "
            f"{defender.mention if defender else 'a rival'} "
            f"(−{penalty_pct}% revenue).\n\n"
            f"Respond with **/business defend** or the button below within "
            f"**{config.BUSINESS_DEFENSE_WINDOW_SECONDS // 60} minutes** to cut the damage in half."
        ),
        color=discord.Color.orange(),
    )
    view = DefendView(cog, guild.id, defender_id)
    content = defender.mention if defender else None
    with contextlib.suppress(discord.HTTPException):
        await channel.send(
            content=content,
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions(users=True),
        )


class ActionSelect(discord.ui.Select):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        options = [
            discord.SelectOption(
                label=action.name,
                value=action.action_id,
                description=f"{fmt_amount(action.cost)} · {action.target}"[:100],
                emoji=action.emoji,
            )
            for action in COMPETITIVE_ACTIONS
        ]
        super().__init__(placeholder="Choose an action…", options=options, row=0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        action = action_by_id(self.values[0])
        view: BusinessActionView = self.view  # type: ignore[assignment]
        if action is None:
            await interaction.response.send_message("Unknown action.", ephemeral=True)
            return
        if action.target == "opponent":
            view.selected_action = action.action_id
            await interaction.response.send_message(
                f"Selected **{action.emoji} {action.name}**. Now pick your rival below.",
                view=TargetView(self.cog, self.guild_id, self.user_id, action.action_id),
                ephemeral=True,
            )
            return
        result = await self.cog.bot.db.perform_business_action(
            self.user_id, self.guild_id, action.action_id,
        )
        await interaction.response.send_message(
            _format_result(result, action), ephemeral=True,
        )
        if result.get("error") is None:
            await record_quest_event(
                self.cog.bot.db, self.guild_id, self.user_id, "business_action",
            )


class TargetSelect(discord.ui.UserSelect):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int, action_id: str) -> None:
        super().__init__(placeholder="Choose a rival to target…", min_values=1, max_values=1, row=0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.action_id = action_id

    async def callback(self, interaction: discord.Interaction) -> None:
        target = self.values[0]
        action = action_by_id(self.action_id)
        result = await self.cog.bot.db.perform_business_action(
            self.user_id, self.guild_id, self.action_id, target_id=target.id,
        )
        await interaction.response.send_message(
            _format_result(result, action, target=target), ephemeral=True,
        )
        if result.get("error") is None:
            await record_quest_event(
                self.cog.bot.db, self.guild_id, self.user_id, "business_action",
            )
            await _apply_action_result(interaction, self.cog, result)


def _format_result(
    result: dict[str, object],
    action: object,
    *,
    target: discord.abc.User | None = None,
) -> str:
    err = result.get("error")
    name = getattr(action, "name", "Action")
    if err is None:
        kind = result.get("kind")
        if kind == "buff":
            return f"✅ **{name}** active! Revenue boosted until <t:{int(float(result['ends_at']))}:R>."
        if kind == "attack":
            pct = int(float(result["penalty"]) * 100)
            mitig = int(float(result["mitigated"]) * 100)
            tgt = target.mention if target else "your rival"
            extra = f" ({mitig}% blocked by their security)" if mitig > 0 else ""
            return f"💥 **{name}** hit {tgt} for −{pct}% revenue{extra}!"
        if kind == "influence":
            return f"🗺️ **{name}** done — influence now **{int(float(result['influence']))}%**."
        return f"✅ **{name}** complete."
    messages = {
        "no_business": "You need a business first (`/business create`).",
        "invalid_action": "Unknown action.",
        "invalid_target": "Pick a valid rival (not yourself).",
        "target_no_business": "That player doesn't own a business.",
        "cooldown": f"On cooldown — retry <t:{int(time.time() + float(result.get('retry_after', 0)))}:R>.",
        "insufficient_funds": f"You need **{fmt_amount(float(result.get('cost', 0)))}**.",
        "no_district": "Relocate to a district first (`/business districts`).",
    }
    return messages.get(str(err), f"Could not complete action: {err}")


class TargetView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int, action_id: str) -> None:
        super().__init__(timeout=120.0)
        self.user_id = user_id
        self.add_item(TargetSelect(cog, guild_id, user_id, action_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your panel.", ephemeral=True)
            return False
        return True


class BusinessActionView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.selected_action: str | None = None
        self.add_item(ActionSelect(cog, guild_id, user_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your competition panel.", ephemeral=True,
            )
            return False
        return True


class DefendView(discord.ui.View):
    """Attached to attack notifications; only the defender can use it."""

    def __init__(self, cog: commands.Cog, guild_id: int, defender_id: int) -> None:
        super().__init__(timeout=float(config.BUSINESS_DEFENSE_WINDOW_SECONDS))
        self.cog = cog
        self.guild_id = guild_id
        self.defender_id = defender_id

    @discord.ui.button(label="🛡️ Defend", style=discord.ButtonStyle.success)
    async def defend_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        if interaction.user.id != self.defender_id:
            await interaction.response.send_message(
                "Only the targeted owner can defend.", ephemeral=True,
            )
            return
        result = await self.cog.bot.db.defend_business(self.defender_id, self.guild_id)
        if result.get("error"):
            await interaction.response.send_message(
                "No active attack to defend right now.", ephemeral=True,
            )
            return
        pct = int(float(result["new_penalty"]) * 100)
        await record_quest_event(
            self.cog.bot.db, self.guild_id, self.defender_id, "business_defend",
        )
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            f"🛡️ Defended! The attack's penalty is cut to **−{pct}%**.", ephemeral=True,
        )


async def send_action_panel(interaction: discord.Interaction, cog: commands.Cog) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    view = BusinessActionView(cog, interaction.guild_id, interaction.user.id)
    await interaction.followup.send(embed=build_action_embed(), view=view, ephemeral=True)
