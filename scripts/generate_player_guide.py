#!/usr/bin/env python3
"""Generate docs/NuggetBot_Player_Guide.pdf - run from repo root."""

from __future__ import annotations

from pathlib import Path

from fpdf import FPDF

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "NuggetBot_Player_Guide.pdf"


class GuidePDF(FPDF):
    def header(self) -> None:
        if self.page_no() > 1:
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(120, 120, 120)
            self.cell(0, 8, "NuggetBot Player Guide", align="R", new_x="LMARGIN", new_y="NEXT")
            self.ln(2)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, f"Page {self.page_no()}", align="C")

    def chapter_title(self, title: str) -> None:
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(30, 60, 120)
        self.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(30, 60, 120)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)
        self.set_text_color(0, 0, 0)

    def body(self, text: str) -> None:
        self.set_font("Helvetica", "", 10)
        w = self.w - self.l_margin - self.r_margin
        self.set_x(self.l_margin)
        self.multi_cell(w, 5, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def bullet(self, text: str) -> None:
        self.set_font("Helvetica", "", 10)
        w = self.w - self.l_margin - self.r_margin
        self.set_x(self.l_margin)
        self.multi_cell(w, 5, f"- {text}", new_x="LMARGIN", new_y="NEXT")


def build() -> None:
    pdf = GuidePDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Cover
    pdf.set_font("Helvetica", "B", 28)
    pdf.set_text_color(30, 60, 120)
    pdf.ln(40)
    pdf.cell(0, 14, "NuggetBot", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 16)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 10, "Player Guide", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)
    pdf.set_font("Helvetica", "", 11)
    cover_w = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(
        cover_w,
        6,
        "Everything you need to earn nuggets, gear up, raid bosses, "
        "run heists, dodge the virus, and climb to endgame.",
        align="C",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(20)
    pdf.set_font("Helvetica", "I", 9)
    pdf.cell(0, 6, "Type / in Discord to see all slash commands.", align="C")

    # 1 - Start
    pdf.add_page()
    pdf.chapter_title("1. Getting started")
    pdf.body(
        "NuggetBot is a server economy game. Your currency is nuggets. "
        "Most actions use slash commands: type / in any channel where the bot can read messages."
    )
    pdf.bullet("Check your wallet: /balance")
    pdf.bullet("Claim free nuggets daily: /daily")
    pdf.bullet("See your combat build: /stats")
    pdf.bullet("Browse gear: /shop")
    pdf.ln(2)
    pdf.body(
        "Passive income: chatting, staying active each hour, hanging in voice, "
        "and random coin drops in the main channel all add nuggets without commands."
    )

    # 2 - Economy
    pdf.chapter_title("2. Earning nuggets")
    pdf.bullet("/daily - once every 24 hours (default 75 nuggets)")
    pdf.bullet("Chat in server - small reward per message")
    pdf.bullet("Voice chat - nuggets per minute in VC")
    pdf.bullet("Coin drops - first to click Claim wins (needs several active chatters)")
    pdf.bullet("/pay @user amount - send nuggets to friends")
    pdf.bullet("/leaderboard - richest players")
    pdf.bullet("/hall-of-fame - richest, boss kills, heals, achievements")
    pdf.bullet("/quests - onboarding steps and daily goals")
    pdf.bullet("Boss raids - damage share when Hannah falls")
    pdf.bullet("/trivia - guess a word from server history")
    pdf.bullet("/heist - steal from others (risky)")
    pdf.bullet("/bounty - reward when someone says a trigger word")
    pdf.ln(2)
    pdf.body("Prestige and seasonal events can multiply your income (see section 10).")

    # 3 - Gear
    pdf.add_page()
    pdf.chapter_title("3. Shop and gear")
    pdf.bullet("/shop - list weapons and armor (10 tiers each)")
    pdf.bullet("/buy item_id - purchase (use autocomplete)")
    pdf.bullet("/inventory - owned items with stat lines")
    pdf.bullet("/equip item_id - wear weapon or armor")
    pdf.bullet("/sell item_id - sell one copy for half shop price")
    pdf.ln(2)
    pdf.body("Weapons: base damage + random 1-5 per hit, plus crit chance.")
    pdf.body("Armor: reduces boss counter damage and adds max HP in raids.")
    pdf.body("Top shop tier: Nugget Excalibur + Nugget Immortal Plate (120,000 each).")
    pdf.body("Beyond that: Mythic Voidreaver + Mythic Aetherplate (175,000 each).")
    pdf.body("Ultimate shop: Apex / Sovereign / Transcendent sets (500k / 750k / 1.5M per piece).")
    pdf.ln(2)
    pdf.chapter_title("Gear sets (bonus)")
    pdf.body(
        "Equip a matching weapon + armor theme for +5% damage and extra mitigation. "
        "Examples: Ember Axe + Ember Mail, Void Blade + Void Ward, "
        "Nugget Excalibur + Nugget Immortal Plate."
    )
    pdf.body("/stats shows your full combat sheet including set and prestige bonuses.")

    # 4 - Boss
    pdf.chapter_title("4. Boss raids (Hannah)")
    pdf.body(
        "A boss spawns automatically about every 30–45 minutes when none is active, or an admin can /summon. "
        "Everyone attacks together; rewards split by damage dealt."
    )
    pdf.bullet("/boss - HP bar and threat level")
    pdf.bullet("/attack - hit Hannah (need gear equipped for best damage)")
    pdf.bullet("/raid-leaderboard - who is contributing most")
    pdf.bullet("/heal @user - revive downed raiders (+1000 nuggets; +100 if you revive yourself)")
    pdf.ln(2)
    pdf.body("Boss variants (weakest to strongest): normal, enraged, shadow, celestial, mythic.")
    pdf.body("TomAss: rare enraged mirror boss with regen every 3 hits (admin /summon).")
    pdf.body("Bosses have elements - class element can boost or reduce your /attack damage.")
    pdf.body("At 75%, 50%, and 25% HP Hannah enters new phases - counters get nastier.")
    pdf.body("Loot: battle-worn gear (common), epic raid pieces (rare), mythic drops on celestial/mythic kills.")
    pdf.body("/craft - upgrade battle-worn drops into real shop items for a fee.")

    # 5 - Quests & hall of fame
    pdf.add_page()
    pdf.chapter_title("5. Quests and hall of fame")
    pdf.body("Quests:")
    pdf.bullet("/quests - view onboarding or daily goals (resets at UTC midnight)")
    pdf.bullet("/quest-hint - nudge for your current objective")
    pdf.bullet("New players get a short onboarding chain; veterans get 3 random dailies")
    pdf.bullet("Rewards pay automatically when you complete a goal")
    pdf.ln(2)
    pdf.body("Hall of fame:")
    pdf.bullet("/hall-of-fame - server legends in one embed")
    pdf.bullet("Categories: richest, boss kills, heals given, achievement count")

    # 6 - Casino & duels
    pdf.chapter_title("6. Casino and PvP duels")
    pdf.body("Casino (tax on winnings):")
    pdf.bullet("/coinflip amount - 50/50 vs the house")
    pdf.bullet("/coinflip-duel @user amount - PvP coinflip with Accept button")
    pdf.bullet("/blackjack amount - hit or stand vs the dealer")
    pdf.ln(2)
    pdf.body("Duels:")
    pdf.bullet("/duel @player - gear-based turn fight; battle log in channel")
    pdf.bullet("Loser pays 10% of wallet to the winner (default)")
    pdf.bullet("40-minute cooldown before attacking the same player again")
    pdf.bullet("Max 3 duels started per hour")

    # 7 - Heist & bounty
    pdf.chapter_title("7. Heists and bounties")
    pdf.body("Heists:")
    pdf.bullet("/heist @target [@crew1] [@crew2] - steal 20% of wallet on success")
    pdf.bullet("Better weapon = higher success (intimidation bonus, up to +10%)")
    pdf.bullet("On failure, target can /arrest you within 5 minutes")
    pdf.bullet("30-minute cooldown between heists")
    pdf.ln(2)
    pdf.body("Bounties:")
    pdf.bullet("/bounty @user amount trigger_word - pay to place (min 50 + tax)")
    pdf.bullet("Whoever makes them say the word (alone in a message) wins the pot")
    pdf.bullet("/bounties - list active bounties")

    # 8 - Virus
    pdf.chapter_title("8. The virus (hot potato)")
    pdf.bullet("/hack @user - start the virus on someone (5 min cooldown per hacker)")
    pdf.bullet("Holder must /transfer @user before the timer runs out")
    pdf.bullet("Each pass increases the penalty if it detonates")
    pdf.bullet("Larger wallets take slightly less damage when it pops")
    pdf.body("Watch the embed timer bar - do not hold it when time is low!")

    # 9 - Progression
    pdf.chapter_title("9. Progression and classes")
    pdf.bullet("/achievements - track unlockable goals")
    pdf.bullet("/prestige confirm:true - reset wallet (need 100k+) for permanent +crit and +income")
    pdf.bullet("Max prestige 10 - stacks +1% crit and +2% income per level")
    pdf.ln(2)
    pdf.body("Classes:")
    pdf.bullet("/class choose - pick Vanguard, Mogul, or Shade (one time)")
    pdf.bullet("/class - view XP and modifiers; /class evolve when ready")
    pdf.bullet("Earn class XP from duels and boss /attack damage")
    pdf.bullet("Hybrids Warlord and Archon unlock after two master paths")
    pdf.ln(2)
    pdf.body("Jobs (/jobs, /work) pay 4.5x base rates; class can further modify payouts.")
    pdf.ln(2)
    pdf.chapter_title("Mana and class skills")
    pdf.bullet("/mana - mana bar, regen rules, ready spell")
    pdf.bullet("/skills - list your class spells and mana costs")
    pdf.bullet("/cast skill_id - spend mana to cast (see /skills for ids)")
    pdf.ln(2)
    pdf.body(
        "Most classes regen mana slowly over time (+2 every 45s) but mainly refill from "
        "dealing damage (18% of boss hit damage). High DPS = more casts."
    )
    pdf.body(
        "Healer classes (Vanguard Warden branch) regen mana faster over time (+7 every 20s) "
        "and get less mana from damage (6%) so they can heal without spam-attacking."
    )
    pdf.body(
        "Offensive spells charge your next /attack or /duel. Self-heals apply on cast. "
        "Blessing boosts your next /heal payout. Smoke skills buff your next /heist."
    )
    pdf.ln(2)
    pdf.body("Achievement examples: first boss kill, 25 raids, mythic slayer, heist king, field medic, own Excalibur.")

    # 10 - Events
    pdf.chapter_title("10. Seasonal events (admin)")
    pdf.bullet("/event status - see active event")
    pdf.body("Admins can run:")
    pdf.bullet("double_drops - 2x boss loot chance")
    pdf.bullet("bonus_income - 1.5x nugget earnings")
    pdf.bullet("festival_boss - 1.25x boss HP")
    pdf.bullet("trivia_fiesta - 2x trivia rewards")

    # 11 - Quick reference
    pdf.add_page()
    pdf.chapter_title("11. Command quick reference")
    cols = [
        ("Economy", "/daily, /balance, /pay, /leaderboard, /hall-of-fame"),
        ("Shop", "/shop, /buy, /inventory, /equip, /sell, /stats"),
        ("Raid", "/boss, /attack, /raid-leaderboard, /heal"),
        ("Progress", "/class, /class choose, /class evolve, /mana, /skills, /cast"),
        ("More", "/achievements, /prestige, /craft, /event, /quests, /jobs, /work"),
        ("Crime", "/heist, /arrest, /bounty, /bounties, /hack, /transfer"),
        ("PvP", "/duel, /coinflip, /coinflip-duel, /blackjack"),
        ("Fun", "/trivia, /quest-hint"),
    ]
    w = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.set_font("Helvetica", "", 9)
    for label, cmds in cols:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(35, 5, label)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_x(pdf.l_margin + 35)
        pdf.multi_cell(w - 35, 5, cmds)
    pdf.ln(4)
    pdf.chapter_title("Tips")
    pdf.bullet("Equip gear before raiding - /stats shows your damage range.")
    pdf.bullet("Match set pieces for the damage bonus.")
    pdf.bullet("Heal downed teammates - you earn nuggets and achievements.")
    pdf.bullet("Craft battle-worn drops instead of selling them cheap.")
    pdf.bullet("Use /stats public:true to show off your build in channel.")
    pdf.bullet("Check /quests daily for bonus nuggets.")
    pdf.bullet("Duel prepared opponents - gear and HP matter.")
    pdf.bullet("Cast a spell before /attack or /duel for bonus damage or defense.")
    pdf.bullet("Warden healers: /cast mend, then keep raiding without burning out on mana.")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(OUTPUT))
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    build()
