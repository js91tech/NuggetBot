"""Dungeon Lobby — solo start, party pointers, and an energy refresh in one panel."""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord

import config
from utils.dungeon_tiers import NORMAL_TIER, VAULT_TIER
from utils.energy import format_energy_display
from utils.goon_theme import FOOTER_BRAND, branded_embed, panel_title
from utils.helpers import fmt_amount, guild_only_message

if TYPE_CHECKING:
    from cogs.dungeon import Dungeon


async def _energy_text(cog: Dungeon, guild_id: int, user_id: int) -> tuple[str, int, int]:
    row = await cog.bot.db.get_user_character(user_id, guild_id)
    regen_per_tick = int(await cog.bot.db.get_config_value(guild_id, "energy_regen_per_tick"))
    tick_seconds = int(await cog.bot.db.get_config_value(guild_id, "energy_regen_interval_seconds"))
    return format_energy_display(
        int(row["energy"]),
        int(row["energy_cap"]),
        int(row["cap_upgrades"]),
        float(row["energy_updated_at"]),
        regen_per_tick=regen_per_tick,
        tick_seconds=tick_seconds,
    )


async def build_dungeon_lobby_embed(
    cog: Dungeon,
    guild_id: int,
    user_id: int,
    *,
    member_name: str,
) -> discord.Embed:
    energy_text, current_energy, cap = await _energy_text(cog, guild_id, user_id)
    vault_unlocked = await cog.bot.db.has_vault_dungeon_unlocked(user_id, guild_id)

    embed = branded_embed(
        panel_title("Dungeon Lobby", member_name=member_name),
        description=(
            f"**{config.DUNGEON_ROOMS} rooms** per run · **{config.DUNGEON_ENERGY_COST}** energy "
            "to enter.\nNo active run — start solo below, or rally a party for the vault."
        ),
    )
    embed.add_field(
        name=f"{NORMAL_TIER.emoji} Solo — {NORMAL_TIER.name}",
        value=(
            f"Free entry · **{config.DUNGEON_ENERGY_COST}** energy · "
            "tougher enemies the deeper you push"
        ),
        inline=False,
    )
    vault_status = (
        "Unlocked · gather a party" if vault_unlocked
        else f"Locked · **{fmt_amount(config.DUNGEON_VAULT_UNLOCK_COST)}** to unlock"
    )
    embed.add_field(
        name=f"{VAULT_TIER.emoji} Party — {VAULT_TIER.name}",
        value=f"{vault_status} · needs **{VAULT_TIER.min_party_size}+** raiders",
        inline=False,
    )
    embed.add_field(name="Your energy", value=energy_text, inline=False)
    can_start = current_energy >= config.DUNGEON_ENERGY_COST
    embed.set_footer(
        text=(
            f"{FOOTER_BRAND} · Start solo below"
            if can_start
            else f"{FOOTER_BRAND} · Need {config.DUNGEON_ENERGY_COST} energy ({current_energy}/{cap})"
        ),
    )
    return embed


class DungeonLobbyView(discord.ui.View):
    def __init__(self, cog: Dungeon, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Open your own dungeon lobby with `/dungeon`.", ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="🕳️ Start solo dungeon", style=discord.ButtonStyle.success, row=0)
    async def start_solo_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        await interaction.response.defer()
        result = await self.cog.execute_dungeon_start(
            self.guild_id, self.user_id, tier_id=NORMAL_TIER.tier_id,
        )
        if result.error:
            await interaction.followup.send(result.error, ephemeral=True)
            return
        if result.embed is not None:
            from utils.dungeon_ui import DungeonView

            vault_unlocked = await self.cog.bot.db.has_vault_dungeon_unlocked(
                self.user_id, self.guild_id,
            )
            view = DungeonView(
                self.cog, self.guild_id, self.user_id,
                has_run=True, vault_unlocked=vault_unlocked,
            )
            await interaction.edit_original_response(embed=result.embed, view=view)
        if result.message:
            await interaction.followup.send(result.message, ephemeral=True)

    @discord.ui.button(label="👥 Party create/join", style=discord.ButtonStyle.primary, row=0)
    async def party_info_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        embed = branded_embed(
            "👥 Dungeon parties",
            description=(
                "Parties run through `/dungeon`'s **action** option:\n\n"
                "• **Party — create** — start a normal-tier party (others can join before fighting)\n"
                f"• **Party — create vault** — start a **{VAULT_TIER.name}** run "
                f"(needs unlock, {fmt_amount(config.DUNGEON_VAULT_UNLOCK_COST)})\n"
                "• **Party — join** (pick the leader) — join their run\n"
                "• **Party — status** — check HP and room progress\n"
                "• **Party — fight** — attack as a group\n"
                "• **Party — leave** — bail out"
            ),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🔄 Refresh energy", style=discord.ButtonStyle.secondary, row=1)
    async def refresh_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        del button
        await interaction.response.defer()
        run = await self.cog.bot.db.get_dungeon_run(self.user_id, self.guild_id)
        if run is not None:
            embed, _has_run, vault_unlocked = await self.cog.build_dungeon_embed(
                self.guild_id, self.user_id,
            )
            from utils.dungeon_ui import DungeonView

            view = DungeonView(
                self.cog, self.guild_id, self.user_id,
                has_run=True, vault_unlocked=vault_unlocked,
            )
            await interaction.edit_original_response(embed=embed, view=view)
            return
        embed = await build_dungeon_lobby_embed(
            self.cog, self.guild_id, self.user_id, member_name=interaction.user.display_name,
        )
        await interaction.edit_original_response(embed=embed, view=self)


async def send_dungeon_lobby(interaction: discord.Interaction, cog: Dungeon) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message(guild_only_message(), ephemeral=True)
        return
    embed = await build_dungeon_lobby_embed(
        cog, interaction.guild_id, interaction.user.id, member_name=interaction.user.display_name,
    )
    view = DungeonLobbyView(cog, interaction.guild_id, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
