"""Write the stable final trainer summary from an existing metrics artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from optimizer.coverage import coverage_audit_state, coverage_report
from optimizer.knowledge_loader import load_knowledge
from optimizer.paths import TRAINING_OUTPUTS_DIR
from optimizer.training_memory import atomic_write_json
from tools.train_optimizer import stable_metrics_summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the stable final metrics summary.")
    parser.add_argument("--metrics", type=Path, default=TRAINING_OUTPUTS_DIR / "latest_metrics.json")
    parser.add_argument("--output", type=Path, default=TRAINING_OUTPUTS_DIR / "latest_final_summary.json")
    args = parser.parse_args()
    metrics_path = args.metrics if args.metrics.is_absolute() else ROOT / args.metrics
    output_path = args.output if args.output.is_absolute() else ROOT / args.output
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    if "observable_real_data_systems" not in metrics:
        knowledge = load_knowledge()
        coverage = coverage_report(knowledge, coverage_audit_state(knowledge))
        metrics["observable_real_data_systems"] = coverage["observable_real_data_systems"]
    summary = stable_metrics_summary(metrics)
    summary["summary_generated_from"] = str(metrics_path)
    atomic_write_json(output_path, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
