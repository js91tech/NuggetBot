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
    "world_boss_week": ("World Leviathan week (unique boss + loot)", 1.5),
    "summer_festival": ("Summer Festival (business +15%)", 1.15),
    "holiday_rush": ("Holiday Rush (business +25%)", 1.25),
    "economic_crisis": ("Economic Crisis (business -10%)", 0.90),
    "tech_boom": ("Tech Boom (business +20%)", 1.20),
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
            reset_lines = [f"Your wallet ({fmt_amount(wallet)}) will reset to **0**."]
            if next_level >= config.PRESTIGE_MAX_LEVEL:
                reset_lines.append(
                    "At **prestige 10**, your **bank** and **vault expansions** reset too."
                )
            else:
                reset_lines.append("Your bank balance is kept (up to your vault capacity).")
            await interaction.response.send_message(
                f"Prestige to level **{next_level}**?\n"
                + "\n".join(reset_lines)
                + f"\nBonuses stack: **+{crit_bonus}%** crit, **+{income_bonus}%** income.\n"
                f"Run `/prestige confirm:true` when ready.",
                ephemeral=True,
            )
            return

        new_level = await self.bot.db.prestige_user(interaction.user.id, interaction.guild_id)
        bank_note = ""
        if new_level >= config.PRESTIGE_MAX_LEVEL:
            bank_note = "\nBank and vault expansions were reset."
        embed = discord.Embed(
            title="Prestige complete",
            description=(
                f"You are now prestige **{new_level}**.\n"
                f"+{int(new_level * config.PRESTIGE_CRIT_BONUS_PER_LEVEL * 100)}% crit · "
                f"+{int(new_level * config.PRESTIGE_INCOME_BONUS_PER_LEVEL * 100)}% income"
                f"{bank_note}"
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
        crew = await self.bot.db.get_crew_membership(
            interaction.user.id, interaction.guild_id,
        )
        held = await self.bot.db.get_crew_territory_perk_ids(
            interaction.guild_id, crew,
        )
        if "foundry" in held:
            cost *= 1.0 - config.TERRITORY_PERK_FOUNDRY_CRAFT_DISCOUNT
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
                text = (
                    fmt_amount(value)
                    if value_key == "wallet"
                    else f"{int(value)} {value_label}"
                )
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
        embed.add_field(
            name="Most duel wins",
            value=format_rows(
                snapshot.get("duel_wins", []), value_label="wins", value_key="score"
            ),
            inline=False,
        )
        embed.add_field(
            name="Top duel ELO",
            value=format_rows(
                snapshot.get("duel_elo", []), value_label="ELO", value_key="score"
            ),
            inline=False,
        )
        embed.add_field(
            name="Business empire",
            value=format_rows(
                snapshot.get("business_prestige", []), value_label="score", value_key="score"
            ),
            inline=False,
        )
        embed.add_field(
            name="Drug sales (units)",
            value=format_rows(
                snapshot.get("drug_sales", []), value_label="units", value_key="score"
            ),
            inline=False,
        )
        corp_treasury = snapshot.get("corp_treasury", [])
        if corp_treasury:
            corp_lines = []
            for index, row in enumerate(corp_treasury, start=1):
                corp_lines.append(
                    f"**{index}.** {row['user_id']} — {fmt_amount(float(row['score']))}"
                )
            embed.add_field(name="Richest corporations", value="\n".join(corp_lines), inline=False)
        embed.add_field(
            name="District influence",
            value=format_rows(
                snapshot.get("district_influence", []), value_label="influence", value_key="score"
            ),
            inline=False,
        )
        crew_rows = snapshot.get("crews", [])
        if crew_rows:
            crew_lines = [
                f"**{i}. {row['crew_name']}** — Lv{int(row['level'])} · {fmt_amount(float(row['score']))}"
                for i, row in enumerate(crew_rows, start=1)
            ]
            embed.add_field(
                name="Top crews",
                value="\n".join(crew_lines) if crew_lines else "_None_",
                inline=False,
            )
        embed.set_footer(text="Casino: /slots · /jackpot · Solo PvE: /dungeon")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Progression(bot))
