from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ShopItem:
    id: str
    name: str
    category: str
    price: float
    power: int
    description: str
    verbs: tuple[str, ...] = ()
    crit_chance: float = 0.0
    hp_bonus: int = 0


STARTER_WEAPON = ShopItem(
    "training_stick",
    "Training Stick",
    "weapon",
    0,
    6,
    "A free launch gift. Weaker than the cheapest shop weapon.",
    ("whacks", "taps"),
)
STARTER_ARMOR = ShopItem(
    "cardboard_shield",
    "Cardboard Shield",
    "armor",
    0,
    4,
    "A free launch gift. Weaker than the cheapest shop armor.",
    hp_bonus=12,
)

WEAPONS: tuple[ShopItem, ...] = (
    ShopItem("twig_sword", "Twig Sword", "weapon", 250, 12, "A starter blade with splinters.", ("pokes", "swats")),
    ShopItem("rusty_dagger", "Rusty Dagger", "weapon", 750, 43, "Fast, cheap, and suspicious.", ("stabs", "jabs"), crit_chance=0.02),
    ShopItem("iron_sword", "Iron Sword", "weapon", 1_800, 75, "Reliable boss-fighting steel.", ("slashes", "cleaves"), crit_chance=0.04),
    ShopItem("ember_axe", "Ember Axe", "weapon", 4_000, 106, "Hot enough to leave a mark.", ("chops", "scorches"), crit_chance=0.05),
    ShopItem("storm_spear", "Storm Spear", "weapon", 8_500, 137, "Crackles with static.", ("skewers", "thunders into"), crit_chance=0.07),
    ShopItem("void_blade", "Void Blade", "weapon", 16_000, 169, "Cuts where armor forgets to exist.", ("rifts", "carves"), crit_chance=0.09),
    ShopItem("sunhammer", "Sunhammer", "weapon", 30_000, 200, "Heavy enough to change the weather.", ("smashes", "craters"), crit_chance=0.11),
    ShopItem("dragon_lance", "Dragon Lance", "weapon", 52_000, 232, "Built for impossible raids.", ("impales", "pierces"), crit_chance=0.13),
    ShopItem(
        "cosmic_greatsword",
        "Cosmic Greatsword",
        "weapon",
        82_000,
        263,
        "A galaxy with a handle.",
        ("cleaves", "star-slashes"),
        crit_chance=0.14,
    ),
    ShopItem(
        "nugget_excalibur",
        "Nugget Excalibur",
        "weapon",
        120_000,
        295,
        "The endgame flex.",
        ("obliterates", "royally slashes"),
        crit_chance=0.16,
    ),
)

ARMOR: tuple[ShopItem, ...] = (
    ShopItem("paper_hat", "Paper Hat", "armor", 250, 8, "Technically protection.", hp_bonus=18),
    ShopItem("padded_hoodie", "Padded Hoodie", "armor", 750, 29, "Comfortable and mildly sturdy.", hp_bonus=54),
    ShopItem("bronze_vest", "Bronze Vest", "armor", 1_800, 51, "Entry-level raid gear.", hp_bonus=90),
    ShopItem("iron_plate", "Iron Plate", "armor", 4_000, 72, "Classic clanking defense.", hp_bonus=127),
    ShopItem("ember_mail", "Ember Mail", "armor", 8_500, 93, "Warm, dramatic, defensive.", hp_bonus=163),
    ShopItem("stormguard", "Stormguard", "armor", 16_000, 115, "Turns shocks into shrugs.", hp_bonus=199),
    ShopItem("void_ward", "Void Ward", "armor", 30_000, 136, "Makes danger miss its appointment.", hp_bonus=236),
    ShopItem("dragon_scale", "Dragon Scale", "armor", 52_000, 157, "Premium monster-proofing.", hp_bonus=272),
    ShopItem("celestial_aegis", "Celestial Aegis", "armor", 82_000, 179, "A wearable constellation.", hp_bonus=309),
    ShopItem(
        "nugget_immortal_plate",
        "Nugget Immortal Plate",
        "armor",
        120_000,
        200,
        "Endgame armor for dedicated grinders.",
        hp_bonus=345,
    ),
)

GRANT_ITEMS: tuple[ShopItem, ...] = (STARTER_WEAPON, STARTER_ARMOR)
ITEMS: dict[str, ShopItem] = {item.id: item for item in (*GRANT_ITEMS, *WEAPONS, *ARMOR)}
ITEM_ORDER: tuple[str, ...] = tuple(item.id for item in (*WEAPONS, *ARMOR))
CATEGORIES = ("all", "weapon", "armor")


def get_item(item_id: str) -> ShopItem | None:
    return ITEMS.get(item_id)


def armor_mitigation_percent(power: int) -> int:
    return int(round(100 * power / (power + 100)))


def items_for_category(category: str) -> list[ShopItem]:
    normalized = category.lower()
    if normalized == "all":
        return [ITEMS[item_id] for item_id in ITEM_ORDER]
    if normalized not in CATEGORIES:
        return []
    return [item for item in ITEMS.values() if item.category == normalized]
