"""Business Empire — own a business, earn passive income, upgrade, and grow."""
from __future__ import annotations

import logging
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from utils.business_ui import build_business_payload
from utils.businesses import tier_def
from utils.helpers import fmt_amount, guild_only_message
from utils.quests import record_quest_event

logger = logging.getLogger(__name__)


class Business(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.business_income_tick.start()
        self.stock_market_tick.start()
        self.district_war_tick.start()
        self._last_district_war: dict[int, float] = {}

    def cog_unload(self) -> None:
        self.business_income_tick.cancel()
        self.stock_market_tick.cancel()
        self.district_war_tick.cancel()

    business_group = app_commands.Group(
        name="business",
        description="Build and manage your business empire.",
        guild_only=True,
    )

    async def _send_panel(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild_id
        member = interaction.user
        if guild_id is None or not isinstance(member, discord.Member):
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        payload = await build_business_payload(self, member, guild_id, member.id)
        if payload is None:
            await interaction.response.send_message(
                "You don't own a business yet. Use **/business create** to start "
                f"with a Lemon Stand ({fmt_amount(tier_def(1).purchase_cost)}).",
                ephemeral=True,
            )
            return
        embed, files, view = payload
        await interaction.response.send_message(embed=embed, files=files, view=view)

    @business_group.command(name="create", description="Open your first business (a Lemon Stand).")
    async def create(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild_id
        member = interaction.user
        if guild_id is None or not isinstance(member, discord.Member):
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        await interaction.response.defer()
        err = await self.bot.db.create_business(member.id, guild_id)
        defn = tier_def(1)
        if err == "already_owns":
            await interaction.followup.send(
                "You already own a business. Use **/business info** to manage it.",
                ephemeral=True,
            )
            return
        if err == "insufficient_funds":
            await interaction.followup.send(
                f"You need **{fmt_amount(defn.purchase_cost)}** in your pocket to "
                "open a Lemon Stand.",
                ephemeral=True,
            )
            return
        if err:
            await interaction.followup.send("Could not create a business right now.", ephemeral=True)
            return
        await record_quest_event(self.bot.db, guild_id, member.id, "business_create")
        payload = await build_business_payload(self, member, guild_id, member.id)
        if payload is None:
            await interaction.followup.send("Business created!", ephemeral=True)
            return
        embed, files, view = payload
        embed.description = (
            f"🎉 You opened a **{defn.name}**! It earns "
            f"{fmt_amount(defn.base_income_per_hour)}/hr. Collect revenue with the "
            "button below and reinvest to grow your empire."
        )
        await interaction.followup.send(embed=embed, files=files, view=view)

    @business_group.command(name="info", description="View and manage your business.")
    async def info(self, interaction: discord.Interaction) -> None:
        await self._send_panel(interaction)

    @business_group.command(name="collect", description="Collect stored revenue from your business.")
    async def collect(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild_id
        if guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        amount, err = await self.bot.db.collect_business_income(interaction.user.id, guild_id)
        if err == "no_business":
            await interaction.followup.send(
                "You don't own a business. Use **/business create**.", ephemeral=True,
            )
            return
        if err == "empty":
            await interaction.followup.send(
                "No revenue stored yet — let it build up.", ephemeral=True,
            )
            return
        if err:
            await interaction.followup.send("Could not collect right now.", ephemeral=True)
            return
        await record_quest_event(self.bot.db, guild_id, interaction.user.id, "business_collect")
        await interaction.followup.send(
            f"💰 Collected **{fmt_amount(amount)}** to your pocket!", ephemeral=True,
        )

    @business_group.command(name="upgrade", description="Open the upgrade panel for your business.")
    async def upgrade(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild_id
        if guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        from utils.business_ui import UpgradeBranchView, build_upgrade_embed

        row = await self.bot.db.get_business(interaction.user.id, guild_id)
        if row is None:
            await interaction.response.send_message(
                "You don't own a business. Use **/business create**.", ephemeral=True,
            )
            return
        view = UpgradeBranchView(self, guild_id, interaction.user.id)
        await interaction.response.send_message(
            embed=build_upgrade_embed(row), view=view, ephemeral=True,
        )

    @business_group.command(name="districts", description="View the district map, relocate, and build influence.")
    async def districts(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        from utils.district_ui import send_district_panel

        await send_district_panel(interaction, self)

    @business_group.command(name="action", description="Launch a competitive action against a rival.")
    async def action(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        from utils.business_action_ui import send_action_panel

        await send_action_panel(interaction, self)

    @business_group.command(name="defend", description="Respond to an attack on your business.")
    async def defend(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild_id
        if guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        result = await self.bot.db.defend_business(interaction.user.id, guild_id)
        if result.get("error"):
            await interaction.response.send_message(
                "No active attack to defend right now.", ephemeral=True,
            )
            return
        pct = int(float(result["new_penalty"]) * 100)
        await record_quest_event(self.bot.db, guild_id, interaction.user.id, "business_defend")
        await interaction.response.send_message(
            f"🛡️ Defended! The attack's penalty is cut to **−{pct}%**.", ephemeral=True,
        )

    @business_group.command(name="market", description="Open the stock market to invest in corporations.")
    async def market(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        from utils.stock_ui import send_stock_panel

        await send_stock_panel(interaction, self)

    @business_group.command(
        name="prestige",
        description="Prestige your maxed business for a permanent income bonus.",
    )
    async def prestige(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild_id
        if guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        from utils.business_ui import build_prestige_embed

        row = await self.bot.db.get_business(interaction.user.id, guild_id)
        if row is None:
            await interaction.response.send_message(
                "You don't own a business. Use **/business create**.", ephemeral=True,
            )
            return
        from utils.businesses import MAX_TIER

        if int(row["tier"]) < MAX_TIER:
            await interaction.response.send_message(
                "Business prestige unlocks at the **Corporation** tier (tier 7). "
                "Keep tiering up first!",
                ephemeral=True,
            )
            return
        from utils.business_ui import PrestigeConfirmView

        await interaction.response.send_message(
            embed=build_prestige_embed(row),
            view=PrestigeConfirmView(self, guild_id, interaction.user.id),
            ephemeral=True,
        )

    @business_group.command(
        name="megaprojects",
        description="Fund massive personal endgame projects for permanent bonuses.",
    )
    async def megaprojects(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        from utils.mega_project_ui import send_mega_project_panel

        await send_mega_project_panel(interaction, self)

    @business_group.command(
        name="manage",
        description="Manage employees: raise wages or host a team event.",
    )
    async def manage(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild_id
        if guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        from utils.business_manage_ui import send_manage_panel

        await send_manage_panel(interaction, self)

    @business_group.command(
        name="supplychain",
        description="Configure auto-funded drug supply chain (Tier 5+).",
    )
    async def supplychain(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        from utils.supply_chain_ui import send_supply_chain_panel

        await send_supply_chain_panel(interaction, self)

    @business_group.command(
        name="acquisitions",
        description="Purchase sub-empire acquisitions after completing all mega projects.",
    )
    async def acquisitions(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        from utils.acquisition_ui import send_acquisition_panel

        await send_acquisition_panel(interaction, self)

    @business_group.command(
        name="starter",
        description="Guide to starting your business empire.",
    )
    async def starter(self, interaction: discord.Interaction) -> None:
        defn = tier_def(1)
        embed = discord.Embed(
            title="🍋 Business Empire Starter Guide",
            description=(
                "1. **Create** — `/business create` opens a Lemon Stand.\n"
                "2. **Collect** — passive revenue builds every 5 min; collect to your wallet.\n"
                "3. **Upgrade** — reinvest in efficiency, reputation, and branches.\n"
                "4. **Tier up** — climb 7 tiers to Corporation.\n"
                "5. **Manage** — `/business manage` keeps employees happy (+/-15% income).\n"
                "6. **Drugs** — `/drugs lab` runs parallel to your business for active income.\n"
                "7. **Prestige** — at Corporation, reset for permanent +5%/level income."
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"Lemon Stand costs {fmt_amount(defn.purchase_cost if defn else 500)}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tasks.loop(seconds=config.DISTRICT_WAR_TICK_SECONDS)
    async def district_war_tick(self) -> None:
        now = time.time()
        for guild in self.bot.guilds:
            last = self._last_district_war.get(guild.id, 0.0)
            if now - last < config.DISTRICT_WAR_TICK_SECONDS:
                continue
            try:
                await self.bot.db.process_district_wars(guild.id)
                self._last_district_war[guild.id] = now
            except Exception:
                logger.exception("district war tick failed guild=%s", guild.id)

    @district_war_tick.before_loop
    async def before_district_war_tick(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=config.BUSINESS_INCOME_TICK_SECONDS)
    async def business_income_tick(self) -> None:
        for guild in self.bot.guilds:
            try:
                await self.bot.db.process_business_income(guild.id)
                await self.bot.db.prune_expired_business_buffs(guild.id)
            except Exception:
                logger.exception("business income tick failed guild=%s", guild.id)

    @business_income_tick.before_loop
    async def before_business_income_tick(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=config.STOCK_DIVIDEND_TICK_SECONDS)
    async def stock_market_tick(self) -> None:
        for guild in self.bot.guilds:
            try:
                await self.bot.db.process_stock_dividends(guild.id)
                await self.bot.db.maybe_roll_stock_event(guild.id)
            except Exception:
                logger.exception("stock market tick failed guild=%s", guild.id)

    @stock_market_tick.before_loop
    async def before_stock_market_tick(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Business(bot))
