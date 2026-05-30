# NuggetBot

A chaos-driven Discord economy bot built with **discord.py** and PostgreSQL or SQLite.

## Features

| Phase | Module | Description |
|-------|--------|-------------|
| 1 | **The Vault** | Economy: passive chat earning, active bonus, VC earning, daily claims, payments, leaderboards |
| 2 | **The Hit** | Bounty system: place bounties with trigger words, claim when targets slip up |
| 3 | **The Steal** | Heist & crew system: rob users, form crews, arrest failed thieves |
| 4 | **The Virus** | Hot potato: infect users, give every holder a timer, scaling penalties |
| 5 | **The Boss** | Boss raids: fight Hannah variants, scale HP with economy, down/heal mechanics |
| 6 | **The AI** | Imposter webhook word sabotage + Lore Roulette trivia |

## Quick Start

### 1. Prerequisites

- Python 3.11+
- A Discord bot token
- Message Content intent enabled in the Discord Developer Portal
- Optional: an OpenAI-compatible API key for the Imposter module

### 2. Install

```bash
pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
```

Edit `.env` and fill in `DISCORD_TOKEN`. If you want the Imposter AI module
to call an external service, also set `AI_API_KEY`, `AI_API_URL`, and `AI_MODEL`.
For the web dashboard, set `DASHBOARD_TOKEN` to a long random secret.

For production on Railway, set the bot service's `DATABASE_URL` to reference
your Railway PostgreSQL database. SQLite remains available for local development
or deployments with a persistent volume.

### 4. Run

```bash
python bot.py
```

## Commands

### Economy

| Command | Description |
|---------|-------------|
| `/daily` | Claim 75 nuggets daily |
| `/balance` | Pocket + bank panel — deposit, withdraw, **+10k storage** upgrades |
| `/deposit amount` | Move nuggets pocket → bank (50k base cap, upgradeable to 500k) |
| `/withdraw amount` | Move nuggets bank → pocket |
| `/leaderboard` | Top 10 by net worth (pocket + bank) |
| `/pay @user amount` | Send nuggets from your pocket to another user |

### Bounty

| Command | Description |
|---------|-------------|
| `/bounty @user amount trigger_word` | Place a bounty (min 50 + tax) |
| `/bounties` | List active bounties |

### Heist

| Command | Description |
|---------|-------------|
| `/heist @user [@crew1] [@crew2]` | Rob a user's **pocket** (wallet) |
| `/bank-heist @user` | High-risk vault robbery — tier panel steals from **bank** |
| `/arrest @thief` | Arrest a failed wallet heist thief (5-minute window) |
| `/fix` | Repair unstable gear after a failed Tier 3 bank heist |

**Bank heist tiers**

| Tier | Success | Loot | Fail jail | Extra penalty |
|------|---------|------|-----------|---------------|
| 1 | 10% | 10% of bank | 120 min | — |
| 2 | 8% | 20% of bank | 4 hours | — |
| 3 | 5% | 35% of bank | 12 hours | 60% chance one equipped item becomes **unstable** |

Unstable gear gives **no stat bonuses** until repaired for **80%** of the item's shop price (`/fix`).

| `/crew` | Interactive crew panel |

### Hacker

| Command | Description |
|---------|-------------|
| `/hack @user` | Start the hannah hentai hanta virus; usable every 5 minutes per user |
| `/transfer @user` | Pass the virus to someone else and give them the timer |

### Boss

| Command | Description |
|---------|-------------|
| `/boss` | Open the interactive raid fight panel (attack, refresh, leaderboard) |
| `/attack` | Strike the boss once (includes fight panel buttons) |
| `/boss-status` | Quick HP check without buttons |
| `/heal @user` | Revive a downed teammate |
| `/summon variant` | **Admin only:** force-spawn a boss |

### Raid avatars

| Command | Description |
|---------|-------------|
| `/avatar` | List, buy, equip, or preview victory-pose art for your character |
| | Victory GIF/PNG shows on **duel wins** and when you land the **boss killing blow** |

Five avatars ship with the bot (`nugget_raider` is free). Regenerate art with `python3 scripts/generate_avatar_assets.py`.

### QoL & DLC commands

| Command | Description |
|---------|-------------|
| `/help` | Paginated command guide |
| `/profile` | Wallet, class, ELO, crew, cooldowns |
| `/loadout` | Save/apply gear presets (slots 1–3) |
| `/equip-best` | Auto-equip best weapon and armor |
| `/sell-worn` | Sell all battle-worn drops |
| `/use` | Raid potion, energy drink, duel scroll |
| `/dungeon` | Solo 5-room dungeon run |
| `/crew` | Interactive crew panel — join, deposit, withdraw, loans, repay |
| `/slots` · `/jackpot` | Casino slots + server jackpot |

### Crew banking

Persistent crews share a **treasury** funded by member deposits. Deposits earn crew **XP** and raise **level**, which unlocks higher loan caps and lower interest.

| `/crew` action | What it does |
|----------------|--------------|
| **Bank / status** | Opens the interactive crew panel (treasury, deposits, loans, buttons) |
| **Deposit** | Move nuggets from your wallet into the crew treasury (tracks your contribution) |
| **Withdraw** | Pull up to what you deposited (blocked while you owe a loan) |
| **Loan** | Borrow from the treasury within your crew level cap (min 50 nuggets) |
| **Repay** | Pay down your loan; interest returns to the treasury |
| **Leave** | Blocked until your crew loan is paid off |

`/heist` still gives +10% per tagged `crew1`/`crew2`. Members in the same **persistent** `/crew` roster add **+5%** success each (up to +15%).

### Territories

Five zones per server (**Docks** → **Citadel**). Crews hold zones for **hourly nuggets paid into crew treasury** (1,800–6,000/hr by tier).

| `/territory` action | What it does |
|---------------------|--------------|
| **Map / status** | Interactive territory map — hire guards on held zones (+1/+5 wallet or treasury) |
| **Attack / claim** | Claim neutral zones instantly; contested zones enter a **30 min** siege |
| **Buy guards** | Spend nuggets to add defenders (improves hold chance when sieged) |
| **Abandon** | Release a zone your crew holds |

Max **3 zones per crew**. Siege cooldown **12h** per zone after an attack.

**Zone perks** (while your crew holds the zone): Docks +5% heist loot · Market +5% sell · Foundry −5% craft cost · Vault +3% heist success · Citadel +10% Citadel income.
| `/fuse-aspects` | Burn 3 aspects → stronger roll |

### Shop and Gear

| Command | Description |
|---------|-------------|
| `/shop [category]` | Visual item grid with buy buttons (pocket balance) |
| `/shop-list [category]` | Text catalog fallback |
| `/buy item_id` | Buy a weapon or armor piece |
| `/inventory [user]` | View owned and equipped gear |
| `/equip item_id` | Equip an owned weapon or armor piece |

Weapons define base boss damage (plus a 1–5 roll) and tier crit chance; unarmed
attacks deal 1–15. Armor adds HP and percentage mitigation on Hannah's counters.
The best weapon and armor each cost 120,000 nuggets.

Top-end items:

| Type | Item | Price | Effect |
|------|------|-------|--------|
| Weapon | Nugget Excalibur | 120,000 | 295 base damage, 16% crit |
| Armor | Nugget Immortal Plate | 120,000 | ~67% mitigation, +345 HP |

Use `/set-main-channel` so boss spawns, defeat payouts, and coin drops post in one
place instead of random channels.

### Trivia

| Command | Description |
|---------|-------------|
| `/trivia` | Lore Roulette: guess the blanked word from server history |

### Admin Dashboard

All dashboard commands require Discord administrator permission.

| Command | Description |
|---------|-------------|
| `/gift @user amount` | Give nuggets to one user from thin air |
| `/gift-all amount` | Give nuggets to every human in the server |
| `/set-currency @user amount` | Set a user's wallet to an exact amount |
| `/reset-user @user` | Wipe a user's wallet and stats |
| `/config` | View all live tuneable settings |
| `/config setting value` | Change a setting live for this server |
| `/config-reset setting` | Revert a setting to its default |
| `/bot-status` | View economy totals, active games, and custom settings |
| `/set-main-channel #channel` | Boss spawns, defeats, and coin drops post here |
| `/clear-main-channel` | Revert announcements to system-channel fallback |
| `/despawn-boss` | Despawn this server's active boss |
| `/despawn-all-bosses` | Emergency clear every active boss session |

## Live Config

`/config` includes autocomplete for setting names. Settings are stored per
server and take effect without restarting the bot.

| Setting | Default | Description |
|---------|---------|-------------|
| `passive_chat_reward` | 0.5 | Per-message earning |
| `passive_active_bonus` | 15.0 | Per active hour earning |
| `voice_chat_reward` | 3.0 | Per minute in VC |
| `daily_reward` | 75.0 | `/daily` claim amount |
| `bounty_min_amount` | 50.0 | Minimum bounty |
| `bounty_bot_tax` | 5.0 | Bot tax on bounties |
| `heist_base_success` | 0.20 | Heist success rate |
| `heist_cooldown_seconds` | 1800 | Heist cooldown |
| `arrest_lockout_seconds` | 3600 | Arrest lockout duration |
| `hack_timer_seconds` | 60 | Hot potato timer |
| `hack_base_penalty` | 15.0 | Starting virus penalty |
| `hack_penalty_increment` | 2.0 | Penalty increase per pass |
| `hack_cooldown_seconds` | 300 | Per-user `/hack` cooldown |
| `boss_health_scale_factor` | 0.02 | Boss HP scaling (base capped at 15,000 before variant multiplier) |
| `boss_downed_seconds` | 120 | Boss downed duration |
| `imposter_chance` | 0.01 | Per-message sabotage chance |
| `trivia_reward` | 25.0 | Trivia answer reward |

## Web Dashboard

NuggetBot includes a browser dashboard served by the same bot process, so it can
run on the same Railway service without a second paid app. It shows server
economy totals, active bosses/viruses, bounty counts, custom config settings,
and top wallets.

Set these variables in Railway:

| Variable | Description |
|----------|-------------|
| `DASHBOARD_TOKEN` | Required to view dashboard data. Use a long random secret. |
| `DASHBOARD_ENABLED` | Optional, defaults to `true`. Set `false` to disable the server. |
| `PORT` | Railway sets this automatically. |

Routes:

| Route | Description |
|-------|-------------|
| `/` or `/dashboard` | Login-protected HTML dashboard |
| `/api/status` | Login/header-token protected JSON status |
| `/health` | Public health check that does not expose server data |

You can log in through the form or send `X-Dashboard-Token: your-token` for API
requests. If `DASHBOARD_TOKEN` is missing, only `/health` returns normal data.

### Accessing the dashboard on Railway

1. In Railway, open the NuggetBot service.
2. Go to **Variables** and add `DASHBOARD_TOKEN` with a long random value.
3. Redeploy the service.
4. Open the service's public Railway domain. If one is not shown, go to
   **Settings -> Networking** and generate a public domain.
5. Visit `https://your-railway-domain.up.railway.app/dashboard` and log in with
   the `DASHBOARD_TOKEN` value.

## Railway persistence

Coins, inventory, equipped gear, config, and boss state are stored in the
configured database. On Railway, PostgreSQL is recommended:

1. Add a Railway PostgreSQL database.
2. In the NuggetBot service, set `DATABASE_URL` to the PostgreSQL service's
   internal `DATABASE_URL` reference.
3. If Railway logs show `socket.gaierror: Name or service not known`, also set
   `DATABASE_PUBLIC_URL` to the PostgreSQL service's `DATABASE_PUBLIC_URL`.
   The bot tries `DATABASE_URL` first, then falls back to `DATABASE_PUBLIC_URL`.
4. Redeploy the service.

On Railway, the bot now refuses to use SQLite unless `ALLOW_SQLITE_ON_RAILWAY`
is explicitly set to `true`. This prevents accidental local-file storage that
gets wiped on redeploy. For local development, SQLite still works when Railway
environment variables are absent.

## One-time launch grant

This job is disabled by default. If `LAUNCH_GRANT_ENABLED=true`, for server
`1388136234827649116`, the bot will:

- gift every human member 150 nuggets
- grant and equip a Training Stick and Cardboard Shield
- clear any existing boss for that server
- spawn one normal 500 HP Hannah
- announce the gift in chat

The grant is tracked in the database per member, but keep
`LAUNCH_GRANT_ENABLED=false` after the grant has been run. This prevents a fresh
or misconfigured database from repeating the welcome gift.

## Security and permissions

- The bot token is read only from environment variables and is never logged.
- `/summon` and all dashboard commands are protected with Discord's
  administrator permission check.
- Despawn controls are admin-only and can clear stuck boss sessions after
  deployment issues.
- Webhook reposts use `AllowedMentions.none()` so altered messages cannot
  trigger accidental mass mentions.
- Economy debit paths validate funds and never create negative balances.
- External AI calls are optional, use a timeout, and require HTTPS unless the
  URL points at localhost.
- The web dashboard does not expose bot data unless `DASHBOARD_TOKEN` is set
  and provided by the browser or API client.

## Project Structure

```text
NuggetBot/
├── bot.py
├── config.py
├── dashboard.py
├── database.py
├── requirements.txt
├── .env.example
├── cogs/
│   ├── economy.py
│   ├── bounty.py
│   ├── heist.py
│   ├── hacker.py
│   ├── boss.py
│   ├── shop.py
│   ├── imposter.py
│   ├── trivia.py
│   └── admin.py
├── utils/
│   └── helpers.py
├── models/
│   └── __init__.py
└── docs/
    └── ARCHITECTURE.md
```
