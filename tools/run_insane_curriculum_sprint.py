import json, re, subprocess, sys, time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
outdir = root / "training_outputs"
outdir.mkdir(exist_ok=True)

TESTS = [
    "tests/test_optimizer_red_team_real_gameplay.py",
    "tests/test_optimizer_anti_ai_real_gameplay.py",
    "tests/test_optimizer_red_team_realistic_hard.py",
    "tests/test_adversarial_optimizer_features.py",
    "tests/test_global_planner.py",
]

CLASSIFIERS = [
    ("inactive_inventory_counted", r"unequipped|owned_not_equipped|inventory gear|unequipped pets"),
    ("locked_preview_counted", r"locked|preview|not unlocked|next Astral Forge|breakpoint"),
    ("inactive_mode_counted", r"inactive Twinborn|inactive mode|unselected survivor|roster"),
    ("unslotted_counted", r"unslotted|candidate_resonance|assist candidates"),
    ("format_or_alias", r"2\.35x|175%|source-pack multiplier|Relic Core|Yang shard|alias"),
    ("missing_real_damage_output", r"total_damage is not None|fake score|must return real total damage"),
    ("rare_blocker_bait", r"cheap material|rare blocker|relic core|awakening core|resonance chip|xeno"),
    ("hp_or_defense_bait", r"\bhp\b|health|defense|damage_reduction"),
]

cmd = [sys.executable, "-m", "pytest", *TESTS, "-q", "--tb=short"]
print("INSANE CURRICULUM SPRINT")
print("Running:", " ".join(cmd))
print()

p = subprocess.run(cmd, cwd=root, text=True, capture_output=True)
text = (p.stdout or "") + "\n" + (p.stderr or "")

raw_path = outdir / "insane_curriculum_pytest_raw.txt"
raw_path.write_text(text, encoding="utf-8", errors="ignore")

fail_lines = []
for line in text.splitlines():
    if line.startswith("FAILED ") or "AssertionError" in line or "VULNERABILITY" in line:
        fail_lines.append(line.strip())

counts = {name: 0 for name, _ in CLASSIFIERS}
classified = []

for line in fail_lines:
    cats = []
    for name, pat in CLASSIFIERS:
        if re.search(pat, line, re.I):
            counts[name] += 1
            cats.append(name)
    classified.append({"line": line, "categories": cats or ["uncategorized"]})

curriculum = {
    "created_at": time.time(),
    "goal": "smarter faster smaller Survivor.io optimizer",
    "rules": {
        "dps_first": True,
        "hp_weight": 0,
        "active_state_only": True,
        "locked_preview_never_counts": True,
        "inventory_not_equipped_never_counts": True,
        "unselected_roster_never_counts": True,
        "unslotted_resonance_never_counts": True,
        "rare_blockers_protected": [
            "relic_core",
            "awakening_core",
            "resonance_chip",
            "xeno_core",
            "selector",
            "s_shard",
            "pet_awakening_crystal"
        ],
        "cheap_materials_are_not_primary_blockers": True,
        "result_must_return_real_damage": True,
        "must_route_to_global_plan": True
    },
    "failure_class_counts": counts,
    "failed_lines": classified[:250],
    "pytest_returncode": p.returncode,
    "raw_report": str(raw_path),
    "next_patch_targets": [
        "optimizer/damage_engine.py active-state traversal",
        "optimizer/global_planner.py global_plan final answer",
        "optimizer/rare_blocker_guardrails.py scarce blocker dominance",
        "optimizer/scorer.py HP/defense downrank and cheap bait penalty",
    ],
}

bank_path = outdir / "insane_mistake_bank.json"
curr_path = outdir / "insane_training_curriculum.json"

bank_path.write_text(json.dumps(classified, indent=2), encoding="utf-8")
curr_path.write_text(json.dumps(curriculum, indent=2), encoding="utf-8")

print("RETURN CODE:", p.returncode)
print()
print("FAILURE CLASS COUNTS:")
for k, v in sorted(counts.items(), key=lambda x: x[1], reverse=True):
    print(f"{k}: {v}")

print()
print("Saved mistake bank:", bank_path)
print("Saved curriculum:", curr_path)
print("Saved raw report:", raw_path)
print()

if p.returncode == 0:
    print("No failures found. Enemy is too weak; increase trap generation.")
else:
    print("GOOD: failures found. This is the learning fuel.")
    print("Patch active-state filtering first, then rerun this exact sprint.")
