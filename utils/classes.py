from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import config

CombatRole = Literal["bulwark", "striker", "skirmisher"]
ClassTier = Literal["starter", "evolved", "master", "hybrid", "special"]
Element = Literal["fire", "frost", "storm", "void", "verdant"]


@dataclass(frozen=True)
class ClassModifiers:
    income_mult: float = 1.0
    job_payout_mult: float = 1.0
    duel_damage_mult: float = 1.0
    duel_mitigation_mult: float = 1.0
    boss_damage_mult: float = 1.0
    max_hp_mult: float = 1.0
    crit_bonus: float = 0.0
    heist_success_bonus: float = 0.0
    heist_success_penalty: float = 0.0
    counter_damage_taken_mult: float = 1.0

    def scaled(self, mult: float) -> ClassModifiers:
        if mult == 1.0:
            return self
        return ClassModifiers(
            income_mult=self.income_mult * mult,
            job_payout_mult=self.job_payout_mult * mult,
            duel_damage_mult=self.duel_damage_mult * mult,
            duel_mitigation_mult=self.duel_mitigation_mult * mult,
            boss_damage_mult=self.boss_damage_mult * mult,
            max_hp_mult=self.max_hp_mult * mult,
            crit_bonus=self.crit_bonus * mult,
            heist_success_bonus=self.heist_success_bonus * mult,
            heist_success_penalty=self.heist_success_penalty * mult,
            counter_damage_taken_mult=self.counter_damage_taken_mult * mult,
        )


@dataclass(frozen=True)
class ClassDef:
    class_id: str
    name: str
    tier: ClassTier
    description: str
    parent_id: str | None = None
    children_ids: tuple[str, ...] = ()
    starter_root: str | None = None
    required_master_roots: frozenset[str] = field(default_factory=frozenset)
    element: Element | None = None
    combat_role: CombatRole | None = None
    modifiers: ClassModifiers = field(default_factory=ClassModifiers)
    emoji: str = "⚔️"


def _mod(**kwargs: float) -> ClassModifiers:
    return ClassModifiers(**{k: v for k, v in kwargs.items() if v != 1.0 and v != 0.0})


STARTER_BRANCHES: dict[str, tuple[tuple[str, str, CombatRole, ClassModifiers], ...]] = {
    "vanguard": (
        (
            "bulwark",
            "Bulwark",
            "bulwark",
            _mod(duel_mitigation_mult=1.12, max_hp_mult=1.08, duel_damage_mult=0.94, income_mult=0.95),
        ),
        (
            "slayer",
            "Slayer",
            "striker",
            _mod(duel_damage_mult=1.12, boss_damage_mult=1.10, duel_mitigation_mult=0.92),
        ),
        (
            "warden",
            "Warden",
            "skirmisher",
            _mod(boss_damage_mult=1.08, crit_bonus=0.02, counter_damage_taken_mult=0.92),
        ),
    ),
    "mogul": (
        (
            "prospector",
            "Prospector",
            "skirmisher",
            _mod(income_mult=1.10, job_payout_mult=1.12, duel_damage_mult=0.90),
        ),
        (
            "broker",
            "Broker",
            "bulwark",
            _mod(income_mult=1.14, job_payout_mult=1.08, heist_success_penalty=0.04),
        ),
        (
            "tycoon",
            "Tycoon",
            "striker",
            _mod(income_mult=1.18, job_payout_mult=1.06, duel_damage_mult=0.88, boss_damage_mult=0.92),
        ),
    ),
    "shade": (
        (
            "cutpurse",
            "Cutpurse",
            "skirmisher",
            _mod(heist_success_bonus=0.10, income_mult=1.04, duel_damage_mult=0.94),
        ),
        (
            "saboteur",
            "Saboteur",
            "striker",
            _mod(heist_success_bonus=0.08, boss_damage_mult=0.90, duel_damage_mult=1.06),
        ),
        (
            "phantom",
            "Phantom",
            "bulwark",
            _mod(heist_success_bonus=0.12, duel_mitigation_mult=0.90, counter_damage_taken_mult=1.08),
        ),
    ),
}

STARTER_DEFS: dict[str, tuple[str, str, Element, ClassModifiers]] = {
    "vanguard": (
        "Vanguard",
        "Front-line duelist and raid striker.",
        "fire",
        _mod(duel_damage_mult=1.06, boss_damage_mult=1.05, income_mult=0.94),
    ),
    "mogul": (
        "Mogul",
        "Passive income and job specialist.",
        "verdant",
        _mod(income_mult=1.10, job_payout_mult=1.08, duel_damage_mult=0.92),
    ),
    "shade": (
        "Shade",
        "Heist expert with risky combat profile.",
        "void",
        _mod(heist_success_bonus=0.06, duel_damage_mult=0.96, boss_damage_mult=0.96),
    ),
}

MASTER_NAMES: dict[str, tuple[tuple[str, str], tuple[str, str]]] = {
    "bulwark": (("ironwall", "Ironwall"), ("bastion", "Bastion")),
    "slayer": (("reaper", "Reaper"), ("ravager", "Ravager")),
    "warden": (("sentinel", "Sentinel"), ("hexward", "Hexward")),
    "prospector": (("veinlord", "Veinlord"), ("gilder", "Gilder")),
    "broker": (("ledger", "Ledger"), ("vaultkeeper", "Vaultkeeper")),
    "tycoon": (("magnate", "Magnate"), ("baron", "Baron")),
    "cutpurse": (("filcher", "Filcher"), ("blackhand", "Blackhand")),
    "saboteur": (("demolisher", "Demolisher"), ("incursor", "Incursor")),
    "phantom": (("nightshade", "Nightshade"), ("wraith", "Wraith")),
}


def _master_modifiers(branch_mod: ClassModifiers, branch_idx: int, master_idx: int) -> ClassModifiers:
    bump = 1.0 + 0.04 * (master_idx + 1)
    penalty = 0.96 - 0.02 * branch_idx
    return ClassModifiers(
        income_mult=branch_mod.income_mult * (bump if branch_mod.income_mult > 1 else penalty),
        job_payout_mult=branch_mod.job_payout_mult * bump,
        duel_damage_mult=branch_mod.duel_damage_mult * bump,
        duel_mitigation_mult=branch_mod.duel_mitigation_mult * bump,
        boss_damage_mult=branch_mod.boss_damage_mult * bump,
        max_hp_mult=branch_mod.max_hp_mult * bump,
        crit_bonus=branch_mod.crit_bonus + 0.01 * (master_idx + 1),
        heist_success_bonus=branch_mod.heist_success_bonus + 0.02,
        heist_success_penalty=branch_mod.heist_success_penalty + 0.01,
        counter_damage_taken_mult=branch_mod.counter_damage_taken_mult * penalty,
    )


def _build_class_map() -> dict[str, ClassDef]:
    classes: dict[str, ClassDef] = {}

    for starter_id, (name, desc, element, mods) in STARTER_DEFS.items():
        branch_children: list[str] = []
        for branch_idx, (branch_key, branch_name, role, branch_mod) in enumerate(
            STARTER_BRANCHES[starter_id]
        ):
            evolved_id = f"{starter_id}_{branch_key}"
            branch_children.append(evolved_id)
            master_children: list[str] = []
            for master_idx, (mid, mname) in enumerate(MASTER_NAMES[branch_key]):
                master_id = f"{evolved_id}_{mid}"
                master_children.append(master_id)
                master_mod = _master_modifiers(branch_mod, branch_idx, master_idx)
                classes[master_id] = ClassDef(
                    class_id=master_id,
                    name=mname,
                    tier="master",
                    description=f"Master {branch_name} — peak {name.lower()} path.",
                    parent_id=evolved_id,
                    starter_root=starter_id,
                    element=element,
                    combat_role=role,
                    modifiers=master_mod,
                    emoji="👑",
                )
            classes[evolved_id] = ClassDef(
                class_id=evolved_id,
                name=branch_name,
                tier="evolved",
                description=f"Evolved {name} — {branch_name} specialization.",
                parent_id=starter_id,
                children_ids=tuple(master_children),
                starter_root=starter_id,
                element=element,
                combat_role=role,
                modifiers=branch_mod,
                emoji="📈",
            )

        classes[starter_id] = ClassDef(
            class_id=starter_id,
            name=name,
            tier="starter",
            description=desc,
            children_ids=tuple(branch_children),
            starter_root=starter_id,
            element=element,
            combat_role="striker" if starter_id == "vanguard" else "skirmisher",
            modifiers=mods,
            emoji="🎖️" if starter_id == "vanguard" else "💰" if starter_id == "mogul" else "🌑",
        )

    classes["warlord"] = ClassDef(
        class_id="warlord",
        name="Warlord",
        tier="hybrid",
        description="Hybrid of Vanguard and Shade masters — duels and heists.",
        required_master_roots=frozenset({"vanguard", "shade"}),
        element="fire",
        combat_role="striker",
        modifiers=_mod(
            duel_damage_mult=1.10,
            heist_success_bonus=0.08,
            income_mult=0.90,
            boss_damage_mult=1.05,
        ),
        emoji="⚔️",
    )
    classes["archon"] = ClassDef(
        class_id="archon",
        name="Archon",
        tier="hybrid",
        description="Hybrid of Vanguard and Mogul masters — combat and income.",
        required_master_roots=frozenset({"vanguard", "mogul"}),
        element="verdant",
        combat_role="bulwark",
        modifiers=_mod(
            duel_damage_mult=1.08,
            income_mult=1.12,
            duel_mitigation_mult=0.92,
            boss_damage_mult=1.06,
        ),
        emoji="🏛️",
    )
    classes[config.JESTER_CLASS_ID] = ClassDef(
        class_id=config.JESTER_CLASS_ID,
        name="Jester",
        tier="special",
        description="Exclusive trickster — weak stats, deadly misdirection.",
        modifiers=_mod(
            income_mult=config.JESTER_STAT_MULT,
            job_payout_mult=config.JESTER_STAT_MULT,
            duel_damage_mult=config.JESTER_STAT_MULT,
            duel_mitigation_mult=config.JESTER_STAT_MULT,
            boss_damage_mult=config.JESTER_STAT_MULT,
            max_hp_mult=config.JESTER_STAT_MULT,
            heist_success_penalty=0.20,
        ),
        emoji="🃏",
    )
    return classes


CLASS_MAP: dict[str, ClassDef] = _build_class_map()
STARTER_IDS: tuple[str, ...] = tuple(STARTER_DEFS.keys())
HYBRID_IDS: tuple[str, ...] = ("warlord", "archon")


def get_class(class_id: str | None) -> ClassDef | None:
    if not class_id:
        return None
    return CLASS_MAP.get(class_id.strip().lower())


def get_modifiers(class_id: str | None) -> ClassModifiers:
    cls = get_class(class_id)
    if cls is None:
        return ClassModifiers()
    return cls.modifiers


def is_jester_class(class_id: str | None) -> bool:
    return class_id == config.JESTER_CLASS_ID


def is_jester_user(user_id: int) -> bool:
    return user_id == config.JESTER_EXCLUSIVE_USER_ID


def is_healer_class(class_id: str | None) -> bool:
    """Warden branch and their masters — strong mana regen over time."""
    if not class_id:
        return False
    parts = class_id.split("_")
    return bool(len(parts) >= 2 and parts[1] == "warden")


def element_multiplier(attacker_element: Element | None, boss_element: str | None) -> float:
    if attacker_element is None or not boss_element:
        return 1.0
    be = config.BOSS_ELEMENT_BEATS.get(attacker_element)
    if be == boss_element:
        return 1.0 + config.BOSS_ELEMENT_STRONG_BONUS
    if config.BOSS_ELEMENT_BEATS.get(boss_element) == attacker_element:
        return 1.0 - config.BOSS_ELEMENT_WEAK_PENALTY
    return 1.0


def pvp_matchup_multiplier(
    attacker_role: CombatRole | None,
    defender_role: CombatRole | None,
    *,
    attacker_element: Element | None = None,
    defender_element: Element | None = None,
) -> float:
    mult = 1.0
    if attacker_role and defender_role:
        beats: dict[CombatRole, CombatRole] = {
            "striker": "skirmisher",
            "skirmisher": "bulwark",
            "bulwark": "striker",
        }
        if beats.get(attacker_role) == defender_role:
            mult += config.PVP_ROLE_ADVANTAGE_BONUS
        elif beats.get(defender_role) == attacker_role:
            mult -= config.PVP_ROLE_DISADVANTAGE_PENALTY
    if attacker_element and defender_element and attacker_element == defender_element:
        mult += config.PVP_SAME_ELEMENT_BONUS
    return max(0.5, mult)


def evolution_threshold(tier: ClassTier) -> int | None:
    if tier == "starter":
        return config.CLASS_XP_EVOLVE_TIER2
    if tier == "evolved":
        return config.CLASS_XP_EVOLVE_TIER3
    return None


def can_evolve(class_id: str | None, class_xp: int, master_roots: set[str]) -> list[ClassDef]:
    current = get_class(class_id)
    if current is None:
        return []
    threshold = evolution_threshold(current.tier)
    if threshold is not None and class_xp < threshold:
        return []
    if current.tier == "starter":
        return [CLASS_MAP[cid] for cid in current.children_ids if cid in CLASS_MAP]
    if current.tier == "evolved":
        return [CLASS_MAP[cid] for cid in current.children_ids if cid in CLASS_MAP]
    if current.tier == "master":
        options: list[ClassDef] = []
        for hid in HYBRID_IDS:
            hybrid = CLASS_MAP[hid]
            if hybrid.required_master_roots.issubset(master_roots):
                options.append(hybrid)
        return options
    return []


def format_modifiers_summary(mod: ClassModifiers) -> str:
    parts: list[str] = []
    if mod.income_mult != 1.0:
        parts.append(f"Income ×{mod.income_mult:.2f}")
    if mod.job_payout_mult != 1.0:
        parts.append(f"Jobs ×{mod.job_payout_mult:.2f}")
    if mod.duel_damage_mult != 1.0:
        parts.append(f"Duel dmg ×{mod.duel_damage_mult:.2f}")
    if mod.duel_mitigation_mult != 1.0:
        parts.append(f"Duel mit ×{mod.duel_mitigation_mult:.2f}")
    if mod.boss_damage_mult != 1.0:
        parts.append(f"Boss dmg ×{mod.boss_damage_mult:.2f}")
    if mod.heist_success_bonus > 0:
        parts.append(f"Heist +{int(mod.heist_success_bonus * 100)}%")
    if mod.heist_success_penalty > 0:
        parts.append(f"Heist risk +{int(mod.heist_success_penalty * 100)}%")
    return " · ".join(parts) if parts else "Balanced baseline"
