from __future__ import annotations

import json
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
paths = [
    ROOT / "training_outputs" / "adversarial_hard_examples.jsonl",
    ROOT / "training_outputs" / "feature_spam_battle_rounds.jsonl",
    ROOT / "training_outputs" / "evolution_duel_rounds.jsonl",
]

target = "cheap material bait vs rare blockers"

print("AUDIT: cheap material bait vs rare blockers")
print("=" * 80)

for path in paths:
    print(f"\nFILE: {path}")
    if not path.exists():
        print("missing")
        continue

    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    obj = json.loads(line)
                    text = json.dumps(obj, sort_keys=True).lower()
                    if target in text:
                        rows.append(obj)
                except Exception:
                    pass

    print(f"matching rows: {len(rows)}")

    cats = Counter()
    for row in rows:
        cat = str(row.get("category") or row.get("top_fail") or "unknown")
        cats[cat] += 1
    print("categories:", dict(cats.most_common(10)))

    for i, row in enumerate(rows[-5:], start=1):
        print(f"\n--- sample {i} ---")
        small = {
            "round": row.get("round"),
            "case_id": row.get("case_id"),
            "category": row.get("category"),
            "passed": row.get("passed"),
            "severity": row.get("severity"),
            "suggested_optimizer_rule": row.get("suggested_optimizer_rule"),
            "missing_proof": row.get("missing_proof"),
            "top_rules_to_fix": row.get("top_rules_to_fix"),
            "failure_categories": row.get("failure_categories"),
        }
        print(json.dumps(small, indent=2, sort_keys=True, default=str))

print("\nDONE")
