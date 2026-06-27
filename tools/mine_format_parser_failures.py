import copy, json, importlib.util, sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

test_path = root / "tests" / "test_optimizer_red_team_realistic_hard.py"

spec = importlib.util.spec_from_file_location("hardtest", test_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

base = mod._base_profile()

FIELDS = [
    (("gear", "weapon", "damage_multiplier"), 2.35),
    (("gear", "belt", "damage_multiplier"), 1.75),
    (("necklace_bonus", "damage_multiplier"), 1.45),
    (("survivor", "damage_multiplier"), 2.10),
    (("tech", "drone", "damage_multiplier"), 2.25),
    (("tech", "lightning", "damage_multiplier"), 1.55),
    (("tech", "twinborn", "damage_multiplier"), 1.40),
    (("pet", "main", "damage_multiplier"), 1.85),
    (("collectibles", "damage_multiplier"), 2.20),
]

def set_nested(d, path, val):
    x = d
    for p in path[:-1]:
        if p not in x or not isinstance(x[p], dict):
            x[p] = {}
        x = x[p]
    x[path[-1]] = val

def variants(v):
    f = float(v)
    return [
        f,
        f"{f}x",
        f"{f:.2f}x",
        f"{f*100:g}%",
        f"{f*100:.0f}%",
        f" {f}x ",
        f" {f*100:g}% ",
    ]

def dmg(profile):
    return mod._damage(mod._run(profile))

failures = []
tested = 0

print("FORMAT PARSER FAILURE MINER V2")
print()

for path, numeric_value in FIELDS:
    path_name = ".".join(path)

    numeric_profile = copy.deepcopy(base)
    set_nested(numeric_profile, path, numeric_value)

    try:
        expected = dmg(numeric_profile)
    except Exception as e:
        print("BASE CRASH:", path_name, numeric_value, repr(e))
        failures.append({
            "field": path_name,
            "numeric": numeric_value,
            "error": "numeric_crash",
            "detail": repr(e),
        })
        continue

    for formatted_value in variants(numeric_value):
        tested += 1
        formatted_profile = copy.deepcopy(base)
        set_nested(formatted_profile, path, formatted_value)

        try:
            actual = dmg(formatted_profile)
        except Exception as e:
            print("CRASH:", path_name, numeric_value, "=>", formatted_value, repr(e))
            failures.append({
                "field": path_name,
                "numeric": numeric_value,
                "formatted": formatted_value,
                "error": repr(e),
            })
            continue

        if actual != expected:
            print("FAIL:", path_name, numeric_value, "=>", formatted_value, "actual", actual, "expected", expected)
            failures.append({
                "field": path_name,
                "numeric": numeric_value,
                "formatted": formatted_value,
                "expected_damage": expected,
                "actual_damage": actual,
                "delta": round(actual - expected, 4),
            })

out = root / "training_outputs" / "format_parser_failures.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(failures, indent=2), encoding="utf-8")

print()
print("TESTED:", tested)
print("TOTAL FORMAT FAILURES:", len(failures))
print("Saved:", out)

if failures:
    raise SystemExit(1)
