"""Interactive /use panel for shop consumables and drug stash products."""
from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

import discord

import config
from items import CONSUMABLE_USE_IDS, HP_POTION_HEAL, HP_POTION_IDS, get_item
from utils.drugs import drug_by_id, format_consume_message
from utils.fertilizer import FERTILIZER_IDS
from utils.player_combat import player_max_hp
from utils.quests import record_quest_event

if TYPE_CHECKING:
    from discord.ext import commands

logger = logging.getLogger(__name__)

# Crafting mats and gift-only items are not meant for /use.
_SHOP_USE_SKIP: frozenset[str] = frozenset({
    "trap_bomb",
    "alchemy_scrap",
    "void_hardener",
    "celestial_shard",
    "chia_seeds",
    "fertilizer",
    "xl_fertilizer",
})

SHOP_USE_IDS: frozenset[str] = CONSUMABLE_USE_IDS - _SHOP_USE_SKIP - FERTILIZER_IDS

BOSS_SHOP_USE_IDS: frozenset[str] = frozenset({"raid_potion"}) | HP_POTION_IDS


async def list_useable_entries(
    cog: commands.Cog, user_id: int, guild_id: int,
) -> list[tuple[str, str, int]]:
    """Return (entry_id, label, quantity) for shop items and stash drugs."""
    return await _collect_useable_entries(cog, user_id, guild_id, shop_ids=SHOP_USE_IDS)


async def list_boss_useable_entries(
    cog: commands.Cog, user_id: int, guild_id: int,
) -> list[tuple[str, str, int]]:
    """Raid panel: HP potions, raid potion, and all stash drugs."""
    return await _collect_useable_entries(cog, user_id, guild_id, shop_ids=BOSS_SHOP_USE_IDS)


async def _collect_useable_entries(
    cog: commands.Cog,
    user_id: int,
    guild_id: int,
    *,
    shop_ids: frozenset[str],
) -> list[tuple[str, str, int]]:
    entries: list[tuple[str, str, int]] = []
    rows = await cog.bot.db.get_inventory(user_id, guild_id)
    for row in rows:
        item_id = str(row["item_id"])
        if item_id not in shop_ids:
            continue
        item = get_item(item_id)
        if item is None:
            continue
        qty = int(row["quantity"])
        if qty <= 0:
            continue
        entries.append((item_id, item.name, qty))
    drug_inv = await cog.bot.db.get_drug_inventory(user_id, guild_id)
    for drug_id, qty in drug_inv.items():
        if qty <= 0:
            continue
        defn = drug_by_id(drug_id)
        if defn is None:
            continue
        entries.append((drug_id, f"{defn.emoji} {defn.name}", qty))
    entries.sort(key=lambda row: row[1].lower())
    return entries


async def execute_use(
    cog: commands.Cog,
    user_id: int,
    guild_id: int,
    entry_id: str,
    *,
    shop_ids: frozenset[str] | None = None,
) -> tuple[str | None, str | None]:
    """Use one shop consumable or drug. Returns (error_code, success_message)."""
    allowed_shop = shop_ids or SHOP_USE_IDS
    item_id = entry_id.strip().lower()
    if not item_id:
        return "invalid_item", None
    drug = drug_by_id(item_id)
    if drug is not None:
        stash_qty = (await cog.bot.db.get_drug_inventory(user_id, guild_id)).get(drug.drug_id, 0)
        if stash_qty <= 0:
            return "insufficient_product", None
        max_hp = await player_max_hp(cog, user_id, guild_id)
        result = await cog.bot.db.consume_drug(user_id, guild_id, drug.drug_id, max_hp=max_hp)
        if result.get("error"):
            return str(result["error"]), None
        await record_quest_event(cog.bot.db, guild_id, user_id, "drug_use")
        return None, format_consume_message(result)

    shop_item = get_item(item_id)
    if shop_item is None or item_id not in allowed_shop:
        return "invalid_item", None
    qty = await cog.bot.db.get_inventory_quantity(user_id, guild_id, item_id)
    if qty <= 0:
        return "insufficient_items", None

    if item_id == "energy_drink":
        if not await cog.bot.db.consume_inventory_item(user_id, guild_id, item_id):
            return "consume_failed", None
        new_energy = await cog.bot.db.add_energy(user_id, guild_id, 15)
        return None, f"**Energy Drink** — energy restored to **{new_energy}**."

    if item_id == "companion_stamina_pack":
        equipped = await cog.bot.db.list_equipped_companion_ids(user_id, guild_id)
        if not equipped:
            return "no_equipped_companion", None
        if not await cog.bot.db.consume_inventory_item(user_id, guild_id, item_id):
            return "consume_failed", None
        restored: list[str] = []
        for cid in equipped:
            new_stamina = await cog.bot.db.add_companion_stamina(
                user_id, guild_id, cid, config.COMPANION_STAMINA_PACK_RESTORE,
            )
            if new_stamina is not None:
                restored.append(f"`{cid}` → **{new_stamina}**")
        if not restored:
            return "consume_failed", None
        return None, (
            f"**Companion Stamina Pack** — restored **"
            f"{config.COMPANION_STAMINA_PACK_RESTORE}** stamina to active henchlings:\n"
            + "\n".join(restored)
        )

    if item_id in HP_POTION_HEAL:
        if not await cog.bot.db.consume_inventory_item(user_id, guild_id, item_id):
            return "consume_failed", None
        max_hp = await player_max_hp(cog, user_id, guild_id)
        heal_amt = float(HP_POTION_HEAL[item_id])
        new_hp, _ = await cog.bot.db.heal_player(user_id, guild_id, heal_amt, max_hp)
        return None, (
            f"**{shop_item.name}** — restored HP to **{int(new_hp)}/{int(max_hp)}**."
        )

    if item_id == "sakunas_finger":
        if not await cog.bot.db.consume_inventory_item(user_id, guild_id, item_id):
            return "consume_failed", None
        expires = await cog.bot.db.set_active_sakuna_buff(user_id, guild_id)
        chance_pct = int(round(config.SAKUNAS_FINGER_DEFLECT_CHANCE * 100))
        return None, (
            f"**{shop_item.name}** — domain ward active until "
            f"<t:{int(expires)}:R>. **{chance_pct}%** chance to deflect incoming duel attacks."
        )

    if item_id in {"jail_key", "pick_key"}:
        if not await cog.bot.db.is_arrested(user_id, guild_id):
            return "not_jailed", None
        if not await cog.bot.db.consume_inventory_item(user_id, guild_id, item_id):
            return "consume_failed", None
        if item_id == "jail_key":
            await cog.bot.db.clear_arrested(user_id, guild_id)
            return None, f"**{shop_item.name}** — the cell door swings open. You are free!"
        if random.random() < config.PICK_KEY_ESCAPE_CHANCE:
            await cog.bot.db.clear_arrested(user_id, guild_id)
            return None, f"**{shop_item.name}** — the lock clicks. You slip out into the night!"
        return None, f"**{shop_item.name}** — the pick snaps. Guards drag you back to your cell."

    if not await cog.bot.db.consume_inventory_item(user_id, guild_id, item_id):
        return "consume_failed", None
    await cog.bot.db.set_pending_consumable(user_id, guild_id, item_id)
    hint = {
        "raid_potion": "Next **/attack** deals +20% boss damage.",
    }.get(item_id, "Buff active.")
    return None, f"Used **{shop_item.name}**. {hint} (5 min window)"


def use_error_message(code: str | None) -> str:
    return {
        "invalid_item": "That item cannot be used.",
        "insufficient_items": "You do not have that item.",
        "insufficient_product": "You don't have that product in your stash.",
        "not_jailed": "You are not in jail — save keys for when you get arrested.",
        "no_equipped_companion": "Equip a henchling with `/companion equip` first, or use `/companion feed`.",
        "consume_failed": "Could not consume item.",
        "invalid_drug": "Unknown product.",
    }.get(code or "", "Could not use that item.")


async def build_use_embed(cog: commands.Cog, user_id: int, guild_id: int) -> discord.Embed:
    entries = await list_useable_entries(cog, user_id, guild_id)
    embed = discord.Embed(
        title="💊 Use consumables",
        description="Select an item or drug product below to use its effect.",
        color=discord.Color.blurple(),
    )
    if entries:
        lines = [f"**{label}** ×{qty}" for _, label, qty in entries[:20]]
        if len(entries) > 20:
            lines.append(f"_…and {len(entries) - 20} more in the dropdown_")
        embed.add_field(name="Available", value="\n".join(lines), inline=False)
    else:
        embed.add_field(
            name="Available",
            value="_Nothing to use — buy consumables from `/shop` or harvest from `/drugs lab`._",
            inline=False,
        )
    try:
        pending = await cog.bot.db.peek_pending_drug_buff(user_id, guild_id)
    except Exception:
        logger.exception("Failed to read pending drug buff for /use panel")
        pending = None
    if pending:
        embed.add_field(
            name="Active drug high",
            value=f"**{pending['name']}** — expires <t:{int(float(pending['expires']))}:R>",
            inline=False,
        )
    try:
        sakuna = await cog.bot.db.peek_active_sakuna_buff(user_id, guild_id)
    except Exception:
        logger.exception("Failed to read Sakuna buff for /use panel")
        sakuna = None
    if sakuna:
        chance_pct = int(round(config.SAKUNAS_FINGER_DEFLECT_CHANCE * 100))
        embed.add_field(
            name="Sakuna's Finger ward",
            value=(
                f"**{chance_pct}%** deflect chance on incoming duels — expires "
                f"<t:{int(float(sakuna['expires']))}:R>"
            ),
            inline=False,
        )
    embed.set_footer(text="Raid potions buff your next boss attack · drugs have timed effects")
    return embed


class UseItemSelect(discord.ui.Select):
    def __init__(self, view: "UsePanelView", entries: list[tuple[str, str, int]]) -> None:
        self._view = view
        if entries:
            options = [
                discord.SelectOption(
                    label=f"{label} ×{qty}"[:100],
                    value=entry_id,
                    description="Use one"[:100],
                )
                for entry_id, label, qty in entries[:25]
            ]
            disabled = False
        else:
            options = [discord.SelectOption(label="Nothing to use", value="_none")]
            disabled = True
        super().__init__(
            placeholder="Choose something to use…",
            min_values=1,
            max_values=1,
            options=options,
            disabled=disabled,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "_none":
            await interaction.response.send_message("Nothing to use.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        err, message = await execute_use(
            self._view.cog,
            self._view.user_id,
            self._view.guild_id,
            self.values[0],
        )
        if err:
            await interaction.followup.send(use_error_message(err), ephemeral=True)
            return
        view = await UsePanelView.build(
            self._view.cog, self._view.guild_id, self._view.user_id,
        )
        embed = await build_use_embed(self._view.cog, self._view.user_id, self._view.guild_id)
        embed.description = message
        await interaction.edit_original_response(embed=embed, view=view)


class UseRefreshButton(discord.ui.Button):
    def __init__(self, view: "UsePanelView") -> None:
        super().__init__(label="Refresh", style=discord.ButtonStyle.secondary, row=1)
        self._view = view

    async def callback(self, interaction: discord.Interaction) -> None:
        view = await UsePanelView.build(
            self._view.cog, self._view.guild_id, self._view.user_id,
        )
        embed = await build_use_embed(self._view.cog, self._view.user_id, self._view.guild_id)
        await interaction.response.edit_message(embed=embed, view=view)


class UsePanelView(discord.ui.View):
    def __init__(self, cog: commands.Cog, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=180.0)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id

    @classmethod
    async def build(cls, cog: commands.Cog, guild_id: int, user_id: int) -> UsePanelView:
        view = cls(cog, guild_id, user_id)
        entries = await list_useable_entries(cog, user_id, guild_id)
        view.add_item(UseItemSelect(view, entries))
        view.add_item(UseRefreshButton(view))
        return view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This panel is not yours.", ephemeral=True)
            return False
        return True


async def send_use_panel(interaction: discord.Interaction, cog: commands.Cog) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message("Guild only.", ephemeral=True)
        return
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    try:
        embed = await build_use_embed(cog, interaction.user.id, interaction.guild_id)
        view = await UsePanelView.build(cog, interaction.guild_id, interaction.user.id)
        await interaction.edit_original_response(embed=embed, view=view)
    except Exception:
        logger.exception("Failed to open /use panel for user %s", interaction.user.id)
        if interaction.response.is_done():
            await interaction.followup.send(
                "Could not open the use panel. Try again in a moment.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Could not open the use panel. Try again in a moment.",
                ephemeral=True,
            )
