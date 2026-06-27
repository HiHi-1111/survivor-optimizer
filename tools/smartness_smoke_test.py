import json
import re
from pathlib import Path
from optimizer.main import optimize

CASES = [
    {
        "name": "save_rare_core_when_no_breakpoint",
        "profile": {
            "resources": {"relic_core": 1},
            "items": {},
            "goal_scenario": "normal",
        },
        "must_include_any": ["save", "hold", "wait"],
        "must_not_include_any": ["spend relic", "waste relic"],
        "reason": "Rare core should not be spent if no meaningful breakpoint is available.",
    },
    {
        "name": "pet_chest_plus_xeno_core_combo",
        "profile": {
            "items": {"epic_pet_chest": 1},
            "resources": {"xeno_core": 1},
            "goal_scenario": "normal",
        },
        "must_include_any": ["pet", "xeno", "chest"],
        "must_not_include_any": ["collectible"],
        "reason": "Pet chest and xeno core should be evaluated as a combo, not isolated.",
    },
    {
        "name": "astral_core_prefers_ss_or_af_path",
        "profile": {
            "resources": {"astral_core": 2},
            "items": {},
            "goal_scenario": "normal",
        },
        "must_include_any": ["astral", "ss", "forge", "core"],
        "must_not_include_any": ["trash"],
        "reason": "Astral cores should point toward SS/AF-style value, not random spending.",
    },
    {
        "name": "collectible_chest_with_shards",
        "profile": {
            "items": {"red_collectible_chest": 1},
            "resources": {"collectible_shard": 10},
            "goal_scenario": "normal",
        },
        "must_include_any": ["collectible", "shard", "set"],
        "must_not_include_any": ["xeno"],
        "reason": "Collectible chest and shards should trigger collectible/set logic.",
    },
    {
        "name": "steamroll_prioritizes_damage_not_survival",
        "profile": {
            "resources": {"astral_core": 3, "xeno_core": 2, "resonance_chip": 3},
            "items": {"epic_pet_chest": 2, "core_selector": 2},
            "goal_scenario": "steamroll",
        },
        "must_include_any": ["damage", "dps", "attack", "steamroll", "core", "xeno", "astral"],
        "must_not_include_any": ["hp", "healing", "revive"],
        "reason": "Steamroll should bias toward damage/DPS, not defensive value.",
    },
]

def flatten_text(obj):
    return json.dumps(obj, ensure_ascii=False, default=str).lower()

def has_any(text, words):
    return any(word.lower() in text for word in words)

def run_case(case):
    result = optimize(case["profile"])
    text = flatten_text(result)

    checks = []
    checks.append({
        "check": "must_include_any",
        "passed": has_any(text, case["must_include_any"]),
        "expected": case["must_include_any"],
    })
    checks.append({
        "check": "must_not_include_any",
        "passed": not has_any(text, case["must_not_include_any"]),
        "expected_absent": case["must_not_include_any"],
    })

    passed = all(c["passed"] for c in checks)
    return {
        "name": case["name"],
        "passed": passed,
        "checks": checks,
        "reason": case["reason"],
        "output_preview": text[:1200],
    }

results = [run_case(case) for case in CASES]
passed = sum(1 for r in results if r["passed"])
score = passed / len(results) * 100

report = {
    "smartness_score_percent": round(score, 2),
    "passed": passed,
    "total": len(results),
    "verdict": (
        "STOP_SMARTNESS_START_WEIGHT_CUTTING" if score >= 90 else
        "SMARTNESS_NEEDS_MORE_CASES_OR_FIXES"
    ),
    "results": results,
}

Path("reports").mkdir(exist_ok=True)
Path("reports/smartness_smoke_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
