#!/usr/bin/env python3
"""Generate unique shop item icons from reference tier sprites."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils.item_art import NEW_ITEM_IDS, write_item_icons


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate shop item icons.")
    parser.add_argument(
        "item_ids",
        nargs="*",
        help="Item IDs to generate (default: all new endgame items)",
    )
    parser.add_argument(
        "--out-dir",
        default=str(ROOT / "assets" / "items"),
        help="Output directory for PNG icons",
    )
    args = parser.parse_args()
    ids = tuple(args.item_ids) if args.item_ids else NEW_ITEM_IDS
    paths = write_item_icons(ids, Path(args.out_dir))
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
