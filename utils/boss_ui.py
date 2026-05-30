from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from items import get_item
from utils.consumable_actions import BOSS_RAID_CONSUMABLE_IDS, execute_use_consumable
from utils.helpers import fmt_amount
from utils.skills import skills_for_class
from utils.spell_actions import execute_cast_skill

if TYPE_CHECKING:
    from cogs.boss import Boss


@dataclass
class BossAttackResult:
    embed: discord.Embed | None = None
    defeated: bool = False
    error: str | None = None


async def build_boss_fight_view(
    cog: Boss,
    guild_id: int,
    user_id: int,
) -> "BossFightView":
    class_id = await cog.bot.db.get_class_id(user_id, guild_id)
    skills = skills_for_class(class_id)
    skill_options: list[discord.SelectOption] = []
    for skill in sorted(skills, key=lambda s: s.mana_cost)[:25]:
        skill_options.append(
            discord.SelectOption(
                label=f"{skill.name} ({skill.mana_cost} mana)",
                value=skill.skill_id,
                emoji=skill.emoji,
                description=skill.description[:100],
            )
        )

    consumable_options: list[discord.SelectOption] = []
    rows = await cog.bot.db.get_inventory(user_id, guild_id)
    for row in rows:
        item_id = str(row["item_id"])
        if item_id not in BOSS_RAID_CONSUMABLE_IDS:
            continue
        item = get_item(item_id)
        if item is None:
            continue
        consumable_options.append(
            discord.SelectOption(
                label=f"{item.name} ×{int(row['quantity'])}",
                value=item_id,
                description="+20% on next attack",
            )
        )

    return BossFightView(
        cog,
        guild_id,
        user_id,
        skill_options=skill_options,
        consumable_options=consumable_options,
    )


class BossFightView(discord.ui.View):
    """Interactive boss raid panel — attack, skills, items, heal, refresh."""

    def __init__(
        self,
        cog: Boss,
        guild_id: int,
        user_id: int,
        *,
        skill_options: list[discord.SelectOption] | None = None,
        consumable_options: list[discord.SelectOption] | None = None,
    ) -> None:
        super().__init__(timeout=300.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

        if skill_options:
            skill_select = discord.ui.Select(
                placeholder="Cast skill…",
                options=skill_options,
                row=1,
                min_values=1,
                max_values=1,
            )
            skill_select.callback = self._skill_callback
            self.add_item(skill_select)

        if consumable_options:
            item_select = discord.ui.Select(
                placeholder="Use item…",
                options=consumable_options,
                row=2,
                min_values=1,
                max_values=1,
            )
            item_select.callback = self._item_callback
            self.add_item(item_select)

        heal_select = discord.ui.UserSelect(
            placeholder="Heal downed ally…",
            row=3,
            min_values=1,
            max_values=1,
        )
        heal_select.callback = self._heal_callback
        self.add_item(heal_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Open your own fight panel with `/boss`.", ephemeral=True
            )
            return False
        return True

    async def _refresh_panel(self, interaction: discord.Interaction) -> None:
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        embed, err = await self.cog.build_boss_fight_embed(self.guild_id, member=member)
        if err or embed is None:
            await interaction.response.send_message(err or "No boss active.", ephemeral=True)
            return
        view = await build_boss_fight_view(self.cog, self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _skill_callback(self, interaction: discord.Interaction) -> None:
        skill_id = interaction.data["values"][0]  # type: ignore[index]
        result = await execute_cast_skill(
            self.cog.bot.db,
            self.user_id,
            self.guild_id,
            skill_id,
        )
        if not result.ok:
            await interaction.response.send_message(result.error or "Cast failed.", ephemeral=True)
            return
        await self._refresh_panel(interaction)
        await interaction.followup.send(result.message, ephemeral=True)

    async def _item_callback(self, interaction: discord.Interaction) -> None:
        item_id = interaction.data["values"][0]  # type: ignore[index]
        result = await execute_use_consumable(
            self.cog.bot.db,
            self.user_id,
            self.guild_id,
            item_id,
            boss_context=True,
        )
        if not result.ok:
            await interaction.response.send_message(result.error or "Use failed.", ephemeral=True)
            return
        await self._refresh_panel(interaction)
        await interaction.followup.send(result.message, ephemeral=True)

    async def _heal_callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Guild only.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Members only.", ephemeral=True)
            return
        raw_id = interaction.data["values"][0]  # type: ignore[index]
        target = interaction.guild.get_member(int(raw_id))
        if target is None:
            await interaction.response.send_message("That user left the server.", ephemeral=True)
            return
        embed, err = await self.cog.execute_boss_heal(interaction.user, target, interaction.guild.id)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await self._refresh_panel(interaction)
        if embed is not None:
            await interaction.followup.send(embed=embed, ephemeral=True)
        from utils.achievements import evaluate_unlocks, format_unlock_message

        unlocked = await evaluate_unlocks(
            self.cog.bot.db,
            interaction.guild.id,
            interaction.user.id,
        )
        unlock_msg = format_unlock_message(unlocked)
        if unlock_msg:
            await interaction.followup.send(unlock_msg, ephemeral=True)

    @discord.ui.button(label="⚔️ Attack", style=discord.ButtonStyle.danger, row=0)
    async def attack_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        if interaction.guild is None:
            await interaction.response.send_message("Guild only.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Members only.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        result = await self.cog.execute_boss_attack(
            interaction.user,
            interaction.guild,
            interaction=interaction,
        )
        if result.error:
            await interaction.followup.send(result.error, ephemeral=True)
            return
        if result.defeated:
            for item in self.children:
                item.disabled = True
            if result.embed is not None:
                await interaction.edit_original_response(embed=result.embed, view=self)
            return
        if result.embed is None:
            await interaction.followup.send("Attack failed.", ephemeral=True)
            return
        view = await build_boss_fight_view(self.cog, self.guild_id, self.user_id)
        await interaction.edit_original_response(embed=result.embed, view=view)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=0)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._refresh_panel(interaction)

    @discord.ui.button(label="Raid LB", style=discord.ButtonStyle.secondary, row=0)
    async def leaderboard_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        rows = await self.cog.bot.db.list_boss_damage(self.guild_id)
        if not rows:
            await interaction.response.send_message("Nobody has attacked yet.", ephemeral=True)
            return
        lines = []
        guild = interaction.guild
        for index, row in enumerate(rows[:10], start=1):
            uid = int(row["user_id"])
            member = guild.get_member(uid) if guild else None
            name = member.display_name if member else f"User {uid}"
            lines.append(f"**{index}.** {name} — **{fmt_amount(float(row['damage']))}** dmg")
        embed = discord.Embed(
            title="Raid damage leaderboard",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(text="Rewards scale with damage share when the boss falls")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def send_boss_fight_panel(
    interaction: discord.Interaction,
    cog: Boss,
) -> None:
    """Shared handler for /boss fight UI."""
    if interaction.guild_id is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return

    boss_row = await cog.bot.db.apply_boss_passive_decay(interaction.guild_id)
    if boss_row is None:
        await interaction.response.send_message("No boss is active right now.", ephemeral=True)
        return

    if float(boss_row["hp"]) <= 0 and interaction.guild is not None:
        await cog._complete_boss_defeat(
            interaction.guild,
            interaction=interaction,
            killer_user_id=None,
        )
        return

    embed, err = await cog.build_boss_fight_embed(
        interaction.guild_id,
        boss_row=boss_row,
        member=interaction.user if isinstance(interaction.user, discord.Member) else None,
    )
    if err or embed is None:
        await interaction.response.send_message(err or "No boss active.", ephemeral=True)
        return

    view = await build_boss_fight_view(cog, interaction.guild_id, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
