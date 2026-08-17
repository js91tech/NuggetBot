from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from utils.game_guide import (
    GUIDE_SECTIONS,
    build_guide_embed,
    guide_section_options,
)
from utils.goon_theme import brand_color
from utils.help_content import HELP_PAGES, NSFW_NOTICE
from utils.helpers import guild_only_message


class HelpView(discord.ui.View):
    def __init__(self, *, page: int = 0) -> None:
        super().__init__(timeout=180.0)
        self.page = page
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= len(HELP_PAGES) - 1

    def embed(self) -> discord.Embed:
        title, body = HELP_PAGES[self.page]
        description = body if self.page > 0 else f"{NSFW_NOTICE}\n\n{body}"
        return discord.Embed(
            title=f"GoonBot guide — {title}",
            description=description,
            color=brand_color(),
        ).set_footer(text=f"Page {self.page + 1}/{len(HELP_PAGES)} · /guide for full systems + items")

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        self.page = max(0, self.page - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        self.page = min(len(HELP_PAGES) - 1, self.page + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)


class GuideTopicSelect(discord.ui.Select):
    def __init__(self, view: GameGuideView) -> None:
        self._guide_view = view
        options = [
            discord.SelectOption(
                label=label[:100],
                value=section_id,
                description=description[:100],
            )
            for section_id, label, description in guide_section_options()
        ]
        super().__init__(
            placeholder="Choose a topic…",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self._guide_view.section_id = self.values[0]
        self._guide_view.page_index = 0
        await self._guide_view.refresh(interaction)


class GameGuideView(discord.ui.View):
    def __init__(self, *, section_id: str = "overview", page_index: int = 0) -> None:
        super().__init__(timeout=300.0)
        self.section_id = section_id
        self.page_index = page_index
        self.add_item(GuideTopicSelect(self))
        self._sync_nav_buttons()

    def _sync_nav_buttons(self) -> None:
        section = next(s for s in GUIDE_SECTIONS if s.section_id == self.section_id)
        total = len(section.pages)
        self.prev_page.disabled = self.page_index <= 0
        self.next_page.disabled = self.page_index >= total - 1

    def embed(self) -> discord.Embed:
        embed, self.page_index, _total = build_guide_embed(self.section_id, self.page_index)
        self._sync_nav_buttons()
        return embed

    async def refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="◀ Page", style=discord.ButtonStyle.secondary, row=1)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        self.page_index = max(0, self.page_index - 1)
        await self.refresh(interaction)

    @discord.ui.button(label="Page ▶", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        section = next(s for s in GUIDE_SECTIONS if s.section_id == self.section_id)
        self.page_index = min(len(section.pages) - 1, self.page_index + 1)
        await self.refresh(interaction)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, row=1)
    async def close_guide(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="Guide closed. Run `/guide` anytime.",
            embed=None,
            view=self,
        )


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="Browse GoonBot commands by category.")
    @app_commands.guild_only()
    async def help_cmd(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        view = HelpView(page=0)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)

    @app_commands.command(
        name="guide",
        description="Interactive guide to all game systems, gear, and items.",
    )
    @app_commands.guild_only()
    async def guide_cmd(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(guild_only_message(), ephemeral=True)
            return
        view = GameGuideView()
        await interaction.response.send_message(
            embed=view.embed(),
            view=view,
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
