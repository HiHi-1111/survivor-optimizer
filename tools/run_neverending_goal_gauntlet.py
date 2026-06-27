import copy, importlib.util, json, os, random, re, shutil, subprocess, sys, time, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "training_outputs"
OUT.mkdir(exist_ok=True)

STATE_FILE = OUT / "neverending_goal_gauntlet_state.json"
BANK_FILE = OUT / "neverending_mistake_bank.json"
SUMMARY_FILE = OUT / "neverending_goal_gauntlet_summary.json"
LOG_FILE = OUT / "neverending_goal_gauntlet.log"

GOALS = {
    "dps_first": True,
    "hp_weight": 0,
    "active_state_only": True,
    "locked_preview_never_counts": True,
    "inactive_inventory_never_counts": True,
    "rare_blockers_protected": [
        "relic_core", "awakening_core", "resonance_chip", "xeno_core",
        "selector", "s_shard", "pet_awakening_crystal"
    ],
    "cheap_material_bait_must_lose": True,
    "global_plan_required": True,
    "tiny_runtime_target_mb": 1.0,
    "micro_runtime_target_mb": 5.0,
    "potato_target_profiles_per_second": 20,
}

HARD_TESTS = [
    "tests/test_optimizer_red_team.py",
    "tests/test_optimizer_red_team_real_gameplay.py",
    "tests/test_optimizer_red_team_realistic_hard.py",
    "tests/test_optimizer_anti_ai_real_gameplay.py",
    "tests/test_global_planner.py",
    "tests/test_adversarial_optimizer_features.py",
    "tests/test_generated_failure_factory_memory.py",
]

TRAP_PATHS = [
    ("gear","belt","next_astral_forge_preview"),
    ("gear","belt","locked_e5_preview"),
    ("gear","weapon","inventory_copy"),
    ("gear","gloves","unequipped_copy"),
    ("gear","necklace","candidate_upgrade"),
    ("tech","drone","assist_candidate"),
    ("tech","lightning","unslotted_resonance"),
    ("tech","twinborn","inactive_mode"),
    ("pet","owned_pet","not_deployed"),
    ("pet","xeno_preview","not_awakened"),
    ("survivor","roster","inactive_survivor"),
    ("survivor","awakening_preview","not_unlocked"),
    ("collectibles","locked_set_preview"),
    ("collectibles","inventory_only"),
    ("resonance","candidate_chip","not_slotted"),
    ("resonance","future_overload","locked"),
]

FORMAT_FIELDS = [
    (("gear","weapon","damage_multiplier"), 2.35, "2.35x"),
    (("gear","belt","damage_multiplier"), 1.75, "175%"),
    (("survivor","damage_multiplier"), 2.10, "2.1x"),
    (("tech","drone","damage_multiplier"), 2.25, "225%"),
    (("tech","lightning","damage_multiplier"), 1.55, "1.55x"),
    (("tech","twinborn","damage_multiplier"), 1.40, "140%"),
    (("pet","main","damage_multiplier"), 1.85, "1.85x"),
    (("collectibles","damage_multiplier"), 2.20, "220%"),
]

HP_TRAPS = [
    ("gear","armor","hp"),
    ("gear","armor","health"),
    ("gear","armor","defense"),
    ("gear","armor","damage_reduction"),
    ("survivor","hp"),
    ("collectibles","hp"),
]

def log(msg=""):
    print(msg, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(str(msg) + "\n")

def run(cmd, seconds=None):
    started = time.time()
    p = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=seconds,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    elapsed = round(time.time() - started, 3)
    return {
        "cmd": " ".join(map(str, cmd)),
        "returncode": p.returncode,
        "stdout": p.stdout,
        "stderr": p.stderr,
        "elapsed": elapsed,
    }

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {
        "cycle": 0,
        "difficulty": 1,
        "trap_strength": 1000,
        "random_cases": 100,
        "best_pass_streak": 0,
        "total_failures_found": 0,
        "created_at": time.time(),
    }

def save_state(s):
    STATE_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")

def setp(d, path, val):
    x = d
    for p in path[:-1]:
        if p not in x or not isinstance(x[p], dict):
            x[p] = {}
        x = x[p]
    x[path[-1]] = val

def close(a, b):
    return abs(float(a) - float(b)) < 0.001

def import_hardtest():
    test_path = ROOT / "tests" / "test_optimizer_red_team_realistic_hard.py"
    spec = importlib.util.spec_from_file_location("hardtest", test_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def mine_enemy_cases(state):
    mod = import_hardtest()
    random.seed(9000 + state["cycle"])

    def profile():
        return copy.deepcopy(mod._base_profile())

    def dmg(p):
        return mod._damage(mod._run(p))

    base = profile()
    base_damage = dmg(base)
    failures = []
    tested = 0

    strengths = [
        state["trap_strength"],
        state["trap_strength"] * 10,
        state["trap_strength"] * 100,
        "999x",
        "99900%",
        "999999%",
    ]

    # Active-state traps: these should never change damage.
    for path in TRAP_PATHS:
        for val in strengths:
            tested += 1
            p = profile()
            setp(p, path + ("damage_multiplier",), val)
            setp(p, path + ("active",), False)
            setp(p, path + ("equipped",), False)
            setp(p, path + ("owned",), True)
            setp(p, path + ("unlocked",), False)
            setp(p, path + ("source_kind",), "enemy_trap")
            setp(p, path + ("damage_relevance",), "should_not_count")

            try:
                actual = dmg(p)
            except Exception as e:
                failures.append({"category": "crash_on_inactive_trap", "path": ".".join(path), "value": val, "error": repr(e), "profile": p})
                continue

            if not close(actual, base_damage):
                failures.append({
                    "category": "inactive_or_locked_damage_counted",
                    "path": ".".join(path),
                    "value": val,
                    "expected_damage": base_damage,
                    "actual_damage": actual,
                    "delta": round(actual - base_damage, 4),
                    "profile": p,
                })

    # HP bait should not change damage.
    for path in HP_TRAPS:
        for val in strengths:
            tested += 1
            p = profile()
            setp(p, path, val)
            try:
                actual = dmg(p)
            except Exception as e:
                failures.append({"category": "hp_defense_crash", "path": ".".join(path), "value": val, "error": repr(e), "profile": p})
                continue

            if not close(actual, base_damage):
                failures.append({
                    "category": "hp_defense_changed_damage",
                    "path": ".".join(path),
                    "value": val,
                    "expected_damage": base_damage,
                    "actual_damage": actual,
                    "delta": round(actual - base_damage, 4),
                    "profile": p,
                })

    # Format aliases must match numeric.
    for i in range(int(state["random_cases"])):
        numeric = profile()
        formatted = profile()
        chosen = random.sample(FORMAT_FIELDS, random.randint(1, len(FORMAT_FIELDS)))

        for path, num, fmt in chosen:
            setp(numeric, path, num)
            setp(formatted, path, fmt)

        tested += 1
        try:
            nd = dmg(numeric)
            fd = dmg(formatted)
        except Exception as e:
            failures.append({
                "category": "format_alias_crash",
                "round": i,
                "fields": [".".join(x[0]) for x in chosen],
                "error": repr(e),
                "numeric_profile": numeric,
                "formatted_profile": formatted,
            })
            continue

        if not close(nd, fd):
            failures.append({
                "category": "format_alias_mismatch",
                "round": i,
                "fields": [".".join(x[0]) for x in chosen],
                "numeric_damage": nd,
                "formatted_damage": fd,
                "delta": round(fd - nd, 4),
                "numeric_profile": numeric,
                "formatted_profile": formatted,
            })

    return tested, failures

def update_mistake_bank(new_failures):
    old = []
    if BANK_FILE.exists():
        try:
            old = json.loads(BANK_FILE.read_text(encoding="utf-8"))
        except Exception:
            old = []

    all_cases = old + new_failures
    # Keep it useful and not gigantic.
    all_cases = all_cases[-1000:]
    BANK_FILE.write_text(json.dumps(all_cases, indent=2), encoding="utf-8")
    return len(all_cases)

def write_generated_test():
    test_file = ROOT / "tests" / "test_neverending_mistake_memory.py"
    test_file.write_text(r'''
import json
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
BANK = ROOT / "training_outputs" / "neverending_mistake_bank.json"

spec = importlib.util.spec_from_file_location("hardtest", ROOT / "tests" / "test_optimizer_red_team_realistic_hard.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def _damage(p):
    return mod._damage(mod._run(p))

def test_neverending_mistake_memory():
    if not BANK.exists():
        return

    cases = json.loads(BANK.read_text(encoding="utf-8"))
    for c in cases[-200:]:
        cat = c.get("category")

        if cat in ("inactive_or_locked_damage_counted", "hp_defense_changed_damage"):
            assert _damage(c["profile"]) == c["expected_damage"], c

        elif cat == "format_alias_mismatch":
            assert _damage(c["numeric_profile"]) == _damage(c["formatted_profile"]), c
''', encoding="utf-8")
    return test_file


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


def run_hard_tests():
    tests = [t for t in HARD_TESTS if (ROOT / t).exists()]
    return run([sys.executable, "-m", "pytest", *tests, "-q", "--tb=short"], seconds=120)

def run_adversarial_training(minutes):
    script = ROOT / "tools" / "run_live_adversarial_battle.py"
    if not script.exists():
        return {"returncode": 99, "stdout": "", "stderr": "missing run_live_adversarial_battle.py", "elapsed": 0}
    return run([
        sys.executable, str(script),
        "--run-mode", "timed",
        "--minutes", str(minutes),
        "--batch-size", "256",
        "--max-generated-per-round", "500",
        "--use-gpu",
        "--cpu-workers", "6",
        "--skip-baseline",
    ], seconds=(minutes * 80) + 60)

def run_speed_check():
    script = ROOT / "tools" / "benchmark_optimizer.py"
    if script.exists():
        return run([sys.executable, str(script)], seconds=90)
    return {"returncode": 0, "stdout": "benchmark_optimizer.py missing; skipped", "stderr": "", "elapsed": 0}

def latest_runtime():
    desktop = Path.home() / "Desktop"
    candidates = list(desktop.glob("survivor_nano*_runtime_*")) + list(desktop.glob("survivor_micro_runtime_*"))
    candidates = [p for p in candidates if p.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)

def folder_mb(p):
    return round(sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1024 / 1024, 3)

def zip_runtime(p):
    if not p:
        return None
    zp = p.with_suffix(".zip")
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in p.rglob("*"):
            if f.is_file() and "training_outputs" not in str(f):
                z.write(f, f.relative_to(p))
    return {"folder": str(p), "zip": str(zp), "folder_mb": folder_mb(p), "zip_mb": round(zp.stat().st_size / 1024 / 1024, 3)}

def summarize_failures(failures):
    counts = {}
    for f in failures:
        counts[f["category"]] = counts.get(f["category"], 0) + 1
    return counts

def adapt(state, failures, hard_ok, speed_ok, size_ok):
    if failures or not hard_ok:
        state["pass_streak"] = 0
        state["random_cases"] = min(2000, int(state["random_cases"] * 1.25) + 25)
        state["trap_strength"] = min(10**9, int(state["trap_strength"] * 2))
        state["difficulty"] += 1
    else:
        state["pass_streak"] = state.get("pass_streak", 0) + 1
        state["best_pass_streak"] = max(state.get("best_pass_streak", 0), state["pass_streak"])
        state["random_cases"] = min(5000, int(state["random_cases"] * 1.5) + 50)
        state["trap_strength"] = min(10**12, int(state["trap_strength"] * 3))
        state["difficulty"] += 2

    if not speed_ok:
        state["speed_pressure"] = state.get("speed_pressure", 0) + 1
    if not size_ok:
        state["size_pressure"] = state.get("size_pressure", 0) + 1

def main():
    state = load_state()
    log("NEVERENDING GOAL GAUNTLET STARTED")
    log("Press Ctrl+C to stop.")
    log("Goals: smarter, faster, more with less, smaller.")
    log("")

    while True:
        state["cycle"] += 1
        cycle = state["cycle"]
        started = time.time()

        log("=" * 80)
        log(f"CYCLE {cycle} | difficulty={state['difficulty']} | traps={state['trap_strength']} | random_cases={state['random_cases']}")

        tested, failures = mine_enemy_cases(state)
        state["total_failures_found"] += len(failures)
        bank_size = update_mistake_bank(failures)
        test_file = write_generated_test()

        counts = summarize_failures(failures)
        log(f"Enemy cases tested: {tested}")
        log(f"New failures: {len(failures)}")
        log(f"Mistake bank size: {bank_size}")
        log(f"Generated test: {test_file}")
        log("Failure counts: " + json.dumps(counts, sort_keys=True))

        hard = run_hard_tests()
        hard_ok = hard["returncode"] == 0
        pytest_failures = extract_pytest_failures(hard)
        if pytest_failures:
            failures.extend(pytest_failures)
            bank_size = update_mistake_bank(failures)
            counts = summarize_failures(failures)
        log(f"Hard tests: {'PASS' if hard_ok else 'FAIL'} in {hard['elapsed']}s")
        if pytest_failures:
            log(f"Learned pytest failures: {len(pytest_failures)}")
        if not hard_ok:
            tail = "\n".join((hard["stdout"] + "\n" + hard["stderr"]).splitlines()[-30:])
            log("Hard test tail:")
            log(tail)

        train_minutes = 1 if hard_ok else 2
        train = run_adversarial_training(train_minutes)
        log(f"Adversarial training: rc={train['returncode']} elapsed={train['elapsed']}s")
        train_tail = "\n".join((train["stdout"] + "\n" + train["stderr"]).splitlines()[-10:])
        log(train_tail)

        ensure_speed_profiles()
        speed = run_speed_check()
        speed_ok = speed["returncode"] == 0
        log(f"Speed check: rc={speed['returncode']} elapsed={speed['elapsed']}s")
        speed_tail = "\n".join((speed["stdout"] + "\n" + speed["stderr"]).splitlines()[-8:])
        log(speed_tail)

        rt = latest_runtime()
        zipped = zip_runtime(rt) if rt else None
        size_ok = True
        if zipped:
            size_ok = zipped["folder_mb"] <= GOALS["micro_runtime_target_mb"] or zipped["zip_mb"] <= 1.0
            log("Compressed runtime: " + json.dumps(zipped))
        else:
            log("No runtime found to compress.")

        adapt(state, failures, hard_ok, speed_ok, size_ok)
        state["last_cycle_seconds"] = round(time.time() - started, 3)
        state["last_failures"] = len(failures)
        state["last_failure_counts"] = counts
        state["last_hard_ok"] = hard_ok
        state["last_speed_ok"] = speed_ok
        state["last_size_ok"] = size_ok
        state["last_runtime_zip"] = zipped

        save_state(state)
        SUMMARY_FILE.write_text(json.dumps({
            "goals": GOALS,
            "state": state,
            "last_failure_counts": counts,
            "latest_runtime": zipped,
        }, indent=2), encoding="utf-8")

        log(f"CYCLE {cycle} DONE in {state['last_cycle_seconds']}s")
        log(f"Next difficulty: {state['difficulty']}")
        log("")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("")
        log("STOPPED CLEANLY BY USER")
        raise SystemExit(0)
