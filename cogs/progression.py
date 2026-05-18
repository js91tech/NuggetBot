from __future__ import annotations

import time

import discord
from discord import app_commands
from discord.ext import commands

import config
from items import get_item
from utils.achievements import ACHIEVEMENTS, evaluate_unlocks, format_unlock_message
from utils.gear_sets import craft_base_id, craft_upgrade_cost
from utils.helpers import fmt_amount, guild_only_message
from utils.quests import record_quest_event

EVENT_LABELS: dict[str, tuple[str, float]] = {
    "double_drops": ("Double boss drop rolls", 2.0),
    "bonus_income": ("Bonus nugget income", 1.5),
    "festival_boss": ("Festival boss HP (+25%)", 1.25),
    "trivia_fiesta": ("Double trivia rewards", 2.0),
}


class Progression(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _notify_unlocks(
        self,
        interaction: discord.Interaction,
        user_id: int,
        *,
        wallet: float | None = None,
    ) -> None:
        unlocked = await evaluate_unlocks(
            self.bot.db,
            interaction.guild_id,
            user_id,
            wallet=wallet,
        )
        if not unlocked:
            return
        message = format_unlock_message(unlocked)
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="achievements", description="View unlocked achievements.")
    @app_commands.describe(user="Player to inspect. Defaults to you.")
    @app_commands.guild_only()
    async def achievements(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        target = user or interaction.user
        owned = await self.bot.db.list_achievements(target.id, interaction.guild_id)
        progress = await self.bot.db.get_user_progress(target.id, interaction.guild_id)

        lines = []
        for achievement_id, achievement in ACHIEVEMENTS.items():
            mark = "✅" if achievement_id in owned else "⬜"
            lines.append(f"{mark} {achievement.emoji} **{achievement.name}** — {achievement.description}")

        embed = discord.Embed(
            title=f"{target.display_name}'s Achievements",
            description="\n".join(lines) if lines else "No achievements defined.",
            color=discord.Color.purple(),
        )
        embed.add_field(
            name="Progress",
            value=(
                f"Boss kills: **{int(progress['bosses_killed'])}** · "
                f"Heists won: **{int(progress['heists_won'])}** · "
                f"Heals: **{int(progress['heals_given'])}** · "
                f"Prestige: **{int(progress['prestige_level'])}**"
            ),
            inline=False,
        )
        embed.set_footer(text=f"{len(owned)}/{len(ACHIEVEMENTS)} unlocked")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="prestige",
        description="Reset your wallet for permanent crit and income bonuses.",
    )
    @app_commands.describe(confirm="Type true to confirm wallet reset")
    @app_commands.guild_only()
    async def prestige(self, interaction: discord.Interaction, confirm: bool = False) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        progress = await self.bot.db.get_user_progress(interaction.user.id, interaction.guild_id)
        level = int(progress["prestige_level"])
        if level >= config.PRESTIGE_MAX_LEVEL:
            await interaction.response.send_message(
                f"You are at max prestige ({config.PRESTIGE_MAX_LEVEL}).",
                ephemeral=True,
            )
            return

        wallet = await self.bot.db.get_balance(interaction.user.id, interaction.guild_id)
        min_wallet = await self.bot.db.get_config_value(interaction.guild_id, "prestige_min_wallet")
        if wallet < min_wallet:
            await interaction.response.send_message(
                f"You need at least {fmt_amount(min_wallet)} to prestige. "
                f"You have {fmt_amount(wallet)}.",
                ephemeral=True,
            )
            return

        if not confirm:
            next_level = level + 1
            crit_bonus = int(next_level * config.PRESTIGE_CRIT_BONUS_PER_LEVEL * 100)
            income_bonus = int(next_level * config.PRESTIGE_INCOME_BONUS_PER_LEVEL * 100)
            await interaction.response.send_message(
                f"Prestige to level **{next_level}**?\n"
                f"Your wallet ({fmt_amount(wallet)}) will reset to **0**.\n"
                f"Bonuses stack: **+{crit_bonus}%** crit, **+{income_bonus}%** income.\n"
                f"Run `/prestige confirm:true` when ready.",
                ephemeral=True,
            )
            return

        new_level = await self.bot.db.prestige_user(interaction.user.id, interaction.guild_id)
        embed = discord.Embed(
            title="Prestige complete",
            description=(
                f"You are now prestige **{new_level}**.\n"
                f"+{int(new_level * config.PRESTIGE_CRIT_BONUS_PER_LEVEL * 100)}% crit · "
                f"+{int(new_level * config.PRESTIGE_INCOME_BONUS_PER_LEVEL * 100)}% income"
            ),
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        unlocked = await evaluate_unlocks(
            self.bot.db,
            interaction.guild_id,
            interaction.user.id,
            wallet=0,
        )
        unlock_msg = format_unlock_message(unlocked)
        if unlock_msg:
            await interaction.followup.send(unlock_msg, ephemeral=True)

    async def craft_item_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        current_lower = current.lower()
        rows = await self.bot.db.get_inventory(interaction.user.id, interaction.guild_id)
        choices: list[app_commands.Choice[str]] = []
        for row in rows:
            item_id = str(row["item_id"])
            if not item_id.startswith("boss_weak_"):
                continue
            item = get_item(item_id)
            if item is None:
                continue
            if current_lower not in item_id.lower() and current_lower not in item.name.lower():
                continue
            choices.append(app_commands.Choice(name=item.name, value=item_id))
            if len(choices) >= 25:
                break
        return choices

    @app_commands.command(
        name="craft",
        description="Upgrade battle-worn boss drops into real shop gear.",
    )
    @app_commands.describe(item="Battle-worn item in your inventory")
    @app_commands.autocomplete(item=craft_item_autocomplete)
    @app_commands.guild_only()
    async def craft(self, interaction: discord.Interaction, item: str) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        weak_id = item.strip()
        base_id = craft_base_id(weak_id)
        if base_id is None:
            await interaction.response.send_message(
                "Only **Battle-Worn** items (`boss_weak_*`) can be crafted up.",
                ephemeral=True,
            )
            return

        base_item = get_item(base_id)
        craft_factor = await self.bot.db.get_config_value(
            interaction.guild_id, "craft_upgrade_cost_factor"
        )
        cost = craft_upgrade_cost(base_id, cost_factor=craft_factor)
        if base_item is None or cost is None:
            await interaction.response.send_message("That recipe is not valid.", ephemeral=True)
            return

        if not await self.bot.db.consume_inventory_item(
            interaction.user.id, interaction.guild_id, weak_id
        ):
            await interaction.response.send_message(
                "You do not have that battle-worn item.",
                ephemeral=True,
            )
            return

        if not await self.bot.db.debit_wallet(interaction.user.id, interaction.guild_id, cost):
            await self.bot.db.grant_item(interaction.user.id, interaction.guild_id, weak_id)
            await interaction.response.send_message(
                f"Crafting costs {fmt_amount(cost)}. You were refunded the item.",
                ephemeral=True,
            )
            return

        await self.bot.db.grant_item(interaction.user.id, interaction.guild_id, base_id)
        await self.bot.db.increment_progress(
            interaction.user.id,
            interaction.guild_id,
            crafts_done=1,
        )
        await interaction.response.send_message(
            f"Crafted **{base_item.name}** for {fmt_amount(cost)}!",
            ephemeral=True,
        )
        await record_quest_event(
            self.bot.db,
            interaction.guild_id,
            interaction.user.id,
            "craft_done",
        )
        await self._notify_unlocks(interaction, interaction.user.id)

    @app_commands.command(name="event", description="View or manage server seasonal events.")
    @app_commands.describe(
        action="What to do",
        event_type="Event type (admin start only)",
        hours="Duration in hours (admin start only)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Status", value="status"),
            app_commands.Choice(name="Start (admin)", value="start"),
            app_commands.Choice(name="Stop (admin)", value="stop"),
        ],
        event_type=[
            app_commands.Choice(name=label, value=key)
            for key, (label, _) in EVENT_LABELS.items()
        ],
    )
    @app_commands.guild_only()
    async def event(
        self,
        interaction: discord.Interaction,
        action: str,
        event_type: str | None = None,
        hours: int = 24,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        if action == "status":
            active = await self.bot.db.get_active_guild_event(interaction.guild_id)
            if active is None:
                await interaction.response.send_message("No seasonal event is active.", ephemeral=True)
                return
            label, _ = EVENT_LABELS.get(str(active["event_type"]), (str(active["event_type"]), 1.0))
            remaining = max(0, int(float(active["ends_at"]) - time.time()))
            hours_left = remaining // 3600
            mins_left = (remaining % 3600) // 60
            embed = discord.Embed(title="Seasonal event", color=discord.Color.green())
            embed.add_field(name="Type", value=label, inline=True)
            embed.add_field(name="Multiplier", value=f"{float(active['multiplier']):g}×", inline=True)
            embed.add_field(name="Time left", value=f"{hours_left}h {mins_left}m", inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Admin only.", ephemeral=True)
            return

        if action == "stop":
            await self.bot.db.clear_guild_event(interaction.guild_id)
            await interaction.response.send_message("Seasonal event cleared.", ephemeral=True)
            return

        if action == "start":
            if event_type is None or event_type not in EVENT_LABELS:
                await interaction.response.send_message("Pick an event type.", ephemeral=True)
                return
            hours = max(1, min(168, hours))
            label, mult = EVENT_LABELS[event_type]
            ends = time.time() + hours * 3600
            await self.bot.db.set_guild_event(interaction.guild_id, event_type, mult, ends)
            await interaction.response.send_message(
                f"Started **{label}** ({mult:g}×) for **{hours}** hour(s).",
                ephemeral=True,
            )
            return

        await interaction.response.send_message("Unknown action.", ephemeral=True)

    @app_commands.command(
        name="hall-of-fame",
        description="Server legends: richest, raid kills, heals, and achievements.",
    )
    @app_commands.guild_only()
    async def hall_of_fame(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return

        snapshot = await self.bot.db.hall_of_fame_snapshot(interaction.guild.id, limit=10)

        def format_rows(
            rows: list,
            *,
            value_label: str,
            value_key: str = "wallet",
        ) -> str:
            if not rows:
                return "_No entries yet_"
            lines = []
            for index, row in enumerate(rows, start=1):
                member = interaction.guild.get_member(int(row["user_id"]))
                name = member.display_name if member is not None else f"User {row['user_id']}"
                value = float(row[value_key])
                if value_key == "wallet":
                    text = fmt_amount(value)
                else:
                    text = f"{int(value)} {value_label}"
                lines.append(f"**{index}.** {name} — {text}")
            return "\n".join(lines)

        embed = discord.Embed(
            title=f"{interaction.guild.name} Hall of Fame",
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Richest",
            value=format_rows(snapshot["richest"], value_label="nuggets"),
            inline=False,
        )
        embed.add_field(
            name="Most boss kills",
            value=format_rows(
                snapshot["boss_kills"], value_label="kills", value_key="score"
            ),
            inline=False,
        )
        embed.add_field(
            name="Most heals",
            value=format_rows(snapshot["heals"], value_label="heals", value_key="score"),
            inline=False,
        )
        embed.add_field(
            name="Most achievements",
            value=format_rows(
                snapshot["achievements"], value_label="unlocked", value_key="score"
            ),
            inline=False,
        )
        embed.set_footer(text="Pinned stats · Try /coinflip or /blackjack in the casino corner")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Progression(bot))
