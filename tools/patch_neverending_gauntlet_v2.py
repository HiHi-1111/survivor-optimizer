from pathlib import Path
import re

root = Path(r"C:\Users\iyoua\Downloads\survivor-optimizer")
f = root / "tools" / "run_neverending_goal_gauntlet.py"
t = f.read_text(encoding="utf-8")

insert = r'''
def extract_pytest_failures(hard_result):
    text = (hard_result.get("stdout", "") or "") + "\n" + (hard_result.get("stderr", "") or "")
    failures = []

    for line in text.splitlines():
        s = line.strip()
        if s.startswith("FAILED ") or "AssertionError:" in s or "VULNERABILITY:" in s:
            cat = "hard_pytest_failure"
            low = s.lower()
            if "2.35x" in low or "175%" in low or "format" in low:
                cat = "format_or_alias_hard_failure"
            elif "cheap material" in low or "rare blocker" in low:
                cat = "rare_blocker_bait_hard_failure"
            elif "locked" in low or "preview" in low:
                cat = "locked_preview_hard_failure"
            elif "unequipped" in low or "inventory" in low:
                cat = "inactive_inventory_hard_failure"
            elif "hp" in low or "health" in low or "defense" in low:
                cat = "hp_defense_hard_failure"

            failures.append({
                "category": cat,
                "source": "pytest",
                "line": s,
                "goal": "learn from hard regression failure"
            })

    return failures

def ensure_speed_profiles():
    raw = ROOT / "training_outputs" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    p = raw / "synthetic_profiles.jsonl"

    if p.exists() and p.stat().st_size > 100:
        return str(p)

    import json, random
    random.seed(1234)

    rows = []
    for i in range(300):
        rows.append({
            "id": f"speed_profile_{i}",
            "attack": random.randint(50000, 300000),
            "crit_rate": random.choice([71, 101, 121, 131, 151]),
            "gear": {
                "weapon": {"damage_multiplier": random.choice([1.5, 2.0, "2.35x"])},
                "belt": {"damage_multiplier": random.choice([1.0, 1.75, "175%"])}
            },
            "tech": {
                "drone": {"damage_multiplier": random.choice([1.2, 2.25, "225%"])},
                "lightning": {"damage_multiplier": random.choice([1.1, 1.55, "1.55x"])}
            },
            "pet": {
                "main": {"damage_multiplier": random.choice([1.0, 1.85, "1.85x"])}
            },
            "resources": {
                "relic_core": random.randint(0, 80),
                "resonance_chip": random.randint(0, 200),
                "awakening_core": random.randint(0, 50)
            }
        })

    with p.open("w", encoding="utf-8") as out:
        for r in rows:
            out.write(json.dumps(r) + "\n")

    return str(p)
'''

if "def extract_pytest_failures" not in t:
    t = t.replace("def run_hard_tests():", insert + "\n\ndef run_hard_tests():")

t = t.replace(
'''        hard = run_hard_tests()
        hard_ok = hard["returncode"] == 0
        log(f"Hard tests: {'PASS' if hard_ok else 'FAIL'} in {hard['elapsed']}s")''',
'''        hard = run_hard_tests()
        hard_ok = hard["returncode"] == 0
        pytest_failures = extract_pytest_failures(hard)
        if pytest_failures:
            failures.extend(pytest_failures)
            bank_size = update_mistake_bank(failures)
            counts = summarize_failures(failures)
        log(f"Hard tests: {'PASS' if hard_ok else 'FAIL'} in {hard['elapsed']}s")
        if pytest_failures:
            log(f"Learned pytest failures: {len(pytest_failures)}")'''
)

t = t.replace(
'''        speed = run_speed_check()''',
'''        ensure_speed_profiles()
        speed = run_speed_check()'''
)

f.write_text(t, encoding="utf-8")
print("PATCHED NEVERENDING GAUNTLET V2")
print(f)
