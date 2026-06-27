import json
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
        "best_must_include_any": ["save", "hold", "wait"],
        "best_must_not_include_any": ["trash", "waste"],
    },
    {
        "name": "pet_chest_plus_xeno_core_combo",
        "profile": {
            "items": {"epic_pet_chest": 1},
            "resources": {"xeno_core": 1},
            "goal_scenario": "normal",
        },
        "best_must_include_any": ["xeno", "pet", "xeno_breakpoint"],
        "best_must_not_include_any": ["collectible"],
    },
    {
        "name": "astral_core_prefers_ss_or_af_path",
        "profile": {
            "resources": {"astral_core": 2},
            "items": {},
            "goal_scenario": "normal",
        },
        "best_must_include_any": ["astral", "ss", "forge", "breakpoint"],
        "best_must_not_include_any": ["trash"],
    },
    {
        "name": "collectible_chest_with_shards",
        "profile": {
            "items": {"red_collectible_chest": 1},
            "resources": {"collectible_shard": 10},
            "goal_scenario": "normal",
        },
        "best_must_include_any": ["collectible", "shard", "set"],
        "best_must_not_include_any": ["xeno_pets"],
    },
    {
        "name": "steamroll_prioritizes_damage_not_survival",
        "profile": {
            "resources": {"astral_core": 3, "xeno_core": 2, "resonance_chip": 3},
            "items": {"epic_pet_chest": 2, "core_selector": 2},
            "goal_scenario": "steamroll",
        },
        "best_must_include_any": ["damage", "xeno", "astral", "breakpoint", "core"],
        "best_must_not_include_any": ["healing", "revive"],
    },
]

def flatten_text(obj):
    return json.dumps(obj, ensure_ascii=False, default=str).lower()

def has_any(text, words):
    return any(word.lower() in text for word in words)

def run_case(case):
    result = optimize(case["profile"])
    global_plan = result.get("global_plan", {}) or {}
    best = global_plan.get("best_action_chain", {}) or {}
    best_text = flatten_text(best)

    checks = [
        {
            "check": "best_must_include_any",
            "passed": has_any(best_text, case["best_must_include_any"]),
            "expected": case["best_must_include_any"],
        },
        {
            "check": "best_must_not_include_any",
            "passed": not has_any(best_text, case["best_must_not_include_any"]),
            "expected_absent": case["best_must_not_include_any"],
        },
    ]

    return {
        "name": case["name"],
        "passed": all(c["passed"] for c in checks),
        "checks": checks,
        "best_preview": best_text[:1500],
    }

results = [run_case(case) for case in CASES]
passed = sum(1 for r in results if r["passed"])
score = passed / len(results) * 100

report = {
    "smartness_score_percent": round(score, 2),
    "passed": passed,
    "total": len(results),
    "scored_field": "result.global_plan.best_action_chain",
    "verdict": (
        "SMART_ENOUGH_FOR_SITE_ALPHA" if score >= 80 else
        "SMARTNESS_NEEDS_MORE_CASES_OR_FIXES"
    ),
    "results": results,
}

Path("reports").mkdir(exist_ok=True)
Path("reports/smartness_global_plan_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
