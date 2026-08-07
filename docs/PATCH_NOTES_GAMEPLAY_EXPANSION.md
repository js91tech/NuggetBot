# Gameplay Expansion Patch Notes

## PR1 — Foundation
- New DB tables: relics, affixes, companions, blueprints, contracts, museum, expeditions, phenotypes, crew legacy, territory cosmetics, season tokens
- Config constants in `config.py`
- Shared utils: `relics`, `companions`, `affixes`, `blueprints`, `contracts`, `museum`, `expeditions`, `phenotypes`, `expansion_bonuses`, `expansion_events`, `expansion_loot`
- `database_expansion.py` mixin

## PR2 — Combat Chase
- `/relics`, `/relics equip`, `/relics unequip`
- `/companion` (status/equip/unequip)
- Gear affixes on dungeon accessory drops
- Alchemy expanded to 9 recipes with blueprint gates
- New consumables and crafting materials

## PR3 — Progression Chase
- `/season` shop + token redemption
- `/codex` blueprint viewer
- `/contracts` dynamic contract board
- `/museum` collection meta with passive bonuses

## PR4 — Social Endgame
- `/expedition` cooperative server events
- Crew legacy + territory cosmetics on siege wins
- `/drugs crossbreed` phenotype discovery
- Boss/vault relic and companion drops

## New Commands
`/relics` · `/relics equip` · `/relics unequip` · `/companion` · `/codex` · `/contracts` · `/museum` · `/expedition` · `/drugs crossbreed` · `/season` (shop/redeem)
