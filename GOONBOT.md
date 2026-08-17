# GoonBot

**Adult (18+) Discord economy RPG** — a full fork of NuggetBot with explicit NSFW flavor, 18+ age gates, NSFW-channel requirements, and interactive Discord menus for every major player system.

> **Do not merge this branch into NuggetBot `main`.** Deploy GoonBot as its own Discord application, Railway service, and database (ideally its own GitHub repo).

## Product differences from NuggetBot

| Area | GoonBot |
|------|---------|
| Brand | GoonBot · currency **goonbux** 💋 |
| Boss | **Velvet Vixen** (and adult specials) |
| Safety | First-use **18+ confirm**; guild setting `nsfw_channel_only` (default on) |
| UX | Hub panels (Views/Selects/Buttons) for profile, gear, jobs, character, alchemy, companions, relics, museum, crime, casino, dungeon lobby, drugs extras, contracts, expeditions, season, chaos |

## Deploy (new stack)

1. Create a **new Discord application** + bot token (do not reuse NuggetBot’s token).
2. Enable **Message Content** + privileged intents as in the parent project.
3. Prefer a **new GitHub repo** (`GoonBot`). Until then, this branch is the seed — never merge NSFW into NuggetBot production.
4. New **Railway** project + **PostgreSQL**. Set:
   - `DISCORD_TOKEN`
   - `DATABASE_URL` (Postgres)
   - `DASHBOARD_TOKEN` (strong secret)
   - Optional: `GUILD_ID` for guild-scoped slash sync during testing
   - Optional: `DASHBOARD_ENABLED`, `PORT`
5. Invite the bot only to **18+ / NSFW** servers. Mark play channels as Discord **NSFW**.
6. Admins can set live config `nsfw_channel_only` to `0` to allow non-NSFW channels (not recommended for public servers).

## Age / NSFW gates

- Unverified users get an ephemeral **I am 18+ / I am under 18** panel; under-18 is refused.
- When `nsfw_channel_only` is on (default), slash commands fail outside NSFW channels (server admins can still run commands for setup).
- No sexual content involving minors is permitted in code, lore, items, or bosses.

## Player hubs (entry points)

- `/profile` — launcher
- `/jobs`, `/inventory` (self), `/class view`, `/alchemy list`, `/companion status`, `/relics list`, `/museum`
- `/heist` (no target), `/bounty-board`, `/casino`, `/dungeon` (no run), `/drugs stash`
- `/contracts list`, `/expedition` status, `/season` status/shop
- Chaos: trivia answer button on rounds; Chaos Hub via meta panel wiring

## Local run

```bash
pip install -r requirements.txt
cp .env.example .env   # if present; set DISCORD_TOKEN
python3 bot.py
```

SQLite default path is `goonbot.sqlite3` (or volume `/data/goonbot.sqlite3`).

## Tests

```bash
python3 -m pytest tests/ -q
```

Guards include slash-command count &lt; 100, hub panel smoke tests, and age-gate unit tests.

## License / content warning

Adult erotic game content. Operators are responsible for Discord ToS, local law, and keeping the bot out of underage spaces.
