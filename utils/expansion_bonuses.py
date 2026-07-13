from __future__ import annotations

from dataclasses import dataclass

from utils.affixes import AffixBonuses, merge_affix_bonuses
from utils.companions import CompanionBonuses, merge_companion_bonuses
from utils.museum import museum_bonuses_for_pct
from utils.relics import RelicBonuses, merge_relic_bonuses


@dataclass
class ExpansionBonuses:
    damage_mult: float = 1.0
    boss_damage_mult: float = 1.0
    crit_bonus: float = 0.0
    mitigation_bonus: float = 0.0
    hp_bonus: int = 0
    energy_regen_mult: float = 1.0
    duel_steal_mult: float = 1.0
    work_income_mult: float = 1.0
    income_mult: float = 1.0
    raid_heal_chance: float = 0.0
    enhance_safety_charges: int = 0
    alchemy_scrap_mult: float = 1.0
    dungeon_reward_mult: float = 1.0
    lifesteal_pct: float = 0.0
  # crew legacy
    crew_income_mult: float = 1.0


def merge_expansion_bonuses(
    *,
    relic: RelicBonuses | None = None,
    companion: CompanionBonuses | None = None,
    affix: AffixBonuses | None = None,
    museum_pct: float = 0.0,
    crew_legacy_income: float = 1.0,
) -> ExpansionBonuses:
    relic_b = merge_relic_bonuses([relic]) if relic else RelicBonuses()
    comp_b = merge_companion_bonuses([companion]) if companion else CompanionBonuses()
    affix_b = merge_affix_bonuses([affix]) if affix else AffixBonuses()
    museum_income, museum_damage, _ = museum_bonuses_for_pct(museum_pct)

    out = ExpansionBonuses()
    out.damage_mult = relic_b.damage_mult * comp_b.damage_mult * affix_b.damage_mult * museum_damage
    out.boss_damage_mult = relic_b.boss_damage_mult * comp_b.boss_damage_mult * affix_b.boss_damage_mult
    out.crit_bonus = relic_b.crit_bonus + comp_b.crit_bonus + affix_b.crit_bonus
    out.mitigation_bonus = relic_b.mitigation_bonus + affix_b.mitigation_bonus
    out.hp_bonus = relic_b.hp_bonus
    out.energy_regen_mult = relic_b.energy_regen_mult
    out.duel_steal_mult = relic_b.duel_steal_mult * comp_b.duel_steal_mult
    out.work_income_mult = relic_b.work_income_mult * comp_b.work_income_mult * affix_b.work_income_mult
    out.income_mult = museum_income * crew_legacy_income
    out.raid_heal_chance = relic_b.raid_heal_chance
    out.enhance_safety_charges = relic_b.enhance_safety_charges
    out.alchemy_scrap_mult = relic_b.alchemy_scrap_mult * comp_b.alchemy_scrap_mult
    out.dungeon_reward_mult = relic_b.dungeon_reward_mult * comp_b.dungeon_reward_mult
    out.lifesteal_pct = affix_b.lifesteal_pct
    out.crew_income_mult = crew_legacy_income
    return out
