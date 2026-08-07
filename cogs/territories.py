from __future__ import annotations

import contextlib
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from utils.achievements import evaluate_unlocks, format_unlock_message
from utils.expansion_events import record_expansion_event
from utils.helpers import fmt_amount, guild_only_message, resolve_main_channel, send_error
from utils.quests import record_quest_event
from utils.territory_ui import send_territory_map_panel
from utils.territories import (
    TERRITORY_MAP,
    guard_cost_per_unit,
    perks_from_held,
    siege_success_chance,
    territory_by_id,
)

logger = logging.getLogger(__name__)


def build_siege_embed(
    *,
    name: str,
    defender: str,
    attacker: str,
    ends_at: float,
    guards: int,
    max_guards: int,
    chance: float,
    income_per_hour: float,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"⚔️ Siege — {name}",
        description=(
            f"**{attacker}** is attacking **{defender}**!\n"
            f"Resolves <t:{int(ends_at)}:R> · Attacker win chance **~{int(chance * 100)}%**"
        ),
        color=discord.Color.dark_red(),
    )
    embed.add_field(
        name="Zone",
        value=f"{fmt_amount(income_per_hour)}/hr · Guards **{guards}/{max_guards}**",
        inline=False,
    )
    embed.set_footer(text="Defenders: hire guards (wallet or crew treasury) to lower attacker odds.")
    return embed


def build_siege_result_embed(item: dict[str, object]) -> discord.Embed:
    name = str(item["name"])
    if item["won"]:
        return discord.Embed(
            title=f"🏴 {name} captured!",
            description=(
                f"**{item['attacker']}** took **{name}** from **{item['defender']}** "
                f"({int(float(item['chance']) * 100)}% roll)."
            ),
            color=discord.Color.gold(),
        )
    return discord.Embed(
        title=f"🛡️ {name} held!",
        description=(
            f"**{item['defender']}** defended **{name}** against **{item['attacker']}** "
            f"({int(float(item['chance']) * 100)}% attacker chance)."
        ),
        color=discord.Color.blue(),
    )


class SiegeGuardView(discord.ui.View):
    def __init__(
        self,
        cog: Territories,
        guild_id: int,
        territory_id: str,
        *,
        timeout: float | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.cog = cog
        self.guild_id = guild_id
        self.territory_id = territory_id
        self.add_item(SiegeGuardTreasuryButton(cog, guild_id, territory_id))
        self.add_item(SiegeGuardWalletButton(cog, guild_id, territory_id))

    async def refresh_siege_message(self, interaction: discord.Interaction) -> None:
        row = await self.cog.bot.db.get_territory_row(self.guild_id, self.territory_id)
        if row is None or row["siege_ends_at"] is None:
            return
        if float(row["siege_ends_at"]) <= time.time():
            return
        defn = territory_by_id(self.territory_id)
        if defn is None:
            return
        attacker = str(row["siege_attacker_crew"])
        defender = str(row["owner_crew_name"])
        members = await self.cog.bot.db.count_crew_members(self.guild_id, attacker)
        guards = int(row["guards"])
        chance = siege_success_chance(members, guards, defn)
        embed = build_siege_embed(
            name=defn.name,
            defender=defender,
            attacker=attacker,
            ends_at=float(row["siege_ends_at"]),
            guards=guards,
            max_guards=defn.max_guards,
            chance=chance,
            income_per_hour=defn.income_per_hour,
        )
        if interaction.message:
            await interaction.message.edit(embed=embed, view=self)


class SiegeGuardTreasuryButton(discord.ui.Button):
    def __init__(self, cog: Territories, guild_id: int, territory_id: str) -> None:
        super().__init__(
            label="+1 Guard (treasury)",
            style=discord.ButtonStyle.success,
        )
        self.cog = cog
        self.guild_id = guild_id
        self.territory_id = territory_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        err = await self.cog.bot.db.buy_territory_guards(
            interaction.user.id,
            self.guild_id,
            self.territory_id,
            1,
            pay_from="treasury",
        )
        if err:
            msgs = {
                "not_in_crew": "You must be in the defending crew.",
                "not_owner": "Only the holding crew can hire guards.",
                "under_siege": "No active siege.",
                "guard_cap": "Guard cap reached.",
                "insufficient_treasury": "Crew treasury cannot cover this guard.",
            }
            await interaction.followup.send(msgs.get(err, err), ephemeral=True)
            return
        await record_quest_event(
            self.cog.bot.db, self.guild_id, interaction.user.id, "territory_guards",
        )
        view = self.view
        if isinstance(view, SiegeGuardView):
            await view.refresh_siege_message(interaction)
        await interaction.followup.send("Hired **1** guard from crew treasury.", ephemeral=True)


class SiegeGuardWalletButton(discord.ui.Button):
    def __init__(self, cog: Territories, guild_id: int, territory_id: str) -> None:
        super().__init__(
            label="+1 Guard (wallet)",
            style=discord.ButtonStyle.primary,
        )
        self.cog = cog
        self.guild_id = guild_id
        self.territory_id = territory_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        err = await self.cog.bot.db.buy_territory_guards(
            interaction.user.id,
            self.guild_id,
            self.territory_id,
            1,
            pay_from="wallet",
        )
        if err:
            defn = territory_by_id(self.territory_id)
            unit = guard_cost_per_unit(defn) if defn else 0
            msgs = {
                "not_in_crew": "You must be in the defending crew.",
                "not_owner": "Only the holding crew can hire guards.",
                "under_siege": "No active siege.",
                "guard_cap": "Guard cap reached.",
                "insufficient_funds": f"Need **{fmt_amount(unit)}** in your wallet.",
            }
            await interaction.followup.send(msgs.get(err, err), ephemeral=True)
            return
        await record_quest_event(
            self.cog.bot.db, self.guild_id, interaction.user.id, "territory_guards",
        )
        view = self.view
        if isinstance(view, SiegeGuardView):
            await view.refresh_siege_message(interaction)
        await interaction.followup.send("Hired **1** guard from your wallet.", ephemeral=True)


class Territories(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.territory_income_tick.start()
        self.territory_siege_tick.start()

    def cog_unload(self) -> None:
        self.territory_income_tick.cancel()
        self.territory_siege_tick.cancel()

    async def territory_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        needle = (current or "").strip().lower()
        choices: list[app_commands.Choice[str]] = []
        for defn in TERRITORY_MAP.values():
            if needle and needle not in defn.name.lower() and needle not in defn.territory_id:
                continue
            label = f"{defn.name} ({fmt_amount(defn.income_per_hour)}/hr)"
            choices.append(app_commands.Choice(name=label[:100], value=defn.territory_id))
        return choices[:25]

    @app_commands.command(
        name="territory",
        description="Control zones: map, attack, buy guards, abandon. Income goes to crew treasury.",
    )
    @app_commands.describe(
        action="What to do",
        zone="Territory (Docks, Market, Foundry, Vault, Citadel)",
        amount="Guards to buy (1–20) for Buy guards",
        pay_from="Pay for guards from wallet or crew treasury",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Map / status", value="map"),
            app_commands.Choice(name="Attack / claim", value="attack"),
            app_commands.Choice(name="Buy guards", value="guards"),
            app_commands.Choice(name="Abandon", value="abandon"),
        ],
        pay_from=[
            app_commands.Choice(name="Your wallet", value="wallet"),
            app_commands.Choice(name="Crew treasury", value="treasury"),
        ],
    )
    @app_commands.autocomplete(zone=territory_autocomplete)
    @app_commands.guild_only()
    async def territory(
        self,
        interaction: discord.Interaction,
        action: str,
        zone: str | None = None,
        amount: app_commands.Range[int, 1, 20] | None = None,
        pay_from: str = "wallet",
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        guild_id = interaction.guild_id
        uid = interaction.user.id

        if action == "map":
            await send_territory_map_panel(interaction, self)
            return

        if not zone:
            await interaction.response.send_message(
                "Pick a **zone** for this action.", ephemeral=True,
            )
            return
        defn = territory_by_id(zone)
        if defn is None:
            await interaction.response.send_message(
                "Unknown territory. Use autocomplete.", ephemeral=True,
            )
            return

        if action == "attack":
            await interaction.response.defer(ephemeral=True)
            try:
                err = await self.bot.db.start_territory_siege(uid, guild_id, defn.territory_id)
                msgs = {
                    "not_in_crew": "Join a crew first (`/crew panel` → Join crew).",
                    "crew_too_small": (
                        f"Need at least {config.TERRITORY_MIN_CREW_MEMBERS_TO_ATTACK} "
                        "crew members to attack."
                    ),
                    "own_territory": "Your crew already holds this zone.",
                    "already_under_siege": "This zone is already under siege.",
                    "siege_cooldown": "This zone was attacked recently. Try again later.",
                    "max_territories": (
                        f"Your crew already holds {config.TERRITORY_MAX_HELD_PER_CREW} zones."
                    ),
                    "invalid_territory": "Unknown territory.",
                }
                if err == "claimed_neutral":
                    crew = await self.bot.db.get_crew_membership(uid, guild_id)
                    await record_quest_event(
                        self.bot.db, guild_id, uid, "territory_claim",
                    )
                    unlocked = await evaluate_unlocks(self.bot.db, guild_id, uid)
                    extra = format_unlock_message(unlocked)
                    await interaction.followup.send(
                        f"**{defn.name}** is unclaimed — crew **{crew}** now holds it!"
                        + (f"\n{extra}" if extra else ""),
                        ephemeral=True,
                    )
                    return
                if err:
                    await interaction.followup.send(msgs.get(err, err), ephemeral=True)
                    return
                row = await self.bot.db.get_territory_row(guild_id, defn.territory_id)
                if row and interaction.guild:
                    await self._announce_siege_start(interaction.guild, guild_id, row, defn)
                mins = int(config.TERRITORY_SIEGE_DURATION_SECONDS // 60)
                await interaction.followup.send(
                    f"Siege started on **{defn.name}**! Resolves in **{mins}** minutes.",
                    ephemeral=True,
                )
            except Exception:
                logger.exception("territory attack failed")
                await send_error(interaction, "Something went wrong starting the siege.")
            return

        if action == "guards":
            if amount is None:
                await interaction.response.send_message(
                    "Set **amount** (how many guards to hire).", ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            unit = guard_cost_per_unit(defn)
            source = pay_from if pay_from in {"wallet", "treasury"} else "wallet"
            try:
                err = await self.bot.db.buy_territory_guards(
                    uid, guild_id, defn.territory_id, int(amount), pay_from=source,
                )
                msgs = {
                    "not_in_crew": "Join a crew first.",
                    "not_owner": "Only the holding crew can buy guards here.",
                    "guard_cap": f"Max **{defn.max_guards}** guards at {defn.name}.",
                    "insufficient_funds": (
                        f"Need **{fmt_amount(unit * int(amount))}** "
                        f"({fmt_amount(unit)} each) in your wallet."
                    ),
                    "insufficient_treasury": (
                        f"Crew treasury needs **{fmt_amount(unit * int(amount))}**."
                    ),
                    "invalid_territory": "Unknown territory.",
                }
                if err:
                    await interaction.followup.send(msgs.get(err, err), ephemeral=True)
                    return
                await record_quest_event(
                    self.bot.db, guild_id, uid, "territory_guards", amount=int(amount),
                )
                row = await self.bot.db.get_territory_row(guild_id, defn.territory_id)
                guards = int(row["guards"]) if row is not None else int(amount)
                src_label = "crew treasury" if source == "treasury" else "your wallet"
                await interaction.followup.send(
                    f"Hired **{int(amount)}** guard(s) at **{defn.name}** "
                    f"({guards}/{defn.max_guards}) from {src_label}.",
                    ephemeral=True,
                )
            except Exception:
                logger.exception("territory guards failed")
                await send_error(interaction, "Something went wrong buying guards.")
            return

        if action == "abandon":
            await interaction.response.defer(ephemeral=True)
            try:
                err = await self.bot.db.abandon_territory(uid, guild_id, defn.territory_id)
                msgs = {
                    "not_in_crew": "Join a crew first.",
                    "not_owner": "Your crew does not hold this zone.",
                    "invalid_territory": "Unknown territory.",
                }
                if err:
                    await interaction.followup.send(msgs.get(err, err), ephemeral=True)
                    return
                await interaction.followup.send(
                    f"Your crew abandoned **{defn.name}**. It is now neutral.",
                    ephemeral=True,
                )
            except Exception:
                logger.exception("territory abandon failed")
                await send_error(interaction, "Something went wrong.")
            return

        await interaction.response.send_message("Unknown action.", ephemeral=True)

    async def _announce_siege_start(
        self,
        guild: discord.Guild,
        guild_id: int,
        row: object,
        defn: object,
    ) -> None:
        from utils.territories import TerritoryDef

        assert isinstance(defn, TerritoryDef)
        owner = str(row["owner_crew_name"])
        attacker = str(row["siege_attacker_crew"])
        ends_at = float(row["siege_ends_at"])
        guards = int(row["guards"])
        members = await self.bot.db.count_crew_members(guild_id, attacker)
        chance = siege_success_chance(members, guards, defn)
        embed = build_siege_embed(
            name=defn.name,
            defender=owner,
            attacker=attacker,
            ends_at=ends_at,
            guards=guards,
            max_guards=defn.max_guards,
            chance=chance,
            income_per_hour=defn.income_per_hour,
        )
        channel = await resolve_main_channel(guild, self.bot.db)
        if channel is None:
            return
        defender_ids = await self.bot.db.list_crew_member_user_ids(guild_id, owner)
        mentions: list[str] = []
        for member_id in defender_ids[:8]:
            member = guild.get_member(member_id)
            if member is not None:
                mentions.append(member.mention)
        ping = " ".join(mentions)
        content = (
            f"{ping} **{owner}** — **{attacker}** is sieging **{defn.name}**!"
            if ping
            else f"**{owner}** — **{attacker}** is sieging **{defn.name}**!"
        )
        view = SiegeGuardView(
            self,
            guild_id,
            defn.territory_id,
            timeout=config.TERRITORY_SIEGE_DURATION_SECONDS + 60,
        )
        try:
            message = await channel.send(
                content,
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions(users=True),
            )
            await self.bot.db.set_territory_siege_message(
                guild_id, defn.territory_id, channel.id, message.id,
            )
        except discord.HTTPException:
            logger.exception("siege announce failed guild=%s", guild_id)

    async def _send_map(
        self, interaction: discord.Interaction, guild_id: int, uid: int,
    ) -> None:
        crew = await self.bot.db.get_crew_membership(uid, guild_id)
        rows = await self.bot.db.list_territory_rows(guild_id)
        now = time.time()
        lines: list[str] = []
        for row in rows:
            tid = str(row["territory_id"])
            defn = TERRITORY_MAP.get(tid)
            if defn is None:
                continue
            owner = row["owner_crew_name"]
            guards = int(row["guards"])
            owner_text = f"**{owner}**" if owner else "_Neutral_"
            siege = row["siege_ends_at"]
            extra = ""
            if siege is not None and float(siege) > now:
                attacker = row["siege_attacker_crew"]
                left = int((float(siege) - now) // 60) + 1
                members = await self.bot.db.count_crew_members(
                    guild_id, str(attacker),
                )
                chance = siege_success_chance(members, guards, defn)
                extra = (
                    f" · ⚔️ **{attacker}** ({left}m, ~{int(chance * 100)}% capture)"
                )
            perk = f" · _{defn.perk_label}_"
            lines.append(
                f"**{defn.name}** — {owner_text} · "
                f"{fmt_amount(defn.income_per_hour)}/hr · "
                f"Guards {guards}/{defn.max_guards}{extra}{perk}",
            )
        embed = discord.Embed(
            title="Territory map",
            description="\n".join(lines) if lines else "_No zones configured_",
            color=discord.Color.dark_green(),
        )
        if crew:
            held = await self.bot.db.list_crew_held_territories(guild_id, crew)
            income_total = sum(
                TERRITORY_MAP[t].income_per_hour for t, _ in held if t in TERRITORY_MAP
            )
            perk_lines = perks_from_held({t for t, _ in held}).summary_lines()
            embed.add_field(
                name=f"Crew {crew} zones",
                value="\n".join(perk_lines) if perk_lines else "_None held_",
                inline=False,
            )
            embed.set_footer(
                text=(
                    f"Holds {len(held)}/{config.TERRITORY_MAX_HELD_PER_CREW} · "
                    f"≈{fmt_amount(income_total)}/hr → treasury"
                ),
            )
        else:
            embed.set_footer(text="Join a crew to attack or claim zones.")
        await interaction.response.send_message(embed=embed)

    @tasks.loop(seconds=config.TERRITORY_HOURLY_TICK_SECONDS)
    async def territory_income_tick(self) -> None:
        for guild in self.bot.guilds:
            try:
                await self.bot.db.process_territory_hourly_income(guild.id)
            except Exception:
                logger.exception("territory income tick failed guild=%s", guild.id)

    @territory_income_tick.before_loop
    async def before_territory_income_tick(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=60)
    async def territory_siege_tick(self) -> None:
        for guild in self.bot.guilds:
            try:
                results = await self.bot.db.resolve_territory_sieges(guild.id)
            except Exception:
                logger.exception("territory siege tick failed guild=%s", guild.id)
                continue
            if not results:
                continue
            channel = await resolve_main_channel(guild, self.bot.db)
            for item in results:
                ch_id = item.get("channel_id")
                msg_id = item.get("message_id")
                if ch_id and msg_id:
                    try:
                        ch = guild.get_channel(int(ch_id))
                        if isinstance(ch, discord.TextChannel):
                            msg = await ch.fetch_message(int(msg_id))
                            await msg.edit(
                                content="**Siege resolved.**",
                                embed=build_siege_result_embed(item),
                                view=None,
                            )
                    except (discord.HTTPException, discord.NotFound):
                        pass
                attacker_uid = item.get("attacker_user_id")
                if item.get("won") and attacker_uid:
                    await record_quest_event(
                        self.bot.db,
                        guild.id,
                        int(attacker_uid),
                        "territory_claim",
                    )
                    await record_expansion_event(
                        self.bot.db, guild.id, int(attacker_uid), "territory_siege",
                    )
                    crew_id = await self.bot.db.get_user_crew_id(int(attacker_uid), guild.id)
                    if crew_id is not None:
                        zone = str(item.get("territory_id", "docks"))
                        await self.bot.db.unlock_territory_cosmetic(
                            guild.id, crew_id, zone, f"banner_{zone}",
                        )
                        if zone == "citadel":
                            await self.bot.db.unlock_crew_legacy(
                                guild.id, crew_id, "citadel_holder",
                            )
                    unlocked = await evaluate_unlocks(
                        self.bot.db, guild.id, int(attacker_uid),
                    )
                    if unlocked and channel is not None:
                        with contextlib.suppress(discord.HTTPException):
                            await channel.send(format_unlock_message(unlocked))
                if channel is not None:
                    try:
                        await channel.send(embed=build_siege_result_embed(item))
                    except discord.HTTPException:
                        logger.exception("territory siege result failed")

    @territory_siege_tick.before_loop
    async def before_territory_siege_tick(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Territories(bot))
