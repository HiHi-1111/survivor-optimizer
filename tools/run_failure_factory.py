import copy, json, importlib.util, random, sys, time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

test_path = root / "tests" / "test_optimizer_red_team_realistic_hard.py"
spec = importlib.util.spec_from_file_location("hardtest", test_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

outdir = root / "training_outputs"
outdir.mkdir(exist_ok=True)

random.seed(777)

def setp(d, path, val):
    x = d
    for p in path[:-1]:
        if p not in x or not isinstance(x[p], dict):
            x[p] = {}
        x = x[p]
    x[path[-1]] = val

def dmg(profile):
    return mod._damage(mod._run(profile))

def close(a, b):
    return abs(float(a) - float(b)) < 0.001

def profile():
    return copy.deepcopy(mod._base_profile())

def add_inactive_trap(p, path, val):
    setp(p, path + ("damage_multiplier",), val)
    setp(p, path + ("active",), False)
    setp(p, path + ("equipped",), False)
    setp(p, path + ("owned",), True)
    setp(p, path + ("unlocked",), False)
    setp(p, path + ("source_kind",), "trap_preview")
    setp(p, path + ("damage_relevance",), "should_not_count")

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

failures = []
tested = 0

base = profile()
base_damage = dmg(base)

print("SURVIVOR FAILURE FACTORY")
print("Base damage:", base_damage)
print()

# 1) Inactive/locked trap cases: should NEVER change damage.
for path in TRAP_PATHS:
    for val in [3, 10, 99, 999, "999x", "99900%"]:
        tested += 1
        p = profile()
        add_inactive_trap(p, path, val)
        try:
            actual = dmg(p)
        except Exception as e:
            failures.append({
                "category": "crash_on_inactive_trap",
                "path": ".".join(path),
                "value": val,
                "error": repr(e),
                "profile": p,
            })
            print("CRASH inactive trap:", ".".join(path), val, repr(e))
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
            print("FAIL inactive counted:", ".".join(path), val, "actual", actual, "expected", base_damage)

# 2) Format equivalence cases: numeric and formatted must match.
for round_id in range(80):
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
            "round": round_id,
            "fields": [".".join(x[0]) for x in chosen],
            "error": repr(e),
            "numeric_profile": numeric,
            "formatted_profile": formatted,
        })
        print("CRASH format combo:", round_id, repr(e))
        continue

    if not close(nd, fd):
        failures.append({
            "category": "format_alias_mismatch",
            "round": round_id,
            "fields": [".".join(x[0]) for x in chosen],
            "numeric_damage": nd,
            "formatted_damage": fd,
            "delta": round(fd - nd, 4),
            "numeric_profile": numeric,
            "formatted_profile": formatted,
        })
        print("FAIL format mismatch:", round_id, "numeric", nd, "formatted", fd)

# 3) HP/defense bait should not increase damage.
HP_TRAPS = [
    ("gear","armor","hp"),
    ("gear","armor","health"),
    ("gear","armor","defense"),
    ("gear","armor","damage_reduction"),
    ("survivor","hp"),
    ("collectibles","hp"),
]

for path in HP_TRAPS:
    for val in [999999, "999999%", "999x"]:
        tested += 1
        p = profile()
        setp(p, path, val)
        try:
            actual = dmg(p)
        except Exception as e:
            failures.append({
                "category": "hp_defense_crash",
                "path": ".".join(path),
                "value": val,
                "error": repr(e),
                "profile": p,
            })
            print("CRASH hp bait:", ".".join(path), val, repr(e))
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
            print("FAIL hp changed damage:", ".".join(path), val, "actual", actual, "expected", base_damage)

counts = {}
for f in failures:
    counts[f["category"]] = counts.get(f["category"], 0) + 1

bank = outdir / "failure_factory_bank.json"
summary = outdir / "failure_factory_summary.json"

bank.write_text(json.dumps(failures, indent=2), encoding="utf-8")
summary.write_text(json.dumps({
    "created_at": time.time(),
    "tested": tested,
    "failures": len(failures),
    "failure_counts": counts,
    "base_damage": base_damage,
    "goal": "more failures, smarter active-state-only DPS optimizer",
}, indent=2), encoding="utf-8")

print()
print("TESTED:", tested)
print("TOTAL FAILURES:", len(failures))
print("FAILURE COUNTS:")
for k,v in sorted(counts.items(), key=lambda x: x[1], reverse=True):
    print(f"{k}: {v}")
print("Saved bank:", bank)
print("Saved summary:", summary)

# Create generated memory test from failures so the optimizer can learn from mistakes forever.
test_file = root / "tests" / "test_generated_failure_factory_memory.py"
test_file.write_text('''
import json
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
BANK = ROOT / "training_outputs" / "failure_factory_bank.json"

spec = importlib.util.spec_from_file_location("hardtest", ROOT / "tests" / "test_optimizer_red_team_realistic_hard.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def _damage(p):
    return mod._damage(mod._run(p))

def test_generated_failure_factory_memory():
    if not BANK.exists():
        return
    cases = json.loads(BANK.read_text(encoding="utf-8"))
    for c in cases[:100]:
        cat = c.get("category")
        if cat in ("inactive_or_locked_damage_counted", "hp_defense_changed_damage"):
            assert _damage(c["profile"]) == c["expected_damage"], c
        elif cat == "format_alias_mismatch":
            assert _damage(c["numeric_profile"]) == _damage(c["formatted_profile"]), c
''', encoding="utf-8")

print("Generated regression test:", test_file)

if failures:
    raise SystemExit(1)
