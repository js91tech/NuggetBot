from __future__ import annotations

HELP_PAGES: tuple[tuple[str, str], ...] = (
    (
        "Economy",
        "**/daily** · **/balance** · **/deposit** · **/withdraw** · **/pay** · **/leaderboard**\n"
        "**/jobs** · **/work** · **/energy** · **/upgrade-energy**\n"
        "Bot Discord accounts can use slash commands and be targeted in PvP (duels, heists, bounties, etc.). Passive chat/VC farming stays human-only to prevent spam.",
    ),
    (
        "Raid & boss",
        "**/boss** · **/attack** · **/heal** · **/boss-status** · **/raid-leaderboard**\n"
        "**/shop** · **/buy** · **/equip** · **/craft** · **/prestige**\n"
        "**/dungeon** — solo or **party** runs · **/alchemy** · **/season**",
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
