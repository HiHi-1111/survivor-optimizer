
import json
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
BANK = ROOT / "training_outputs" / "meaner_global_plan_v2_bank.json"

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

def test_meaner_global_plan_v2_memory():
    if not BANK.exists():
        return
    for c in json.loads(BANK.read_text(encoding="utf-8"))[-200:]:
        result = mod._run(c["profile"])
        text = _plan(result)
        assert "survivor_decision_guardrails_v1" in text, c
        assert len([x for x in ["locked","inactive","not active","not count","ignore"] if x in text]) >= 2, c
        assert len([x for x in ["dps","damage","hp","not priority"] if x in text]) >= 2, c
        assert len([x for x in ["relic","awakening","resonance","xeno","blocker","save"] if x in text]) >= 2, c
        assert len([x for x in ["crit","100","130","150","breakpoint"] if x in text]) >= 2, c
