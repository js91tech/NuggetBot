# Palworld Companion

A fan-made web app to **search Palworld resources** and find out **how** and **where** to get them on Palpagos Islands.

**Live demo:** Deploy via [Vercel](https://vercel.com/new) after connecting this repository.

## Features

- **Instant search** — filter by name, location, Pal drops, crafting stations, and more
- **Category filters** — Basic Materials, Ores, Organic, Refined, Pal Drops, Late Game, Consumables
- **Progression tiers** — Early, Mid, Late, and Endgame resources
- **Detailed resource pages** — acquisition methods, map locations, related Pals, crafting recipes, and tips
- **60+ curated resources** covering the full progression from Wood to Hexolite

## Quick Start

```bash
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) in your browser.

## Build for Production

```bash
npm run build
npm run preview
```

## Deploy to Vercel

### Option A — Vercel Dashboard (recommended)

1. Push this repo to GitHub
2. Go to [vercel.com/new](https://vercel.com/new)
3. Import the `palworld-companion` repository
4. Vercel auto-detects Vite — click **Deploy**

### Option B — Vercel CLI

```bash
npm install -g vercel
vercel --prod
```

### Option C — GitHub Actions

Add these secrets to the repository:

- `VERCEL_TOKEN`
- `VERCEL_ORG_ID`
- `VERCEL_PROJECT_ID`

Push to `main` and the included workflow deploys automatically.

## Publish to GitHub

```bash
chmod +x scripts/publish.sh
./scripts/publish.sh
```

## Tech Stack

- [Vite](https://vitejs.dev/) + [React](https://react.dev/) + TypeScript
- Static resource database (no backend required)
- Responsive layout with mobile-friendly detail panel

## Data Sources

Resource information is compiled from community wikis and game knowledge:

- [Palworld Wiki](https://palworld.wiki.gg/)
- [Palworld Fandom](https://palworld.fandom.com/)

This is an unofficial fan project and is not affiliated with Pocketpair.

## License

MIT
