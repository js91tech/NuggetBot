from __future__ import annotations

from dataclasses import dataclass

import config
from utils.classes import get_modifiers
from utils.combat_engine import max_hp_from_armor
from utils.loadout import parse_loadout
from utils.skills import SkillDef, get_skill, skill_available, spell_buff_from_skill
from utils.spell_effects import combat_state_from_spell


@dataclass
class CastResult:
    ok: bool
    message: str = ""
    error: str | None = None


async def cast_skill_for_user(
    db,
    user_id: int,
    guild_id: int,
    skill_id: str,
    *,
    class_id: str | None = None,
) -> CastResult:
    if class_id is None:
        class_id = await db.get_class_id(user_id, guild_id)
    if not class_id:
        return CastResult(ok=False, error="Choose a class with `/class choose` first.")

    skill_def = get_skill(skill_id)
    if skill_def is None or not skill_available(skill_def, class_id):
        return CastResult(ok=False, error="Unknown or locked skill.")

    ok, err = await db.spend_mana(user_id, guild_id, skill_def.mana_cost)
    if not ok:
        snap = await db.get_mana_snapshot(user_id, guild_id)
        return CastResult(
            ok=False,
            error=f"Not enough mana. Need **{skill_def.mana_cost}**, you have **{snap.current}/{snap.cap}**.",
        )

    state = combat_state_from_spell(spell_buff_from_skill(skill_def))
    extra_lines: list[str] = []

    if state.heal_self_fraction > 0:
        equipment = await db.get_equipment(user_id, guild_id)
        loadout = parse_loadout(equipment)
        max_hp = float(
            max_hp_from_armor(loadout.armor, class_modifiers=get_modifiers(class_id))
        )
        heal = max(1, int(max_hp * state.heal_self_fraction))
        await db.heal_player(user_id, guild_id, float(heal), max_hp)
        extra_lines.append(f"Restored **{heal}** HP.")

    if state.heal_ally_fraction > 0:
        await db.set_pending_spell(user_id, guild_id, skill_def.skill_id)
        extra_lines.append(
            f"**{skill_def.name}** ready — your next heal pays "
            f"**+{int(state.heal_ally_fraction * 100)}%** bonus reward."
        )

    if state.income_bonus > 0:
        from utils.helpers import fmt_amount

        await db.credit_wallet(user_id, guild_id, state.income_bonus)
        extra_lines.append(f"Gained **{fmt_amount(state.income_bonus)}** nuggets.")

    if state.heist_bonus > 0:
        await db.add_heist_spell_bonus(user_id, guild_id, state.heist_bonus)
        extra_lines.append(
            f"Next heist gains **+{int(state.heist_bonus * 100)}%** success chance."
        )

    if (
        (state.damage_mult > 1.0 or state.fortify_mult < 1.0 or state.extra_crit > 0)
        and state.heal_ally_fraction <= 0
        and state.heal_self_fraction <= 0
    ):
        await db.set_pending_spell(user_id, guild_id, skill_def.skill_id)
        extra_lines.append(
            f"**{skill_def.name}** charged — next attack within "
            f"{config.PENDING_SPELL_SECONDS}s."
        )

    snap = await db.get_mana_snapshot(user_id, guild_id)
    desc = (
        f"{skill_def.emoji} **{skill_def.name}** cast (−{skill_def.mana_cost} mana).\n"
        f"Mana: **{snap.current}/{snap.cap}**"
    )
    if extra_lines:
        desc += "\n" + "\n".join(extra_lines)
    return CastResult(ok=True, message=desc)


def skill_choices_for_class(class_id: str) -> list[SkillDef]:
    from utils.skills import skills_for_class

    return list(skills_for_class(class_id))
