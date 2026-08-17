from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.companions import COMPANION_DEFINITIONS, companion_by_id, companion_display_name
from utils.helpers import fmt_amount, guild_only_message


class Companions(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def companion_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        rows = await self.bot.db.list_companions(interaction.user.id, interaction.guild_id)
        needle = current.lower()
        choices: list[app_commands.Choice[str]] = []
        for row in rows:
            cid = str(row["companion_id"])
            defn = companion_by_id(cid)
            if defn is None:
                continue
            label = companion_display_name(cid, row["custom_name"])
            if needle and needle not in cid and needle not in label.lower():
                continue
            choices.append(app_commands.Choice(name=f"{defn.emoji} {label}", value=cid))
            if len(choices) >= 25:
                break
        return choices

    @app_commands.command(
        name="companion",
        description="View and manage your henchling companions.",
    )
    @app_commands.describe(
        action="List, equip, unequip, rename, evolve, or feed stamina",
        companion_id="Companion id",
        name="Custom name (rename action)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Status", value="status"),
            app_commands.Choice(name="Equip", value="equip"),
            app_commands.Choice(name="Unequip", value="unequip"),
            app_commands.Choice(name="Rename", value="rename"),
            app_commands.Choice(name="Evolve", value="evolve"),
            app_commands.Choice(name="Feed", value="feed"),
        ],
    )
    @app_commands.autocomplete(companion_id=companion_autocomplete)
    @app_commands.guild_only()
    async def companion(
        self,
        interaction: discord.Interaction,
        action: str,
        companion_id: str | None = None,
        name: str | None = None,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        uid = interaction.user.id
        gid = interaction.guild_id

        if action == "status":
            owned = await self.bot.db.list_companions(uid, gid)
            equipped = set(await self.bot.db.list_equipped_companion_ids(uid, gid))
            if not owned:
                await interaction.response.send_message(
                    "No henchlings yet. Raid adds and vault clears can drop them.",
                    ephemeral=True,
                )
                return
            lines = []
            for row in owned:
                cid = str(row["companion_id"])
                defn = companion_by_id(cid)
                if defn is None:
                    continue
                label = companion_display_name(cid, row["custom_name"])
                mark = " **(active)**" if cid in equipped else ""
                tier = int(row["evolution_tier"])
                stamina = int(row["stamina"])
                lines.append(
                    f"{defn.emoji} **{label}**{mark} — _{defn.description}_\n"
                    f"   Tier **{tier}** · Stamina **{stamina}** · "
                    f"Rarity **{defn.rarity.title()}** · `{cid}`"
                )
            await interaction.response.send_message(
                f"**Companion Dex** ({len(owned)}/{len(COMPANION_DEFINITIONS)})\n"
                f"Active slots: **{len(equipped)}/{config.COMPANION_MAX_EQUIP}**\n\n"
                + "\n".join(lines),
                ephemeral=True,
            )
            return

        if action == "equip":
            if not companion_id:
                await interaction.response.send_message("Provide a companion id.", ephemeral=True)
                return
            ok, err = await self.bot.db.equip_companion(uid, gid, companion_id)
            if not ok:
                messages = {
                    "not_owned": "Companion not owned.",
                    "already_equipped": "Already active.",
                    "max_equipped": (
                        f"You can only have **{config.COMPANION_MAX_EQUIP}** "
                        "companions active at once."
                    ),
                }
                await interaction.response.send_message(
                    messages.get(err or "", "Could not equip companion."),
                    ephemeral=True,
                )
                return
            defn = companion_by_id(companion_id)
            await interaction.response.send_message(
                f"Equipped **{defn.name if defn else companion_id}** — "
                "it will auto-attack during boss raids.",
                ephemeral=True,
            )
            return

        if action == "unequip":
            if not companion_id:
                removed = await self.bot.db.unequip_companion(uid, gid)
            else:
                removed = await self.bot.db.unequip_companion(uid, gid, companion_id)
            if removed:
                await interaction.response.send_message("Companion unequipped.", ephemeral=True)
            else:
                await interaction.response.send_message("No matching active companion.", ephemeral=True)
            return

        if action == "rename":
            if not companion_id or not name:
                await interaction.response.send_message(
                    "Provide a companion id and a new name (max 24 chars).",
                    ephemeral=True,
                )
                return
            ok, err = await self.bot.db.rename_companion(uid, gid, companion_id, name)
            if not ok:
                messages = {
                    "invalid_name": "Name must be 1–24 characters.",
                    "not_owned": "Companion not owned.",
                    "insufficient_funds": (
                        f"Rename costs **{fmt_amount(config.COMPANION_RENAME_COST)}** "
                        "after your first free rename."
                    ),
                }
                await interaction.response.send_message(
                    messages.get(err or "", "Could not rename."),
                    ephemeral=True,
                )
                return
            row = await self.bot.db.get_companion_row(uid, gid, companion_id)
            cost_note = ""
            if row is not None and int(row["rename_count"]) == 1:
                cost_note = " (first rename — free!)"
            await interaction.response.send_message(
                f"Renamed to **{name.strip()}**.{cost_note}",
                ephemeral=True,
            )
            return

        if action == "evolve":
            if not companion_id:
                await interaction.response.send_message("Provide a companion id.", ephemeral=True)
                return
            row = await self.bot.db.get_companion_row(uid, gid, companion_id)
            if row is None:
                await interaction.response.send_message("Companion not owned.", ephemeral=True)
                return
            tier = int(row["evolution_tier"])
            next_tier = tier + 1
            cost = config.COMPANION_EVOLUTION_COSTS.get(next_tier)
            if cost is None:
                await interaction.response.send_message(
                    f"Already at max tier (**{config.COMPANION_MAX_EVOLUTION_TIER}**).",
                    ephemeral=True,
                )
                return
            ok, err = await self.bot.db.evolve_companion(uid, gid, companion_id)
            if not ok:
                if err == "insufficient_funds":
                    await interaction.response.send_message(
                        f"Evolution to tier **{next_tier}** costs "
                        f"**{fmt_amount(cost)}**.",
                        ephemeral=True,
                    )
                else:
                    await interaction.response.send_message("Could not evolve.", ephemeral=True)
                return
            defn = companion_by_id(companion_id)
            label = companion_display_name(companion_id, row["custom_name"])
            await interaction.response.send_message(
                f"**{label}** evolved to tier **{next_tier}**! "
                f"{defn.emoji if defn else '🐾'} More raid bite.",
                ephemeral=True,
            )
            return

        if action == "feed":
            if not companion_id:
                await interaction.response.send_message("Provide a companion id.", ephemeral=True)
                return
            qty = await self.bot.db.get_inventory_quantity(uid, gid, "companion_stamina_pack")
            if qty <= 0:
                await interaction.response.send_message(
                    "No **Companion Stamina Pack** in inventory. Buy one from `/shop`.",
                    ephemeral=True,
                )
                return
            if not await self.bot.db.consume_inventory_item(uid, gid, "companion_stamina_pack"):
                await interaction.response.send_message("Could not use stamina pack.", ephemeral=True)
                return
            new_stamina = await self.bot.db.add_companion_stamina(
                uid, gid, companion_id, config.COMPANION_STAMINA_PACK_RESTORE,
            )
            if new_stamina is None:
                await interaction.response.send_message("Companion not owned.", ephemeral=True)
                return
            label = companion_display_name(companion_id, None)
            row = await self.bot.db.get_companion_row(uid, gid, companion_id)
            if row is not None:
                label = companion_display_name(companion_id, row["custom_name"])
            await interaction.response.send_message(
                f"Fed **{label}** — stamina now **{new_stamina}** "
                f"(+{config.COMPANION_STAMINA_PACK_RESTORE}).",
                ephemeral=True,
            )
            return

        await interaction.response.send_message("Unknown action.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Companions(bot))
