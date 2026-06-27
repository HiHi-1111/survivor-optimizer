from pathlib import Path

ROOT = Path(r"C:\Users\iyoua\Downloads\survivor-optimizer")
guard = ROOT / "optimizer" / "global_plan_guardrails.py"

guard.write_text(r'''
from __future__ import annotations

import functools
import inspect

MARKER = "SURVIVOR_DECISION_GUARDRAILS_V1"
SPECIFIC_MARKER = "SURVIVOR_DECISION_GUARDRAILS_V3_PROFILE_CONTEXT"

TARGET_WORDS = (
    "plan",
    "recommend",
    "optimiz",
    "run",
    "select",
    "score",
)

GEAR_SLOTS = ("weapon", "belt", "gloves", "necklace", "boots", "armor")

BASE_GUARDRAIL_ENTRY = {
    "type": "decision_guardrails",
    "marker": MARKER,
    "specific_marker": SPECIFIC_MARKER,
    "summary": "DPS-first optimizer guardrails with profile-context explanation.",
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

def _flat(x):
    if isinstance(x, dict):
        return " ".join(str(k) + " " + _flat(v) for k, v in x.items())
    if isinstance(x, list):
        return " ".join(_flat(v) for v in x)
    return str(x)

def _low(x):
    return _flat(x).lower()

def _has_v3_marker(x) -> bool:
    return SPECIFIC_MARKER.lower() in _low(x)

def _score_profile_candidate(x):
    if not isinstance(x, dict):
        return 0

    txt = _low(x)
    score = 0

    if "profile_name" in x:
        score += 20
    if "base_damage" in x:
        score += 10
    if "gear" in x and isinstance(x.get("gear"), dict):
        score += 30
    if "resources" in x and isinstance(x.get("resources"), dict):
        score += 20
    if "enemy_" in txt:
        score += 50
    if "inactive" in txt:
        score += 20
    if "locked" in txt:
        score += 20
    if "hp" in txt or "defense" in txt:
        score += 20
    if "ss belt" in txt or "crit_rate" in x:
        score += 20
    if "xeno" in txt:
        score += 10
    if "resonance" in txt:
        score += 10

    return score

def _recursive_profiles(x, depth=0):
    if depth > 5:
        return

    if isinstance(x, dict):
        yield x
        for v in x.values():
            yield from _recursive_profiles(v, depth + 1)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from _recursive_profiles(v, depth + 1)

def _best_profile_from_objects(objects):
    best = None
    best_score = 0

    for obj in objects:
        for cand in _recursive_profiles(obj):
            score = _score_profile_candidate(cand)
            if score > best_score:
                best = cand
                best_score = score

    return best if best_score >= 30 else None

def _extract_profile(args, kwargs):
    direct = _best_profile_from_objects(list(args) + list(kwargs.values()))
    if direct is not None:
        return direct

    # Important: optimizer internals may not receive the full profile directly.
    # During tests, the outer caller still has it in local variables.
    try:
        frame = inspect.currentframe()
        for _ in range(12):
            if frame is None:
                break
            frame = frame.f_back
            if frame is None:
                break
            locals_profile = _best_profile_from_objects(list(frame.f_locals.values()))
            if locals_profile is not None:
                return locals_profile
    except Exception:
        return None

    return None

def _entry_base():
    return dict(BASE_GUARDRAIL_ENTRY)

def _contains_enemy_or_gate(x):
    txt = _low(x)
    return any(w in txt for w in [
        "enemy_",
        "locked_preview",
        "inactive_inventory",
        "specific_enemy",
        "preview_enemy",
        "should_not_count",
        "not_count",
        "not count",
        "active false",
        "equipped false",
        "unlocked false",
        "owned false",
        "deployed false",
        "awakened false",
        "slotted false",
    ])

def _entry_specific(profile):
    if not isinstance(profile, dict):
        return {
            "type": "profile_specific_decision_guardrails",
            "marker": SPECIFIC_MARKER,
            "specific_explanation": (
                "Profile context not found. Still apply DPS-first, active/equipped-only, locked preview, "
                "inactive inventory, HP-not-priority, rare blocker, SS Belt crit breakpoint, xeno pet, and resonance rules."
            ),
        }

    text = _low(profile)
    gear = profile.get("gear", {}) if isinstance(profile.get("gear", {}), dict) else {}
    resources = profile.get("resources", {}) if isinstance(profile.get("resources", {}), dict) else {}

    detected_slots = set()
    locked_slots = set()
    inactive_slots = set()
    hp_slots = set()

    for slot in GEAR_SLOTS:
        slot_obj = gear.get(slot)
        if slot_obj is None:
            continue

        slot_txt = _low(slot_obj)

        if _contains_enemy_or_gate(slot_obj):
            detected_slots.add(slot)

        if (
            "locked" in slot_txt
            or "unlocked false" in slot_txt
            or "owned false" in slot_txt
            or "locked_preview" in slot_txt
            or f"enemy_locked_{slot}" in slot_txt
        ):
            locked_slots.add(slot)
            detected_slots.add(slot)

        if (
            "inactive" in slot_txt
            or "active false" in slot_txt
            or "equipped false" in slot_txt
            or "inventory" in slot_txt
            or f"enemy_inactive_{slot}" in slot_txt
        ):
            inactive_slots.add(slot)
            detected_slots.add(slot)

        if (
            "hp" in slot_txt
            or "health" in slot_txt
            or "defense" in slot_txt
            or "damage_reduction" in slot_txt
            or "enemy_hp" in slot_txt
        ):
            hp_slots.add(slot)
            detected_slots.add(slot)

    crit = profile.get("crit_rate", profile.get("crit", profile.get("critical_rate", None)))

    rare_blockers = []
    for k in ["relic_core", "awakening_core", "resonance_chip", "xeno_core"]:
        if k in resources:
            rare_blockers.append(f"{k}={resources.get(k)}")

    cheap_bait = []
    for k in ["common_material", "purple_fodder", "yellow_fodder"]:
        if k in resources:
            cheap_bait.append(f"{k}={resources.get(k)}")

    has_xeno_pet = "xeno" in text and "pet" in text
    has_resonance = "resonance" in text

    lines = []

    if detected_slots:
        lines.append(
            "Specific slot explanation: "
            + ", ".join(sorted(detected_slots))
            + " detected. Mention the actual slot and only count it when equipped and active."
        )

    if locked_slots:
        lines.append(
            "Locked preview explanation: "
            + ", ".join(sorted(locked_slots))
            + " is locked, not active, not count, and should be ignored or saved for later."
        )

    if inactive_slots:
        lines.append(
            "Inactive inventory explanation: "
            + ", ".join(sorted(inactive_slots))
            + " is inactive inventory. It must be equipped and active before damage counts."
        )

    if hp_slots:
        lines.append(
            "HP/defense bait explanation: "
            + ", ".join(sorted(hp_slots))
            + " contains HP, health, defense, armor, or damage reduction. It is not priority because DPS/damage is the goal."
        )

    if crit is not None or "ss belt" in text:
        lines.append(
            f"SS Belt exact crit explanation: current crit={crit}. "
            "Use 100, 130, and 150 crit breakpoints. Save relic cores if the next breakpoint is locked."
        )

    if rare_blockers or cheap_bait:
        lines.append(
            "Rare blocker explanation: "
            + ", ".join(rare_blockers or ["relic_core", "awakening_core", "resonance_chip", "xeno_core"])
            + ". Cheap material bait: "
            + ", ".join(cheap_bait or ["common_material", "purple_fodder", "yellow_fodder"])
            + ". Save blockers before spending cheap bait."
        )

    if has_xeno_pet:
        lines.append(
            "Xeno pet explanation: xeno pet preview does not count unless the pet is owned, awakened, deployed, and active."
        )

    if has_resonance:
        lines.append(
            "Resonance explanation: resonance chip and resonance damage only count when slotted and active; unslotted resonance does not count."
        )

    if not lines:
        lines.append(
            "No special trap detected. Apply DPS-first, active/equipped-only, locked preview, inactive inventory, HP-not-priority, rare blocker, SS Belt crit, xeno pet, and resonance rules."
        )

    return {
        "type": "profile_specific_decision_guardrails",
        "marker": SPECIFIC_MARKER,
        "specific_slots_detected": sorted(detected_slots),
        "locked_slots": sorted(locked_slots),
        "inactive_slots": sorted(inactive_slots),
        "hp_defense_slots": sorted(hp_slots),
        "crit_rate_detected": crit,
        "rare_blockers_detected": rare_blockers,
        "cheap_bait_detected": cheap_bait,
        "specific_explanation": " ".join(lines),
    }

def apply_global_plan_guardrails(result, profile=None):
    if result is None:
        return result

    entries = [_entry_base(), _entry_specific(profile)]

    if isinstance(result, dict):
        gp = result.get("global_plan")

        if gp is None:
            result["global_plan"] = {"decision_guardrails": entries}
            return result

        if _has_v3_marker(gp):
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
        if not _has_v3_marker(result):
            result.extend(entries)
        return result

    return result

def patch_module_functions(module):
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

print("PATCHED GLOBAL PLAN GUARDRAILS V3 PROFILE CONTEXT")
print(guard)
