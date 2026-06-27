import copy, json, importlib.util, sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

test_path = root / "tests" / "test_optimizer_red_team_realistic_hard.py"
spec = importlib.util.spec_from_file_location("hardtest", test_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

FIELDS = [
    (("gear", "weapon", "damage_multiplier"), 2.35, "2.35x"),
    (("gear", "belt", "damage_multiplier"), 1.75, "175%"),
    (("necklace_bonus", "damage_multiplier"), 1.45, "1.45x"),
    (("survivor", "damage_multiplier"), 2.10, "2.1x"),
    (("tech", "drone", "damage_multiplier"), 2.25, "225%"),
    (("tech", "lightning", "damage_multiplier"), 1.55, "1.55x"),
    (("tech", "twinborn", "damage_multiplier"), 1.40, "140%"),
    (("pet", "main", "damage_multiplier"), 1.85, "1.85x"),
    (("collectibles", "damage_multiplier"), 2.20, "220%"),
]

def set_nested(d, path, val):
    x = d
    for p in path[:-1]:
        if p not in x or not isinstance(x[p], dict):
            x[p] = {}
        x = x[p]
    x[path[-1]] = val

def dmg(profile):
    return mod._damage(mod._run(profile))

def make_profile(mode, skip=None):
    p = copy.deepcopy(mod._base_profile())
    for path, num, fmt in FIELDS:
        name = ".".join(path)
        if name == skip:
            continue
        set_nested(p, path, fmt if mode == "formatted" else num)
    return p

base = copy.deepcopy(mod._base_profile())
base_damage = dmg(base)

all_numeric = make_profile("numeric")
all_formatted = make_profile("formatted")

numeric_damage = dmg(all_numeric)
formatted_damage = dmg(all_formatted)

print("COMBO FORMAT FAILURE MINER")
print("base_damage:", base_damage)
print("all_numeric_damage:", numeric_damage)
print("all_formatted_damage:", formatted_damage)
print("delta formatted-numeric:", round(formatted_damage - numeric_damage, 4))
print()

failures = []

if formatted_damage != numeric_damage:
    failures.append({
        "case": "all_fields_combo",
        "expected_numeric_damage": numeric_damage,
        "actual_formatted_damage": formatted_damage,
        "delta": round(formatted_damage - numeric_damage, 4),
    })
    print("FAIL: all_fields_combo", formatted_damage, "expected", numeric_damage)

print()
print("ABLATION: remove one field from both numeric/formatted")
for path, num, fmt in FIELDS:
    name = ".".join(path)
    nprof = make_profile("numeric", skip=name)
    fprof = make_profile("formatted", skip=name)
    nd = dmg(nprof)
    fd = dmg(fprof)
    delta = round(fd - nd, 4)
    print(name, "numeric", nd, "formatted", fd, "delta", delta)
    if fd != nd:
        failures.append({
            "case": "ablation_remove_" + name,
            "numeric_damage": nd,
            "formatted_damage": fd,
            "delta": delta,
        })

out = root / "training_outputs" / "combo_format_failures.json"
out.write_text(json.dumps(failures, indent=2), encoding="utf-8")

print()
print("TOTAL COMBO FAILURES:", len(failures))
print("Saved:", out)

if failures:
    raise SystemExit(1)
