"""Shared "hub" panels for contracts, expeditions, season, and chaos modules.

Each hub mirrors the equivalent slash command's logic (see ``cogs/contracts.py``,
``cogs/expeditions.py``, ``cogs/season.py``) but presents it as an interactive,
button/select-driven panel so players do not need to memorize sub-actions.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord

import config
from utils.aspects import random_aspect_definition, roll_pct_shop
from utils.contracts import CONTRACT_MAP, format_contract_reward
from utils.expansion_events import ensure_guild_contracts, record_expansion_event
from utils.expeditions import (
    EXPEDITION_TEMPLATES,
    format_expedition_status,
    scale_expedition_goal,
)
from utils.goon_theme import branded_embed, danger_color, panel_title
from utils.helpers import fmt_amount, guild_only_message

if TYPE_CHECKING:
    from discord.ext import commands

logger = logging.getLogger(__name__)


async def _hub_error(interaction: discord.Interaction, message: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


# ---------------------------------------------------------------------------
# Contracts hub
# ---------------------------------------------------------------------------


async def build_contracts_hub_embed(
    cog: commands.Cog,
    guild_id: int,
    user_id: int,
) -> tuple[discord.Embed, list[str]]:
    await ensure_guild_contracts(cog.bot.db, guild_id)
    active = await cog.bot.db.list_guild_contract_ids(guild_id)
    progress_rows = {
        str(r["contract_id"]): r
        for r in await cog.bot.db.get_contract_progress_rows(user_id, guild_id)
    }
    lines: list[str] = []
    for cid in active:
        defn = CONTRACT_MAP.get(cid)
        if defn is None:
            continue
        row = progress_rows.get(cid)
        prog = int(row["progress"]) if row else 0
        claimed = bool(int(row["claimed"])) if row else False
        status = "✅ Claimed" if claimed else f"{prog}/{defn.target}"
        lines.append(
            f"**{defn.name}** (`{cid}`) — {status}\n"
            f"_{defn.description}_\nReward: {format_contract_reward(defn)}"
        )
    refresh = await cog.bot.db.get_contract_refresh_at(guild_id)
    embed = branded_embed(
        panel_title("Contracts Hub"),
        description=(
            f"Refreshes <t:{int(refresh)}:R>\n\n"
            + ("\n\n".join(lines) if lines else "No contracts.")
        ),
    )
    return embed, list(active)


class ContractClaimSelect(discord.ui.Select):
    def __init__(self, active_ids: list[str]) -> None:
        claimable = [cid for cid in active_ids if cid in CONTRACT_MAP][:25]
        options = [
            discord.SelectOption(
                label=CONTRACT_MAP[cid].name[:100],
                value=cid,
                description=f"`{cid}`"[:100],
            )
            for cid in claimable
        ] or [discord.SelectOption(label="No contracts available", value="_none")]
        super().__init__(
            placeholder="Claim a contract…",
            min_values=1,
            max_values=1,
            options=options,
            disabled=not claimable,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: ContractsHubView = self.view  # type: ignore[assignment]
        contract_id = self.values[0]
        uid = interaction.user.id
        guild_id = view.guild_id
        reward = await view.cog.bot.db.claim_contract(uid, guild_id, contract_id)
        if reward is None:
            await interaction.response.send_message(
                "Contract incomplete, already claimed, or invalid.", ephemeral=True,
            )
            return
        if reward["nuggets"] > 0:
            await view.cog.bot.db.credit_wallet(uid, guild_id, float(reward["nuggets"]))
        if reward["tokens"] > 0:
            season, _ = await view.cog.bot.db.get_elo_season(guild_id)
            await view.cog.bot.db.add_season_tokens(uid, guild_id, int(reward["tokens"]), season)
        if reward["item_id"]:
            for _ in range(int(reward["qty"])):
                await view.cog.bot.db.grant_item(uid, guild_id, str(reward["item_id"]))
        parts = [fmt_amount(float(reward["nuggets"]))] if reward["nuggets"] else []
        if reward["tokens"]:
            parts.append(f"{reward['tokens']} season tokens")
        if reward["item_id"]:
            parts.append(f"`{reward['item_id']}` ×{reward['qty']}")
        embed, active = await build_contracts_hub_embed(view.cog, guild_id, uid)
        embed.description = (
            f"✅ Contract claimed! Rewards: {' + '.join(parts)}\n\n{embed.description}"
        )
        new_view = ContractsHubView(view.cog, guild_id, uid, active)
        await interaction.response.edit_message(embed=embed, view=new_view)


class ContractsHubView(discord.ui.View):
    def __init__(
        self,
        cog: commands.Cog,
        guild_id: int,
        user_id: int,
        active_ids: list[str],
    ) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.add_item(ContractClaimSelect(active_ids))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This contract board is not yours.", ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=1)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        embed, active = await build_contracts_hub_embed(self.cog, self.guild_id, self.user_id)
        view = ContractsHubView(self.cog, self.guild_id, self.user_id, active)
        await interaction.response.edit_message(embed=embed, view=view)


async def send_contracts_hub(interaction: discord.Interaction, cog: commands.Cog) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message(guild_only_message(), ephemeral=True)
        return
    guild_id = interaction.guild_id
    user_id = interaction.user.id
    try:
        embed, active = await build_contracts_hub_embed(cog, guild_id, user_id)
        view = ContractsHubView(cog, guild_id, user_id, active)
    except Exception:
        logger.exception("Failed to open contracts hub for user %s", user_id)
        await _hub_error(interaction, "Could not open the contracts board.")
        return
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ---------------------------------------------------------------------------
# Expeditions hub
# ---------------------------------------------------------------------------


async def build_expeditions_hub_embed(
    cog: commands.Cog,
    guild_id: int,
) -> tuple[discord.Embed, Any | None]:
    active = await cog.bot.db.get_active_expedition(guild_id)
    if active is None:
        embed = branded_embed(
            panel_title("Expeditions Hub"),
            description="No active expedition. One spawns automatically when the server is active.",
        )
        return embed, None
    template = next(
        (t for t in EXPEDITION_TEMPLATES if t.expedition_id == str(active["expedition_id"])),
        EXPEDITION_TEMPLATES[0],
    )
    text = format_expedition_status(
        template,
        int(active["contributed_scrap"]),
        float(active["contributed_nuggets"]),
        float(active["ends_at"]),
    )
    goal = scale_expedition_goal(
        template.goal_scrap, len(await cog.bot.db.list_guild_user_ids(guild_id)),
    )
    embed = branded_embed(
        panel_title("Expeditions Hub"),
        description=f"{text}\nScaled scrap goal: **{goal}**",
    )
    return embed, template


class ExpeditionContributeModal(discord.ui.Modal, title="Contribute to expedition"):
    scrap_input = discord.ui.TextInput(
        label="Scrap",
        placeholder="0",
        required=False,
        max_length=10,
    )
    currency_input = discord.ui.TextInput(
        label=config.CURRENCY_NAME.title(),
        placeholder="0",
        required=False,
        max_length=16,
    )

    def __init__(self, cog: commands.Cog, guild_id: int, template_name: str) -> None:
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.template_name = template_name

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            scrap = max(0, int(float(str(self.scrap_input.value or "0").replace(",", "").strip() or 0)))
        except ValueError:
            scrap = 0
        try:
            currency = max(
                0.0, float(str(self.currency_input.value or "0").replace(",", "").strip() or 0),
            )
        except ValueError:
            currency = 0.0
        if scrap <= 0 and currency <= 0:
            await interaction.response.send_message(
                f"Provide scrap and/or {config.CURRENCY_NAME} to contribute.", ephemeral=True,
            )
            return
        uid = interaction.user.id
        result = await self.cog.bot.db.contribute_expedition(
            self.guild_id, uid, scrap=scrap, nuggets=currency,
        )
        if result is None:
            await interaction.response.send_message(
                f"Contribution failed — check scrap/{config.CURRENCY_NAME} balance.", ephemeral=True,
            )
            return
        await record_expansion_event(self.cog.bot.db, self.guild_id, uid, "expedition_contribute")
        embed, _ = await build_expeditions_hub_embed(self.cog, self.guild_id)
        embed.description = (
            f"✅ Contributed **{scrap}** scrap and **{fmt_amount(currency)}** to "
            f"**{self.template_name}**!\n\n{embed.description}"
        )
        view = ExpeditionsHubView(self.cog, self.guild_id, uid)
        await interaction.response.edit_message(embed=embed, view=view)


class ExpeditionsHubView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This expedition board is not yours.", ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Contribute", style=discord.ButtonStyle.success, row=0)
    async def contribute_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        active = await self.cog.bot.db.get_active_expedition(self.guild_id)
        if active is None:
            await interaction.response.send_message("No active expedition to contribute to.", ephemeral=True)
            return
        template = next(
            (t for t in EXPEDITION_TEMPLATES if t.expedition_id == str(active["expedition_id"])),
            EXPEDITION_TEMPLATES[0],
        )
        await interaction.response.send_modal(
            ExpeditionContributeModal(self.cog, self.guild_id, template.name),
        )

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=0)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        embed, _ = await build_expeditions_hub_embed(self.cog, self.guild_id)
        view = ExpeditionsHubView(self.cog, self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)


async def send_expeditions_hub(interaction: discord.Interaction, cog: commands.Cog) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message(guild_only_message(), ephemeral=True)
        return
    guild_id = interaction.guild_id
    try:
        embed, _ = await build_expeditions_hub_embed(cog, guild_id)
        view = ExpeditionsHubView(cog, guild_id, interaction.user.id)
    except Exception:
        logger.exception("Failed to open expeditions hub in guild %s", guild_id)
        await _hub_error(interaction, "Could not open the expedition board.")
        return
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ---------------------------------------------------------------------------
# Season hub
# ---------------------------------------------------------------------------


async def _season_shop_embed(cog: commands.Cog, guild_id: int, user_id: int, season_num: int, tokens: int) -> discord.Embed:
    lines: list[str] = []
    for rid, (cost, kind) in config.SEASON_TOKEN_SHOP.items():
        redeemed = await cog.bot.db.has_season_redemption(user_id, guild_id, season_num, rid)
        mark = "✅" if redeemed else f"{cost} tokens"
        lines.append(f"**{rid}** ({kind}) — {mark}")
    return branded_embed(
        panel_title(f"Season {season_num} Shop"),
        description=f"You have **{tokens}** tokens\n\n" + "\n".join(lines),
    )


async def _season_status_embed(cog: commands.Cog, guild_id: int, user_id: int, season_num: int, tokens: int) -> discord.Embed:
    _, last_reset = await cog.bot.db.get_elo_season(guild_id)
    rating, wins, losses = await cog.bot.db.get_duel_elo(user_id, guild_id)
    reset_text = "Never" if last_reset <= 0 else f"<t:{int(last_reset)}:R>"
    return branded_embed(
        panel_title(f"Season {season_num}"),
        description=(
            f"Last reset: {reset_text}\n"
            f"Your ELO: **{rating}** ({wins}W / {losses}L)\n"
            f"Season tokens: **{tokens}**"
        ),
    )


class SeasonModeSelect(discord.ui.Select):
    def __init__(self, mode: str) -> None:
        options = [
            discord.SelectOption(label="Status", value="status", default=mode == "status"),
            discord.SelectOption(label="Shop", value="shop", default=mode == "shop"),
        ]
        super().__init__(placeholder="View…", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: SeasonHubView = self.view  # type: ignore[assignment]
        view.mode = self.values[0]
        embed = await view.build_embed()
        new_view = SeasonHubView(
            view.cog, view.guild_id, view.user_id, view.season_num, view.tokens, mode=view.mode,
        )
        await interaction.response.edit_message(embed=embed, view=new_view)


class SeasonRedeemSelect(discord.ui.Select):
    def __init__(self) -> None:
        options = [
            discord.SelectOption(
                label=f"{rid} ({cost} tokens)"[:100],
                value=rid,
                description=kind[:100],
            )
            for rid, (cost, kind) in config.SEASON_TOKEN_SHOP.items()
        ]
        super().__init__(
            placeholder="Redeem a reward…", min_values=1, max_values=1, options=options, row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: SeasonHubView = self.view  # type: ignore[assignment]
        reward = self.values[0]
        cost, kind = config.SEASON_TOKEN_SHOP[reward]
        uid, guild_id, season_num = view.user_id, view.guild_id, view.season_num
        ok = await view.cog.bot.db.redeem_season_reward(uid, guild_id, season_num, reward, cost)
        if not ok:
            await interaction.response.send_message(
                "Not enough tokens or already redeemed.", ephemeral=True,
            )
            return
        if kind == "aspect":
            defn = random_aspect_definition()
            roll = roll_pct_shop()
            await view.cog.bot.db.create_aspect_instance(uid, guild_id, defn.id, roll)
            msg = f"Redeemed **{defn.name}** aspect ({roll:.1f}%)!"
        elif kind == "relic":
            await view.cog.bot.db.create_relic_instance(uid, guild_id, "relic_plunder_seal")
            msg = "Redeemed **Plunderer's Seal** relic!"
        elif kind == "avatar":
            await view.cog.bot.db.unlock_avatar(uid, guild_id, "season_gold")
            msg = "Unlocked **Season Gold** avatar!"
        else:
            msg = "Redeemed **Raider** title! Show it off in duels."
        view.tokens = await view.cog.bot.db.get_season_tokens(uid, guild_id, season_num)
        embed = await view.build_embed()
        embed.description = f"✅ {msg}\n\n{embed.description}"
        new_view = SeasonHubView(view.cog, guild_id, uid, season_num, view.tokens, mode=view.mode)
        await interaction.response.edit_message(embed=embed, view=new_view)


class SeasonHubView(discord.ui.View):
    def __init__(
        self,
        cog: commands.Cog,
        guild_id: int,
        user_id: int,
        season_num: int,
        tokens: int,
        *,
        mode: str = "status",
    ) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.season_num = season_num
        self.tokens = tokens
        self.mode = mode
        self.add_item(SeasonModeSelect(mode))
        self.add_item(SeasonRedeemSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This panel is not yours.", ephemeral=True)
            return False
        return True

    async def build_embed(self) -> discord.Embed:
        if self.mode == "shop":
            return await _season_shop_embed(self.cog, self.guild_id, self.user_id, self.season_num, self.tokens)
        return await _season_status_embed(self.cog, self.guild_id, self.user_id, self.season_num, self.tokens)


async def send_season_hub(
    interaction: discord.Interaction,
    cog: commands.Cog,
    *,
    mode: str = "status",
) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message(guild_only_message(), ephemeral=True)
        return
    guild_id = interaction.guild_id
    uid = interaction.user.id
    try:
        season_num, _ = await cog.bot.db.get_elo_season(guild_id)
        tokens = await cog.bot.db.get_season_tokens(uid, guild_id, season_num)
        view = SeasonHubView(cog, guild_id, uid, season_num, tokens, mode=mode)
        embed = await view.build_embed()
    except Exception:
        logger.exception("Failed to open season hub for user %s", uid)
        await _hub_error(interaction, "Could not open the season panel.")
        return
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ---------------------------------------------------------------------------
# Chaos hub — quick actions for virus / scourge / trivia
# ---------------------------------------------------------------------------


class ChaosHubView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This panel is not yours.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="🦠 Start trivia", style=discord.ButtonStyle.success, row=0)
    async def trivia_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        trivia_cog = self.cog.bot.get_cog("Trivia")
        start_via_hub = getattr(trivia_cog, "start_via_hub", None)
        if trivia_cog is None or start_via_hub is None:
            await interaction.response.send_message(
                "Trivia module is unavailable right now — try `/trivia`.", ephemeral=True,
            )
            return
        await start_via_hub(interaction)

    @discord.ui.button(label="☣️ Scourge Virus", style=discord.ButtonStyle.secondary, row=0)
    async def scourge_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await interaction.response.send_message(
            f"**{config.SCOURGE_VIRUS_NAME}** infects the server's top wallets on a world timer "
            f"(warning GIF, then a **{config.SCOURGE_ACTIVE_SECONDS // 60}-minute** outbreak). "
            "Infected? Use `/scourge-pass @user` before the timer pops to dodge the bank penalty.",
            ephemeral=True,
        )

    @discord.ui.button(label="💀 Hack virus", style=discord.ButtonStyle.secondary, row=0)
    async def hack_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await interaction.response.send_message(
            f"**{config.HACK_VIRUS_NAME.title()}** — start the hot-potato with `/hack @user`, "
            "then the holder runs `/transfer @user` to pass it along before it detonates and "
            "docks their wallet.",
            ephemeral=True,
        )


async def send_chaos_hub(interaction: discord.Interaction, cog: commands.Cog) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message(guild_only_message(), ephemeral=True)
        return
    try:
        embed = branded_embed(
            panel_title("Chaos Hub"),
            description=(
                "Quick actions for the server's chaos modules.\n\n"
                "🦠 **Trivia** — start a Lore Roulette round right now\n"
                "☣️ **Scourge Virus** — how to pass an active infection\n"
                "💀 **Hack virus** — how the hot-potato virus works"
            ),
            color=danger_color(),
        )
        view = ChaosHubView(cog, interaction.guild_id, interaction.user.id)
    except Exception:
        logger.exception("Failed to open chaos hub for user %s", interaction.user.id)
        await _hub_error(interaction, "Could not open the chaos hub.")
        return
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
