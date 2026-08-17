"""GoonBot game guide — sections and item catalogs for /guide UI."""
from __future__ import annotations

from dataclasses import dataclass

import discord

import config
from items import (
    ACCESSORIES,
    ARMOR,
    CONSUMABLES,
    GUNS,
    WEAPONS,
    ShopItem,
    armor_mitigation_percent,
)
from utils.alchemy import RECIPES
from utils.aspects import ASPECT_DEFINITIONS
from utils.businesses import BUSINESS_TIERS
from utils.corporations import CORPORATE_PROJECTS, CORPORATE_UPGRADES
from utils.districts import DISTRICT_MAP
from utils.drugs import DRUGS
from utils.helpers import clip_embed_field, fmt_amount
from utils.mega_projects import MEGA_PROJECTS

MAX_PAGE_CHARS = 3600


@dataclass(frozen=True)
class GuidePage:
    title: str
    body: str


@dataclass(frozen=True)
class GuideSection:
    section_id: str
    label: str
    emoji: str
    description: str
    pages: tuple[GuidePage, ...]


def _chunk_lines(lines: list[str], *, max_chars: int = MAX_PAGE_CHARS) -> list[str]:
    if not lines:
        return [""]
    pages: list[str] = []
    current: list[str] = []
    length = 0
    for line in lines:
        line_len = len(line) + 1
        if current and length + line_len > max_chars:
            pages.append("\n".join(current))
            current = [line]
            length = line_len
        else:
            current.append(line)
            length += line_len
    if current:
        pages.append("\n".join(current))
    return pages


def _format_weapon_line(item: ShopItem) -> str:
    crit = f" · {int(item.crit_chance * 100)}% crit" if item.crit_chance else ""
    return f"**{item.name}** — {fmt_amount(item.price)} · {item.power} dmg{crit}"


def _format_armor_line(item: ShopItem) -> str:
    mit = armor_mitigation_percent(item.power)
    return (
        f"**{item.name}** — {fmt_amount(item.price)} · "
        f"{mit}% mit · +{item.hp_bonus} HP"
    )


def _format_consumable_line(item: ShopItem) -> str:
    price = "drop/craft" if item.price <= 0 else fmt_amount(item.price)
    return f"**{item.name}** — {price} — {item.description}"


def _format_accessory_line(item: ShopItem) -> str:
    parts: list[str] = []
    if item.flat_damage:
        parts.append(f"+{item.flat_damage} dmg")
    if item.flat_hp:
        parts.append(f"+{item.flat_hp} HP")
    if item.flat_crit:
        parts.append(f"+{int(item.flat_crit * 100)}% crit")
    if item.flat_mitigation:
        parts.append(f"+{int(item.flat_mitigation * 100)}% mit")
    stats = " · ".join(parts) if parts else "flat bonuses"
    return f"**{item.name}** — {stats} — {item.description}"


def _item_catalog_pages(
    title: str,
    items: tuple[ShopItem, ...],
    *,
    formatter,
) -> tuple[GuidePage, ...]:
    listed = [item for item in items if item.shop_listed]
    lines = [formatter(item) for item in listed]
    bodies = _chunk_lines(lines)
    if not bodies:
        bodies = ["No shop items in this category."]
    return tuple(
        GuidePage(
            title=title if len(bodies) == 1 else f"{title} ({index + 1}/{len(bodies)})",
            body=body,
        )
        for index, body in enumerate(bodies)
    )


def _static_pages(section_title: str, paragraphs: list[str]) -> tuple[GuidePage, ...]:
    bodies = _chunk_lines(paragraphs)
    return tuple(
        GuidePage(
            title=section_title if len(bodies) == 1 else f"{section_title} ({i + 1}/{len(bodies)})",
            body=body,
        )
        for i, body in enumerate(bodies)
    )


def _build_sections() -> tuple[GuideSection, ...]:
    aspect_lines = [
        f"**{aspect.name}** — {aspect.description}"
        for aspect in ASPECT_DEFINITIONS
    ]
    alchemy_lines = [
        f"**{recipe.name}** — {recipe.scrap_cost} scrap + {fmt_amount(recipe.nugget_cost)} — {recipe.description}"
        for recipe in RECIPES
    ]
    business_tier_lines = [
        f"{defn.emoji} **{defn.name}** (T{defn.tier}) — buy {fmt_amount(defn.purchase_cost)} · "
        f"{fmt_amount(defn.base_income_per_hour)}/hr"
        for defn in BUSINESS_TIERS
    ]
    district_lines = [
        f"{defn.emoji} **{defn.name}** — {defn.label} (income x{defn.income_mult:.2f})"
        for defn in DISTRICT_MAP.values()
    ]
    corp_upgrade_lines = [
        f"{defn.emoji} **{defn.name}** — {defn.description}"
        for defn in CORPORATE_UPGRADES
    ]
    corp_project_lines = [
        f"{defn.emoji} **{defn.name}** — goal {fmt_amount(defn.target_amount)} · "
        f"reward {fmt_amount(defn.reward_treasury)}"
        for defn in CORPORATE_PROJECTS
    ]
    mega_lines = [
        f"{defn.emoji} **{defn.name}** — {fmt_amount(defn.cost)} · {defn.reward_label}"
        for defn in MEGA_PROJECTS
    ]
    drug_lines = [
        f"{defn.emoji} **{defn.name}** ({defn.category}) — seed {fmt_amount(defn.seed_cost)} · "
        f"{defn.grow_seconds // 60}m grow · ~{fmt_amount(defn.street_price)}/unit · _{defn.effect_summary}_"
        for defn in DRUGS
    ]

    return (
        GuideSection(
            section_id="overview",
            label="Overview",
            emoji="📖",
            description="Quick start and core loops",
            pages=_static_pages(
                "Getting started",
                [
                    "🔞 **GoonBot is an 18+ adult economy RPG.** Play in NSFW channels when your "
                    "server requires it.",
                    "**GoonBot** is a Discord economy RPG. Earn **goonbux**, gear up, raid bosses, "
                    "fight duels, run heists, and climb prestige.",
                    "**First hour**\n"
                    "1. `/daily` — free goonbux\n"
                    "2. `/shop` — buy a weapon and armor\n"
                    "3. `/equip` — wear your gear\n"
                    "4. `/class choose` — pick Vanguard, Mogul, or Shade\n"
                    "5. `/boss` or `/attack` — join the raid\n"
                    "6. `/balance` — pocket vs bank vault",
                    "**Core loops**\n"
                    "· **Economy** — chat, VC, jobs, daily, pay friends\n"
                    "· **Raid** — boss panels, heals, loot, dungeons\n"
                    "· **PvP** — duels, heists, territories, crews\n"
                    "· **Build** — craft, aspects, attributes, prestige, **enhancement**\n"
                    "· **Empire** — `/business` tiers, districts, competition, stock market\n"
                    "· **Contraband** — `/drugs` grow lab and black market\n"
                    "Use the dropdown below to browse every system and item catalog.",
                ],
            ),
        ),
        GuideSection(
            section_id="economy",
            label="Economy",
            emoji="💰",
            description="Wallet, bank, jobs, and income",
            pages=_static_pages(
                "Economy",
                [
                    "**Pocket vs bank**\n"
                    "· **Pocket** — spent at `/shop`, visible to wallet heists\n"
                    f"· **Bank** — safer storage; `/bank-heist` targets bank only\n"
                    f"· Base bank cap **{fmt_amount(config.BANK_BASE_CAPACITY)}** — "
                    "`/expand-bank` tiered expansions: "
                    f"**+{fmt_amount(float(config.BANK_EXPANSION_TIERS[1]['capacity']))}** "
                    f"({fmt_amount(float(config.BANK_EXPANSION_TIERS[1]['cost']))}), "
                    f"**+{fmt_amount(float(config.BANK_EXPANSION_TIERS[2]['capacity']))}** "
                    f"({fmt_amount(float(config.BANK_EXPANSION_TIERS[2]['cost']))}), "
                    f"**+{fmt_amount(float(config.BANK_EXPANSION_TIERS[3]['capacity']))}** "
                    f"({fmt_amount(float(config.BANK_EXPANSION_TIERS[3]['cost']))}), "
                    f"**+{fmt_amount(float(config.BANK_EXPANSION_TIERS[4]['capacity']))}** "
                    f"({fmt_amount(float(config.BANK_EXPANSION_TIERS[4]['cost']))})",
                    "**Commands**\n"
                    "`/daily` · `/balance` · `/deposit` · `/withdraw` · `/expand-bank` · "
                    "`/pay` · `/leaderboard` · `/hall-of-fame`\n"
                    "`/jobs` · `/work` · `/energy` · `/upgrade-energy`",
                    "**Income sources**\n"
                    "· Passive chat messages and voice chat minutes\n"
                    "· Active-hour bonus while chatting\n"
                    "· Job shifts (`/work`) — costs energy, regens over time\n"
                    "· Boss damage payouts, quest rewards, trivia, coin drops\n"
                    "· Class modifiers (Mogul boosts income/jobs)",
                    f"**Prestige** (`/prestige`)\n"
                    f"· Requires **{fmt_amount(config.PRESTIGE_MIN_WALLET)}**+ in pocket\n"
                    f"· Max **{config.PRESTIGE_MAX_LEVEL}** — +{int(config.PRESTIGE_CRIT_BONUS_PER_LEVEL * 100)}% "
                    f"crit and +{int(config.PRESTIGE_INCOME_BONUS_PER_LEVEL * 100)}% income per level\n"
                    "· Prestiges **1–9** reset pocket only\n"
                    "· **Prestige 10** also wipes bank and vault expansions",
                ],
            ),
        ),
        GuideSection(
            section_id="boss",
            label="Boss raids",
            emoji="👹",
            description="Velvet Vixen fights, loot, and healing",
            pages=_static_pages(
                "Boss raids",
                [
                    "**Commands** — `/boss` panel · `/attack` · **Attack Add** · `/heal` · `/cast` · `/use` · "
                    "`/boss-status` · `/raid-leaderboard` · `/boss-hunt` · `/boss-crew-lb`",
                    "**How raids work**\n"
                    "· Bosses auto-spawn about every **90** minutes (10‑min warning ping)\n"
                    "· Pick a raid role on `/boss`: **Tank**, **Healer**, or **Glass**\n"
                    "· Boss **mood** shifts as HP drops (aggressive → armored → frantic)\n"
                    "· Faster kill race: first blood, killing blow, and top-damager crate bonuses\n"
                    "· Everyone who deals meaningful damage gets a participation purse + scrap\n"
                    "· Raid adds can drop **companions**; every boss tier can drop **relics**\n"
                    "· Weekly `/boss-hunt` targets + crew scoreboard via `/boss-crew-lb`\n"
                    "· `world_boss_week` event spawns the **World Leviathan** with unique loot\n"
                    "· Your weapon sets base damage (+ small roll); armor adds HP and mitigation\n"
                    "· Velvet Vixen counters can down you — teammates `/heal` to revive\n"
                    "· Damage share when the boss falls; killing blow shows your avatar pose",
                    "**Raid adds** (after boss drops below **50%** HP)\n"
                    "· **Velvet's Henchman** — alchemy scrap (rare void hardener on celestial+ fights)\n"
                    "· **Court of Kitty's Jester** — void hardener (sometimes scrap)\n"
                    "· Use **Attack Add** on the `/boss` panel — adds never drop celestial shards",
                    "**Variants** (weakest → strongest)\n"
                    "normal → enraged → shadow → celestial → mythic\n"
                    "Special: **TomAss**, **ZZ Wrath**, **Freaky Nikki**, **World Leviathan**",
                    "**Elements** — bosses have fire/frost/storm/void/verdant; your class element "
                    "can boost or reduce `/attack` damage.\n"
                    "**Loot** — less battle-worn spam; more epic/aspect/accessory/hardener odds; "
                    "top damager crate; celestial shards on mythic / ZZ / Leviathan",
                ],
            ),
        ),
        GuideSection(
            section_id="dungeon",
            label="Dungeons",
            emoji="🕳️",
            description="Solo delves and party vault raids",
            pages=_static_pages(
                "Dungeons",
                [
                    "`/dungeon` — interactive panel\n"
                    f"· **Delver's Depths** — solo, **{config.DUNGEON_ENERGY_COST}** energy per run\n"
                    f"· **Gilded Vault** — unlock **{fmt_amount(config.DUNGEON_VAULT_UNLOCK_COST)}**, "
                    f"party of **{config.DUNGEON_VAULT_MIN_PARTY_SIZE}**+ raiders",
                    "**Rewards** — room goonbux, clear bonus, **alchemy scrap** for `/alchemy` and **enhancement**\n"
                    f"· Accessory drop chance on clear (~{int(config.DUNGEON_ACCESSORY_DROP_CHANCE * 100)}% standard, "
                    f"~{int(config.DUNGEON_VAULT_ACCESSORY_DROP_CHANCE * 100)}% vault)\n"
                    "· **Gilded Vault** full clear can also roll **void hardener**",
                    "`/alchemy` — craft raid potions, energy drinks, trap bombs from scrap",
                    f"**Energy** — base cap **{config.ENERGY_BASE_CAP}**, regen every "
                    f"{config.ENERGY_REGEN_INTERVAL_SECONDS // 60} min; `/upgrade-energy` raises max",
                ],
            ),
        ),
        GuideSection(
            section_id="pvp",
            label="PvP & crews",
            emoji="⚔️",
            description="Duels, heists, territories",
            pages=_static_pages(
                "PvP & crews",
                [
                    "**Duels** — `/duel` turn-based combat using equipped gear, class modifiers, "
                    "aspects, and skills (`/cast`). ELO tracked; `/season` for ranked resets.",
                    "**Wallet heist** — `/heist @user` steals from **pocket**; crew tags add success. "
                    "`/arrest` failed thieves within 5 minutes.",
                    "**Bank heist** — `/bank-heist` tier panel vs target **bank**; hire `/bodyguards` "
                    "to defend. Tier 3 failures can make gear **unstable** — repair with `/fix`.",
                    "**Crews** — `/crew panel`: treasury, deposits, loans, XP levels\n"
                    "**Territories** — `/territory` map; five zones, hourly crew income, "
                    "30 min sieges, zone perks (heist loot, craft discount, etc.)",
                    "**Casino** — `/coinflip` · `/blackjack` · `/slots` · `/jackpot`",
                ],
            ),
        ),
        GuideSection(
            section_id="character",
            label="Character",
            emoji="🎭",
            description="Classes, stats, aspects, avatars",
            pages=_static_pages(
                "Character build",
                [
                    "**Classes** — `/class choose` then `/class evolve` with class XP from duels and boss damage\n"
                    "Starters: **Vanguard** (combat), **Mogul** (income), **Shade** (heists)\n"
                    "Evolve → master branches → hybrids (**Warlord**, **Archon**) need multiple master roots",
                    "**Attributes** — `/attributes` STR/DEX/AGI/DEF/VIT from class XP; "
                    "caps scale with prestige\n"
                    "**Skills** — `/cast` in raids/duels; mana from damage dealt (healers regen over time)\n"
                    "**Aspects** — `/aspects` Diablo-style rolls; equip slots; `/aspects fuse` burn 3 for stronger roll",
                    "**Avatars** — `/avatar` victory poses on duel wins and boss killing blows\n"
                    "**Accessories** — **ring** + **amulet** slots; flat dmg/HP/crit/mit bonuses\n"
                    "`/profile` · `/stats` · `/inventory` · `/loadout` · `/equip-best` · `/equip-instance`",
                ],
            ),
        ),
        GuideSection(
            section_id="enhancement",
            label="Enhancement",
            emoji="✨",
            description="BDO-style +1 to PENTA gear upgrades",
            pages=_static_pages(
                "Gear enhancement",
                [
                    "**Per-instance enhancement** — levels stick to the gear piece, not the slot. "
                    "Swap weapons and your +levels travel with the item.",
                    "**Commands**\n"
                    "`/enhance` — pick a gear instance; shows material, goonbux cost, success %\n"
                    "`/repair-gear` — fix **broken** gear (10% of base item shop price in goonbux)\n"
                    "`/equip-instance` — equip a specific instance by id (`/inventory` lists them)",
                    "**Level ladder**\n"
                    "· **+1 … +10** — alchemy scrap\n"
                    "· **+11 … +15** — void hardener\n"
                    "· **PRI → DUO → TRI → TET → PENTA** — celestial shard",
                    "**Costs** — every attempt debits materials **and** goonbux (win or lose). "
                    f"Goonbux anchors: ~{fmt_amount(config.ENHANCE_NUGGET_COST_AT_PLUS_10)} at +10, "
                    f"~{fmt_amount(config.ENHANCE_NUGGET_COST_AT_PLUS_15)} at +15, "
                    f"~{fmt_amount(config.ENHANCE_NUGGET_COST_AT_PENTA)} at PENTA",
                    "**Failure rules**\n"
                    f"· From +{config.ENHANCE_FAIL_DOWNGRADE_FROM}+ failures can **downgrade** 1 level\n"
                    f"· From +{config.ENHANCE_FAIL_BREAK_FROM}+ failures can **break** gear (0 stats until repaired)\n"
                    "· **Unstable** gear from bank heist tier 3 is separate — use `/fix` (80% item price)",
                    "**Material sources**\n"
                    "· **Alchemy scrap** — dungeons, Freaky Nikki, henchman raid adds\n"
                    "· **Void hardener** — vault dungeons, jester adds, boss defeat rolls\n"
                    "· **Celestial shard** — mythic / ZZ Wrath boss defeat only (never from adds or dungeons)",
                ],
            ),
        ),
        GuideSection(
            section_id="accessories",
            label="Accessories",
            emoji="💍",
            description="Ring and amulet drops",
            pages=_static_pages(
                "Accessory catalog",
                [
                    "Accessories equip in **ring** or **amulet** slots. They grant **flat** combat bonuses. "
                    "Accessories are enhanceable instances like weapons and armor.",
                    "",
                    *[_format_accessory_line(item) for item in ACCESSORIES],
                    "",
                    "**Drop sources** — boss defeat (~6%), dungeon clears (2–5% vault), "
                    "not from raid adds.",
                ],
            ),
        ),
        GuideSection(
            section_id="aspects",
            label="Aspects",
            emoji="💎",
            description="All aspect types",
            pages=_static_pages("Aspect catalog", aspect_lines),
        ),
        GuideSection(
            section_id="weapons",
            label="Weapons",
            emoji="🗡️",
            description="Melee weapon shop catalog",
            pages=_item_catalog_pages("Weapons", WEAPONS, formatter=_format_weapon_line),
        ),
        GuideSection(
            section_id="guns",
            label="Guns",
            emoji="🔫",
            description="Ranged weapon shop catalog",
            pages=_item_catalog_pages("Guns", GUNS, formatter=_format_weapon_line),
        ),
        GuideSection(
            section_id="armor",
            label="Armor",
            emoji="🛡️",
            description="Armor shop catalog",
            pages=_item_catalog_pages("Armor", ARMOR, formatter=_format_armor_line),
        ),
        GuideSection(
            section_id="consumables",
            label="Consumables",
            emoji="🧪",
            description="Potions, keys, and alchemy",
            pages=_static_pages(
                "Consumables & alchemy",
                [_format_consumable_line(item) for item in CONSUMABLES if item.shop_listed]
                + ["", "**Alchemy recipes** (`/alchemy`)"]
                + alchemy_lines
                + [
                    "",
                    "**Usage**\n"
                    "`/use` — raid potion, energy drink, jail/pick keys, HP potions in raids\n"
                    "Trap bombs — duel consumable from alchemy\n"
                    "**Enhancement materials** (drops, not shop-listed)\n"
                    "· **Alchemy scrap** — +1–+10 enhancement\n"
                    "· **Void hardener** — +11–+15 enhancement\n"
                    "· **Celestial shard** — PRI–PENTA (mythic / ZZ Wrath boss only)",
                ],
            ),
        ),
        GuideSection(
            section_id="chaos",
            label="Chaos events",
            emoji="🦠",
            description="Bounties, viruses, scourge",
            pages=_static_pages(
                "Chaos modules",
                [
                    "**Bounty** — `/bounty @user amount word` · `/bounties` — claim when target says the trigger word",
                    "**Hot potato** — `/hack @user` starts the virus; `/transfer` passes it; "
                    "scaling wallet penalties",
                    "**Scourge Virus** — world event every **8** hours; warning GIF, then **7** min outbreak; "
                    "`/scourge-pass` to pass infection; hits top wallets' banks",
                    "**Trivia** — hourly Lore Roulette (**3 min**, faster answers pay more + free drug chance; "
                    "`/trivia` to start early)\n"
                    "**Imposter** — random AI word sabotage in messages (server config)",
                    "**House pot** — gambling taxes and unclaimed drops fund random **Claim** coin drops",
                ],
            ),
        ),
        GuideSection(
            section_id="business",
            label="Business Empire",
            emoji="🏢",
            description="Businesses, districts, competition, stocks",
            pages=_static_pages(
                "Business Empire",
                [
                    "**Build an empire** — `/business create` opens a Lemon Stand. Income "
                    "accrues passively into a capped store; `/business collect` banks it. "
                    "`/business info` opens the panel (Collect · Upgrade · Tier up · "
                    "Districts · Compete · Refresh).",
                    "**Business tiers** (`/business` → Tier up)\n" + "\n".join(business_tier_lines),
                    "**Attributes & branches** (`/business upgrade`)\n"
                    "· 📣 Reputation & ⚙️ Efficiency — **+income**/hr per level\n"
                    "· 📈 Growth branch & 🏗️ Production branch — **+income**/hr per level\n"
                    "· 🛡️ Security & 🔒 Security branch — **defense only** (no income)\n"
                    "· 📦 Capacity — **storage cap only** (no income)\n"
                    "· 😀 Employee Satisfaction — `/business manage` (wages, team events); "
                    "drifts without care",
                    "**Districts** (`/business districts`) — relocate for an income bonus, "
                    "claim exclusive deeds (owner gets full bonus + 20% tenant rent; tenants "
                    "get half the district bonus), or hostile-buyout an owned deed "
                    "(5 days of district-bonus value + 15% burned; "
                    f"{int(config.DISTRICT_BUYOUT_INFLUENCE_DISCOUNT_THRESHOLD)}+ influence "
                    f"cuts buyout burn by "
                    f"{int(config.DISTRICT_BUYOUT_INFLUENCE_DISCOUNT * 100)}%). "
                    "Influence ops: Invest, Contest war control, Undermine rivals, "
                    "Fortify temp influence, and deed-owner Suppress:\n"
                    + "\n".join(district_lines),
                    "**Competition & defense** (`/business action`, `/business defend`)\n"
                    "· 📣 Marketing & 🧑\u200d💼 Talent — buff your own revenue\n"
                    "· 💸 Price War & 📰 Reputation Attack — debuff a rival (mitigated by their "
                    "security; they get 15 min to **/business defend**)\n"
                    "· 🗺️ Market Expansion — instant district influence\n"
                    "_No attack ever permanently destroys a business._",
                    "**Corporations** (`/crew panel`) — crews double as corporations:\n"
                    + "\n".join(corp_upgrade_lines)
                    + "\n**Projects:** " + ", ".join(p.split(' — ')[0] for p in corp_project_lines)
                    + "\n**Corporate War** — weekly; top corp by vault + territory wins a treasury bonus.",
                    "**Stock market** (`/business market`) — buy/sell corporation shares; "
                    "prices track treasury + headcount and swing with market events "
                    "(tech boom, crash, tourism surge, supply shortage). Shareholders earn "
                    "hourly dividends from the corporate vault.",
                    "**Prestige & endgame**\n"
                    f"· `/business prestige` at the Corporation tier — reset for a permanent "
                    f"+{int(config.BUSINESS_PRESTIGE_INCOME_BONUS_PER_LEVEL * 100)}%/level income bonus\n"
                    "· **Mega projects** (`/business megaprojects`):\n"
                    + "\n".join(mega_lines)
                    + "\n· **Acquisitions** (`/business acquisitions`) after all megas: "
                    "Media Conglomerate, Private Security, Pharma Lab\n"
                    "· **Supply chain** (`/business supplychain`) at tier 5+: auto-fund lab "
                    "plants from stored revenue\n"
                    "· **Legacy perks** at prestige 10+: Automation, Diversification, or "
                    "Hostile Takeover\n"
                    "· **District wars** — dominant crew in a district earns +5% member income\n"
                    "· Seasonal events (admin `/event`): Summer Festival, Holiday Rush, "
                    "Economic Crisis, Tech Boom.",
                ],
            ),
        ),
        GuideSection(
            section_id="drugs",
            label="Drug Trade",
            emoji="🧪",
            description="Grow lab, street deals, black market",
            pages=_static_pages(
                "Drug Trade",
                [
                    "**High risk, high reward contraband.** `/drugs lab` opens your grow lab: "
                    "plant strains (extra slot at dealer rank 5), wait, **Harvest**, then sell "
                    "or **Use** for effects.\n"
                    "· `/drugs rank` — dealer rank unlocks (market at 3, wholesale at 7, "
                    "Cartel title at 10)\n"
                    "· `/drugs wholesale` — fixed-price NPC buyer, no raid risk (rank 7+)\n"
                    "· `/drugs catalog` — all strains, prices, and effects\n"
                    "· `/drugs stash` — your inventory and active buffs\n"
                    "· `/drugs use` — consume product from stash\n"
                    "· `/drugs gift` — give stash product to another player",
                    "**Strains**\n" + "\n".join(drug_lines),
                    "**Selling**\n"
                    "· **Street** (lab panel) — instant sale at a volatile price, but a "
                    f"**{int(config.DRUG_RAID_CHANCE * 100)}%** chance of a raid that seizes part of "
                    "your stash\n"
                    "· **Black market** (`/drugs market`) — list product for other players or buy "
                    f"theirs ({int(config.DRUG_MARKET_TAX * 100)}% sale tax)",
                    "**Tips**\n"
                    "· Owning a business in the **Industrial Zone** boosts harvest yield by "
                    f"+{int(config.DRUG_INDUSTRIAL_YIELD_BONUS * 100)}%\n"
                    "· Higher-tier strains take longer but pay far more per unit\n"
                    "· **Use** product from your stash for energy, healing, or combat buffs\n"
                    "· Spread sales to avoid big losses to raids",
                ],
            ),
        ),
        GuideSection(
            section_id="progression",
            label="Progression",
            emoji="🏆",
            description="Quests, achievements, craft, events",
            pages=_static_pages(
                "Progression",
                [
                    "**Quests** — `/quests` onboarding steps + **3** daily goals (UTC midnight reset)",
                    "**Achievements** — `/achievements` track boss kills, heals, heists, duels, dungeons, territories",
                    "**Craft** — `/craft` upgrades **battle-worn** boss drops (`boss_weak_*`) into real shop gear",
                    "**Events** (admin `/event`) — double drops, bonus income, festival boss HP, trivia fiesta, world boss week",
                    "**Sell** — `/sell-worn` battle-worn drops · `/shop-list` text catalog fallback",
                ],
            ),
        ),
    )


GUIDE_SECTIONS: tuple[GuideSection, ...] = _build_sections()
GUIDE_SECTION_MAP: dict[str, GuideSection] = {section.section_id: section for section in GUIDE_SECTIONS}


def guide_section_options() -> list[tuple[str, str, str]]:
    """(section_id, label, description) for select menu."""
    return [
        (section.section_id, f"{section.emoji} {section.label}", section.description)
        for section in GUIDE_SECTIONS
    ]


def build_guide_embed(section_id: str, page_index: int) -> tuple[discord.Embed, int, int]:
    """Return embed, current page index, and total pages."""
    section = GUIDE_SECTION_MAP[section_id]
    total_pages = len(section.pages)
    page_index = max(0, min(page_index, total_pages - 1))
    page = section.pages[page_index]
    body = clip_embed_field(page.body, 4096)
    embed = discord.Embed(
        title=f"{section.emoji} {page.title}",
        description=body,
        color=discord.Color.blurple(),
    )
    embed.set_footer(
        text=f"{section.label} · Page {page_index + 1}/{total_pages} · Use the menu to switch topics",
    )
    return embed, page_index, total_pages
