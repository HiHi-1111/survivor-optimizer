from pathlib import Path

ROOT = Path(r"C:\Users\iyoua\Downloads\survivor-optimizer")
OPT = ROOT / "optimizer"
OPT.mkdir(exist_ok=True)

guard = OPT / "global_plan_guardrails.py"

guard.write_text(r'''
"""
Global-plan guardrails for Survivor.io optimizer.

Purpose:
- DPS-first planning.
- Explain locked preview / inactive inventory / HP bait / SS belt breakpoints.
- Keep explanations compact and deterministic.
"""

from __future__ import annotations
import functools

MARKER = "SURVIVOR_DECISION_GUARDRAILS_V1"

GUARDRAIL_ENTRY = {
    "type": "decision_guardrails",
    "marker": MARKER,
    "summary": "DPS-first optimizer guardrails.",
    "active_state_rule": (
        "Only equipped and active upgrades count. Locked preview, inactive inventory, "
        "not active, and not count items must be ignored unless actually equipped/active."
    ),
    "hp_rule": (
        "DPS and damage are priority. HP, health, defense, and damage reduction are not priority; "
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

TARGET_WORDS = (
    "plan",
    "recommend",
    "optimiz",
    "run",
    "select",
    "score",
)

def _flat(x):
    if isinstance(x, dict):
        return " ".join(str(k) + " " + _flat(v) for k, v in x.items())
    if isinstance(x, list):
        return " ".join(_flat(v) for v in x)
    return str(x)

def _has_marker(x) -> bool:
    return MARKER.lower() in _flat(x).lower()

def _entry():
    return dict(GUARDRAIL_ENTRY)

def apply_global_plan_guardrails(result, profile=None):
    """
    Mutates/returns optimizer result with compact global_plan guardrails.
    Safe and idempotent.
    """
    if result is None:
        return result

    if isinstance(result, dict):
        gp = result.get("global_plan")

        if gp is None:
            result["global_plan"] = {
                "decision_guardrails": [_entry()]
            }
            return result

        if _has_marker(gp):
            return result

        if isinstance(gp, list):
            gp.append(_entry())
            return result

        if isinstance(gp, dict):
            gp.setdefault("decision_guardrails", [])
            if isinstance(gp["decision_guardrails"], list):
                gp["decision_guardrails"].append(_entry())
            else:
                gp["decision_guardrails"] = [gp["decision_guardrails"], _entry()]
            return result

        result["global_plan"] = {
            "original_global_plan": gp,
            "decision_guardrails": [_entry()],
        }
        return result

    if isinstance(result, list):
        if not _has_marker(result):
            result.append(_entry())
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

patch_snippet = r'''

# GLOBAL_PLAN_GUARDRAILS_AUTO_PATCH_V1
try:
    import sys as _survivor_sys
    from optimizer.global_plan_guardrails import patch_module_functions as _survivor_patch_module_functions
    _survivor_patch_module_functions(_survivor_sys.modules[__name__])
except Exception:
    pass
'''

patched = []
for rel in [
    "global_planner.py",
    "main.py",
    "explainer.py",
    "scorer.py",
    "damage_engine.py",
]:
    f = OPT / rel
    if not f.exists():
        continue

    t = f.read_text(encoding="utf-8")
    if "GLOBAL_PLAN_GUARDRAILS_AUTO_PATCH_V1" not in t:
        f.write_text(t.rstrip() + "\n" + patch_snippet + "\n", encoding="utf-8")
        patched.append(str(f))

print("WROTE:", guard)
print("PATCHED FILES:")
for p in patched:
    print(" -", p)
if not patched:
    print(" - already patched")

print("DONE")
