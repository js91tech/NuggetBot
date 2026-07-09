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
)

RECIPE_MAP = {r.recipe_id: r for r in RECIPES}
