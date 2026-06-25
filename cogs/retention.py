"""Retention: notifications, weekly boards, activity rank."""
from __future__ import annotations

import logging
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from utils.activity_levels import level_from_total_xp, progress_bar
from utils.drugs import drug_by_id
from utils.helpers import fmt_amount, guild_only_message
from utils.notify_ui import send_notify_panel


async def grant_activity_xp(
    bot: commands.Bot,
    member: discord.Member,
    guild_id: int,
    amount: int,
) -> None:
    if amount <= 0 or member.bot:
        return
    _, old_level, new_level = await bot.db.add_activity_xp(member.id, guild_id, amount)
    if new_level > old_level:
        await assign_activity_roles(member, new_level)


async def assign_activity_roles(member: discord.Member, level: int) -> None:
    if member.guild is None:
        return
    for milestone in config.ACTIVITY_ROLE_MILESTONES:
        if level < milestone:
            break
        role_name = config.ACTIVITY_ROLE_NAMES.get(milestone)
        if not role_name:
            continue
        role = discord.utils.get(member.guild.roles, name=role_name)
        if role is None or role in member.roles:
            continue
        try:
            await member.add_roles(role, reason=f"Activity level {milestone}")
        except discord.HTTPException:
            logging.debug("Could not assign activity role %s to %s", role_name, member.id)


class Retention(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.notify_tick.start()
        self.trade_expire_tick.start()

    def cog_unload(self) -> None:
        self.notify_tick.cancel()
        self.trade_expire_tick.cancel()

    @tasks.loop(seconds=config.NOTIFY_TICK_SECONDS)
    async def notify_tick(self) -> None:
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            await self._tick_guild_notifications(guild)

    @notify_tick.before_loop
    async def before_notify_tick(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=2)
    async def trade_expire_tick(self) -> None:
        await self.bot.db.expire_stale_trades()

    @trade_expire_tick.before_loop
    async def before_trade_expire_tick(self) -> None:
        await self.bot.wait_until_ready()

    async def _maybe_dm(
        self,
        user_id: int,
        guild_id: int,
        *,
        flag: int,
        notify_key: str,
        body: str,
        cooldown_seconds: float = 6 * 3600,
    ) -> None:
        flags = await self.bot.db.get_notify_flags(user_id, guild_id)
        if not (flags & flag):
            return
        remaining = await self.bot.db.notify_cooldown_remaining(
            user_id, guild_id, notify_key, cooldown_seconds=cooldown_seconds,
        )
        if remaining > 0:
            return
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        member = guild.get_member(user_id)
        if member is None or member.bot:
            return
        try:
            await member.send(body)
            await self.bot.db.record_notify_sent(user_id, guild_id, notify_key)
        except discord.HTTPException:
            pass

    async def _tick_guild_notifications(self, guild: discord.Guild) -> None:
        from utils.businesses import capacity_for_level

        for row in await self.bot.db.list_crop_ready_grows(guild.id):
            user_id = int(row["user_id"])
            grow_id = int(row["grow_id"])
            defn = drug_by_id(str(row["drug_id"]))
            name = defn.name if defn else str(row["drug_id"])
            await self._maybe_dm(
                user_id,
                guild.id,
                flag=config.NOTIFY_CROPS,
                notify_key=f"crop:{grow_id}",
                body=(
                    f"🌿 **{guild.name}** — your **{name}** crop is ready to harvest! "
                    "Open `/drugs` to collect."
                ),
                cooldown_seconds=12 * 3600,
            )

        boss = await self.bot.db.get_active_boss(guild.id)
        if boss is not None and float(boss["hp"]) > 0:
            spawned_at = int(float(boss["spawned_at"]))
            for user_id in await self.bot.db.list_notify_users(guild.id, config.NOTIFY_BOSS):
                await self._maybe_dm(
                    user_id,
                    guild.id,
                    flag=config.NOTIFY_BOSS,
                    notify_key=f"boss:{guild.id}:{spawned_at}",
                    body=(
                        f"👹 **{guild.name}** — **{boss['name']}** is raiding! "
                        "Join the fight with `/boss-attack`."
                    ),
                    cooldown_seconds=24 * 3600,
                )

        for row in await self.bot.db.list_business_vault_alerts(guild.id):
            user_id = int(row["user_id"])
            cap = capacity_for_level(int(row["tier"]), int(row["capacity"]))
            stored = float(row["stored_income"])
            pct = int(stored / cap * 100) if cap > 0 else 100
            await self._maybe_dm(
                user_id,
                guild.id,
                flag=config.NOTIFY_BUSINESS,
                notify_key=f"biz:{user_id}:{int(stored)}",
                body=(
                    f"🏢 **{guild.name}** — business vault is **{pct}%** full "
                    f"({fmt_amount(stored)}). Collect with `/business collect`."
                ),
                cooldown_seconds=8 * 3600,
            )

        for attack in await self.bot.db.list_active_defense_attacks(guild.id):
            defender_id = int(attack["defender_id"])
            attack_id = int(attack["attack_id"])
            await self._maybe_dm(
                defender_id,
                guild.id,
                flag=config.NOTIFY_DEFENSE,
                notify_key=f"defense:{attack_id}",
                body=(
                    f"🛡️ **{guild.name}** — your business is under attack! "
                    f"Use `/business defend` within "
                    f"**{config.BUSINESS_DEFENSE_WINDOW_SECONDS // 60}m** to halve the penalty."
                ),
                cooldown_seconds=3600,
            )

    @app_commands.command(name="notify", description="Manage DM reminders (crops, boss, business, defense).")
    @app_commands.guild_only()
    async def notify(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        await send_notify_panel(self, interaction)

    @app_commands.command(name="rank", description="Your server activity level and XP progress.")
    @app_commands.describe(user="Player to inspect (defaults to you)")
    @app_commands.guild_only()
    async def rank(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        target = user or interaction.user
        xp = await self.bot.db.get_activity_xp(target.id, interaction.guild_id)
        level, into, need = level_from_total_xp(xp)
        streak = await self.bot.db.get_daily_streak(target.id, interaction.guild_id)
        bar = progress_bar(into, need)
        embed = discord.Embed(
            title=f"{target.display_name} — Activity rank",
            color=discord.Color.blue(),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Level", value=f"**{level}**", inline=True)
        embed.add_field(name="Total XP", value=f"**{xp:,}**", inline=True)
        embed.add_field(name="Daily streak", value=f"**{streak}** day(s)", inline=True)
        if need > 0:
            embed.add_field(
                name="Progress",
                value=f"`{bar}` {into:,} / {need:,} XP",
                inline=False,
            )
        else:
            embed.add_field(name="Progress", value="**Max level**", inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="weekly", description="This week's leaderboards (boss, business, drug sales).")
    @app_commands.guild_only()
    async def weekly(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        guild_id = interaction.guild_id
        week_id = self.bot.db.current_week_id()
        embed = discord.Embed(
            title=f"Weekly boards — {week_id}",
            color=discord.Color.dark_purple(),
        )
        categories = (
            ("boss_damage", "👹 Boss damage", lambda v: f"{int(v):,}"),
            ("business_collected", "🏢 Business collected", fmt_amount),
            ("drug_sales", "💊 Drug units sold", lambda v: f"{int(v):,}"),
        )
        for column, title, fmt in categories:
            rows = await self.bot.db.weekly_leaderboard(guild_id, column, limit=5)
            if not rows:
                embed.add_field(name=title, value="_No entries yet_", inline=False)
                continue
            lines: list[str] = []
            for i, row in enumerate(rows, 1):
                uid = int(row["user_id"])
                score = row["score"]
                member = interaction.guild.get_member(uid) if interaction.guild else None
                name = member.display_name if member else f"User {uid}"
                lines.append(f"**{i}.** {name} — {fmt(score)}")
            embed.add_field(name=title, value="\n".join(lines), inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="calendar", description="Claim today's login calendar reward (7-day cycle).")
    @app_commands.guild_only()
    async def calendar(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        now = time.time()
        state = await self.bot.db.get_calendar_state(interaction.user.id, interaction.guild_id)
        reward, day, err = await self.bot.db.claim_calendar(
            interaction.user.id, interaction.guild_id, now,
        )
        if err == "cooldown":
            last = float(state["last_claim"])
            remaining = (last + config.CALENDAR_CLAIM_COOLDOWN_SECONDS) - now
            hours = int(max(0, remaining) // 3600)
            mins = int((max(0, remaining) % 3600) // 60)
            await interaction.response.send_message(
                f"Calendar already claimed. Next slot in **{hours}h {mins}m**.",
                ephemeral=True,
            )
            return
        lines = [
            f"**Day {day}/{config.CALENDAR_CYCLE_DAYS}** — you received **{fmt_amount(reward or 0)}**!",
        ]
        if day < config.CALENDAR_CYCLE_DAYS:
            next_reward = config.CALENDAR_REWARDS[day]
            lines.append(f"Tomorrow: **{fmt_amount(next_reward)}** if you claim on time.")
        else:
            lines.append("Cycle complete — tomorrow starts back at day 1.")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="pass", description="Monthly activity pass progress and rewards.")
    @app_commands.describe(action="View progress or claim unlocked tiers")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Status", value="status"),
            app_commands.Choice(name="Claim rewards", value="claim"),
        ],
    )
    @app_commands.guild_only()
    async def pass_cmd(self, interaction: discord.Interaction, action: str = "status") -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        uid = interaction.user.id
        gid = interaction.guild_id
        if action == "claim":
            total, tiers = await self.bot.db.claim_pass_tiers(uid, gid)
            if not tiers:
                await interaction.response.send_message(
                    "No pass rewards ready. Earn activity XP (chat, VC, raids) to unlock tiers.",
                    ephemeral=True,
                )
                return
            tier_nums = ", ".join(str(t + 1) for t in tiers)
            await interaction.response.send_message(
                f"Claimed pass tiers **{tier_nums}** for **{fmt_amount(total)}**!",
                ephemeral=True,
            )
            return

        state = await self.bot.db.get_pass_state(uid, gid)
        pass_xp = state["pass_xp"]
        mask = state["claimed_mask"]
        month = self.bot.db.current_pass_reset_key()
        embed = discord.Embed(
            title=f"Activity pass — {month}",
            description="Earn activity XP this month to unlock free nugget tiers.",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Pass XP", value=f"**{pass_xp:,}**", inline=True)
        tier_lines: list[str] = []
        for i, need in enumerate(config.PASS_TIER_XP):
            if i >= len(config.PASS_TIER_REWARDS):
                break
            reward = config.PASS_TIER_REWARDS[i]
            if mask & (1 << i):
                status = "✅ claimed"
            elif pass_xp >= need:
                status = "🎁 **ready**"
            else:
                status = f"{pass_xp:,}/{need:,} XP"
            tier_lines.append(f"**T{i + 1}** — {fmt_amount(reward)} · {status}")
        embed.add_field(name="Tiers", value="\n".join(tier_lines), inline=False)
        embed.set_footer(text="Use /pass → Claim rewards when tiers are ready")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="refer", description="Your invite code or redeem a friend's code.")
    @app_commands.describe(code="Friend's referral code (omit to see yours)")
    @app_commands.guild_only()
    async def refer(self, interaction: discord.Interaction, code: str | None = None) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        if code:
            err = await self.bot.db.apply_referral_code(
                interaction.user.id, interaction.guild_id, code,
            )
            errors = {
                "invalid_code": "That referral code doesn't exist.",
                "already_referred": "You already used a referral code.",
                "self_referral": "You can't use your own code.",
            }
            if err:
                await interaction.response.send_message(errors.get(err, "Could not apply code."), ephemeral=True)
                return
            await interaction.response.send_message(
                f"Code applied! You received **{fmt_amount(config.REFERRAL_REFEREE_REWARD)}** "
                f"(referrer gets **{fmt_amount(config.REFERRAL_REFERRER_REWARD)}**).",
                ephemeral=True,
            )
            return
        my_code = await self.bot.db.ensure_referral_code(
            interaction.user.id, interaction.guild_id,
        )
        await interaction.response.send_message(
            f"Your referral code: **`{my_code}`**\n"
            f"New players run `/refer code:{my_code}` — you each earn "
            f"**{fmt_amount(config.REFERRAL_REFERRER_REWARD)}** / "
            f"**{fmt_amount(config.REFERRAL_REFEREE_REWARD)}**.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Retention(bot))
