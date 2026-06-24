from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

import config
from items import HP_POTION_HEAL, HP_POTION_IDS, get_item
from utils.consumables_ui import (
    BOSS_SHOP_USE_IDS,
    build_use_embed,
    execute_use,
    list_boss_useable_entries,
    use_error_message,
)
from utils.skills import skills_for_class
from utils.spell_cast import cast_skill_for_user

if TYPE_CHECKING:
    from cogs.boss import Boss

POTION_TIER_OPTIONS: tuple[tuple[str, str | None], ...] = (
    ("Off", None),
    ("Small", "hp_potion_small"),
    ("Medium", "hp_potion_medium"),
    ("Large", "hp_potion_large"),
    ("XXL", "hp_potion_xxl"),
)

POTION_TIER_BY_ID: dict[str | None, str] = {item_id: label for label, item_id in POTION_TIER_OPTIONS}
POTION_TIER_BY_LABEL: dict[str, str | None] = {
    label: item_id for label, item_id in POTION_TIER_OPTIONS
}


@dataclass
class BossAttackResult:
    embed: discord.Embed | None = None
    defeated: bool = False
    error: str | None = None
    files: list[discord.File] | None = None


class AutoPotionSettingsView(discord.ui.View):
    """Configure raid auto-heal potion tier and HP threshold."""

    def __init__(self, cog: Boss, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    @classmethod
    async def create(cls, cog: Boss, guild_id: int, user_id: int) -> AutoPotionSettingsView:
        view = cls(cog, guild_id, user_id)
        item_id, threshold_pct = await cog.bot.db.get_auto_potion_settings(user_id, guild_id)
        tier_label = POTION_TIER_BY_ID.get(item_id, "Off")
        threshold_label = (
            f"{threshold_pct}%"
            if threshold_pct in config.AUTO_POTION_THRESHOLDS
            else f"{config.AUTO_POTION_THRESHOLDS[2]}%"
        )

        tier_select = discord.ui.Select(
            placeholder="Potion tier",
            options=[
                discord.SelectOption(label=label, value=label, default=(label == tier_label))
                for label, _ in POTION_TIER_OPTIONS
            ],
            row=0,
        )

        async def tier_callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != user_id:
                await interaction.response.send_message(
                    "This panel is not yours.", ephemeral=True
                )
                return
            label = tier_select.values[0]
            new_item_id = POTION_TIER_BY_LABEL[label]
            _, cur_threshold = await cog.bot.db.get_auto_potion_settings(user_id, guild_id)
            threshold = (
                cur_threshold
                if cur_threshold in config.AUTO_POTION_THRESHOLDS
                else config.AUTO_POTION_THRESHOLDS[2]
            )
            await cog.bot.db.set_auto_potion_settings(
                user_id, guild_id, new_item_id, threshold
            )
            await interaction.response.send_message(
                f"Auto-heal potion set to **{label}**.", ephemeral=True
            )

        tier_select.callback = tier_callback
        view.add_item(tier_select)

        threshold_select = discord.ui.Select(
            placeholder="Trigger at HP %",
            options=[
                discord.SelectOption(
                    label=f"{pct}% HP",
                    value=str(pct),
                    default=(f"{pct}%" == threshold_label),
                )
                for pct in config.AUTO_POTION_THRESHOLDS
            ],
            row=1,
        )

        async def threshold_callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != user_id:
                await interaction.response.send_message(
                    "This panel is not yours.", ephemeral=True
                )
                return
            threshold = int(threshold_select.values[0])
            cur_item_id, _ = await cog.bot.db.get_auto_potion_settings(user_id, guild_id)
            await cog.bot.db.set_auto_potion_settings(
                user_id, guild_id, cur_item_id, threshold
            )
            await interaction.response.send_message(
                f"Auto-heal triggers at **{threshold}%** HP or below.", ephemeral=True
            )

        threshold_select.callback = threshold_callback
        view.add_item(threshold_select)
        return view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Open your own fight panel with `/boss`.", ephemeral=True
            )
            return False
        return True


class BossFightView(discord.ui.View):
    """Interactive boss raid panel — attack, refresh, and raid leaderboard."""

    def __init__(self, cog: Boss, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=300.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Open your own fight panel with `/boss`.", ephemeral=True
            )
            return False
        return True

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
        kwargs: dict = {"embed": result.embed, "view": self}
        if result.files:
            kwargs["attachments"] = result.files
        await interaction.edit_original_response(**kwargs)

    @discord.ui.button(label="👹 Attack Add", style=discord.ButtonStyle.danger, row=0)
    async def attack_add_button(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        if interaction.guild is None:
            await interaction.response.send_message("Guild only.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Members only.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        result = await self.cog.execute_raid_add_attack(
            interaction.user,
            interaction.guild,
        )
        if result.error:
            await interaction.followup.send(result.error, ephemeral=True)
            return
        if result.embed is None:
            await interaction.followup.send("Attack failed.", ephemeral=True)
            return
        await interaction.edit_original_response(embed=result.embed, view=self)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=0)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        embed, err = await self.cog.build_boss_fight_embed(self.guild_id, member=member)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        await interaction.response.edit_message(embed=embed, view=self)

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

    @discord.ui.button(label="🧪 Auto-heal", style=discord.ButtonStyle.success, row=1)
    async def auto_heal_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        item_id, threshold_pct = await self.cog.bot.db.get_auto_potion_settings(
            self.user_id,
            self.guild_id,
        )
        if item_id and threshold_pct > 0:
            potion = get_item(item_id)
            tier = potion.name if potion is not None else item_id
            current = f"**{tier}** at **{threshold_pct}%** HP or below"
        else:
            current = "_Off_"
        embed = discord.Embed(
            title="Auto-heal settings",
            description=(
                f"Current: {current}\n\n"
                "Choose a potion tier and HP threshold. When a boss counter drops you "
                "to or below that %, the bot consumes one potion from your inventory."
            ),
            color=discord.Color.green(),
        )
        for potion_id in sorted(HP_POTION_IDS):
            potion = get_item(potion_id)
            if potion is not None:
                embed.add_field(
                    name=potion.name,
                    value=potion.description,
                    inline=False,
                )
        view = await AutoPotionSettingsView.create(self.cog, self.guild_id, self.user_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="✨ Cast", style=discord.ButtonStyle.primary, row=1)
    async def cast_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        class_id = await self.cog.bot.db.get_class_id(self.user_id, self.guild_id)
        if not class_id:
            await interaction.response.send_message(
                "Choose a class with `/class-choose` first.", ephemeral=True,
            )
            return
        skills = skills_for_class(class_id)
        if not skills:
            await interaction.response.send_message("No skills available.", ephemeral=True)
            return
        options = [
            discord.SelectOption(
                label=f"{s.name}"[:100],
                value=s.skill_id,
                description=f"{s.mana_cost} mana"[:100],
                emoji=s.emoji,
            )
            for s in skills[:25]
        ]
        select = discord.ui.Select(placeholder="Pick a skill to cast", options=options)

        async def cast_callback(sel_interaction: discord.Interaction) -> None:
            if sel_interaction.user.id != self.user_id:
                await sel_interaction.response.send_message(
                    "This panel is not yours.", ephemeral=True,
                )
                return
            skill_id = select.values[0]
            result = await cast_skill_for_user(
                self.cog.bot.db,
                self.user_id,
                self.guild_id,
                skill_id,
                class_id=class_id,
            )
            if not result.ok:
                await sel_interaction.response.send_message(
                    result.error or "Cast failed.", ephemeral=True,
                )
                return
            await sel_interaction.response.send_message(result.message, ephemeral=True)

        select.callback = cast_callback
        view = discord.ui.View(timeout=120.0)
        view.add_item(select)
        await interaction.response.send_message(
            "Select a skill to cast:", view=view, ephemeral=True,
        )

    @discord.ui.button(label="💊 Items", style=discord.ButtonStyle.secondary, row=1)
    async def items_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        entries = await list_boss_useable_entries(self.cog, self.user_id, self.guild_id)
        if not entries:
            await interaction.response.send_message(
                "No raid consumables or stash drugs — buy from `/shop` or harvest from `/drugs lab`.",
                ephemeral=True,
            )
            return
        options = [
            discord.SelectOption(
                label=f"{label} ×{qty}"[:100],
                value=entry_id,
                description="Use one"[:100],
            )
            for entry_id, label, qty in entries[:25]
        ]
        select = discord.ui.Select(placeholder="Use consumable or drug…", options=options)

        async def use_callback(sel_interaction: discord.Interaction) -> None:
            if sel_interaction.user.id != self.user_id:
                await sel_interaction.response.send_message(
                    "This panel is not yours.", ephemeral=True,
                )
                return
            await sel_interaction.response.defer(ephemeral=True)
            err, message = await execute_use(
                self.cog,
                self.user_id,
                self.guild_id,
                select.values[0],
                shop_ids=BOSS_SHOP_USE_IDS,
            )
            if err:
                await sel_interaction.followup.send(use_error_message(err), ephemeral=True)
                return
            await sel_interaction.followup.send(message or "Used.", ephemeral=True)

        select.callback = use_callback
        view = discord.ui.View(timeout=120.0)
        view.add_item(select)
        embed = await build_use_embed(self.cog, self.user_id, self.guild_id)
        embed.title = "💊 Raid consumables"
        embed.set_footer(text="Raid potions buff your next strike · drugs have timed raid effects")
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="❤️ Heal", style=discord.ButtonStyle.success, row=1)
    async def heal_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        if interaction.guild is None:
            await interaction.response.send_message("Guild only.", ephemeral=True)
            return
        downed_ids = await self.cog.bot.db.list_downed_users(self.guild_id)
        if not downed_ids:
            await interaction.response.send_message(
                "Nobody is downed right now.", ephemeral=True,
            )
            return
        options: list[discord.SelectOption] = []
        for uid in downed_ids[:25]:
            member = interaction.guild.get_member(uid)
            label = member.display_name if member is not None else f"User {uid}"
            options.append(discord.SelectOption(label=label[:100], value=str(uid)))
        select = discord.ui.Select(placeholder="Revive a downed raider", options=options)

        async def heal_callback(sel_interaction: discord.Interaction) -> None:
            if sel_interaction.user.id != self.user_id:
                await sel_interaction.response.send_message(
                    "This panel is not yours.", ephemeral=True,
                )
                return
            if not isinstance(sel_interaction.user, discord.Member):
                await sel_interaction.response.send_message("Members only.", ephemeral=True)
                return
            target_id = int(select.values[0])
            target = sel_interaction.guild.get_member(target_id) if sel_interaction.guild else None
            if target is None:
                await sel_interaction.response.send_message(
                    "Target not found.", ephemeral=True,
                )
                return
            embed, err = await self.cog.execute_field_heal(
                sel_interaction.user,
                target,
                sel_interaction.guild,
            )
            if err:
                await sel_interaction.response.send_message(err, ephemeral=True)
                return
            await sel_interaction.response.send_message(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        select.callback = heal_callback
        view = discord.ui.View(timeout=120.0)
        view.add_item(select)
        await interaction.response.send_message(
            "Select a downed ally to revive:", view=view, ephemeral=True,
        )


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

    if cog.bot.db.boss_has_expired(boss_row) and interaction.guild is not None:
        await cog._despawn_boss_timeout(interaction.guild)
        await interaction.response.send_message(
            "The boss despawned before you could fight.", ephemeral=True
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
