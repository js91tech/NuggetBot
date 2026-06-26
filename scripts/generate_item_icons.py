#!/usr/bin/env python3
"""Bake AI-generated item art into 64×64 shop icons.

Place full-size AI renders in assets/sprites/generated/ using the filenames
defined in utils/item_art.AI_ITEM_SOURCES, then run:

  python scripts/generate_item_icons.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils.item_art import GENERATED_ROOT, NEW_ITEM_IDS, write_item_icons


def main() -> int:
    parser = argparse.ArgumentParser(description="Bake AI item art into shop icons.")
    parser.add_argument(
        "item_ids",
        nargs="*",
        help="Item IDs to bake (default: all new endgame items)",
    )
    parser.add_argument(
        "--source-dir",
        default=str(GENERATED_ROOT),
        help="Directory containing AI source PNGs",
    )
    parser.add_argument(
        "--out-dir",
        default=str(ROOT / "assets" / "items"),
        help="Output directory for 64×64 icons",
    )
    args = parser.parse_args()
    ids = tuple(args.item_ids) if args.item_ids else NEW_ITEM_IDS
    paths = write_item_icons(ids, out_dir=Path(args.out_dir), source_dir=Path(args.source_dir))
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
