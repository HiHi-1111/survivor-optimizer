"""Build the learned profile assumption chart from prior training results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from optimizer.profile_priors import add_observation, finalize_chart, new_chart, save_chart, write_reports
from tools.train_optimizer import ASSUMPTION_CHART_PATH, PRIOR_REPORT_JSON, PRIOR_REPORT_MD, PROFILES_PATH, RESULTS_PATH, read_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build learned profile priors from previous optimizer training output.")
    parser.add_argument("--profiles", type=Path, default=PROFILES_PATH)
    parser.add_argument("--results", type=Path, default=RESULTS_PATH)
    parser.add_argument("--output", type=Path, default=ASSUMPTION_CHART_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    profiles_path = args.profiles if args.profiles.is_absolute() else ROOT / args.profiles
    results_path = args.results if args.results.is_absolute() else ROOT / args.results
    output_path = args.output if args.output.is_absolute() else ROOT / args.output
    results = read_jsonl(results_path)
    results_by_id: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        results_by_id.setdefault(str(result.get("profile_id")), []).append(result)
    chart = new_chart()
    matched = 0
    with profiles_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            profile = json.loads(line)
            profile_id = str(profile.get("id"))
            for result in results_by_id.get(profile_id, []):
                add_observation(chart, profile, result)
                matched += 1
    chart = finalize_chart(chart)
    save_chart(chart, output_path)
    write_reports(chart, PRIOR_REPORT_JSON, PRIOR_REPORT_MD)
    bucket_count = len(chart.get("buckets", {}) or {})
    print(f"wrote {output_path.relative_to(ROOT)}")
    print(f"samples: {chart.get('total_samples', 0)}")
    print(f"buckets: {bucket_count}")
    print(f"matched_results: {matched}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
