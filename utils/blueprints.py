from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BlueprintDefinition:
    blueprint_id: str
    name: str
    description: str
    category: str
    unlock_hint: str


BLUEPRINT_DEFINITIONS: dict[str, BlueprintDefinition] = {
    # Alchemy
    "bp_flask_enrage": BlueprintDefinition(
        "bp_flask_enrage", "Flask of Enrage", "Craft high-risk raid flasks.", "alchemy",
        "Clear Delver's Depths",
    ),
    "bp_smuggler_satchel": BlueprintDefinition(
        "bp_smuggler_satchel", "Smuggler's Satchel", "Risk-free street sales.", "alchemy",
        "Reach dealer rank 5",
    ),
    "bp_duelist_antidote": BlueprintDefinition(
        "bp_duelist_antidote", "Duelist's Antidote", "Cleanse duel debuffs.", "alchemy",
        "Win 5 duels",
    ),
    "bp_raid_elixir": BlueprintDefinition(
        "bp_raid_elixir", "Raid Elixir", "Stronger raid potion recipe.", "alchemy",
        "Help defeat a mythic boss",
    ),
    "bp_energy_surge": BlueprintDefinition(
        "bp_energy_surge", "Energy Surge", "Big energy restoration drink.", "alchemy",
        "Complete 10 job shifts",
    ),
    "bp_trap_cluster": BlueprintDefinition(
        "bp_trap_cluster", "Trap Cluster", "Multi-trap duel bombs.", "alchemy",
        "Clear Gilded Vault",
    ),
    # Business
    "bp_auto_marketing": BlueprintDefinition(
        "bp_auto_marketing", "Auto-Marketing Module", "Passive business reputation.", "business",
        "Own a Corporation-tier business",
    ),
    "bp_smuggler_route": BlueprintDefinition(
        "bp_smuggler_route", "Smuggler Route", "Drug lab efficiency boost.", "business",
        "Dealer rank 7+",
    ),
    "bp_security_drone": BlueprintDefinition(
        "bp_security_drone", "Security Drone", "Stronger business defense window.", "business",
        "Defend a business attack",
    ),
    # Heist
    "bp_heist_lockpick": BlueprintDefinition(
        "bp_heist_lockpick", "Master Lockpick", "Improved wallet heist odds.", "heist",
        "Succeed at 3 heists",
    ),
    "bp_heist_jammer": BlueprintDefinition(
        "bp_heist_jammer", "Signal Jammer", "Bank heist tier-1 boost.", "heist",
        "Complete a bank heist",
    ),
    "bp_insider_tip": BlueprintDefinition(
        "bp_insider_tip", "Insider Tip", "Reduced bank heist detection.", "heist",
        "Win a territory siege",
    ),
    # Combat
    "bp_affix_reroll": BlueprintDefinition(
        "bp_affix_reroll", "Affix Reroll Kit", "Reroll one gear affix.", "combat",
        "Clear Gilded Vault 3 times",
    ),
    "bp_relic_polish": BlueprintDefinition(
        "bp_relic_polish", "Relic Polish", "Duplicate relic converts to scrap.", "combat",
        "Own any relic",
    ),
    "bp_glyph_socket": BlueprintDefinition(
        "bp_glyph_socket", "Glyph Socket", "Unlock TET+ affix slot.", "combat",
        "Enhance gear to TET",
    ),
}

ALCHEMY_BLUEPRINT_MAP: dict[str, str] = {
    "bp_flask_enrage": "flask_enrage",
    "bp_smuggler_satchel": "smugglers_satchel",
    "bp_duelist_antidote": "duelist_antidote",
    "bp_raid_elixir": "raid_elixir",
    "bp_energy_surge": "energy_surge",
    "bp_trap_cluster": "trap_cluster",
}

EVENT_BLUEPRINT_UNLOCKS: dict[str, str] = {
    "dungeon_clear": "bp_flask_enrage",
    "dungeon_vault_clear": "bp_trap_cluster",
    "boss_mythic_kill": "bp_raid_elixir",
    "duel_win_5": "bp_duelist_antidote",
    "heist_success_3": "bp_heist_lockpick",
    "bank_heist_success": "bp_heist_jammer",
    "territory_siege_win": "bp_insider_tip",
    "corporation_tier": "bp_auto_marketing",
    "dealer_rank_7": "bp_smuggler_route",
    "business_defend": "bp_security_drone",
    "job_shifts_10": "bp_energy_surge",
    "vault_clear_3": "bp_affix_reroll",
    "relic_owned": "bp_relic_polish",
    "enhance_tet": "bp_glyph_socket",
}


def blueprint_by_id(blueprint_id: str) -> BlueprintDefinition | None:
    return BLUEPRINT_DEFINITIONS.get(blueprint_id)
