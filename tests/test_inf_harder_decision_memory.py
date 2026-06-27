
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
