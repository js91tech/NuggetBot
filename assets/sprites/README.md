# Shop sprite sheets

Drop source PNGs here, then run from repo root:

```bash
python scripts/slice_shop_sprites.py
```

Expected filenames:

- `shop_sheet.png` — normal item grid (8×7)
- `battle_worn_sheet.png` — battle-worn variants (same layout)

Output icons are written to `assets/items/` as `{item_id}.png` and `boss_weak_{item_id}.png`.

For new endgame items that are not yet on the sheet, add AI-generated full renders under
`assets/sprites/generated/` (see filenames in `utils/item_art.AI_ITEM_SOURCES`), then bake
64×64 shop icons:

```bash
python scripts/generate_item_icons.py
```
