"""Shared Discord UI for all crew PvP raid types."""
from __future__ import annotations

import random
from enum import Enum
from typing import TYPE_CHECKING

import discord

import config
from utils.crew_bank_raid import (
    build_defender_order,
    format_business_raid_embed,
    format_drug_raid_embed,
    format_raid_embed,
    load_duel_fighter,
    simulate_crew_raid,
)
from utils.drugs import drug_by_id
from utils.helpers import fmt_amount

if TYPE_CHECKING:
    from cogs.crews import Crews


class RaidKind(str, Enum):
    BANK = "bank"
    DRUGS = "drugs"
    BUSINESS = "business"


RAID_META: dict[RaidKind, dict[str, str]] = {
    RaidKind.BANK: {
        "title": "crew bank",
        "emoji": "🏦",
        "not_in_crew": "Join a crew before raiding another crew's bank.",
        "target_low": "defender_treasury_low",
        "target_low_msg": (
            f"That crew's treasury is below **{fmt_amount(config.CREW_BANK_RAID_MIN_TREASURY)}**."
        ),
    },
    RaidKind.DRUGS: {
        "title": "cartel drug lab",
        "emoji": "🌿",
        "not_in_crew": "Join a crew before raiding another crew's cartel stash.",
        "target_low": "defender_stash_low",
        "target_low_msg": (
            f"That crew's cartel stash needs at least **{config.CREW_DRUG_RAID_MIN_STASH}** units."
        ),
    },
    RaidKind.BUSINESS: {
        "title": "business vaults",
        "emoji": "🏢",
        "not_in_crew": "Join a crew before raiding another crew's business income.",
        "target_low": "defender_stored_low",
        "target_low_msg": (
            f"That crew's uncollected business income is below "
            f"**{fmt_amount(config.CREW_BUSINESS_RAID_MIN_STORED)}**."
        ),
    },
}


COMMON_ERROR_MESSAGES = {
    "same_crew": "You cannot raid your own crew.",
    "invalid_defender": "That crew does not exist.",
    "duplicate_fighters": "Pick two different backup members — you cannot reuse the same fighter.",
    "fighter_not_in_crew": "Every raider must be a member of your crew.",
    "fighter_restricted": "One of your raiders is arrested or downed and cannot fight.",
    "attacker_cooldown": "Your crew raided recently. Wait for the attack cooldown to expire.",
    "defender_cooldown": "That crew was raided recently and is still on defense cooldown.",
}

KIND_ERROR_MESSAGES: dict[RaidKind, dict[str, str]] = {
    RaidKind.BANK: {
        "attacker_too_small": (
            f"Your crew needs at least **{config.CREW_BANK_RAID_MIN_MEMBERS}** members to launch a raid."
        ),
        "defender_too_small": (
            f"The target crew needs at least **{config.CREW_BANK_RAID_MIN_MEMBERS}** members to be raided."
        ),
    },
    RaidKind.DRUGS: {
        "attacker_too_small": (
            f"Your crew needs at least **{config.CREW_DRUG_BUSINESS_RAID_MIN_MEMBERS}** members "
            f"(you + two backups) to launch a drug raid."
        ),
    },
    RaidKind.BUSINESS: {
        "attacker_too_small": (
            f"Your crew needs at least **{config.CREW_DRUG_BUSINESS_RAID_MIN_MEMBERS}** members "
            f"(you + two backups) to launch a business raid."
        ),
    },
}


def _raid_requirements_text(kind: RaidKind) -> str:
    if kind is RaidKind.BANK:
        return (
            f"Both crews need **{config.CREW_BANK_RAID_MIN_MEMBERS}+** members.\n"
            f"Attack cooldown: **{config.CREW_BANK_RAID_ATTACK_COOLDOWN_SECONDS // 3600}h**"
        )
    cooldown_h = config.CREW_DRUG_RAID_COOLDOWN_SECONDS // 3600
    return (
        f"Your crew needs **{config.CREW_DRUG_BUSINESS_RAID_MIN_MEMBERS}+** members "
        f"(you + two backups). Targets can have **any crew size**.\n"
        f"Raid cooldown: **{cooldown_h}h** (attack and defense)"
    )


def error_message(kind: RaidKind, code: str) -> str:
    meta = RAID_META[kind]
    if code == meta["target_low"]:
        return meta["target_low_msg"]
    if code == "not_in_crew":
        return meta["not_in_crew"]
    kind_msg = KIND_ERROR_MESSAGES.get(kind, {}).get(code)
    if kind_msg:
        return kind_msg
    return COMMON_ERROR_MESSAGES.get(code, code)


class BackupSelect(discord.ui.Select):
    def __init__(
        self,
        *,
        view: CrewRaidView,
        slot: int,
        options: list[discord.SelectOption],
    ) -> None:
        self.slot = slot
        super().__init__(
            placeholder=f"Backup raider #{slot}",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"crew_raid_{view.kind.value}_backup_{slot}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.view.leader_id:
            await interaction.response.send_message("This raid setup isn't yours.", ephemeral=True)
            return
        self.view.backups[self.slot - 1] = int(self.values[0])
        self.view._sync_select_options()
        embed, _ = await build_raid_setup_embed(
            self.view.cog, self.view.guild, self.view.leader_id, self.view.target_crew, self.view.kind,
        )
        await interaction.response.edit_message(embed=embed, view=self.view)


class LaunchRaidButton(discord.ui.Button):
    def __init__(self, view: CrewRaidView) -> None:
        meta = RAID_META[view.kind]
        super().__init__(
            label="Launch raid",
            style=discord.ButtonStyle.danger,
            emoji=meta["emoji"],
        )
        self._view_ref = view

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self._view_ref
        if interaction.user.id != view.leader_id:
            await interaction.response.send_message("This raid setup isn't yours.", ephemeral=True)
            return
        if view.backups[0] is None or view.backups[1] is None:
            await interaction.response.send_message(
                "Pick **two backup raiders** before launching.", ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        await run_crew_raid(
            view.cog,
            interaction,
            kind=view.kind,
            attacker_crew=view.attacker_crew,
            defender_crew=view.target_crew,
            primary_id=view.leader_id,
            backup_ids=(view.backups[0], view.backups[1]),
        )


class CrewRaidView(discord.ui.View):
    def __init__(
        self,
        cog: Crews,
        guild: discord.Guild,
        leader_id: int,
        attacker_crew: str,
        target_crew: str,
        member_options: list[discord.SelectOption],
        kind: RaidKind,
    ) -> None:
        super().__init__(timeout=300.0)
        self.cog = cog
        self.guild = guild
        self.leader_id = leader_id
        self.attacker_crew = attacker_crew
        self.target_crew = target_crew
        self.kind = kind
        self.backups: list[int | None] = [None, None]
        self.add_item(BackupSelect(view=self, slot=1, options=member_options))
        self.add_item(BackupSelect(view=self, slot=2, options=member_options))
        self.add_item(LaunchRaidButton(self))

    def _sync_select_options(self) -> None:
        for item in self.children:
            if not isinstance(item, BackupSelect):
                continue
            current = self.backups[item.slot - 1]
            for option in item.options:
                uid = int(option.value)
                option.default = current == uid
                option.description = "Selected" if current == uid else None


async def build_raid_setup_embed(
    cog: Crews,
    guild: discord.Guild,
    leader_id: int,
    target_crew: str,
    kind: RaidKind,
) -> tuple[discord.Embed, str | None]:
    meta = RAID_META[kind]
    snap = await cog.bot.db.get_crew_banking_snapshot(leader_id, guild.id)
    if snap is None:
        return discord.Embed(title=f"Crew {meta['title']} raid"), "not_in_crew"
    attacker_crew = str(snap["crew_name"])
    defender = await cog.bot.db.resolve_crew_name(guild.id, target_crew)
    if defender is None:
        return discord.Embed(title=f"Crew {meta['title']} raid"), "invalid_defender"
    defender_count = await cog.bot.db.count_crew_members(guild.id, defender)
    attacker_count = await cog.bot.db.count_crew_members(guild.id, attacker_crew)

    target_label = ""
    potential_loot = ""
    if kind is RaidKind.BANK:
        defender_stats = await cog.bot.db.get_crew_stats(guild.id, defender)
        treasury = float(defender_stats["treasury"]) if defender_stats is not None else 0.0
        target_label = fmt_amount(treasury)
        potential_loot = fmt_amount(treasury * config.CREW_BANK_RAID_LOOT_FRACTION)
        target_field = "Target treasury"
        loot_field = "Potential loot"
    elif kind is RaidKind.DRUGS:
        stash = await cog.bot.db.get_cartel_stash(guild.id, defender)
        stash_total = sum(stash.values())
        target_label = f"{stash_total} units"
        potential_loot = f"{config.CREW_DRUG_RAID_LOOT_MIN}–{config.CREW_DRUG_RAID_LOOT_MAX} units (random)"
        target_field = "Cartel stash"
        loot_field = "Potential loot"
    else:
        stored = await cog.bot.db.get_crew_business_stored_total(guild.id, defender)
        target_label = fmt_amount(stored)
        potential_loot = fmt_amount(stored * config.CREW_BUSINESS_RAID_LOOT_FRACTION)
        target_field = "Uncollected income"
        loot_field = "Potential loot"

    embed = discord.Embed(
        title=f"Raid {defender}'s {meta['title']}",
        description=(
            f"**{attacker_crew}** ({attacker_count} members) vs **{defender}** ({defender_count} members)\n\n"
            f"You lead the assault. Pick **two backups** who will take over if you fall.\n"
            f"Raids are automated gear duels through the defender roster — first target is random, "
            f"then the rest of their roster in join order."
        ),
        color=discord.Color.dark_red(),
    )
    embed.add_field(name=target_field, value=target_label, inline=True)
    embed.add_field(name=loot_field, value=potential_loot, inline=True)
    embed.add_field(
        name="Requirements",
        value=_raid_requirements_text(kind),
        inline=False,
    )
    embed.set_footer(text="Launch when both backups are selected.")
    return embed, None


async def send_crew_raid_panel(
    cog: Crews,
    interaction: discord.Interaction,
    target_crew: str,
    kind: RaidKind,
) -> None:
    if interaction.guild is None:
        if not interaction.response.is_done():
            await interaction.response.send_message("Guild only.", ephemeral=True)
        return
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    uid = interaction.user.id
    snap = await cog.bot.db.get_crew_banking_snapshot(uid, guild.id)
    if snap is None:
        await interaction.followup.send(error_message(kind, "not_in_crew"), ephemeral=True)
        return
    attacker_crew = str(snap["crew_name"])
    defender = await cog.bot.db.resolve_crew_name(guild.id, target_crew)
    if defender is None:
        await interaction.followup.send(error_message(kind, "invalid_defender"), ephemeral=True)
        return
    if defender.lower() == attacker_crew.lower():
        await interaction.followup.send(error_message(kind, "same_crew"), ephemeral=True)
        return

    members = await cog.bot.db.list_crew_members(guild.id, attacker_crew)
    options: list[discord.SelectOption] = []
    for row in members:
        member_id = int(row["user_id"])
        if member_id == uid:
            continue
        member = guild.get_member(member_id)
        label = member.display_name if member is not None else f"User {member_id}"
        if len(label) > 100:
            label = label[:97] + "..."
        options.append(discord.SelectOption(label=label, value=str(member_id)))
    if len(options) < config.CREW_BANK_RAID_BACKUP_COUNT:
        await interaction.followup.send(
            f"You need at least **{config.CREW_BANK_RAID_BACKUP_COUNT}** other crew members "
            f"to pick as backups.",
            ephemeral=True,
        )
        return

    embed, err = await build_raid_setup_embed(cog, guild, uid, defender, kind)
    if err:
        await interaction.followup.send(error_message(kind, err), ephemeral=True)
        return
    view = CrewRaidView(cog, guild, uid, attacker_crew, defender, options[:25], kind)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


def pick_drug_loot(stash: dict[str, int], rng: random.Random) -> tuple[str, int]:
    """Roll 2–5 units from a random in-stock drug type."""
    eligible = [(drug_id, qty) for drug_id, qty in stash.items() if qty > 0]
    if not eligible:
        return "", 0
    drug_id, available = rng.choice(eligible)
    target = rng.randint(config.CREW_DRUG_RAID_LOOT_MIN, config.CREW_DRUG_RAID_LOOT_MAX)
    return drug_id, min(target, available)


async def run_crew_raid(
    cog: Crews,
    interaction: discord.Interaction,
    *,
    kind: RaidKind,
    attacker_crew: str,
    defender_crew: str,
    primary_id: int,
    backup_ids: tuple[int, int],
) -> None:
    if interaction.guild is None:
        return
    guild = interaction.guild
    guild_id = guild.id
    err = await cog.bot.db.validate_crew_raid(
        guild_id,
        primary_id,
        attacker_crew,
        defender_crew,
        backup_ids,
        raid_type=kind.value,
    )
    if err:
        await interaction.followup.send(error_message(kind, err), ephemeral=True)
        return

    defender = await cog.bot.db.resolve_crew_name(guild_id, defender_crew)
    if defender is None:
        await interaction.followup.send(error_message(kind, "invalid_defender"), ephemeral=True)
        return

    defender_ids = await cog.bot.db.list_crew_member_user_ids(guild_id, defender)
    fight_order = build_defender_order(defender_ids, rng=random.Random())
    attacker_ids = [primary_id, backup_ids[0], backup_ids[1]]

    try:
        attackers = [await load_duel_fighter(cog.bot.db, guild, uid) for uid in attacker_ids]
        defenders = [await load_duel_fighter(cog.bot.db, guild, uid) for uid in fight_order]
    except Exception:
        await interaction.followup.send(
            "Could not load fighter gear for this raid. Try again in a moment.",
            ephemeral=True,
        )
        return

    result = simulate_crew_raid(attackers, defenders)
    fighters = {f.user_id: f for f in (*attackers, *defenders)}
    meta = RAID_META[kind]

    if kind is RaidKind.BANK:
        settlement = await cog.bot.db.settle_crew_bank_raid(
            guild_id, attacker_crew, defender, attacker_won=result.attacker_won,
        )
        if settlement.get("error"):
            await interaction.followup.send(error_message(kind, str(settlement["error"])), ephemeral=True)
            return
        embed = format_raid_embed(
            attacker_crew=attacker_crew,
            defender_crew=defender,
            result=result,
            fighters=fighters,
            loot=float(settlement["loot"]),
            defender_treasury_after=float(settlement["treasury_after"]),
        )
        public_loot = (
            f" for **{fmt_amount(float(settlement['loot']))}**" if result.attacker_won else ""
        )
    elif kind is RaidKind.DRUGS:
        stash = await cog.bot.db.get_cartel_stash(guild_id, defender)
        rng = random.Random()
        drug_id, loot_qty = pick_drug_loot(stash, rng) if result.attacker_won else ("", 0)
        settlement = await cog.bot.db.settle_crew_drug_raid(
            guild_id,
            attacker_crew,
            defender,
            attacker_won=result.attacker_won,
            loot_qty=loot_qty,
            drug_id=drug_id,
        )
        if settlement.get("error"):
            await interaction.followup.send(error_message(kind, str(settlement["error"])), ephemeral=True)
            return
        defn = drug_by_id(str(settlement["drug_id"])) if settlement.get("drug_id") else None
        embed = format_drug_raid_embed(
            attacker_crew=attacker_crew,
            defender_crew=defender,
            result=result,
            fighters=fighters,
            drug_id=str(settlement.get("drug_id", "")),
            drug_name=defn.name if defn else "Unknown",
            drug_emoji=defn.emoji if defn else "💊",
            loot_qty=int(settlement.get("loot_qty", 0)),
            defender_stash_after=int(settlement.get("stash_after", 0)),
        )
        if result.attacker_won and defn is not None:
            public_loot = f" for **{settlement['loot_qty']}× {defn.emoji} {defn.name}**"
        else:
            public_loot = ""
    else:
        settlement = await cog.bot.db.settle_crew_business_raid(
            guild_id, attacker_crew, defender, attacker_won=result.attacker_won,
        )
        if settlement.get("error"):
            await interaction.followup.send(error_message(kind, str(settlement["error"])), ephemeral=True)
            return
        embed = format_business_raid_embed(
            attacker_crew=attacker_crew,
            defender_crew=defender,
            result=result,
            fighters=fighters,
            loot=float(settlement["loot"]),
            defender_stored_after=float(settlement["stored_after"]),
        )
        public_loot = (
            f" for **{fmt_amount(float(settlement['loot']))}**" if result.attacker_won else ""
        )

    await interaction.followup.send(embed=embed, ephemeral=True)

    channel = interaction.channel
    if isinstance(channel, discord.abc.Messageable):
        public = discord.Embed(
            title=f"{meta['emoji']} Crew {meta['title']} raid",
            description=(
                f"**{attacker_crew}** {'robbed' if result.attacker_won else 'failed to rob'} "
                f"**{defender}**{public_loot}."
            ),
            color=discord.Color.green() if result.attacker_won else discord.Color.red(),
        )
        try:
            await channel.send(embed=public)
        except discord.HTTPException:
            pass


async def format_crew_raid_cooldowns(cog: Crews, guild_id: int, crew_name: str) -> str:
    import time

    row = await cog.bot.db._crew_bank_raid_cooldown_row(guild_id, crew_name)
    if row is None:
        return "All raid types **ready**."
    now = time.time()
    lines: list[str] = []
    specs = (
        ("🏦 Bank attack", "last_attack_at", config.CREW_BANK_RAID_ATTACK_COOLDOWN_SECONDS),
        ("🌿 Drug raid", "last_drug_attack_at", config.CREW_DRUG_RAID_COOLDOWN_SECONDS),
        ("🏢 Business raid", "last_business_attack_at", config.CREW_BUSINESS_RAID_COOLDOWN_SECONDS),
    )
    for label, col, cd_seconds in specs:
        elapsed = now - float(row[col])
        if elapsed < cd_seconds:
            left = int(cd_seconds - elapsed)
            lines.append(f"{label}: **{left // 60}m {left % 60}s**")
        else:
            lines.append(f"{label}: **ready**")
    return "\n".join(lines)


class RaidTargetSelect(discord.ui.Select):
    def __init__(
        self,
        cog: Crews,
        kind: RaidKind,
        targets: list[tuple[str, str]],
    ) -> None:
        options = [
            discord.SelectOption(label=label[:100], value=name[:100])
            for name, label in targets[:25]
        ]
        super().__init__(
            placeholder=f"Pick a crew to raid ({RAID_META[kind]['title']})…",
            options=options,
            min_values=1,
            max_values=1,
        )
        self.cog = cog
        self.kind = kind

    async def callback(self, interaction: discord.Interaction) -> None:
        await send_crew_raid_panel(self.cog, interaction, self.values[0], self.kind)


class RaidTargetPickerView(discord.ui.View):
    def __init__(self, cog: Crews, kind: RaidKind, targets: list[tuple[str, str]]) -> None:
        super().__init__(timeout=120.0)
        self.add_item(RaidTargetSelect(cog, kind, targets))


async def open_raid_target_picker(
    cog: Crews,
    interaction: discord.Interaction,
    kind: RaidKind,
) -> None:
    if interaction.guild_id is None or interaction.guild is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return
    crew = await cog.bot.db.get_crew_membership(interaction.user.id, interaction.guild_id)
    if crew is None:
        await interaction.response.send_message(error_message(kind, "not_in_crew"), ephemeral=True)
        return
    if kind is RaidKind.BANK:
        raw = await cog.bot.db.list_raidable_crews(interaction.guild_id, crew)
        targets = [
            (name, f"{name} ({count} members · {fmt_amount(treasury)})")
            for name, count, treasury in raw
        ]
    elif kind is RaidKind.DRUGS:
        raw = await cog.bot.db.list_raidable_drug_crews(interaction.guild_id, crew)
        targets = [
            (name, f"{name} ({count} members · {stash} units)")
            for name, count, stash in raw
        ]
    else:
        raw = await cog.bot.db.list_raidable_business_crews(interaction.guild_id, crew)
        targets = [
            (name, f"{name} ({count} members · {fmt_amount(stored)} uncollected)")
            for name, count, stored in raw
        ]
    if not targets:
        msg = RAID_META[kind]["target_low_msg"]
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        return
    meta = RAID_META[kind]
    embed = discord.Embed(
        title=f"{meta['emoji']} Crew {meta['title']} raid",
        description=_raid_requirements_text(kind),
        color=discord.Color.dark_red(),
    )
    cooldowns = await format_crew_raid_cooldowns(cog, interaction.guild_id, crew)
    embed.add_field(name="Your crew cooldowns", value=cooldowns, inline=False)
    view = RaidTargetPickerView(cog, kind, targets)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
