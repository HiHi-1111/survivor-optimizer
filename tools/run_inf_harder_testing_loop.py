import copy, importlib.util, json, os, random, re, subprocess, sys, time, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "training_outputs"
OUT.mkdir(exist_ok=True)

STATE = OUT / "inf_harder_state.json"
BANK = OUT / "inf_harder_decision_bank.json"
SUMMARY = OUT / "inf_harder_summary.json"
LOG = OUT / "inf_harder_testing.log"

spec = importlib.util.spec_from_file_location("hardtest", ROOT / "tests" / "test_optimizer_red_team_realistic_hard.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def log(x=""):
    print(x, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(str(x) + "\n")

def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {
        "cycle": 0,
        "difficulty": 50,
        "case_count": 60,
        "trap_power": 1000000000,
        "pass_streak": 0,
        "total_failures": 0,
    }

def save_state(s):
    STATE.write_text(json.dumps(s, indent=2), encoding="utf-8")

def setp(d, path, val):
    x = d
    for p in path[:-1]:
        if p not in x or not isinstance(x[p], dict):
            x[p] = {}
        x = x[p]
    x[path[-1]] = val

def flat(x):
    if isinstance(x, dict):
        return " ".join(str(k) + " " + flat(v) for k, v in x.items())
    if isinstance(x, list):
        return " ".join(flat(v) for v in x)
    return str(x)

def run_profile(p):
    return mod._run(copy.deepcopy(p))

def plan_text(result):
    if isinstance(result, dict) and result.get("global_plan") is not None:
        return flat(result["global_plan"]).lower(), True
    return flat(result).lower(), False

def positive_near(text, token):
    token = token.lower()
    i = text.find(token)
    if i < 0:
        return False
    window = text[max(0, i-180):i+260]
    positive = ["do", "use", "upgrade", "prioritize", "best", "recommend", "spend", "choose", "top"]
    negative = ["ignore", "locked", "inactive", "do not", "dont", "don't", "save", "avoid", "not active", "not count"]
    return any(p in window for p in positive) and not any(n in window for n in negative)

def actionable(text):
    return any(x in text for x in ["do", "save", "locked", "conditional", "priority", "recommend", "next", "breakpoint", "missing", "blocker"])

def base():
    return copy.deepcopy(mod._base_profile())

def make_case(rng, kind, trap_power):
    p = base()

    if kind == "locked_preview":
        slot = rng.choice(["belt", "weapon", "gloves", "necklace", "boots", "armor"])
        token = f"ENEMY_LOCKED_{slot.upper()}_{trap_power}X"
        setp(p, ("gear", slot, token), {
            "damage_multiplier": f"{trap_power}x",
            "active": False,
            "equipped": False,
            "owned": False,
            "unlocked": False,
            "source_kind": "locked_preview_enemy",
            "damage_relevance": "should_not_count",
        })
        return {
            "name": f"locked_preview_{slot}",
            "profile": p,
            "trap_token": token.lower(),
            "required_terms": ["locked", "inactive", "ignore", "not active", "not count", "save"],
            "min_terms": 2,
        }

    if kind == "inactive_inventory":
        slot = rng.choice(["belt", "weapon", "gloves", "necklace"])
        token = f"ENEMY_INVENTORY_{slot.upper()}_{trap_power}X"
        setp(p, ("gear", slot, token), {
            "damage_multiplier": f"{trap_power}x",
            "active": False,
            "equipped": False,
            "owned": True,
            "unlocked": True,
            "source_kind": "inventory_enemy",
            "damage_relevance": "should_not_count",
        })
        return {
            "name": f"inactive_inventory_{slot}",
            "profile": p,
            "trap_token": token.lower(),
            "required_terms": ["equipped", "active", "ignore", "not active", "not count"],
            "min_terms": 2,
        }

    if kind == "cheap_material_bait":
        setp(p, ("resources", "relic_core"), 0)
        setp(p, ("resources", "awakening_core"), 0)
        setp(p, ("resources", "resonance_chip"), 0)
        setp(p, ("resources", "xeno_core"), 0)
        setp(p, ("resources", "common_material"), trap_power)
        setp(p, ("resources", "purple_fodder"), trap_power)
        setp(p, ("resources", "yellow_fodder"), trap_power)
        setp(p, ("optimizer_goal"), "DPS first. Cheap material bait must lose to rare blocker shortage.")
        return {
            "name": "cheap_material_bait",
            "profile": p,
            "trap_token": "cheap material bait",
            "required_terms": ["relic", "awakening", "resonance", "xeno", "blocker", "save"],
            "min_terms": 2,
        }

    if kind == "hp_bait":
        token = "ENEMY_HP_DEFENSE_BAIT"
        setp(p, ("gear", "armor", token), {
            "hp": f"{trap_power}%",
            "health": f"{trap_power}%",
            "defense": f"{trap_power}%",
            "damage_reduction": f"{trap_power}%",
            "active": True,
            "equipped": True,
        })
        setp(p, ("optimizer_goal"), "DPS first. HP must not become priority.")
        return {
            "name": "hp_defense_bait",
            "profile": p,
            "trap_token": token.lower(),
            "required_terms": ["dps", "damage", "hp", "ignore", "not priority"],
            "min_terms": 2,
        }

    if kind == "ss_belt_breakpoint":
        crit = rng.choice([71, 99, 100, 101, 121, 129, 130, 131, 149, 150, 151])
        setp(p, ("crit_rate"), crit)
        setp(p, ("resources", "relic_core"), rng.choice([0, 5, 10, 20, 50]))
        setp(p, ("gear", "belt", "current"), "SS Belt")
        setp(p, ("optimizer_goal"), f"SS belt DPS. Crit {crit}. Must know E1/E3/E5 breakpoints.")
        return {
            "name": f"ss_belt_breakpoint_crit_{crit}",
            "profile": p,
            "trap_token": "ss belt",
            "required_terms": ["crit", "100", "130", "150", "relic", "save", "breakpoint"],
            "min_terms": 3,
        }

    if kind == "xeno_pet_gate":
        token = "ENEMY_XENO_PREVIEW"
        setp(p, ("pet", token), {
            "damage_multiplier": f"{trap_power}x",
            "xeno": True,
            "active": False,
            "deployed": False,
            "awakened": False,
            "owned": False,
            "source_kind": "xeno_preview_enemy",
        })
        setp(p, ("optimizer_goal"), "Xeno pet preview must not count unless active/deployed/awakened.")
        return {
            "name": "xeno_pet_gate",
            "profile": p,
            "trap_token": token.lower(),
            "required_terms": ["xeno", "pet", "active", "awaken", "save", "locked"],
            "min_terms": 2,
        }

    token = "ENEMY_RESONANCE_UNSLOTTED"
    setp(p, ("tech", "resonance", token), {
        "resonance_multiplier": f"{trap_power}x",
        "slotted": False,
        "active": False,
        "owned": True,
        "source_kind": "unslotted_resonance_enemy",
    })
    setp(p, ("optimizer_goal"), "Unslotted resonance candidate must not count as active DPS.")
    return {
        "name": "resonance_unslotted_gate",
        "profile": p,
        "trap_token": token.lower(),
        "required_terms": ["resonance", "slotted", "active", "chip", "save"],
        "min_terms": 2,
    }

def generate_cases(state):
    rng = random.Random(10000 + state["cycle"])
    kinds = [
        "locked_preview",
        "inactive_inventory",
        "cheap_material_bait",
        "hp_bait",
        "ss_belt_breakpoint",
        "xeno_pet_gate",
        "resonance_unslotted",
    ]
    cases = []
    for _ in range(int(state["case_count"])):
        cases.append(make_case(rng, rng.choice(kinds), int(state["trap_power"])))
    return cases

def test_cases(cases):
    failures = []
    for c in cases:
        try:
            result = run_profile(c["profile"])
            text, has_gp = plan_text(result)
        except Exception as e:
            failures.append({**c, "category": "global_plan_crash", "error": repr(e)})
            continue

        if not has_gp:
            failures.append({**c, "category": "missing_global_plan", "text_sample": text[:1200]})
            continue

        if not actionable(text):
            failures.append({**c, "category": "not_actionable", "text_sample": text[:1200]})

        if positive_near(text, c["trap_token"]):
            failures.append({**c, "category": "trap_recommended", "text_sample": text[:1200]})

        hits = [t for t in c["required_terms"] if t in text]
        if len(hits) < int(c["min_terms"]):
            failures.append({
                **c,
                "category": "missing_decision_explanation",
                "hits": hits,
                "missing_terms": [t for t in c["required_terms"] if t not in text],
                "text_sample": text[:1200],
            })
    return failures

def update_bank(failures):
    old = []
    if BANK.exists():
        try:
            old = json.loads(BANK.read_text(encoding="utf-8"))
        except Exception:
            old = []
    # save compact-ish; cap at 600
    merged = (old + failures)[-600:]
    BANK.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return len(merged)

def write_memory_test():
    test_file = ROOT / "tests" / "test_inf_harder_decision_memory.py"
    test_file.write_text(r'''
import json
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
BANK = ROOT / "training_outputs" / "inf_harder_decision_bank.json"

spec = importlib.util.spec_from_file_location("hardtest", ROOT / "tests" / "test_optimizer_red_team_realistic_hard.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def _flat(x):
    if isinstance(x, dict):
        return " ".join(str(k)+" "+_flat(v) for k,v in x.items())
    if isinstance(x, list):
        return " ".join(_flat(v) for v in x)
    return str(x)

def _plan(result):
    assert isinstance(result, dict) and result.get("global_plan") is not None
    return _flat(result["global_plan"]).lower()

def _positive_near(text, token):
    token = token.lower()
    i = text.find(token)
    if i < 0:
        return False
    window = text[max(0, i-180):i+260]
    pos = ["do", "use", "upgrade", "prioritize", "best", "recommend", "spend", "choose", "top"]
    neg = ["ignore", "locked", "inactive", "do not", "dont", "don't", "save", "avoid", "not active", "not count"]
    return any(p in window for p in pos) and not any(n in window for n in neg)

def test_inf_harder_decision_memory():
    if not BANK.exists():
        return
    cases = json.loads(BANK.read_text(encoding="utf-8"))[-200:]
    for c in cases:
        result = mod._run(c["profile"])
        text = _plan(result)
        assert any(x in text for x in ["do", "save", "locked", "conditional", "priority", "recommend", "next", "breakpoint", "missing", "blocker"]), c
        assert not _positive_near(text, c.get("trap_token", "")), c
        terms = c.get("required_terms", [])
        min_terms = int(c.get("min_terms", 2))
        hits = [t for t in terms if t in text]
        assert len(hits) >= min_terms, c
''', encoding="utf-8")
    return test_file

def run(cmd, timeout=120):
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=timeout, env={**os.environ, "PYTHONPATH": str(ROOT)})

def hard_pytest():
    tests = [
        "tests/test_global_planner.py",
        "tests/test_generated_global_plan_killer_memory.py",
        "tests/test_inf_harder_decision_memory.py",
        "tests/test_optimizer_red_team_realistic_hard.py",
    ]
    tests = [t for t in tests if (ROOT / t).exists()]
    return run([sys.executable, "-m", "pytest", *tests, "-q", "--tb=short"], timeout=180)

def compress_runtime():
    desktop = Path.home() / "Desktop"
    rts = [x for x in list(desktop.glob("survivor_nano*_runtime_*")) + list(desktop.glob("survivor_micro_runtime_*")) if x.is_dir()]
    if not rts:
        return None
    rt = max(rts, key=lambda x: x.stat().st_mtime)
    zp = rt.with_suffix(".zip")
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in rt.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(rt))
    return {
        "folder": str(rt),
        "zip": str(zp),
        "folder_mb": round(sum(f.stat().st_size for f in rt.rglob("*") if f.is_file()) / 1024 / 1024, 3),
        "zip_mb": round(zp.stat().st_size / 1024 / 1024, 3),
    }

def adapt(state, failure_count, pytest_ok):
    if failure_count == 0 and pytest_ok:
        state["pass_streak"] = int(state.get("pass_streak", 0)) + 1
        state["difficulty"] += 10
        state["case_count"] = min(2000, int(state["case_count"] * 1.4) + 20)
        state["trap_power"] = min(10**18, int(state["trap_power"] * 10))
    else:
        state["pass_streak"] = 0
        state["difficulty"] += 3
        state["case_count"] = min(2000, int(state["case_count"] * 1.15) + 20)
        state["trap_power"] = min(10**18, int(state["trap_power"] * 3))

def counts(failures):
    d = {}
    for f in failures:
        d[f["category"]] = d.get(f["category"], 0) + 1
    return d

def main():
    state = load_state()
    log("INF HARDER DECISION GAUNTLET STARTED")
    log("Ctrl+C stops it. Leave this running.")
    log("")

    while True:
        state["cycle"] += 1
        started = time.time()

        log("=" * 80)
        log(f"CYCLE {state['cycle']} | difficulty={state['difficulty']} | cases={state['case_count']} | trap_power={state['trap_power']}")

        cases = generate_cases(state)
        failures = test_cases(cases)
        bank_size = update_bank(failures)
        test_file = write_memory_test()

        cts = counts(failures)
        log(f"Generated cases: {len(cases)}")
        log(f"New decision failures: {len(failures)}")
        log("Failure counts: " + json.dumps(cts, sort_keys=True))
        log(f"Bank size: {bank_size}")
        log(f"Generated memory test: {test_file}")

        p = hard_pytest()
        pytest_ok = p.returncode == 0
        log(f"Hard pytest: {'PASS' if pytest_ok else 'FAIL'}")
        if not pytest_ok:
            tail = "\n".join((p.stdout + "\n" + p.stderr).splitlines()[-35:])
            log("Pytest tail:")
            log(tail)

        runtime = compress_runtime()
        log("Runtime: " + json.dumps(runtime))

        state["total_failures"] = int(state.get("total_failures", 0)) + len(failures)
        state["last_failure_counts"] = cts
        state["last_pytest_ok"] = pytest_ok
        state["last_runtime"] = runtime
        state["last_seconds"] = round(time.time() - started, 3)

        adapt(state, len(failures), pytest_ok)
        save_state(state)

        SUMMARY.write_text(json.dumps({
            "state": state,
            "last_failure_counts": cts,
            "last_runtime": runtime,
            "bank": str(BANK),
            "log": str(LOG),
        }, indent=2), encoding="utf-8")

        log(f"CYCLE DONE in {state['last_seconds']}s")
        log(f"Next difficulty={state['difficulty']} cases={state['case_count']} trap_power={state['trap_power']}")
        log("")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("")
        log("STOPPED CLEANLY")
        raise SystemExit(0)
