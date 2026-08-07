"""Interactive quest panel with shortcuts to relevant game panels."""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from utils.quests import (
    EMPIRE_QUESTS,
    ONBOARDING_QUESTS,
    TRACK_DAILY,
    TRACK_EMPIRE,
    TRACK_ONBOARDING,
    ensure_daily_quests,
    ensure_empire_quests,
    ensure_onboarding_quests,
    format_quest_lines,
    is_veteran,
    quest_by_id,
)

if TYPE_CHECKING:
    from discord.ext import commands

# quest.event -> (button label, cog lookup name or slash hint)
QUEST_SHORTCUT_HINTS: dict[str, str] = {
    "daily_claim": "Use `/daily` to claim.",
    "wallet_pay": "Use `/pay` to send nuggets.",
    "boss_heal": "Use `/heal` on a downed ally.",
    "gamble_play": "Use `/coinflip`, `/blackjack`, or `/slots`.",
    "craft_done": "Use `/craft` to forge upgrades.",
    "job_work": "Use `/work` for instant shifts.",
    "chat_message": "Stay active in chat to earn passive nuggets.",
    "territory_claim": "Use `/territory` to claim zones.",
    "territory_guards": "Use `/territory guards` to hire mercs.",
    "corp_project": "Open **Corp Projects** from `/crew panel`.",
    "dungeon_clear": "Use `/dungeon` to run a delve.",
}

QUEST_PANEL_COGS: dict[str, str] = {
    "shop_buy": "Shop",
    "boss_attack": "Boss",
    "drug_plant": "Drugs",
    "drug_harvest": "Drugs",
    "drug_sell": "Drugs",
    "drug_use": "Drugs",
    "business_create": "Business",
    "business_collect": "Business",
    "business_upgrade": "Business",
    "business_action": "Business",
    "business_defend": "Business",
    "business_prestige": "Business",
    "duel_win": "Duels",
}


async def _open_quest_shortcut(
    interaction: discord.Interaction,
    bot: commands.Bot,
    event: str,
) -> None:
    if event == "shop_buy":
        from cogs.shop import Shop
        from utils.shop_view import ShopView

        cog = bot.get_cog("Shop")
        if not isinstance(cog, Shop):
            await interaction.response.send_message("Shop is unavailable.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        view = ShopView(cog, interaction.guild_id, interaction.user.id)
        embed, files, view = await view.build_payload()
        await interaction.followup.send(embed=embed, files=files, view=view)
        return

    if event in {"boss_attack", "boss_heal"} and event == "boss_attack":
        from cogs.boss import Boss
        from utils.boss_ui import send_boss_fight_panel

        cog = bot.get_cog("Boss")
        if not isinstance(cog, Boss):
            await interaction.response.send_message("Boss raids are unavailable.", ephemeral=True)
            return
        await send_boss_fight_panel(interaction, cog)
        return

    if event in {"drug_plant", "drug_harvest", "drug_sell", "drug_use"}:
        from cogs.drugs import Drugs
        from utils.drug_ui import send_drug_lab_panel

        cog = bot.get_cog("Drugs")
        if not isinstance(cog, Drugs):
            await interaction.response.send_message("Drug lab is unavailable.", ephemeral=True)
            return
        await send_drug_lab_panel(interaction, cog)
        return

    if event.startswith("business_"):
        from cogs.business import Business
        from utils.business_ui import send_business_panel

        cog = bot.get_cog("Business")
        if not isinstance(cog, Business):
            await interaction.response.send_message("Business panel is unavailable.", ephemeral=True)
            return
        await send_business_panel(interaction, cog)
        return

    if event == "duel_win":
        await interaction.response.send_message(
            "Use `/duel` to challenge another player.", ephemeral=True,
        )
        return

    hint = QUEST_SHORTCUT_HINTS.get(event, "Check `/help` for the matching command.")
    await interaction.response.send_message(hint, ephemeral=True)


class QuestShortcutButton(discord.ui.Button):
    def __init__(self, bot: commands.Bot, event: str, label: str) -> None:
        super().__init__(label=label[:80], style=discord.ButtonStyle.primary)
        self.bot = bot
        self.event = event

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message("Guild only.", ephemeral=True)
            return
        await _open_quest_shortcut(interaction, self.bot, self.event)


class QuestPanelView(discord.ui.View):
    def __init__(
        self,
        bot: commands.Bot,
        guild_id: int,
        user_id: int,
        *,
        pending_events: list[str],
    ) -> None:
        super().__init__(timeout=180.0)
        self.guild_id = guild_id
        self.user_id = user_id
        labels = {
            "shop_buy": "🛒 Shop",
            "boss_attack": "⚔️ Boss",
            "drug_plant": "🧪 Lab",
            "drug_harvest": "🧪 Lab",
            "drug_sell": "🧪 Lab",
            "business_create": "🏪 Business",
            "business_collect": "🏪 Business",
            "business_upgrade": "🏪 Business",
            "business_action": "🏪 Business",
            "duel_win": "🤺 Duel",
        }
        added = 0
        for event in pending_events:
            if event not in QUEST_PANEL_COGS and event not in QUEST_SHORTCUT_HINTS:
                continue
            label = labels.get(event, event.replace("_", " ").title()[:20])
            self.add_item(QuestShortcutButton(bot, event, label))
            added += 1
            if added >= 5:
                break

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This quest panel is not yours.", ephemeral=True)
            return False
        return True


async def build_quest_embeds(
    bot: commands.Bot,
    guild_id: int,
    user_id: int,
) -> tuple[list[discord.Embed], QuestPanelView | None]:
    db = bot.db
    await ensure_onboarding_quests(db, guild_id, user_id)
    onboard_done = await db.count_completed_quests(guild_id, user_id, TRACK_ONBOARDING) >= len(ONBOARDING_QUESTS)

    embeds: list[discord.Embed] = []
    pending_events: list[str] = []

    if not onboard_done:
        rows = await db.list_user_quests(guild_id, user_id, TRACK_ONBOARDING)
        done = await db.count_completed_quests(guild_id, user_id, TRACK_ONBOARDING)
        embed = discord.Embed(
            title="New raider onboarding",
            description="\n".join(format_quest_lines(rows, track=TRACK_ONBOARDING)),
            color=discord.Color.green(),
        )
        embed.add_field(name="Progress", value=f"{done}/{len(ONBOARDING_QUESTS)} complete", inline=False)
        embeds.append(embed)
        for row in rows:
            if row["completed_at"] is None:
                quest = quest_by_id(str(row["quest_id"]))
                if quest is not None:
                    pending_events.append(quest.event)
    else:
        await ensure_empire_quests(db, guild_id, user_id)
        empire_done = await db.count_completed_quests(guild_id, user_id, TRACK_EMPIRE) >= len(EMPIRE_QUESTS)
        if not empire_done:
            rows = await db.list_user_quests(guild_id, user_id, TRACK_EMPIRE)
            done = await db.count_completed_quests(guild_id, user_id, TRACK_EMPIRE)
            embed = discord.Embed(
                title="Empire tutorial",
                description="\n".join(format_quest_lines(rows, track=TRACK_EMPIRE)),
                color=discord.Color.dark_green(),
            )
            embed.add_field(name="Progress", value=f"{done}/{len(EMPIRE_QUESTS)} complete", inline=False)
            embeds.append(embed)
            for row in rows:
                if row["completed_at"] is None:
                    quest = quest_by_id(str(row["quest_id"]))
                    if quest is not None:
                        pending_events.append(quest.event)

    progress = await db.get_user_progress(user_id, guild_id)
    if is_veteran(progress) or onboard_done:
        await ensure_daily_quests(db, guild_id, user_id)
        daily_rows = await db.list_user_quests(guild_id, user_id, TRACK_DAILY)
        daily_embed = discord.Embed(
            title="Daily goals",
            description="\n".join(format_quest_lines(daily_rows, track=TRACK_DAILY)),
            color=discord.Color.blue(),
        )
        daily_embed.set_footer(text="Resets at UTC midnight · Rewards pay on completion")
        embeds.append(daily_embed)
        for row in daily_rows:
            if row["completed_at"] is None:
                quest = quest_by_id(str(row["quest_id"]))
                if quest is not None:
                    pending_events.append(quest.event)

    if not embeds:
        embeds.append(discord.Embed(title="Quests", description="No active quests.", color=discord.Color.greyple()))

    view = None
    if pending_events:
        view = QuestPanelView(bot, guild_id, user_id, pending_events=pending_events)

    return embeds, view
