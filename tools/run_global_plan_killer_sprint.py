import copy, importlib.util, json, os, re, subprocess, sys, time, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "training_outputs"
OUT.mkdir(exist_ok=True)

BANK = OUT / "global_plan_killer_bank.json"
SUMMARY = OUT / "global_plan_killer_summary.json"

spec = importlib.util.spec_from_file_location(
    "hardtest",
    ROOT / "tests" / "test_optimizer_red_team_realistic_hard.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

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

def global_plan_text(result):
    if isinstance(result, dict):
        gp = result.get("global_plan")
        if gp is not None:
            return flat(gp).lower(), True
    return flat(result).lower(), False

def positive_recommendation_near(text, token):
    i = text.find(token.lower())
    if i < 0:
        return False
    window = text[max(0, i-160):i+220]
    positive = ["do", "use", "upgrade", "prioritize", "best", "recommend", "spend", "choose", "top"]
    negative = ["ignore", "locked", "inactive", "do not", "dont", "don't", "save", "avoid", "not active"]
    return any(p in window for p in positive) and not any(n in window for n in negative)

def is_actionable(text):
    return any(x in text for x in ["global_plan", "do", "save", "locked", "conditional", "priority", "recommend", "next", "breakpoint"])

def base():
    return copy.deepcopy(mod._base_profile())

cases = []

def case_locked_af():
    p = base()
    setp(p, ("gear","belt","ENEMY_LOCKED_AF_PREVIEW_999X"), {
        "damage_multiplier": "999x",
        "active": False,
        "equipped": False,
        "owned": False,
        "unlocked": False,
        "source_kind": "locked_preview_trap",
        "damage_relevance": "should_not_count",
        "note": "If recommended, optimizer is hallucinating locked preview damage."
    })
    return p

cases.append({
    "name": "locked_af_preview_must_not_be_recommended",
    "profile": case_locked_af(),
    "trap_token": "enemy_locked_af_preview_999x",
    "must_explain": ["locked", "save", "ignore", "not active", "not count"],
})

def case_inactive_inventory():
    p = base()
    setp(p, ("gear","weapon","ENEMY_UNEQUIPPED_INVENTORY_COPY"), {
        "damage_multiplier": "777x",
        "active": False,
        "equipped": False,
        "owned": True,
        "unlocked": True,
        "source_kind": "inventory_trap",
        "damage_relevance": "should_not_count",
    })
    return p

cases.append({
    "name": "inactive_inventory_copy_must_not_be_recommended",
    "profile": case_inactive_inventory(),
    "trap_token": "enemy_unequipped_inventory_copy",
    "must_explain": ["equipped", "active", "ignore", "not active", "not count"],
})

def case_cheap_material_bait():
    p = base()
    setp(p, ("resources","relic_core"), 0)
    setp(p, ("resources","awakening_core"), 0)
    setp(p, ("resources","resonance_chip"), 0)
    setp(p, ("resources","xeno_core"), 0)
    setp(p, ("resources","common_material"), 999999999)
    setp(p, ("resources","purple_fodder"), 999999999)
    setp(p, ("resources","yellow_fodder"), 999999999)
    setp(p, ("optimizer_goal"), "DPS first; cheap material bait must lose to rare blocker shortage.")
    return p

cases.append({
    "name": "cheap_material_bait_must_not_hide_rare_blockers",
    "profile": case_cheap_material_bait(),
    "trap_token": "cheap material bait",
    "must_explain": ["relic", "awakening", "resonance", "xeno", "save", "blocker"],
})

def case_hp_defense_bait():
    p = base()
    setp(p, ("gear","armor","ENEMY_HP_BAIT"), {
        "hp": "999999999%",
        "defense": "999999999%",
        "damage_reduction": "999999999%",
        "active": True,
        "equipped": True,
        "note": "HP should not beat DPS in this optimizer."
    })
    return p

cases.append({
    "name": "hp_defense_bait_must_not_beat_dps",
    "profile": case_hp_defense_bait(),
    "trap_token": "enemy_hp_bait",
    "must_explain": ["dps", "damage", "ignore hp", "hp weight", "not priority"],
})

def case_ss_belt_breakpoint():
    p = base()
    setp(p, ("crit_rate"), 121)
    setp(p, ("resources","relic_core"), 20)
    setp(p, ("gear","belt","current"), "SS Belt")
    setp(p, ("optimizer_goal"), "SS belt DPS with crit 121. E1 active, E3/E5 locked. Protect relic cores.")
    return p

cases.append({
    "name": "ss_belt_121_crit_needs_breakpoint_plan",
    "profile": case_ss_belt_breakpoint(),
    "trap_token": "ss belt",
    "must_explain": ["100", "130", "150", "crit", "save", "relic", "breakpoint"],
})

failures = []
tested = 0

print("GLOBAL PLAN KILLER SPRINT")
print("Testing decision smartness, not raw damage.")
print()

for c in cases:
    tested += 1
    try:
        result = run_profile(c["profile"])
        text, has_gp = global_plan_text(result)
    except Exception as e:
        failures.append({
            "category": "global_plan_crash",
            "case": c["name"],
            "error": repr(e),
            "profile": c["profile"],
        })
        print("CRASH:", c["name"], repr(e))
        continue

    if not has_gp:
        failures.append({
            "category": "missing_global_plan",
            "case": c["name"],
            "profile": c["profile"],
            "text_sample": text[:800],
        })
        print("FAIL missing_global_plan:", c["name"])

    if not is_actionable(text):
        failures.append({
            "category": "not_actionable",
            "case": c["name"],
            "profile": c["profile"],
            "text_sample": text[:800],
        })
        print("FAIL not_actionable:", c["name"])

    if positive_recommendation_near(text, c["trap_token"]):
        failures.append({
            "category": "trap_recommended",
            "case": c["name"],
            "trap_token": c["trap_token"],
            "profile": c["profile"],
            "text_sample": text[:1200],
        })
        print("FAIL trap_recommended:", c["name"])

    missing = [x for x in c["must_explain"] if x not in text]
    if len(missing) >= max(2, len(c["must_explain"]) // 2):
        failures.append({
            "category": "missing_decision_explanation",
            "case": c["name"],
            "missing_terms": missing,
            "profile": c["profile"],
            "text_sample": text[:1200],
        })
        print("FAIL missing_decision_explanation:", c["name"], "missing", missing)

counts = {}
for f in failures:
    counts[f["category"]] = counts.get(f["category"], 0) + 1

old = []
if BANK.exists():
    try:
        old = json.loads(BANK.read_text(encoding="utf-8"))
    except Exception:
        old = []

bank = (old + failures)[-1000:]
BANK.write_text(json.dumps(bank, indent=2), encoding="utf-8")

test_file = ROOT / "tests" / "test_generated_global_plan_killer_memory.py"
test_file.write_text(r'''
import json
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
BANK = ROOT / "training_outputs" / "global_plan_killer_bank.json"

spec = importlib.util.spec_from_file_location("hardtest", ROOT / "tests" / "test_optimizer_red_team_realistic_hard.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def _flat(x):
    if isinstance(x, dict):
        return " ".join(str(k)+" "+_flat(v) for k,v in x.items())
    if isinstance(x, list):
        return " ".join(_flat(v) for v in x)
    return str(x)

def _plan_text(result):
    if isinstance(result, dict) and result.get("global_plan") is not None:
        return _flat(result["global_plan"]).lower()
    return _flat(result).lower()

def _positive_near(text, token):
    i = text.find(token.lower())
    if i < 0:
        return False
    window = text[max(0, i-160):i+220]
    positive = ["do", "use", "upgrade", "prioritize", "best", "recommend", "spend", "choose", "top"]
    negative = ["ignore", "locked", "inactive", "do not", "dont", "don't", "save", "avoid", "not active"]
    return any(p in window for p in positive) and not any(n in window for n in negative)

def test_generated_global_plan_killer_memory():
    if not BANK.exists():
        return
    cases = json.loads(BANK.read_text(encoding="utf-8"))
    for c in cases[-100:]:
        if "profile" not in c:
            continue
        result = mod._run(c["profile"])
        text = _plan_text(result)
        if c.get("category") == "missing_global_plan":
            assert isinstance(result, dict) and result.get("global_plan") is not None, c
        if c.get("category") == "not_actionable":
            assert any(x in text for x in ["do", "save", "locked", "conditional", "priority", "recommend", "next", "breakpoint"]), c
        if c.get("category") == "trap_recommended":
            assert not _positive_near(text, c.get("trap_token", "")), c
''', encoding="utf-8")

# hard run
cmd = [
    sys.executable, "-m", "pytest",
    "tests/test_global_planner.py",
    "tests/test_generated_global_plan_killer_memory.py",
    "-q", "--tb=short"
]
p = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, env={**os.environ, "PYTHONPATH": str(ROOT)})

# compress runtime
desktop = Path.home() / "Desktop"
rts = [x for x in list(desktop.glob("survivor_nano*_runtime_*")) + list(desktop.glob("survivor_micro_runtime_*")) if x.is_dir()]
runtime = None
if rts:
    rt = max(rts, key=lambda x: x.stat().st_mtime)
    zp = rt.with_suffix(".zip")
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in rt.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(rt))
    runtime = {
        "folder": str(rt),
        "zip": str(zp),
        "folder_mb": round(sum(f.stat().st_size for f in rt.rglob("*") if f.is_file()) / 1024 / 1024, 3),
        "zip_mb": round(zp.stat().st_size / 1024 / 1024, 3),
    }

summary = {
    "tested": tested,
    "new_failures": len(failures),
    "failure_counts": counts,
    "bank_size": len(bank),
    "generated_test": str(test_file),
    "pytest_returncode": p.returncode,
    "pytest_tail": "\n".join((p.stdout + "\n" + p.stderr).splitlines()[-30:]),
    "runtime": runtime,
    "created_at": time.time(),
}
SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")

print()
print("TESTED:", tested)
print("NEW DECISION FAILURES:", len(failures))
print("FAILURE COUNTS:")
for k,v in sorted(counts.items(), key=lambda x:x[1], reverse=True):
    print(f"{k}: {v}")
print("BANK SIZE:", len(bank))
print("Generated test:", test_file)
print("Pytest rc:", p.returncode)
print("Runtime:", runtime)
print("Saved:", SUMMARY)
print()
print("PYTEST TAIL:")
print(summary["pytest_tail"])

if failures or p.returncode != 0:
    raise SystemExit(1)
