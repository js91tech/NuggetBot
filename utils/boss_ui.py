from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

import config
from utils.helpers import fmt_amount

if TYPE_CHECKING:
    from cogs.boss import Boss
    from cogs.consumables import Consumables
    from cogs.spells import Spells


@dataclass
class BossAttackResult:
    embed: discord.Embed | None = None
    defeated: bool = False
    error: str | None = None
    message: str | None = None


@dataclass
class BossPanelResult:
    embed: discord.Embed | None = None
    error: str | None = None
    message: str | None = None
    defeated: bool = False


def _spells_cog(cog: Boss) -> Spells | None:
    from cogs.spells import Spells as SpellsCog

    found = cog.bot.get_cog("Spells")
    return found if isinstance(found, SpellsCog) else None


def _consumables_cog(cog: Boss) -> Consumables | None:
    from cogs.consumables import Consumables as ConsumablesCog

    found = cog.bot.get_cog("Consumables")
    return found if isinstance(found, ConsumablesCog) else None


class BossFightView(discord.ui.View):
    """Interactive boss raid panel — attack, heal, cast, use, refresh."""

    def __init__(self, cog: Boss, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=300.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Open your own fight panel with `/boss`.", ephemeral=True,
            )
            return False
        return True

    async def _refresh_embed(
        self,
        interaction: discord.Interaction,
        *,
        result: BossPanelResult | BossAttackResult | None = None,
    ) -> None:
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if result is not None and result.defeated:
            for child in self.children:
                child.disabled = True
            if result.embed is not None:
                await interaction.edit_original_response(embed=result.embed, view=self)
            return
        embed, err = await self.cog.build_boss_fight_embed(
            self.guild_id,
            member=member,
        )
        if err or embed is None:
            if result and result.message:
                await interaction.followup.send(result.message, ephemeral=True)
            elif err:
                await interaction.followup.send(err, ephemeral=True)
            return
        await interaction.edit_original_response(embed=embed, view=self)
        if result and result.message:
            await interaction.followup.send(result.message, ephemeral=True)

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
            for child in self.children:
                child.disabled = True
            if result.embed is not None:
                await interaction.edit_original_response(embed=result.embed, view=self)
            return
        if result.embed is not None:
            await interaction.edit_original_response(embed=result.embed, view=self)
            return
        await interaction.followup.send("Attack failed.", ephemeral=True)

    @discord.ui.button(label="💊 Heal", style=discord.ButtonStyle.success, row=0)
    async def heal_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Members only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        result = await self.cog.execute_boss_self_heal(interaction.user, interaction.guild)
        if result.error:
            await interaction.followup.send(result.error, ephemeral=True)
            return
        await self._refresh_embed(interaction, result=result)

    @discord.ui.button(label="✨ Cast", style=discord.ButtonStyle.primary, row=0)
    async def cast_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        spells = _spells_cog(self.cog)
        if spells is None:
            await interaction.response.send_message("Spells unavailable.", ephemeral=True)
            return
        options = await spells.build_cast_select_options(self.user_id, self.guild_id)
        if not options:
            await interaction.response.send_message(
                "No skills available. Choose a class with `/class-choose` and check `/skills`.",
                ephemeral=True,
            )
            return
        view = BossCastSelectView(self.cog, self.guild_id, self.user_id, options)
        await interaction.response.send_message(
            "Pick a skill to cast:",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="🧪 Use", style=discord.ButtonStyle.secondary, row=0)
    async def use_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        consumables = _consumables_cog(self.cog)
        if consumables is None:
            await interaction.response.send_message("Consumables unavailable.", ephemeral=True)
            return
        options = await consumables.build_use_select_options(self.user_id, self.guild_id)
        if not options:
            await interaction.response.send_message(
                "No usable consumables in your inventory. Buy a **Raid Potion** from `/shop`.",
                ephemeral=True,
            )
            return
        view = BossUseSelectView(self.cog, self.guild_id, self.user_id, options)
        await interaction.response.send_message(
            "Pick a consumable:",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=1)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        embed, err = await self.cog.build_boss_fight_embed(self.guild_id, member=member)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Raid LB", style=discord.ButtonStyle.secondary, row=1)
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


class BossCastSelectView(discord.ui.View):
    def __init__(
        self,
        cog: Boss,
        guild_id: int,
        user_id: int,
        options: list[discord.SelectOption],
    ) -> None:
        super().__init__(timeout=120.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.add_item(BossCastSelect(self, options))


class BossCastSelect(discord.ui.Select):
    def __init__(self, parent: BossCastSelectView, options: list[discord.SelectOption]) -> None:
        super().__init__(
            placeholder="Choose a skill…",
            min_values=1,
            max_values=1,
            options=options,
        )
        self._parent = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._parent.user_id:
            await interaction.response.send_message("Not your panel.", ephemeral=True)
            return
        spells = _spells_cog(self._parent.cog)
        if spells is None:
            await interaction.response.send_message("Spells unavailable.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        error, message = await spells.execute_cast_skill(
            interaction.user.id,
            self._parent.guild_id,
            self.values[0],
        )
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        await interaction.followup.send(message or "Cast.", ephemeral=True)


class BossUseSelectView(discord.ui.View):
    def __init__(
        self,
        cog: Boss,
        guild_id: int,
        user_id: int,
        options: list[discord.SelectOption],
    ) -> None:
        super().__init__(timeout=120.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.add_item(BossUseSelect(self, options))


class BossUseSelect(discord.ui.Select):
    def __init__(self, parent: BossUseSelectView, options: list[discord.SelectOption]) -> None:
        super().__init__(
            placeholder="Choose a consumable…",
            min_values=1,
            max_values=1,
            options=options,
        )
        self._parent = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self._parent.user_id:
            await interaction.response.send_message("Not your panel.", ephemeral=True)
            return
        consumables = _consumables_cog(self._parent.cog)
        if consumables is None:
            await interaction.response.send_message("Consumables unavailable.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        error, message = await consumables.execute_use_item(
            interaction.user.id,
            self._parent.guild_id,
            self.values[0],
        )
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        await interaction.followup.send(message or "Used.", ephemeral=True)


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

    view = BossFightView(cog, interaction.guild_id, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
