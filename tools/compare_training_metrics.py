"""Create an honest same-command before/after training comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _metric(payload: dict[str, Any], name: str, nested: str | None = None) -> float:
    source = payload.get(nested, {}) if nested else payload
    return float((source or {}).get(name, 0.0) or 0.0)


def compare(baseline: dict[str, Any], final: dict[str, Any], command: str) -> dict[str, Any]:
    baseline_pps = _metric(baseline, "full_mode_profiles_per_second")
    final_pps = _metric(final, "full_mode_profiles_per_second")
    return {
        "command": command,
        "same_command": True,
        "baseline": {
            "full_mode_profiles_per_second": baseline_pps,
            "gpu_idle_percentage": _metric(baseline, "gpu_idle_percentage", "gpu_scoring"),
            "gpu_scored_chain_coverage_percent": _metric(baseline, "gpu_scored_chain_coverage_percent"),
            "gpu_batch_utilization": _metric(baseline, "gpu_batch_utilization", "gpu_scoring"),
            "gpu_wall_rows_per_sec": _metric(baseline, "gpu_wall_rows_per_sec", "gpu_scoring"),
        },
        "final": {
            "full_mode_profiles_per_second": final_pps,
            "gpu_idle_percentage": _metric(final, "gpu_idle_percentage", "gpu_scoring"),
            "gpu_scored_chain_coverage_percent": _metric(final, "gpu_scored_chain_coverage_percent"),
            "gpu_batch_utilization": _metric(final, "gpu_batch_utilization", "gpu_scoring"),
            "gpu_wall_rows_per_sec": _metric(final, "gpu_wall_rows_per_sec", "gpu_scoring"),
        },
        "profiles_per_second_improvement_percent": round((final_pps / baseline_pps - 1.0) * 100.0, 3) if baseline_pps else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--final", type=Path, required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "training" / "latest_benchmark_comparison.json")
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    result = compare(_read(args.baseline), _read(args.final), args.command)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
