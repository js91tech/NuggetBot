from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from items import CONSUMABLE_USE_IDS, get_item
from utils.consumable_actions import execute_use_consumable

if TYPE_CHECKING:
    from cogs.consumables import Consumables


async def build_consumables_embed(
    cog: Consumables,
    guild_id: int,
    user_id: int,
) -> tuple[discord.Embed, str | None]:
    rows = await cog.bot.db.get_inventory(user_id, guild_id)
    pending = await cog.bot.db.get_pending_consumable_id(user_id, guild_id)
    lines: list[str] = []
    for row in rows:
        item_id = str(row["item_id"])
        if item_id not in CONSUMABLE_USE_IDS:
            continue
        item = get_item(item_id)
        if item is None:
            continue
        lines.append(f"**{item.name}** ×{int(row['quantity'])} (`{item_id}`)")

    embed = discord.Embed(
        title="Consumables",
        description="\n".join(lines) if lines else "_No usable consumables in inventory._",
        color=discord.Color.green(),
    )
    if pending:
        pending_item = get_item(pending)
        name = pending_item.name if pending_item else pending
        embed.add_field(name="Active buff", value=f"**{name}** — use on next attack/duel", inline=False)
    embed.set_footer(text="Pick an item below · Buy more from /shop consumables")
    return embed, None


class ConsumablesView(discord.ui.View):
    def __init__(
        self,
        cog: Consumables,
        guild_id: int,
        user_id: int,
        *,
        item_options: list[discord.SelectOption],
    ) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        if item_options:
            select = discord.ui.Select(
                placeholder="Use a consumable…",
                options=item_options,
                row=0,
            )

            async def on_use(interaction: discord.Interaction) -> None:
                item_id = select.values[0]
                result = await execute_use_consumable(
                    self.cog.bot.db,
                    self.user_id,
                    self.guild_id,
                    item_id,
                )
                if not result.ok:
                    await interaction.response.send_message(
                        result.error or "Use failed.",
                        ephemeral=True,
                    )
                    return
                embed, _ = await build_consumables_embed(self.cog, self.guild_id, self.user_id)
                view = await build_consumables_view(self.cog, self.guild_id, self.user_id)
                await interaction.response.edit_message(embed=embed, view=view)
                await interaction.followup.send(result.message, ephemeral=True)

            select.callback = on_use
            self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your inventory panel.", ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        embed, _ = await build_consumables_embed(self.cog, self.guild_id, self.user_id)
        view = await build_consumables_view(self.cog, self.guild_id, self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)


async def build_consumables_view(
    cog: Consumables,
    guild_id: int,
    user_id: int,
) -> ConsumablesView:
    rows = await cog.bot.db.get_inventory(user_id, guild_id)
    options: list[discord.SelectOption] = []
    for row in rows:
        item_id = str(row["item_id"])
        if item_id not in CONSUMABLE_USE_IDS:
            continue
        item = get_item(item_id)
        if item is None:
            continue
        options.append(
            discord.SelectOption(
                label=f"{item.name} ×{int(row['quantity'])}",
                value=item_id,
                description=item.description[:100] if item.description else None,
            )
        )
        if len(options) >= 25:
            break
    return ConsumablesView(cog, guild_id, user_id, item_options=options)


async def send_consumables_panel(interaction: discord.Interaction, cog: Consumables) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return
    embed, err = await build_consumables_embed(
        cog,
        interaction.guild_id,
        interaction.user.id,
    )
    if err:
        await interaction.response.send_message(err, ephemeral=True)
        return
    view = await build_consumables_view(cog, interaction.guild_id, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
