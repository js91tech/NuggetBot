from __future__ import annotations

HELP_PAGES: tuple[tuple[str, str], ...] = (
    (
        "Economy",
        "**/daily** · **/balance** · **/deposit** · **/withdraw** · **/expand-bank** · **/pay** · **/leaderboard**\n"
        "Bank cap **100k** base — tiered vault expansions: **+10k** (10k), **+50k** (50k), **+250k** (250k), **+500k** (500k). Prestige **1–9** resets pocket only; **prestige 10** also wipes bank + expansions.\n"
        "**/jobs** · **/work** · **/energy** · **/upgrade-energy**\n"
        "Bot Discord accounts can use slash commands and be targeted in PvP (duels, heists, bounties, etc.). Passive chat/VC farming stays human-only to prevent spam.",
    ),
    (
        "Raid & boss",
        "**/boss** — fight panel: Attack, **Attack Add**, Cast, Items, Heal, Auto-heal, Refresh, Raid LB\n"
        "**/attack** · **/heal** · **/cast** · **/use** · **/boss-status** · **/raid-leaderboard**\n"
        "**/enhance** · **/repair-gear** · **/equip-instance** — BDO-style gear enhancement (+1→PENTA)\n"
        "Raid adds (**Hannah's Henchmen**, **Court of Kitty's Jesters**) drop scrap/hardener — never celestial shards.\n"
        "**/shop** · **/buy** · **/equip** · **/craft** · **/prestige**\n"
        "**/dungeon** — solo standard (**25** energy) · unlock **Gilded Vault** (**50k**, party raid) · **/alchemy** · **/season**",
    ),
    (
        "PvP & casino",
        "**/duel** · **/coinflip** · **/blackjack** · **/slots** · **/jackpot**\n"
        "**/crew** — corporation panel (join, vault, loans, corporate upgrades, projects, war standings)\n"
        "**/territory** — map panel with guard hiring, zones, sieges\n"
        "**/business** — income, upgrades, tiers, districts, compete/defend, market, prestige & mega projects\n"
        "**/drugs** — grow lab (plant/harvest/use), `/drugs catalog`, `/drugs stash`, street sales & black market\n"
    ),
    (
        "Character",
        "**/class** · **/cast** · **/mana** · **/aspects** · **/avatar** (upload custom)\n"
        "**/use** — raid potion, energy drink, duel scroll, **Jail Key**, **Pick Key** · **/gift-item** — chia seeds\n"
        "**/attributes** — interactive stat panel (50 pt pool +5/prestige; 15 + prestige/stat cap)\n"
        "**/profile** · **/stats** · **/inventory** · **/quests** · **/achievements** · **/fix** (unstable gear)\n"
        "**/guide** — full interactive systems + item catalog (dropdown UI)\n"
        "Ring + amulet accessory slots. Broken enhanced gear: **/repair-gear** (10% base item price).",
    ),
    (
        "Chaos modules",
        "**/bounty** · **/bounty-board** · **/heist** · **/bank-heist** · **/bodyguards** · **/hack** · **/transfer** · **/scourge-pass** · **/trivia**\n"
        "**Jail Key** (100k, guaranteed escape) · **Pick Key** (20k, 15% escape) while arrested.\n"
        "**/bodyguards** — hire up to 5 guards (3 tiers) to defend your bank from heists.\n"
        "House pot — gambling losses, scourge hits, and unclaimed drops fund random coin drops.\n"
        "Scourge Virus — every **8** hours; warning GIF, then 7 min of infections on the top 5.\n"
        "Boss auto-spawn — every **90** minutes when none is active.\n"
        "**/hall-of-fame** · **/event** (admins)",
    ),
)
