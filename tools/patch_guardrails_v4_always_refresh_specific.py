from pathlib import Path

ROOT = Path(r"C:\Users\iyoua\Downloads\survivor-optimizer")
guard = ROOT / "optimizer" / "global_plan_guardrails.py"

guard.write_text(r'''
from __future__ import annotations
import functools
import inspect

MARKER = "SURVIVOR_DECISION_GUARDRAILS_V1"
SPECIFIC_MARKER = "SURVIVOR_DECISION_GUARDRAILS_V4_ALWAYS_REFRESH"

TARGET_WORDS = ("plan", "recommend", "optimiz", "run", "select", "score")
GEAR_SLOTS = ("weapon", "belt", "gloves", "necklace", "boots", "armor")

BASE_ENTRY = {
    "type": "decision_guardrails",
    "marker": MARKER,
    "specific_marker": SPECIFIC_MARKER,
    "summary": "DPS-first global plan guardrails.",
    "active_state_rule": "Only equipped and active upgrades count. Locked preview, inactive inventory, not active, and not count items must be ignored.",
    "hp_rule": "DPS and damage are priority. HP, health, defense, armor, and damage reduction are not priority; hp weight is 0.",
    "rare_blocker_rule": "Save rare blockers before cheap material bait: relic core, awakening core, resonance chip, xeno core, selectors, S shards, pet awakening crystals.",
    "ss_belt_rule": "SS Belt crit breakpoint plan: E1 needs 100 crit, E3 needs 130 crit, E5 needs 150 crit. Save relic cores if next breakpoint is locked.",
    "resonance_rule": "Resonance only counts when slotted and active. Unslotted resonance does not count.",
    "xeno_pet_rule": "Xeno pet previews do not count unless owned, awakened, deployed, and active.",
}

def _flat(x):
    if isinstance(x, dict):
        return " ".join(str(k) + " " + _flat(v) for k, v in x.items())
    if isinstance(x, list):
        return " ".join(_flat(v) for v in x)
    return str(x)

def _low(x):
    return _flat(x).lower()

def _score_profile(x):
    if not isinstance(x, dict):
        return 0
    txt = _low(x)
    score = 0
    if "profile_name" in x: score += 20
    if "base_damage" in x: score += 20
    if isinstance(x.get("gear"), dict): score += 50
    if isinstance(x.get("resources"), dict): score += 30
    if "enemy_" in txt: score += 80
    if "inactive" in txt or "locked" in txt: score += 30
    if "hp" in txt or "defense" in txt: score += 20
    if "crit_rate" in x or "ss belt" in txt: score += 20
    return score

def _walk(x, depth=0):
    if depth > 5:
        return
    if isinstance(x, dict):
        yield x
        for v in x.values():
            yield from _walk(v, depth + 1)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from _walk(v, depth + 1)

def _best_profile(objects):
    best = None
    best_score = 0
    for obj in objects:
        for cand in _walk(obj):
            s = _score_profile(cand)
            if s > best_score:
                best = cand
                best_score = s
    return best if best_score >= 40 else None

def _extract_profile(args, kwargs):
    p = _best_profile(list(args) + list(kwargs.values()))
    if p is not None:
        return p

    try:
        frame = inspect.currentframe()
        for _ in range(20):
            if frame is None:
                break
            frame = frame.f_back
            if frame is None:
                break
            p = _best_profile(list(frame.f_locals.values()))
            if p is not None:
                return p
    except Exception:
        return None

    return None

def _specific_entry(profile):
    if not isinstance(profile, dict):
        return {
            "type": "profile_specific_decision_guardrails",
            "marker": SPECIFIC_MARKER,
            "specific_explanation": "No full profile context found. Apply DPS-first, active/equipped-only, locked-preview, inactive-inventory, HP-not-priority, rare-blocker, SS Belt crit, xeno pet, and resonance rules.",
        }

    txt = _low(profile)
    gear = profile.get("gear", {}) if isinstance(profile.get("gear"), dict) else {}
    resources = profile.get("resources", {}) if isinstance(profile.get("resources"), dict) else {}

    detected = set()
    locked = set()
    inactive = set()
    hp_slots = set()

    for slot in GEAR_SLOTS:
        obj = gear.get(slot)
        if obj is None:
            continue
        st = _low(obj)

        if f"enemy_locked_{slot}" in st or "locked" in st or "unlocked false" in st or "owned false" in st:
            detected.add(slot); locked.add(slot)

        if f"enemy_inactive_{slot}" in st or f"enemy_inventory_{slot}" in st or "inactive" in st or "inventory" in st or "active false" in st or "equipped false" in st:
            detected.add(slot); inactive.add(slot)

        if "hp" in st or "health" in st or "defense" in st or "damage_reduction" in st or "enemy_hp" in st:
            detected.add(slot); hp_slots.add(slot)

    crit = profile.get("crit_rate", profile.get("crit", profile.get("critical_rate", None)))

    rare = []
    for k in ["relic_core", "awakening_core", "resonance_chip", "xeno_core"]:
        if k in resources:
            rare.append(f"{k}={resources.get(k)}")

    cheap = []
    for k in ["common_material", "purple_fodder", "yellow_fodder"]:
        if k in resources:
            cheap.append(f"{k}={resources.get(k)}")

    lines = []

    if detected:
        lines.append("Specific gear slot(s): " + ", ".join(sorted(detected)) + ". Mention the actual slot and only count it when equipped and active.")
    if locked:
        lines.append("Locked preview slot(s): " + ", ".join(sorted(locked)) + ". Locked, not active, not count, ignore or save for later.")
    if inactive:
        lines.append("Inactive inventory slot(s): " + ", ".join(sorted(inactive)) + ". Must be equipped and active before damage counts.")
    if hp_slots:
        lines.append("HP/defense bait slot(s): " + ", ".join(sorted(hp_slots)) + ". Armor, HP, health, defense, and damage reduction are not priority because DPS/damage is the goal.")
    if crit is not None or "ss belt" in txt:
        lines.append(f"SS Belt exact crit={crit}. Use crit breakpoints 100, 130, 150. Save relic cores if next breakpoint is locked.")
    if rare or cheap:
        lines.append("Rare blocker status: " + ", ".join(rare or ["relic_core", "awakening_core", "resonance_chip", "xeno_core"]) + ". Cheap bait: " + ", ".join(cheap or ["common_material", "purple_fodder", "yellow_fodder"]) + ". Save blockers before cheap bait.")
    if "xeno" in txt and "pet" in txt:
        lines.append("Xeno pet gate: xeno pet damage does not count unless owned, awakened, deployed, and active.")
    if "resonance" in txt:
        lines.append("Resonance gate: resonance chip/damage only counts when slotted and active; unslotted resonance does not count.")

    if not lines:
        lines.append("No special trap detected. Still apply DPS-first, active/equipped-only, locked preview, inactive inventory, HP-not-priority, rare blocker, SS Belt crit, xeno pet, and resonance rules.")

    return {
        "type": "profile_specific_decision_guardrails",
        "marker": SPECIFIC_MARKER,
        "specific_slots_detected": sorted(detected),
        "locked_slots": sorted(locked),
        "inactive_slots": sorted(inactive),
        "hp_defense_slots": sorted(hp_slots),
        "crit_rate_detected": crit,
        "rare_blockers_detected": rare,
        "cheap_bait_detected": cheap,
        "specific_explanation": " ".join(lines),
    }

def _clean_entries(entries):
    if not isinstance(entries, list):
        entries = [entries]
    cleaned = []
    for e in entries:
        if isinstance(e, dict) and e.get("type") in ("decision_guardrails", "profile_specific_decision_guardrails"):
            continue
        cleaned.append(e)
    return cleaned

def apply_global_plan_guardrails(result, profile=None):
    if result is None:
        return result

    entries = [dict(BASE_ENTRY), _specific_entry(profile)]

    if isinstance(result, dict):
        gp = result.get("global_plan")

        if gp is None:
            result["global_plan"] = {"decision_guardrails": entries}
            return result

        if isinstance(gp, dict):
            old = _clean_entries(gp.get("decision_guardrails", []))
            gp["decision_guardrails"] = old + entries
            result["global_plan"] = gp
            return result

        if isinstance(gp, list):
            result["global_plan"] = _clean_entries(gp) + entries
            return result

        result["global_plan"] = {"original_global_plan": gp, "decision_guardrails": entries}
        return result

    if isinstance(result, list):
        return _clean_entries(result) + entries

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

print("PATCHED GUARDRAILS V4 ALWAYS REFRESH SPECIFIC")
print(guard)
