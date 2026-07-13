from __future__ import annotations

import random
import time
from dataclasses import dataclass

import config
from utils.helpers import fmt_amount


@dataclass(frozen=True)
class ContractDefinition:
    contract_id: str
    name: str
    description: str
    target: int
    event: str
    reward_nuggets: float
    reward_tokens: int = 0
    reward_item_id: str | None = None
    reward_qty: int = 1


CONTRACT_POOL: tuple[ContractDefinition, ...] = (
    ContractDefinition(
        "contract_boss_damage", "Raid Pressure", "Deal 5,000 boss damage.", 5000, "boss_damage",
        1200.0, reward_tokens=5,
    ),
    ContractDefinition(
        "contract_boss_hits", "Hit Squad", "Land 8 boss attacks.", 8, "boss_attack",
        800.0, reward_tokens=3,
    ),
    ContractDefinition(
        "contract_duel_wins", "Duelist", "Win 2 duels.", 2, "duel_win",
        1000.0, reward_tokens=8,
    ),
    ContractDefinition(
        "contract_dungeon", "Delver", "Clear a dungeon.", 1, "dungeon_clear",
        1500.0, reward_item_id="dungeon_essence", reward_qty=2,
    ),
    ContractDefinition(
        "contract_heal", "Triage", "Heal raiders 4 times.", 4, "boss_heal",
        700.0, reward_tokens=2,
    ),
    ContractDefinition(
        "contract_craft", "Workshop", "Craft any item.", 1, "craft_done",
        900.0, reward_item_id="alchemy_scrap", reward_qty=3,
    ),
    ContractDefinition(
        "contract_business", "Empire", "Collect business revenue.", 1, "business_collect",
        850.0, reward_tokens=4,
    ),
    ContractDefinition(
        "contract_drug_harvest", "Lab Run", "Harvest a crop.", 1, "drug_harvest",
        750.0, reward_item_id="harvest_resin",
    ),
    ContractDefinition(
        "contract_territory", "Land Grab", "Participate in a territory siege.", 1, "territory_siege",
        1100.0, reward_tokens=6,
    ),
    ContractDefinition(
        "contract_expedition", "Community", "Contribute to an expedition.", 1, "expedition_contribute",
        600.0, reward_tokens=5,
    ),
    ContractDefinition(
        "contract_work", "Day Job", "Complete 4 job shifts.", 4, "job_work",
        650.0, reward_item_id="business_waste", reward_qty=2,
    ),
    ContractDefinition(
        "contract_heist", "Heist Pro", "Succeed at a wallet heist.", 1, "heist_success",
        1000.0, reward_tokens=4,
    ),
    ContractDefinition(
        "contract_frost_boss", "Frost Raider", "Deal 3,000 frost boss damage.", 3000, "boss_frost_damage",
        1300.0, reward_item_id="void_hardener",
    ),
    ContractDefinition(
        "contract_shade_duel", "Shadow Duelist", "Win a duel as Shade path.", 1, "duel_win_shade",
        1200.0, reward_tokens=10,
    ),
    ContractDefinition(
        "contract_stock", "Trader", "Buy or sell crew stock.", 1, "stock_trade",
        800.0, reward_tokens=3,
    ),
    ContractDefinition(
        "contract_enhance", "Blacksmith", "Attempt gear enhancement.", 1, "enhance_attempt",
        500.0, reward_item_id="alchemy_scrap", reward_qty=2,
    ),
    ContractDefinition(
        "contract_gamble", "High Roller", "Win a casino game.", 1, "gamble_win",
        700.0,
    ),
    ContractDefinition(
        "contract_crew_deposit", "Treasurer", "Deposit to crew treasury.", 1, "crew_deposit",
        600.0, reward_tokens=2,
    ),
    ContractDefinition(
        "contract_crossbreed", "Alchemist", "Crossbreed strains.", 1, "drug_crossbreed",
        900.0, reward_item_id="phenotype_catalyst",
    ),
    ContractDefinition(
        "contract_vault", "Vault Raider", "Clear Gilded Vault.", 1, "dungeon_vault_clear",
        2000.0, reward_tokens=12, reward_item_id="void_hardener", reward_qty=1,
    ),
)

CONTRACT_MAP = {c.contract_id: c for c in CONTRACT_POOL}


def pick_active_contracts(count: int = 3) -> list[ContractDefinition]:
    pool = list(CONTRACT_POOL)
    random.shuffle(pool)
    return pool[:count]


def contract_refresh_deadline(now: float | None = None) -> float:
    ts = time.time() if now is None else now
    interval = config.CONTRACT_REFRESH_SECONDS
    return (int(ts // interval) + 1) * interval


def format_contract_reward(defn: ContractDefinition) -> str:
    parts: list[str] = []
    if defn.reward_nuggets > 0:
        parts.append(fmt_amount(defn.reward_nuggets))
    if defn.reward_tokens > 0:
        parts.append(f"{defn.reward_tokens} season tokens")
    if defn.reward_item_id:
        parts.append(f"`{defn.reward_item_id}` ×{defn.reward_qty}")
    return " + ".join(parts) if parts else "—"
