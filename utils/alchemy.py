from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AlchemyRecipe:
    recipe_id: str
    name: str
    output_item_id: str
    scrap_cost: int
    nugget_cost: float
    description: str
    blueprint_id: str | None = None
    essence_cost: int = 0
    resin_cost: int = 0
    waste_cost: int = 0


RECIPES: tuple[AlchemyRecipe, ...] = (
    AlchemyRecipe(
        "raid_potion",
        "Raid Potion",
        "raid_potion",
        2,
        150.0,
        "Next boss hit +20% damage.",
    ),
    AlchemyRecipe(
        "energy_drink",
        "Energy Drink",
        "energy_drink",
        2,
        120.0,
        "Instant +15 job energy.",
    ),
    AlchemyRecipe(
        "trap_bomb",
        "Trap Bomb",
        "trap_bomb",
        1,
        250.0,
        "Duel trap consumable.",
    ),
    AlchemyRecipe(
        "flask_enrage",
        "Flask of Enrage",
        "flask_enrage",
        3,
        400.0,
        "High-risk raid flask.",
        blueprint_id="bp_flask_enrage",
        essence_cost=1,
    ),
    AlchemyRecipe(
        "smugglers_satchel",
        "Smuggler's Satchel",
        "smugglers_satchel",
        2,
        600.0,
        "Risk-free street sell.",
        blueprint_id="bp_smuggler_satchel",
        resin_cost=1,
    ),
    AlchemyRecipe(
        "duelist_antidote",
        "Duelist's Antidote",
        "duelist_antidote",
        2,
        500.0,
        "Cleanse duel debuffs.",
        blueprint_id="bp_duelist_antidote",
    ),
    AlchemyRecipe(
        "raid_elixir",
        "Raid Elixir",
        "raid_elixir",
        4,
        800.0,
        "Strong raid elixir.",
        blueprint_id="bp_raid_elixir",
        essence_cost=2,
    ),
    AlchemyRecipe(
        "energy_surge",
        "Energy Surge",
        "energy_surge",
        3,
        350.0,
        "+30 job energy.",
        blueprint_id="bp_energy_surge",
        waste_cost=1,
    ),
    AlchemyRecipe(
        "trap_cluster",
        "Trap Cluster",
        "trap_cluster",
        3,
        700.0,
        "Double trap proc chance.",
        blueprint_id="bp_trap_cluster",
        essence_cost=1,
    ),
)

RECIPE_MAP = {r.recipe_id: r for r in RECIPES}


def recipe_available(recipe: AlchemyRecipe, unlocked_blueprints: set[str]) -> bool:
    if recipe.blueprint_id is None:
        return True
    return recipe.blueprint_id in unlocked_blueprints
