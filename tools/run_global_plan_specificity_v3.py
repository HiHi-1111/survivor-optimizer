import copy, importlib.util, json, os, random, subprocess, sys, time, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "training_outputs"
OUT.mkdir(exist_ok=True)

BANK = OUT / "global_plan_specificity_v3_bank.json"
SUMMARY = OUT / "global_plan_specificity_v3_summary.json"

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

def plan_text(result):
    assert isinstance(result, dict), "optimizer result must be dict"
    assert result.get("global_plan") is not None, "missing global_plan"
    return flat(result["global_plan"]).lower()

def positive_near(text, token):
    token = token.lower()
    i = text.find(token)
    if i < 0:
        return False
    window = text[max(0, i-200):i+300]
    pos = ["do", "use", "upgrade", "prioritize", "best", "recommend", "spend", "choose", "top"]
    neg = ["ignore", "locked", "inactive", "do not", "dont", "don't", "save", "avoid", "not active", "not count", "not priority"]
    return any(p in window for p in pos) and not any(n in window for n in neg)

def base():
    return copy.deepcopy(mod._base_profile())

def make_case(rng, i):
    p = base()
    kind = rng.choice([
        "locked_slot_specific",
        "inactive_slot_specific",
        "hp_armor_specific",
        "ss_belt_exact_crit",
        "rare_blocker_zero_specific",
        "xeno_pet_specific",
        "resonance_slot_specific",
        "mixed_specific",
    ])

    power = 10 ** rng.randint(9, 18)
    slot = rng.choice(["weapon", "belt", "gloves", "necklace", "boots", "armor"])
    crit = rng.choice([71, 99, 100, 101, 121, 129, 130, 131, 149, 150, 151])
    token = ""

    required_specific = []
    required_policy = []

    if kind in ("locked_slot_specific", "mixed_specific"):
        token = f"enemy_locked_{slot}_{power}x"
        setp(p, ("gear", slot, token), {
            "damage_multiplier": f"{power}x",
            "active": False,
            "equipped": False,
            "owned": False,
            "unlocked": False,
            "source_kind": "locked_preview_specific_enemy",
        })
        required_specific += [slot]
        required_policy += ["locked", "not active", "not count", "ignore"]

    if kind in ("inactive_slot_specific", "mixed_specific"):
        token = token or f"enemy_inactive_{slot}_{power}x"
        setp(p, ("gear", slot, token), {
            "damage_multiplier": f"{power}x",
            "active": False,
            "equipped": False,
            "owned": True,
            "unlocked": True,
            "source_kind": "inactive_inventory_specific_enemy",
        })
        required_specific += [slot]
        required_policy += ["equipped", "active", "inactive", "not count"]

    if kind in ("hp_armor_specific", "mixed_specific"):
        token = token or "enemy_hp_armor_specific"
        setp(p, ("gear", "armor", "enemy_hp_armor_specific"), {
            "hp": f"{power}%",
            "health": f"{power}%",
            "defense": f"{power}%",
            "damage_reduction": f"{power}%",
            "active": True,
            "equipped": True,
        })
        required_specific += ["armor"]
        required_policy += ["hp", "defense", "dps", "not priority"]

    if kind in ("ss_belt_exact_crit", "mixed_specific"):
        setp(p, ("crit_rate"), crit)
        setp(p, ("gear", "belt", "current"), "SS Belt")
        setp(p, ("resources", "relic_core"), rng.choice([0, 1, 5, 10, 20, 50]))
        setp(p, ("optimizer_goal"), f"SS Belt exact crit planning. Current crit={crit}. DPS first.")
        token = token or "ss belt"
        required_specific += ["ss", "belt", str(crit)]
        required_policy += ["crit", "100", "130", "150", "breakpoint"]

    if kind in ("rare_blocker_zero_specific", "mixed_specific"):
        setp(p, ("resources", "common_material"), power)
        setp(p, ("resources", "purple_fodder"), power)
        setp(p, ("resources", "yellow_fodder"), power)
        setp(p, ("resources", "relic_core"), 0)
        setp(p, ("resources", "awakening_core"), 0)
        setp(p, ("resources", "resonance_chip"), 0)
        setp(p, ("resources", "xeno_core"), 0)
        token = token or "cheap material bait"
        required_specific += ["relic", "awakening", "resonance", "xeno"]
        required_policy += ["blocker", "save", "cheap"]

    if kind in ("xeno_pet_specific", "mixed_specific"):
        token = token or "enemy_xeno_pet_specific"
        setp(p, ("pet", "enemy_xeno_pet_specific"), {
            "damage_multiplier": f"{power}x",
            "xeno": True,
            "active": False,
            "deployed": False,
            "awakened": False,
            "owned": False,
        })
        required_specific += ["xeno", "pet"]
        required_policy += ["awaken", "deployed", "active", "locked"]

    if kind in ("resonance_slot_specific", "mixed_specific"):
        token = token or "enemy_unslotted_resonance_specific"
        setp(p, ("tech", "resonance", "enemy_unslotted_resonance_specific"), {
            "resonance_multiplier": f"{power}x",
            "slotted": False,
            "active": False,
            "owned": True,
        })
        required_specific += ["resonance"]
        required_policy += ["slotted", "active", "chip", "not count"]

    return {
        "id": i,
        "kind": kind,
        "slot": slot,
        "crit": crit,
        "token": token.lower(),
        "profile": p,
        "required_specific": sorted(set(required_specific)),
        "required_policy": sorted(set(required_policy)),
    }

rng = random.Random(999333)
cases = [make_case(rng, i) for i in range(1000)]

failures = []

for c in cases:
    try:
        result = mod._run(copy.deepcopy(c["profile"]))
        text = plan_text(result)
    except Exception as e:
        failures.append({**c, "category": "crash_or_missing_plan", "error": repr(e)})
        continue

    if "survivor_decision_guardrails_v1" not in text:
        failures.append({**c, "category": "missing_guardrail_marker", "text_sample": text[:1400]})
        continue

    specific_hits = [x for x in c["required_specific"] if x in text]
    policy_hits = [x for x in c["required_policy"] if x in text]

    if len(specific_hits) < max(1, min(2, len(c["required_specific"]))):
        failures.append({
            **c,
            "category": "lazy_generic_guardrail_specific_missing",
            "specific_hits": specific_hits,
            "policy_hits": policy_hits,
            "text_sample": text[:1600],
        })
        continue

    if len(policy_hits) < max(2, min(3, len(c["required_policy"]))):
        failures.append({
            **c,
            "category": "policy_terms_missing",
            "specific_hits": specific_hits,
            "policy_hits": policy_hits,
            "text_sample": text[:1600],
        })
        continue

    if c["token"] and positive_near(text, c["token"]):
        failures.append({
            **c,
            "category": "trap_recommended",
            "specific_hits": specific_hits,
            "policy_hits": policy_hits,
            "text_sample": text[:1600],
        })

old = []
if BANK.exists():
    try:
        old = json.loads(BANK.read_text(encoding="utf-8"))
    except Exception:
        old = []

bank = (old + failures)[-1000:]
BANK.write_text(json.dumps(bank, indent=2), encoding="utf-8")

test_file = ROOT / "tests" / "test_global_plan_specificity_v3_memory.py"
test_file.write_text(r'''
import json
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
BANK = ROOT / "training_outputs" / "global_plan_specificity_v3_bank.json"

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
    assert isinstance(result, dict)
    assert result.get("global_plan") is not None
    return _flat(result["global_plan"]).lower()

def test_global_plan_specificity_v3_memory():
    if not BANK.exists():
        return

    cases = json.loads(BANK.read_text(encoding="utf-8"))[-250:]

    for c in cases:
        result = mod._run(c["profile"])
        text = _plan(result)

        assert "survivor_decision_guardrails_v1" in text, c

        specific = c.get("required_specific", [])
        policy = c.get("required_policy", [])

        specific_hits = [x for x in specific if x in text]
        policy_hits = [x for x in policy if x in text]

        assert len(specific_hits) >= max(1, min(2, len(specific))), c
        assert len(policy_hits) >= max(2, min(3, len(policy))), c
''', encoding="utf-8")

cmd = [
    sys.executable, "-m", "pytest",
    "tests/test_global_plan_specificity_v3_memory.py",
    "tests/test_meaner_global_plan_v2_memory.py",
    "tests/test_inf_harder_decision_memory.py",
    "tests/test_generated_global_plan_killer_memory.py",
    "tests/test_global_planner.py",
    "tests/test_optimizer_red_team_realistic_hard.py",
    "-q", "--tb=short",
]
p = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, env={**os.environ, "PYTHONPATH": str(ROOT)})

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

counts = {}
for f in failures:
    counts[f["category"]] = counts.get(f["category"], 0) + 1

summary = {
    "tested": len(cases),
    "new_failures": len(failures),
    "failure_counts": counts,
    "bank_size": len(bank),
    "generated_test": str(test_file),
    "pytest_returncode": p.returncode,
    "pytest_tail": "\n".join((p.stdout + "\n" + p.stderr).splitlines()[-50:]),
    "runtime": runtime,
    "created_at": time.time(),
}
SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")

print("GLOBAL PLAN SPECIFICITY V3")
print("Tested:", len(cases))
print("New failures:", len(failures))
print("Failure counts:", counts)
print("Bank size:", len(bank))
print("Generated test:", test_file)
print("Pytest rc:", p.returncode)
print("Runtime:", runtime)
print("Saved:", SUMMARY)
print()
print("PYTEST TAIL:")
print(summary["pytest_tail"])

if failures or p.returncode != 0:
    raise SystemExit(1)
