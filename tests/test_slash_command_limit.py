"""Guard Discord's 100 top-level global slash-command limit."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

COGS_DIR = Path(__file__).resolve().parents[1] / "cogs"
DISCORD_GLOBAL_SLASH_LIMIT = 100


def count_top_level_slash_commands() -> list[str]:
    """Return top-level command/group names registered via cog class attributes."""
    names: list[str] = []
    for path in sorted(COGS_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(
            r"=\s*app_commands\.Group\(\s*(?:\n\s*)?name\s*=\s*[\"']([^\"']+)",
            text,
        ):
            names.append(match.group(1))
        for match in re.finditer(
            r"@app_commands\.command\(\s*(?:\n\s*)?name\s*=\s*[\"']([^\"']+)",
            text,
        ):
            names.append(match.group(1))
    return names


class SlashCommandLimitTests(unittest.TestCase):
    def test_under_discord_global_limit(self) -> None:
        names = count_top_level_slash_commands()
        self.assertLess(
            len(names),
            DISCORD_GLOBAL_SLASH_LIMIT,
            msg=(
                f"{len(names)} top-level slash commands — Discord allows "
                f"{DISCORD_GLOBAL_SLASH_LIMIT}. Consolidate into Groups."
            ),
        )

    def test_expected_groups_exist(self) -> None:
        names = set(count_top_level_slash_commands())
        for group in ("class", "relics", "aspects", "crew", "drugs", "business", "admin"):
            self.assertIn(group, names)


if __name__ == "__main__":
    unittest.main()
