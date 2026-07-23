# Palworld Companion

A fan-made web app to **search Palworld resources** and find out **how** and **where** to get them on Palpagos Islands.

![Palworld Companion](https://img.shields.io/badge/Palworld-Companion-4ade80?style=flat-square)

## Features

- **Instant search** — filter by name, location, Pal drops, crafting stations, and more
- **Category filters** — Basic Materials, Ores, Organic, Refined, Pal Drops, Late Game, Consumables
- **Progression tiers** — Early, Mid, Late, and Endgame resources
- **Detailed resource pages** — acquisition methods, map locations, related Pals, crafting recipes, and tips
- **60+ curated resources** covering the full progression from Wood to Hexolite

## Standalone Repository

This app lives in the `palworld-companion/` folder. To use it as its own GitHub repository:

```bash
cd palworld-companion
git init
gh repo create palworld-companion --public --source=. --push
```

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

The static site in `dist/` can be deployed to GitHub Pages, Vercel, Netlify, or any static host.

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
