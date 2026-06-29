"""Back-compat wrappers for crew bank raids."""
from __future__ import annotations

from utils.crew_raid_ui import (
    RaidKind,
    run_crew_raid,
    send_crew_raid_panel,
)

RAID_ERROR_MESSAGES = {
    "not_in_crew": "Join a crew before raiding another crew's bank.",
    "same_crew": "You cannot raid your own crew.",
    "invalid_defender": "That crew does not exist.",
}


async def send_crew_bank_raid_panel(cog, interaction, target_crew: str) -> None:
    await send_crew_raid_panel(cog, interaction, target_crew, RaidKind.BANK)


async def run_crew_bank_raid(cog, interaction, **kwargs) -> None:
    await run_crew_raid(cog, interaction, kind=RaidKind.BANK, **kwargs)
