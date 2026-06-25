from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import config

if TYPE_CHECKING:
    from database import Database


@dataclass(frozen=True)
class Achievement:
    id: str
    name: str
    description: str
    emoji: str = "🏅"


ACHIEVEMENTS: dict[str, Achievement] = {
    "first_blood": Achievement("first_blood", "First Blood", "Help defeat your first boss.", "🩸"),
    "raid_veteran": Achievement("raid_veteran", "Raid Veteran", "Help defeat 25 bosses.", "⚔️"),
    "mythic_slayer": Achievement("mythic_slayer", "Mythic Slayer", "Help defeat a mythic Hannah.", "🌌"),
    "heist_king": Achievement("heist_king", "Heist King", "Succeed at 10 heists.", "🎭"),
    "field_medic": Achievement("field_medic", "Field Medic", "Revive 25 downed raiders.", "💊"),
    "wealthy": Achievement("wealthy", "Nugget Baron", "Hold 200,000 nuggets at once.", "💰"),
    "excalibur_owner": Achievement(
        "excalibur_owner",
        "Excalibur Bearer",
        "Own a Nugget Excalibur.",
        "👑",
    ),
    "master_crafter": Achievement("master_crafter", "Master Crafter", "Upgrade battle-worn gear once.", "🔨"),
    "prestige_1": Achievement("prestige_1", "Reborn", "Prestige once.", "♻️"),
    "prestige_5": Achievement("prestige_5", "Ascendant", "Reach prestige 5.", "✨"),
    "hundred_raids": Achievement("hundred_raids", "Raid Legend", "Help defeat 100 bosses.", "🏆"),
    "duel_master": Achievement("duel_master", "Duel Master", "Win 10 PvP duels.", "🥊"),
    "dungeon_delver": Achievement("dungeon_delver", "Dungeon Delver", "Clear 5 dungeons.", "🗝️"),
    "high_roller": Achievement("high_roller", "High Roller", "Win 20 casino games.", "🎰"),
    "territory_claimed": Achievement(
        "territory_claimed", "Landlord", "Claim or capture a territory.", "🏴",
    ),
    "siege_victor": Achievement(
        "siege_victor", "Conqueror", "Win 5 territory sieges.", "⚔️",
    ),
    "crew_territory_barons": Achievement(
        "crew_territory_barons",
        "Territory Barons",
        "Your crew holds 3 zones at once.",
        "👑",
    ),
    "first_harvest": Achievement("first_harvest", "First Harvest", "Harvest your first lab crop.", "🌿"),
    "corporation_owner": Achievement(
        "corporation_owner", "Corporation Owner", "Reach the Corporation business tier.", "🏢",
    ),
    "cartel_king": Achievement(
        "cartel_king", "Cartel King", "Reach dealer rank 10.", "🕶️",
    ),
    "district_dominator": Achievement(
        "district_dominator", "District Dominator", "Reach 100 district influence.", "🗺️",
    ),
}


async def evaluate_unlocks(
    db: Database,
    guild_id: int,
    user_id: int,
    *,
    wallet: float | None = None,
) -> list[Achievement]:
    progress = await db.get_user_progress(user_id, guild_id)
    unlocked = await db.list_achievements(user_id, guild_id)
    newly: list[Achievement] = []

    async def grant(achievement_id: str) -> None:
        if achievement_id in unlocked:
            return
        if await db.unlock_achievement(user_id, guild_id, achievement_id):
            achievement = ACHIEVEMENTS[achievement_id]
            newly.append(achievement)
            unlocked.add(achievement_id)

    bosses = int(progress["bosses_killed"])
    if bosses >= 1:
        await grant("first_blood")
    if bosses >= 25:
        await grant("raid_veteran")
    if bosses >= 100:
        await grant("hundred_raids")
    if int(progress["mythic_kills"]) >= 1:
        await grant("mythic_slayer")
    if int(progress["heists_won"]) >= 10:
        await grant("heist_king")
    if int(progress["heals_given"]) >= 25:
        await grant("field_medic")
    if int(progress["crafts_done"]) >= 1:
        await grant("master_crafter")
    prestige = int(progress["prestige_level"])
    if prestige >= 1:
        await grant("prestige_1")
    if prestige >= 5:
        await grant("prestige_5")

    if wallet is None:
        wallet = await db.get_balance(user_id, guild_id)
    if wallet >= 200_000:
        await grant("wealthy")

    rows = await db.get_inventory(user_id, guild_id)
    for row in rows:
        if str(row["item_id"]) == "nugget_excalibur" and int(row["quantity"]) > 0:
            await grant("excalibur_owner")
            break

    try:
        duel_wins = int(progress["duel_wins"])
    except (KeyError, TypeError):
        duel_wins = 0
    try:
        dungeons_cleared = int(progress["dungeons_cleared"])
    except (KeyError, TypeError):
        dungeons_cleared = 0
    try:
        gambles_won = int(progress["gambles_won"])
    except (KeyError, TypeError):
        gambles_won = 0
    if duel_wins >= 10:
        await grant("duel_master")
    if dungeons_cleared >= 5:
        await grant("dungeon_delver")
    if gambles_won >= 20:
        await grant("high_roller")

    try:
        territories_claimed = int(progress["territories_claimed"])
    except (KeyError, TypeError):
        territories_claimed = 0
    try:
        sieges_won = int(progress["sieges_won"])
    except (KeyError, TypeError):
        sieges_won = 0
    if territories_claimed >= 1:
        await grant("territory_claimed")
    if sieges_won >= 5:
        await grant("siege_victor")

    crew_name = await db.get_crew_membership(user_id, guild_id)
    if crew_name is not None:
        held = await db.count_crew_territories(guild_id, crew_name)
        if held >= config.TERRITORY_MAX_HELD_PER_CREW:
            await grant("crew_territory_barons")

    biz = await db.get_business(user_id, guild_id)
    if biz is not None and int(biz["tier"]) >= 7:
        await grant("corporation_owner")

    drug_stats = await db.get_drug_stats(user_id, guild_id)
    from utils.dealer_ranks import dealer_rank

    if dealer_rank(drug_stats["units_sold"]) >= config.DEALER_RANK_CARTEL_TITLE:
        await grant("cartel_king")

    total_influence = 0.0
    from utils.districts import DISTRICT_MAP

    for district_id in DISTRICT_MAP:
        inf = await db.get_user_district_influence(user_id, guild_id, district_id)
        total_influence += inf
    if total_influence >= config.BUSINESS_DISTRICT_INFLUENCE_MAX:
        await grant("district_dominator")

    return newly


def format_unlock_message(achievements: list[Achievement]) -> str:
    if not achievements:
        return ""
    parts = [f"{a.emoji} **{a.name}** — {a.description}" for a in achievements]
    return "Achievement unlocked!\n" + "\n".join(parts)
