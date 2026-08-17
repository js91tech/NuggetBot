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
    shop_listed: bool = True
    flat_damage: int = 0
    flat_hp: int = 0
    flat_crit: float = 0.0
    flat_mitigation: float = 0.0


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
        "Goon Excalibur",
        "weapon",
        120_000,
        295,
        "The endgame flex.",
        ("obliterates", "royally slashes"),
        crit_chance=0.16,
    ),
    ShopItem(
        "mythic_voidreaver",
        "Mythic Voidreaver",
        "weapon",
        175_000,
        328,
        "Forged past the shop ceiling for raid veterans.",
        ("void-renders", "annihilates"),
        crit_chance=0.18,
    ),
    ShopItem(
        "apex_nuggetblade",
        "Apex Goonblade",
        "weapon",
        500_000,
        344,
        "Five percent past mythic — the first true apex grind.",
        ("apex-cleaves", "overkills"),
        crit_chance=0.19,
    ),
    ShopItem(
        "sovereign_cleaver",
        "Sovereign Cleaver",
        "weapon",
        750_000,
        361,
        "Ten percent beyond voidreaver. Rule the duel pit.",
        ("sovereign-rends", "executes"),
        crit_chance=0.20,
    ),
    ShopItem(
        "transcendent_worldsplitter",
        "Transcendent Worldsplitter",
        "weapon",
        1_500_000,
        377,
        "Fifteen percent above mythic — a pre-apex milestone blade.",
        ("world-splits", "transcends"),
        crit_chance=0.21,
    ),
    ShopItem(
        "dominion_worldbreaker",
        "Dominion Worldbreaker",
        "weapon",
        15_000_000,
        404,
        "Twenty-three percent past mythic — raw power for endgame grinders.",
        ("dominion-splits", "worldbreaks"),
        crit_chance=0.22,
    ),
    ShopItem(
        "reaper_fang",
        "Reaper Fang",
        "weapon",
        15_000_000,
        379,
        "Crit-focused apex steel — lethal precision with sharpened base damage.",
        ("reaper-strikes", "execution-cuts"),
        crit_chance=0.31,
    ),
    ShopItem(
        "paragon_edge",
        "Paragon Edge",
        "weapon",
        25_000_000,
        445,
        "Ten percent past Dominion — the 25M shop ceiling breaker.",
        ("paragon-cleaves", "eclipses"),
        crit_chance=0.23,
    ),
    ShopItem(
        "eternal_worldcleaver",
        "Eternal Worldcleaver",
        "weapon",
        50_000_000,
        534,
        "Twenty percent beyond Paragon — the 50M eternal grind.",
        ("eternal-rends", "worldcleaves"),
        crit_chance=0.24,
    ),
)

GUNS: tuple[ShopItem, ...] = (
    ShopItem(
        "cap_gun",
        "Cap Gun",
        "gun",
        280,
        12,
        "Fires disappointment at point-blank range.",
        ("pops", "pegs"),
        crit_chance=0.03,
    ),
    ShopItem(
        "rust_revolver",
        "Rust Revolver",
        "gun",
        800,
        43,
        "Six chambers of tetanus.",
        ("blasts", "tags"),
        crit_chance=0.04,
    ),
    ShopItem(
        "iron_pistol",
        "Iron Pistol",
        "gun",
        1_900,
        75,
        "Reliable sidearm for raid night.",
        ("shoots", "drills"),
        crit_chance=0.05,
    ),
    ShopItem(
        "flare_pistol",
        "Flare Pistol",
        "gun",
        4_200,
        106,
        "Incendiary rounds with dramatic flair.",
        ("ignites", "flares into"),
        crit_chance=0.06,
    ),
    ShopItem(
        "storm_rifle",
        "Storm Rifle",
        "gun",
        9_000,
        137,
        "Full-auto thunder in a metal tube.",
        ("strafes", "volleys"),
        crit_chance=0.08,
    ),
    ShopItem(
        "void_carbine",
        "Void Carbine",
        "gun",
        17_000,
        169,
        "Bullets that forget where armor ends.",
        ("void-shots", "hollows"),
        crit_chance=0.10,
    ),
    ShopItem(
        "sunshot_rifle",
        "Sunshot Rifle",
        "gun",
        32_000,
        200,
        "Long-range solar punishment.",
        ("snipes", "solar-bores through"),
        crit_chance=0.12,
    ),
    ShopItem(
        "dragon_shotgun",
        "Dragon Shotgun",
        "gun",
        54_000,
        232,
        "Spread pattern: entire dragon.",
        ("buckshots", "shreds"),
        crit_chance=0.14,
    ),
    ShopItem(
        "cosmic_railgun",
        "Cosmic Railgun",
        "gun",
        85_000,
        263,
        "One shot, one constellation.",
        ("rails", "star-pierces"),
        crit_chance=0.15,
    ),
    ShopItem(
        "nugget_minigun",
        "Goon Minigun",
        "gun",
        125_000,
        295,
        "BRRRRT currency.",
        ("shreds", "minces"),
        crit_chance=0.17,
    ),
    ShopItem(
        "mythic_annihilator",
        "Mythic Annihilator",
        "gun",
        180_000,
        328,
        "Deletes the concept of cover.",
        ("annihilates", "unmakes"),
        crit_chance=0.19,
    ),
    ShopItem(
        "apex_annihilator",
        "Apex Annihilator",
        "gun",
        500_000,
        344,
        "Five percent past mythic — the first true apex sidearm.",
        ("apex-bursts", "overkills"),
        crit_chance=0.19,
    ),
    ShopItem(
        "sovereign_railcannon",
        "Sovereign Railcannon",
        "gun",
        750_000,
        361,
        "Ten percent beyond voidreaver. Rule the duel pit from range.",
        ("sovereign-rails", "executes"),
        crit_chance=0.20,
    ),
    ShopItem(
        "transcendent_voidlance",
        "Transcendent Voidlance",
        "gun",
        1_500_000,
        377,
        "Fifteen percent above mythic — a pre-apex ranged milestone.",
        ("void-lances", "transcends"),
        crit_chance=0.21,
    ),
    ShopItem(
        "dominion_devastator",
        "Dominion Devastator",
        "gun",
        15_000_000,
        404,
        "Twenty-three percent past mythic — maximum ranged base damage.",
        ("dominion-barrages", "devastates"),
        crit_chance=0.22,
    ),
    ShopItem(
        "reaper_crossbow",
        "Reaper Crossbow",
        "gun",
        15_000_000,
        379,
        "Crit-focused apex ordnance — lethal precision with sharpened base damage.",
        ("reaper-bolts", "execution-shots"),
        crit_chance=0.31,
    ),
    ShopItem(
        "paragon_repeater",
        "Paragon Repeater",
        "gun",
        25_000_000,
        445,
        "Ten percent past Dominion — matched 25M ranged firepower.",
        ("paragon-bursts", "eclipses"),
        crit_chance=0.23,
    ),
    ShopItem(
        "eternal_obliteratrix",
        "Eternal Obliteratrix",
        "gun",
        50_000_000,
        534,
        "Twenty percent beyond Paragon — the 50M eternal barrage.",
        ("eternal-obliterates", "unmakes"),
        crit_chance=0.24,
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
        "Goon Immortal Plate",
        "armor",
        120_000,
        200,
        "Endgame armor for dedicated grinders.",
        hp_bonus=345,
    ),
    ShopItem(
        "mythic_aetherplate",
        "Mythic Aetherplate",
        "armor",
        175_000,
        220,
        "Reality-bent plating for players who outgrew the shop.",
        hp_bonus=385,
    ),
    ShopItem(
        "apex_aegis",
        "Apex Aegis",
        "armor",
        500_000,
        231,
        "Matched plating for the Apex Goonblade set.",
        hp_bonus=404,
    ),
    ShopItem(
        "sovereign_bastion",
        "Sovereign Bastion",
        "armor",
        750_000,
        242,
        "Sovereign-tier defense for endgame collectors.",
        hp_bonus=424,
    ),
    ShopItem(
        "transcendent_carapace",
        "Transcendent Carapace",
        "armor",
        1_500_000,
        253,
        "Pre-apex shop armor — pairs with Worldsplitter.",
        hp_bonus=443,
    ),
    ShopItem(
        "apotheosis_carapace",
        "Apotheosis Carapace",
        "armor",
        10_000_000,
        271,
        "Seven percent beyond Transcendent — Dominion-set plating.",
        hp_bonus=474,
    ),
    ShopItem(
        "paragon_aegis",
        "Paragon Aegis",
        "armor",
        15_000_000,
        298,
        "Ten percent past Apotheosis — matched 25M Paragon plating.",
        hp_bonus=521,
    ),
    ShopItem(
        "eternal_bastion",
        "Eternal Bastion",
        "armor",
        30_000_000,
        358,
        "Twenty percent beyond Paragon — the 50M eternal shell.",
        hp_bonus=625,
    ),
)

BOSS_SLAYER_BLADE = ShopItem(
    "boss_slayer_blade",
    "Heartsplitter Fang",
    "weapon",
    6_500,
    118,
    "Boss-forged steel pulsing with leftover raid energy.",
    ("rends", "finishes"),
    crit_chance=0.095,
    shop_listed=False,
)
BOSS_SLAYER_MAIL = ShopItem(
    "boss_slayer_mail",
    "Trophy Bastion Mail",
    "armor",
    6_500,
    84,
    "Plates tempered in Velvet Vixen's defeat — prized raid salvage.",
    hp_bonus=148,
    shop_listed=False,
)
MYTHIC_RAID_BLADE = ShopItem(
    "mythic_raid_blade",
    "Velvet's Shattered Fang",
    "weapon",
    0,
    142,
    "Ultra-rare mythic boss drop — stronger than Heartsplitter Fang.",
    ("shatters", "eclipses"),
    crit_chance=0.11,
    shop_listed=False,
)
MYTHIC_RAID_MAIL = ShopItem(
    "mythic_raid_mail",
    "Velvet's Aegis Fragment",
    "armor",
    0,
    98,
    "Ultra-rare mythic boss drop — endgame raid trophy armor.",
    hp_bonus=178,
    shop_listed=False,
)

TRAP_BOMB = ShopItem(
    "trap_bomb",
    "Trap Bomb",
    "consumable",
    500,
    0,
    "Stacks in inventory. Detonates for 75–125 true damage (ignores mitigation) when a duelist hits you.",
    shop_listed=True,
)
SAKUNAS_FINGER = ShopItem(
    "sakunas_finger",
    "Sakuna's Finger",
    "consumable",
    7_500_000,
    0,
    (
        "Use with /use: for 6 hours, 75% chance to deflect incoming /duel attacks. "
        "On proc you auto-win and steal 5% of the attacker's wallet and 7% of their bank. "
        "Does not work in crew raids."
    ),
    shop_listed=True,
)
RAID_POTION = ShopItem(
    "raid_potion",
    "Raid Potion",
    "consumable",
    350,
    0,
    "Use before /attack: next boss hit deals +20% damage (one fight).",
    shop_listed=True,
)
HP_POTION_SMALL = ShopItem(
    "hp_potion_small",
    "Small HP Potion",
    "consumable",
    2500,
    0,
    "Auto-heals 40 HP during boss raids when your HP drops low.",
    shop_listed=True,
)
HP_POTION_MEDIUM = ShopItem(
    "hp_potion_medium",
    "Medium HP Potion",
    "consumable",
    3500,
    0,
    "Auto-heals 75 HP during boss raids when your HP drops low.",
    shop_listed=True,
)
HP_POTION_LARGE = ShopItem(
    "hp_potion_large",
    "Large HP Potion",
    "consumable",
    5500,
    0,
    "Auto-heals 100 HP during boss raids when your HP drops low.",
    shop_listed=True,
)
HP_POTION_XXL = ShopItem(
    "hp_potion_xxl",
    "XXL HP Potion",
    "consumable",
    6000,
    0,
    "Auto-heals 125 HP during boss raids when your HP drops low.",
    shop_listed=True,
)
ENERGY_DRINK = ShopItem(
    "energy_drink",
    "Energy Drink",
    "consumable",
    400,
    0,
    "Use with /use: restores 15 job energy instantly.",
    shop_listed=True,
)
CHIA_SEEDS = ShopItem(
    "chia_seeds",
    "Chia Seeds",
    "consumable",
    50,
    0,
    "Wholesome snack seeds. Buy from /shop, then gift with /gift-item.",
    shop_listed=True,
)
JAIL_KEY = ShopItem(
    "jail_key",
    "Jail Key",
    "consumable",
    25_000_000,
    0,
    "Use while jailed: guaranteed escape from lockup.",
    shop_listed=True,
)
PICK_KEY = ShopItem(
    "pick_key",
    "Pick Key",
    "consumable",
    4_500_000,
    0,
    "Use while jailed: 15% chance to pick the lock and break out.",
    shop_listed=True,
)

FERTILIZER = ShopItem(
    "fertilizer",
    "Fertilizer",
    "consumable",
    10_000,
    0,
    "Apply in /drugs lab: **1.5×** harvest yield and **25%** shorter grow time (one use per crop).",
    shop_listed=True,
)
XL_FERTILIZER = ShopItem(
    "xl_fertilizer",
    "XL Fertilizer",
    "consumable",
    20_000,
    0,
    "Apply in /drugs lab: **2×** harvest yield and **half** the grow wait (one use per crop).",
    shop_listed=True,
)

ALCHEMY_SCRAP = ShopItem(
    "alchemy_scrap",
    "Alchemy Scrap",
    "consumable",
    0,
    0,
    "Crafting material from dungeons and raid adds. Used for +1–+10 enhancement.",
    shop_listed=False,
)

VOID_HARDENER = ShopItem(
    "void_hardener",
    "Void Hardener",
    "consumable",
    0,
    0,
    "Rare tempering agent for +11–+15 enhancement.",
    shop_listed=False,
)

CELESTIAL_SHARD = ShopItem(
    "celestial_shard",
    "Celestial Shard",
    "consumable",
    0,
    0,
    "Mythic tempering shard for PRI–PENTA enhancement.",
    shop_listed=False,
)

DUNGEON_ESSENCE = ShopItem(
    "dungeon_essence",
    "Dungeon Essence",
    "consumable",
    0,
    0,
    "Alchemy ingredient from delves.",
    shop_listed=False,
)
HARVEST_RESIN = ShopItem(
    "harvest_resin",
    "Harvest Resin",
    "consumable",
    0,
    0,
    "Sticky lab byproduct for advanced recipes.",
    shop_listed=False,
)
BUSINESS_WASTE = ShopItem(
    "business_waste",
    "Business Waste",
    "consumable",
    0,
    0,
    "Recycled empire scrap for alchemy.",
    shop_listed=False,
)
PHENOTYPE_CATALYST = ShopItem(
    "phenotype_catalyst",
    "Phenotype Catalyst",
    "consumable",
    0,
    0,
    "Boosts crossbreeding success.",
    shop_listed=False,
)
FLASK_OF_ENRAGE = ShopItem(
    "flask_enrage",
    "Flask of Enrage",
    "consumable",
    0,
    0,
    "Next boss hit +25% damage but you take +10% counter damage.",
    shop_listed=False,
)
SMUGGLERS_SATCHEL = ShopItem(
    "smugglers_satchel",
    "Smuggler's Satchel",
    "consumable",
    0,
    0,
    "Next street sell has zero raid risk.",
    shop_listed=False,
)
DUELIST_ANTIDOTE = ShopItem(
    "duelist_antidote",
    "Duelist's Antidote",
    "consumable",
    0,
    0,
    "Removes one duel debuff on use.",
    shop_listed=False,
)
RAID_ELIXIR = ShopItem(
    "raid_elixir",
    "Raid Elixir",
    "consumable",
    0,
    0,
    "Next boss hit +35% damage (craft-only).",
    shop_listed=False,
)
ENERGY_SURGE = ShopItem(
    "energy_surge",
    "Energy Surge",
    "consumable",
    0,
    0,
    "Restores 30 job energy instantly.",
    shop_listed=False,
)
TRAP_CLUSTER = ShopItem(
    "trap_cluster",
    "Trap Cluster",
    "consumable",
    0,
    0,
    "Duel trap: 2× proc chance for one fight.",
    shop_listed=False,
)

ACCESSORIES: tuple[ShopItem, ...] = (
    ShopItem(
        "rust_band",
        "Rust Band",
        "accessory",
        0,
        0,
        "A dented ring from a henchman's pocket.",
        flat_damage=3,
        shop_listed=False,
    ),
    ShopItem(
        "storm_loop",
        "Storm Loop",
        "accessory",
        0,
        0,
        "Crackles with leftover raid energy.",
        flat_damage=8,
        flat_crit=0.01,
        shop_listed=False,
    ),
    ShopItem(
        "jester_charm",
        "Jester's Charm",
        "accessory",
        0,
        0,
        "Court of Kitty's token of chaos.",
        flat_damage=5,
        flat_hp=15,
        shop_listed=False,
    ),
    ShopItem(
        "mythic_signet",
        "Mythic Signet",
        "accessory",
        0,
        0,
        "A signet pulsing with mythic residue.",
        flat_damage=15,
        flat_hp=25,
        flat_crit=0.02,
        shop_listed=False,
    ),
    ShopItem(
        "warden_amulet",
        "Warden Amulet",
        "accessory",
        0,
        0,
        "Flat protection from fallen raiders.",
        flat_hp=40,
        flat_mitigation=0.02,
        shop_listed=False,
    ),
    ShopItem(
        "citadel_pendant",
        "Citadel Pendant",
        "accessory",
        0,
        0,
        "Vault-tier accessory for endgame collectors.",
        flat_damage=12,
        flat_hp=35,
        flat_mitigation=0.03,
        shop_listed=False,
    ),
)

BOSS_ACCESSORY_POOL: tuple[str, ...] = tuple(item.id for item in ACCESSORIES)

CONSUMABLES: tuple[ShopItem, ...] = (
    TRAP_BOMB,
    SAKUNAS_FINGER,
    RAID_POTION,
    HP_POTION_SMALL,
    HP_POTION_MEDIUM,
    HP_POTION_LARGE,
    HP_POTION_XXL,
    ENERGY_DRINK,
    CHIA_SEEDS,
    JAIL_KEY,
    PICK_KEY,
    FERTILIZER,
    XL_FERTILIZER,
    ALCHEMY_SCRAP,
    VOID_HARDENER,
    CELESTIAL_SHARD,
    DUNGEON_ESSENCE,
    HARVEST_RESIN,
    BUSINESS_WASTE,
    PHENOTYPE_CATALYST,
    FLASK_OF_ENRAGE,
    SMUGGLERS_SATCHEL,
    DUELIST_ANTIDOTE,
    RAID_ELIXIR,
    ENERGY_SURGE,
    TRAP_CLUSTER,
)

HP_POTION_HEAL: dict[str, int] = {
    "hp_potion_small": 40,
    "hp_potion_medium": 75,
    "hp_potion_large": 100,
    "hp_potion_xxl": 125,
}

HP_POTION_IDS: frozenset[str] = frozenset(HP_POTION_HEAL)

GIFTABLE_ITEM_IDS: frozenset[str] = frozenset({"chia_seeds"})

CONSUMABLE_USE_IDS: frozenset[str] = frozenset(
    item.id for item in CONSUMABLES if item.id != "trap_bomb"
)


def _inferior_boss_drop(base: ShopItem) -> ShopItem:
    """Weaker, sellable variant of shop gear for boss loot."""
    pow_scaled = max(1, int(round(base.power * 0.58)))
    price_scaled = float(max(35, int(round(base.price * 0.28 / 25)) * 25))
    crit = round(base.crit_chance * 0.72, 4)
    hp_b = max(8, int(round(base.hp_bonus * 0.58))) if base.category == "armor" else 0
    desc = f"A battered knockoff of shop-tier gear. {base.description}"
    return ShopItem(
        f"boss_weak_{base.id}",
        f"Battle-Worn {base.name}",
        base.category,
        price_scaled,
        pow_scaled,
        desc,
        base.verbs,
        crit_chance=crit,
        hp_bonus=hp_b,
        shop_listed=False,
    )


BOSS_WEAK_ITEMS: tuple[ShopItem, ...] = tuple(
    _inferior_boss_drop(it) for it in (*WEAPONS, *GUNS, *ARMOR) if it.price > 0
)

GRANT_ITEMS: tuple[ShopItem, ...] = (STARTER_WEAPON, STARTER_ARMOR)
ITEMS: dict[str, ShopItem] = {
    item.id: item
    for item in (
        *GRANT_ITEMS,
        *WEAPONS,
        *GUNS,
        *ARMOR,
        BOSS_SLAYER_BLADE,
        BOSS_SLAYER_MAIL,
        MYTHIC_RAID_BLADE,
        MYTHIC_RAID_MAIL,
        *BOSS_WEAK_ITEMS,
        *CONSUMABLES,
        *ACCESSORIES,
    )
}
ITEM_ORDER: tuple[str, ...] = tuple(
    item.id for item in (*WEAPONS, *GUNS, *ARMOR, *ACCESSORIES, *CONSUMABLES)
)
CATEGORIES = ("all", "weapon", "gun", "armor", "accessory", "consumable")
SHOP_CATEGORIES = ("all", "weapon", "gun", "armor", "accessory", "consumable")
GEAR_INSTANCE_CATEGORIES = frozenset({"weapon", "gun", "armor", "accessory"})


def is_damage_dealer(item: ShopItem) -> bool:
    return item.category in ("weapon", "gun")


def is_gear_instance_item(item: ShopItem | None) -> bool:
    return item is not None and item.category in GEAR_INSTANCE_CATEGORIES


def is_accessory(item: ShopItem | None) -> bool:
    return item is not None and item.category == "accessory"


def accessory_equip_slot(item: ShopItem) -> str:
    """Ring vs amulet slot routing."""
    if item.id in ("warden_amulet", "citadel_pendant", "jester_charm"):
        return "amulet"
    return "ring"


def get_item(item_id: str) -> ShopItem | None:
    return ITEMS.get(item_id)


def sell_refund_for_item(item: ShopItem) -> float | None:
    """Goonbux received when selling one copy (half shop price, minimum 1)."""
    if item.price <= 0:
        return None
    return float(max(1, int(item.price // 2)))


def armor_mitigation_percent(power: int) -> int:
    return int(round(100 * power / (power + 100)))


def items_for_category(category: str) -> list[ShopItem]:
    normalized = category.lower()
    if normalized == "all":
        return [ITEMS[item_id] for item_id in ITEM_ORDER if ITEMS[item_id].shop_listed]
    if normalized not in SHOP_CATEGORIES:
        return []
    return [
        item
        for item in ITEMS.values()
        if item.category == normalized and item.shop_listed
    ]
