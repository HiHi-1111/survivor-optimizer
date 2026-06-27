
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
