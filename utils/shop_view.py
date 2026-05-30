from __future__ import annotations

import io
from math import ceil
from typing import TYPE_CHECKING

import discord

from items import ShopItem, get_item, items_for_category
from utils.helpers import fmt_amount
from utils.shop_canvas import ITEMS_PER_PAGE, render_shop_page
from utils.quests import record_quest_event

if TYPE_CHECKING:
    from cogs.shop import Shop

CATEGORY_LABELS: dict[str, str] = {
    "all": "All",
    "weapon": "Weapons",
    "gun": "Guns",
    "armor": "Armor",
    "consumable": "Consumables",
}


def _short_buy_label(item: ShopItem) -> str:
    name = item.name
    if len(name) <= 12:
        return f"Buy {name}"
    return f"Buy {name[:11]}…"


class ShopView(discord.ui.View):
    def __init__(
        self,
        cog: Shop,
        guild_id: int,
        user_id: int,
        *,
        category: str = "all",
        page: int = 0,
    ) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.category = category
        self.page = page

    def _items(self) -> list[ShopItem]:
        return items_for_category(self.category)

    def _page_count(self) -> int:
        return max(1, ceil(len(self._items()) / ITEMS_PER_PAGE))

    def _page_items(self) -> list[ShopItem]:
        items = self._items()
        start = self.page * ITEMS_PER_PAGE
        return items[start : start + ITEMS_PER_PAGE]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This is not your shop session.", ephemeral=True
            )
            return False
        return True

    def _populate_items(
        self,
        page_items: list[ShopItem],
        can_afford: dict[str, bool],
    ) -> None:
        options = [
            discord.SelectOption(
                label=label,
                value=cat,
                default=(cat == self.category),
            )
            for cat, label in CATEGORY_LABELS.items()
        ]
        select = discord.ui.Select(
            placeholder="Category",
            options=options,
            row=0,
        )

        async def on_category(interaction: discord.Interaction) -> None:
            self.category = select.values[0]
            self.page = 0
            await self.refresh(interaction)

        select.callback = on_category
        self.add_item(select)

        prev = discord.ui.Button(
            label="◀ Prev",
            style=discord.ButtonStyle.secondary,
            disabled=self.page <= 0,
            row=1,
        )
        nxt = discord.ui.Button(
            label="Next ▶",
            style=discord.ButtonStyle.secondary,
            disabled=self.page >= self._page_count() - 1,
            row=1,
        )

        async def on_prev(interaction: discord.Interaction) -> None:
            self.page = max(0, self.page - 1)
            await self.refresh(interaction)

        async def on_next(interaction: discord.Interaction) -> None:
            self.page = min(self._page_count() - 1, self.page + 1)
            await self.refresh(interaction)

        prev.callback = on_prev
        nxt.callback = on_next
        self.add_item(prev)
        self.add_item(nxt)

        for index, item in enumerate(page_items):
            affordable = can_afford.get(item.id, False)
            btn = discord.ui.Button(
                label=_short_buy_label(item),
                style=discord.ButtonStyle.primary if affordable else discord.ButtonStyle.secondary,
                disabled=not affordable,
                row=2 + index // 3,
            )
            item_id = item.id

            async def on_buy(interaction: discord.Interaction, iid: str = item_id) -> None:
                await self.buy_item(interaction, iid)

            btn.callback = on_buy
            self.add_item(btn)

    async def build_payload(
        self,
    ) -> tuple[discord.Embed, list[discord.File], ShopView]:
        wallet = await self.cog.bot.db.get_balance(self.user_id, self.guild_id)
        bank = await self.cog.bot.db.get_bank(self.user_id, self.guild_id)
        page_items = self._page_items()
        can_afford = {
            item.id: item.price > 0 and wallet >= item.price for item in page_items
        }

        self.clear_items()
        self._populate_items(page_items, can_afford)

        png = render_shop_page(page_items, wallet=wallet, can_afford=can_afford)
        file = discord.File(io.BytesIO(png), filename="shop.png")

        label = CATEGORY_LABELS.get(self.category, self.category.title())
        title = "Nugget Shop" if self.category == "all" else f"Nugget Shop — {label}"
        embed = discord.Embed(title=title, color=discord.Color.dark_green())
        embed.description = (
            f"**Pocket:** {fmt_amount(wallet)} · **Bank:** {fmt_amount(bank)} "
            f"· **Net:** {fmt_amount(wallet + bank)}"
        )
        embed.set_image(url="attachment://shop.png")
        embed.set_footer(
            text=(
                f"Page {self.page + 1}/{self._page_count()} · "
                "Buy debits pocket · /equip gear after purchase"
            ),
        )
        return embed, [file], self

    async def refresh(self, interaction: discord.Interaction) -> None:
        embed, files, view = await self.build_payload()
        await interaction.response.edit_message(embed=embed, attachments=files, view=view)

    async def buy_item(self, interaction: discord.Interaction, item_id: str) -> None:
        shop_item = get_item(item_id)
        if shop_item is None or not shop_item.shop_listed or shop_item.price <= 0:
            await interaction.response.send_message("That item is not for sale.", ephemeral=True)
            return

        bought = await self.cog.bot.db.buy_item(
            self.user_id,
            self.guild_id,
            shop_item.id,
            shop_item.price,
            quantity=1,
        )
        if not bought:
            await interaction.response.send_message(
                f"You need **{fmt_amount(shop_item.price)}** in your pocket.",
                ephemeral=True,
            )
            return

        await record_quest_event(
            self.cog.bot.db,
            self.guild_id,
            self.user_id,
            "shop_buy",
        )
        embed, files, view = await self.build_payload()
        await interaction.response.edit_message(
            embed=embed,
            attachments=files,
            view=view,
        )
        await interaction.followup.send(
            f"Purchased **{shop_item.name}** for **{fmt_amount(shop_item.price)}**.",
            ephemeral=True,
        )
