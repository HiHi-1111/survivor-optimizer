
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
