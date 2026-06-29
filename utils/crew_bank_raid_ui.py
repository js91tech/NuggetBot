"""Discord UI for crew bank treasury raids."""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

import discord

import config
from utils.crew_bank_raid import (
    build_defender_order,
    format_raid_embed,
    load_duel_fighter,
    simulate_crew_bank_raid,
)
from utils.helpers import fmt_amount

if TYPE_CHECKING:
    from cogs.crews import Crews


RAID_ERROR_MESSAGES = {
    "not_in_crew": "Join a crew before raiding another crew's bank.",
    "same_crew": "You cannot raid your own crew.",
    "invalid_defender": "That crew does not exist.",
    "attacker_too_small": (
        f"Your crew needs at least **{config.CREW_BANK_RAID_MIN_MEMBERS}** members to launch a raid."
    ),
    "defender_too_small": (
        f"The target crew needs at least **{config.CREW_BANK_RAID_MIN_MEMBERS}** members to be raided."
    ),
    "defender_treasury_low": (
        f"That crew's treasury is below **{fmt_amount(config.CREW_BANK_RAID_MIN_TREASURY)}**."
    ),
    "duplicate_fighters": "Pick two different backup members — you cannot reuse the same fighter.",
    "fighter_not_in_crew": "Every raider must be a member of your crew.",
    "fighter_restricted": "One of your raiders is arrested or downed and cannot fight.",
    "attacker_cooldown": "Your crew raided recently. Wait for the attack cooldown to expire.",
    "defender_cooldown": "That crew was raided recently and is still on defense cooldown.",
}


class BackupSelect(discord.ui.Select):
    def __init__(
        self,
        *,
        view: CrewBankRaidView,
        slot: int,
        options: list[discord.SelectOption],
    ) -> None:
        self.slot = slot
        super().__init__(
            placeholder=f"Backup raider #{slot}",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"crew_raid_backup_{slot}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.view.leader_id:
            await interaction.response.send_message("This raid setup isn't yours.", ephemeral=True)
            return
        self.view.backups[self.slot - 1] = int(self.values[0])
        self.view._sync_select_options()
        embed, _ = await build_raid_setup_embed(self.view.cog, self.view.guild, self.view.leader_id, self.view.target_crew)
        await interaction.response.edit_message(embed=embed, view=self.view)


class LaunchRaidButton(discord.ui.Button):
    def __init__(self, view: CrewBankRaidView) -> None:
        super().__init__(label="Launch raid", style=discord.ButtonStyle.danger, emoji="🏦")
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
        await run_crew_bank_raid(
            view.cog,
            interaction,
            attacker_crew=view.attacker_crew,
            defender_crew=view.target_crew,
            primary_id=view.leader_id,
            backup_ids=(view.backups[0], view.backups[1]),
        )


class CrewBankRaidView(discord.ui.View):
    def __init__(
        self,
        cog: Crews,
        guild: discord.Guild,
        leader_id: int,
        attacker_crew: str,
        target_crew: str,
        member_options: list[discord.SelectOption],
    ) -> None:
        super().__init__(timeout=300.0)
        self.cog = cog
        self.guild = guild
        self.leader_id = leader_id
        self.attacker_crew = attacker_crew
        self.target_crew = target_crew
        self.backups: list[int | None] = [None, None]
        self.add_item(BackupSelect(view=self, slot=1, options=member_options))
        self.add_item(BackupSelect(view=self, slot=2, options=member_options))
        self.add_item(LaunchRaidButton(self))

    def _sync_select_options(self) -> None:
        taken = {self.leader_id, self.backups[0], self.backups[1]}
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
) -> tuple[discord.Embed, str | None]:
    snap = await cog.bot.db.get_crew_banking_snapshot(leader_id, guild.id)
    if snap is None:
        return discord.Embed(title="Crew bank raid"), "not_in_crew"
    attacker_crew = str(snap["crew_name"])
    defender = await cog.bot.db.resolve_crew_name(guild.id, target_crew)
    if defender is None:
        return discord.Embed(title="Crew bank raid"), "invalid_defender"
    defender_stats = await cog.bot.db.get_crew_stats(guild.id, defender)
    treasury = float(defender_stats["treasury"]) if defender_stats is not None else 0.0
    defender_count = await cog.bot.db.count_crew_members(guild.id, defender)
    attacker_count = await cog.bot.db.count_crew_members(guild.id, attacker_crew)
    potential_loot = treasury * config.CREW_BANK_RAID_LOOT_FRACTION
    embed = discord.Embed(
        title=f"Raid {defender}'s crew bank",
        description=(
            f"**{attacker_crew}** ({attacker_count} members) vs **{defender}** ({defender_count} members)\n\n"
            f"You lead the assault. Pick **two backups** who will take over if you fall.\n"
            f"Raids are automated gear duels through the defender roster — first target is random, "
            f"then the rest of their roster in join order."
        ),
        color=discord.Color.dark_red(),
    )
    embed.add_field(name="Target treasury", value=fmt_amount(treasury), inline=True)
    embed.add_field(
        name="Potential loot",
        value=fmt_amount(potential_loot),
        inline=True,
    )
    embed.add_field(
        name="Requirements",
        value=(
            f"Both crews need **{config.CREW_BANK_RAID_MIN_MEMBERS}+** members.\n"
            f"Attack cooldown: **{config.CREW_BANK_RAID_ATTACK_COOLDOWN_SECONDS // 3600}h**"
        ),
        inline=False,
    )
    embed.set_footer(text="Launch when both backups are selected.")
    return embed, None


async def send_crew_bank_raid_panel(
    cog: Crews,
    interaction: discord.Interaction,
    target_crew: str,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return
    guild = interaction.guild
    uid = interaction.user.id
    snap = await cog.bot.db.get_crew_banking_snapshot(uid, guild.id)
    if snap is None:
        await interaction.response.send_message(RAID_ERROR_MESSAGES["not_in_crew"], ephemeral=True)
        return
    attacker_crew = str(snap["crew_name"])
    defender = await cog.bot.db.resolve_crew_name(guild.id, target_crew)
    if defender is None:
        await interaction.response.send_message(RAID_ERROR_MESSAGES["invalid_defender"], ephemeral=True)
        return
    if defender.lower() == attacker_crew.lower():
        await interaction.response.send_message(RAID_ERROR_MESSAGES["same_crew"], ephemeral=True)
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
        await interaction.response.send_message(
            f"You need at least **{config.CREW_BANK_RAID_BACKUP_COUNT}** other crew members "
            f"to pick as backups.",
            ephemeral=True,
        )
        return

    embed, err = await build_raid_setup_embed(cog, guild, uid, defender)
    if err:
        await interaction.response.send_message(RAID_ERROR_MESSAGES.get(err, err), ephemeral=True)
        return
    view = CrewBankRaidView(
        cog,
        guild,
        uid,
        attacker_crew,
        defender,
        options[:25],
    )
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def run_crew_bank_raid(
    cog: Crews,
    interaction: discord.Interaction,
    *,
    attacker_crew: str,
    defender_crew: str,
    primary_id: int,
    backup_ids: tuple[int, int],
) -> None:
    if interaction.guild is None:
        return
    guild = interaction.guild
    guild_id = guild.id
    err = await cog.bot.db.validate_crew_bank_raid(
        guild_id,
        primary_id,
        attacker_crew,
        defender_crew,
        backup_ids,
    )
    if err:
        await interaction.followup.send(RAID_ERROR_MESSAGES.get(err, err), ephemeral=True)
        return

    defender = await cog.bot.db.resolve_crew_name(guild_id, defender_crew)
    if defender is None:
        await interaction.followup.send(RAID_ERROR_MESSAGES["invalid_defender"], ephemeral=True)
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

    result = simulate_crew_bank_raid(attackers, defenders)
    settlement = await cog.bot.db.settle_crew_bank_raid(
        guild_id,
        attacker_crew,
        defender,
        attacker_won=result.attacker_won,
    )
    if settlement.get("error"):
        await interaction.followup.send(
            RAID_ERROR_MESSAGES.get(str(settlement["error"]), str(settlement["error"])),
            ephemeral=True,
        )
        return

    fighters = {f.user_id: f for f in (*attackers, *defenders)}
    embed = format_raid_embed(
        attacker_crew=attacker_crew,
        defender_crew=defender,
        result=result,
        fighters=fighters,
        loot=float(settlement["loot"]),
        defender_treasury_after=float(settlement["treasury_after"]),
    )
    await interaction.followup.send(embed=embed, ephemeral=True)

    channel = interaction.channel
    if isinstance(channel, discord.abc.Messageable):
        public = discord.Embed(
            title="🏦 Crew bank raid",
            description=(
                f"**{attacker_crew}** {'robbed' if result.attacker_won else 'failed to rob'} "
                f"**{defender}**'s treasury"
                + (
                    f" for **{fmt_amount(float(settlement['loot']))}**"
                    if result.attacker_won
                    else ""
                )
                + "."
            ),
            color=discord.Color.green() if result.attacker_won else discord.Color.red(),
        )
        try:
            await channel.send(embed=public)
        except discord.HTTPException:
            pass
