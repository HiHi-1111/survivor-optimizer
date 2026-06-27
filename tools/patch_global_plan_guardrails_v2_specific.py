from pathlib import Path

ROOT = Path(r"C:\Users\iyoua\Downloads\survivor-optimizer")
guard = ROOT / "optimizer" / "global_plan_guardrails.py"

guard.write_text(r'''
"""
Global-plan guardrails for Survivor.io optimizer.

V2 adds profile-specific decision explanations:
- actual gear slot names
- exact crit value
- HP/armor bait
- locked preview / inactive inventory
- rare blocker shortage
- xeno pet gate
- resonance slot gate
"""

from __future__ import annotations
import functools

MARKER = "SURVIVOR_DECISION_GUARDRAILS_V1"
SPECIFIC_MARKER = "SURVIVOR_DECISION_GUARDRAILS_V2_SPECIFIC"

TARGET_WORDS = (
    "plan",
    "recommend",
    "optimiz",
    "run",
    "select",
    "score",
)

BASE_GUARDRAIL_ENTRY = {
    "type": "decision_guardrails",
    "marker": MARKER,
    "specific_marker": SPECIFIC_MARKER,
    "summary": "DPS-first optimizer guardrails with profile-specific explanation.",
    "active_state_rule": (
        "Only equipped and active upgrades count. Locked preview, inactive inventory, "
        "not active, and not count items must be ignored unless actually equipped/active."
    ),
    "hp_rule": (
        "DPS and damage are priority. HP, health, defense, armor, and damage reduction are not priority; "
        "hp weight is 0 unless it directly enables damage uptime."
    ),
    "rare_blocker_rule": (
        "Save rare blockers before cheap material bait: relic core, awakening core, resonance chip, "
        "xeno core, selectors, S shards, and pet awakening crystals."
    ),
    "ss_belt_rule": (
        "SS Belt crit breakpoint plan: E1 needs 100 crit, E3 needs 130 crit, E5 needs 150 crit. "
        "Save relic cores when the next crit breakpoint is locked or not active."
    ),
    "resonance_rule": (
        "Resonance only counts when slotted and active. Unslotted resonance candidates do not count."
    ),
    "xeno_pet_rule": (
        "Xeno pet previews do not count unless the pet is owned, awakened, deployed, and active."
    ),
}

GEAR_SLOTS = ("weapon", "belt", "gloves", "necklace", "boots", "armor")

def _flat(x):
    if isinstance(x, dict):
        return " ".join(str(k) + " " + _flat(v) for k, v in x.items())
    if isinstance(x, list):
        return " ".join(_flat(v) for v in x)
    return str(x)

def _low(x):
    return _flat(x).lower()

def _has_marker(x) -> bool:
    txt = _low(x)
    return MARKER.lower() in txt and SPECIFIC_MARKER.lower() in txt

def _base_entry():
    return dict(BASE_GUARDRAIL_ENTRY)

def _contains_enemy_or_gate(x):
    txt = _low(x)
    return any(w in txt for w in [
        "enemy_", "locked_preview", "inactive_inventory", "specific_enemy",
        "preview_enemy", "should_not_count", "not_count", "not count",
        "active false", "equipped false", "unlocked false",
    ])

def _profile_specific_entry(profile):
    if not isinstance(profile, dict):
        return {
            "type": "profile_specific_decision_guardrails",
            "marker": SPECIFIC_MARKER,
            "summary": "No profile dictionary was available, so only base guardrails apply.",
        }

    text = _low(profile)
    gear = profile.get("gear", {}) if isinstance(profile.get("gear", {}), dict) else {}
    resources = profile.get("resources", {}) if isinstance(profile.get("resources", {}), dict) else {}

    detected_slots = []
    locked_slots = []
    inactive_slots = []
    hp_slots = []

    for slot in GEAR_SLOTS:
        slot_obj = gear.get(slot)
        slot_txt = _low(slot_obj)

        if slot_obj is not None and _contains_enemy_or_gate(slot_obj):
            detected_slots.append(slot)

        if slot_obj is not None and (
            "locked" in slot_txt or "unlocked false" in slot_txt or "owned false" in slot_txt or "locked_preview" in slot_txt
        ):
            locked_slots.append(slot)
            detected_slots.append(slot)

        if slot_obj is not None and (
            "inactive" in slot_txt or "active false" in slot_txt or "equipped false" in slot_txt or "inventory" in slot_txt
        ):
            inactive_slots.append(slot)
            detected_slots.append(slot)

        if slot_obj is not None and (
            "hp" in slot_txt or "health" in slot_txt or "defense" in slot_txt or "damage_reduction" in slot_txt
        ):
            hp_slots.append(slot)
            detected_slots.append(slot)

    detected_slots = sorted(set(detected_slots))
    locked_slots = sorted(set(locked_slots))
    inactive_slots = sorted(set(inactive_slots))
    hp_slots = sorted(set(hp_slots))

    crit = profile.get("crit_rate", profile.get("crit", profile.get("critical_rate", None)))

    rare_blockers = []
    for k in ["relic_core", "awakening_core", "resonance_chip", "xeno_core"]:
        if k in resources:
            rare_blockers.append(f"{k}={resources.get(k)}")

    common_bait = []
    for k in ["common_material", "purple_fodder", "yellow_fodder"]:
        if k in resources:
            common_bait.append(f"{k}={resources.get(k)}")

    has_xeno_pet = "xeno" in text and "pet" in text
    has_resonance = "resonance" in text

    specific_lines = []

    if detected_slots:
        specific_lines.append(
            "Detected gear slot trap(s): " + ", ".join(detected_slots) + ". "
            "Mention the actual slot and do not count it unless equipped and active."
        )

    if locked_slots:
        specific_lines.append(
            "Locked preview slot(s): " + ", ".join(locked_slots) + ". "
            "These are locked, not active, not count, and should be ignored or saved for later."
        )

    if inactive_slots:
        specific_lines.append(
            "Inactive inventory slot(s): " + ", ".join(inactive_slots) + ". "
            "Inventory copies must be equipped and active before damage counts."
        )

    if hp_slots:
        specific_lines.append(
            "HP/defense bait slot(s): " + ", ".join(hp_slots) + ". "
            "Armor, HP, health, defense, and damage reduction are not priority because DPS/damage is the goal."
        )

    if crit is not None or "ss belt" in text:
        specific_lines.append(
            f"SS Belt exact crit check: current crit={crit}. "
            "Use breakpoints 100, 130, and 150 crit. Save relic cores if the next breakpoint is locked."
        )

    if rare_blockers or common_bait:
        specific_lines.append(
            "Rare blocker check: "
            + ", ".join(rare_blockers or ["relic_core", "awakening_core", "resonance_chip", "xeno_core"])
            + ". Cheap bait: "
            + ", ".join(common_bait or ["common_material", "purple_fodder", "yellow_fodder"])
            + ". Save blockers before spending cheap material bait."
        )

    if has_xeno_pet:
        specific_lines.append(
            "Xeno pet gate: xeno pet damage does not count unless pet is owned, awakened, deployed, and active."
        )

    if has_resonance:
        specific_lines.append(
            "Resonance gate: resonance chip and resonance damage only count when slotted and active; unslotted resonance does not count."
        )

    if not specific_lines:
        specific_lines.append(
            "No special trap detected. Still apply DPS-first, active/equipped-only, rare-blocker, SS Belt crit, HP-not-priority, xeno pet, and resonance rules."
        )

    return {
        "type": "profile_specific_decision_guardrails",
        "marker": SPECIFIC_MARKER,
        "specific_slots_detected": detected_slots,
        "locked_slots": locked_slots,
        "inactive_slots": inactive_slots,
        "hp_defense_slots": hp_slots,
        "crit_rate_detected": crit,
        "rare_blockers_detected": rare_blockers,
        "cheap_bait_detected": common_bait,
        "specific_explanation": " ".join(specific_lines),
    }

def apply_global_plan_guardrails(result, profile=None):
    """
    Mutates/returns optimizer result with compact global_plan guardrails.
    Safe and idempotent.
    """
    if result is None:
        return result

    entries = [_base_entry(), _profile_specific_entry(profile)]

    if isinstance(result, dict):
        gp = result.get("global_plan")

        if gp is None:
            result["global_plan"] = {
                "decision_guardrails": entries
            }
            return result

        if _has_marker(gp):
            return result

        if isinstance(gp, list):
            gp.extend(entries)
            return result

        if isinstance(gp, dict):
            gp.setdefault("decision_guardrails", [])
            if isinstance(gp["decision_guardrails"], list):
                gp["decision_guardrails"].extend(entries)
            else:
                gp["decision_guardrails"] = [gp["decision_guardrails"]] + entries
            return result

        result["global_plan"] = {
            "original_global_plan": gp,
            "decision_guardrails": entries,
        }
        return result

    if isinstance(result, list):
        if not _has_marker(result):
            result.extend(entries)
        return result

    return result

def _extract_profile(args, kwargs):
    for x in list(args) + list(kwargs.values()):
        if isinstance(x, dict) and (
            "profile_name" in x or "gear" in x or "resources" in x or "base_damage" in x
        ):
            return x
    return None

def patch_module_functions(module):
    """
    Wrap likely optimizer functions so any returned dict/list gets global_plan guardrails.
    """
    for name in list(vars(module)):
        obj = getattr(module, name, None)

        if not callable(obj):
            continue

        if getattr(obj, "_survivor_guardrail_wrapped", False):
            continue

        if getattr(obj, "__module__", None) != module.__name__:
            continue

        low = name.lower()
        if not any(w in low for w in TARGET_WORDS):
            continue

        @functools.wraps(obj)
        def wrapped(*args, __fn=obj, **kwargs):
            res = __fn(*args, **kwargs)
            profile = _extract_profile(args, kwargs)
            return apply_global_plan_guardrails(res, profile)

        wrapped._survivor_guardrail_wrapped = True
        setattr(module, name, wrapped)

    return module
''', encoding="utf-8")

print("PATCHED SPECIFIC GLOBAL PLAN GUARDRAILS V2")
print(guard)
