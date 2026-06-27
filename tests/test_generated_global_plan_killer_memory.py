
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
