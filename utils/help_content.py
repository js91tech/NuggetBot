from __future__ import annotations

HELP_PAGES: tuple[tuple[str, str], ...] = (
    (
        "Economy",
        "**/daily** · **/balance** · **/deposit** · **/withdraw** · **/pay** · **/leaderboard**\n"
        "Bank starts at **50k** cap — upgrade +10k storage for **15k** each (max **500k**).\n"
        "**/jobs** — jobs panel · **/work** · **/energy** · **/upgrade-energy**\n"
        "Bot Discord accounts can use slash commands and be targeted in PvP (duels, heists, bounties, etc.). Passive chat/VC farming stays human-only to prevent spam.",
    ),
    (
        "Raid & boss",
        "**/boss** — fight panel (attack, cast, items, heal) · **/attack** · **/heal** · **/boss-status**\n"
        "**/skills** · **/cast** — spellbook panel · **/use** — consumables panel\n"
        "**/shop** · **/buy** · **/equip** · **/craft** · **/prestige**\n"
        "**/dungeon** — dungeon panel (solo/party) · **/alchemy** · **/season**",
    ),
    (
        "PvP & casino",
        "**/duel** · **/coinflip** · **/blackjack** · **/slots** · **/jackpot**\n"
        "**/crew** — interactive crew panel (join, deposit, withdraw, loans, repay)\n"
        "**/territory** — map panel with guard hiring, zones, sieges\n"
    ),
    (
        "Character",
        "**/class** · **/cast** · **/mana** · **/aspects** · **/avatar** (upload custom)\n"
        "**/use** — raid potion, energy drink, duel scroll · **/gift** — chia seeds to friends",
        "**/profile** · **/stats** · **/quests** · **/achievements** · **/fix** (unstable gear)",
    ),
    (
        "Chaos modules",
        "**/bounty** · **/bounty-board** · **/heist** · **/bank-heist** · **/hack** · **/trivia**\n"
        "**/hall-of-fame** · **/event** (admins)",
    ),
)
