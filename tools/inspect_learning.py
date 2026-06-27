"""Inspect compact learning state without loading the large assumption chart."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from optimizer.numeric_features import FEATURE_COLUMNS
from optimizer.paths import TRAINING_OUTPUTS_DIR, TRAINING_STATE_DIR
from optimizer.profile_priors import MIN_HARD_PRUNE_AUDITS


MEMORY_PATH = TRAINING_STATE_DIR / "learning_memory.json"
RANKER_PATH = TRAINING_STATE_DIR / "checkpoints" / "learned_ranker.json"
CHART_PATH = TRAINING_STATE_DIR / "profile_assumption_chart.json"
METRICS_PATH = TRAINING_OUTPUTS_DIR / "latest_metrics.json"


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def inspect_learning() -> dict[str, Any]:
    memory = _read(MEMORY_PATH)
    ranker = _read(RANKER_PATH)
    metrics = _read(METRICS_PATH)
    audit = memory.get("audit", {}) or {}
    weights = [float(value) for value in ranker.get("weights", []) if isinstance(value, (int, float))]
    historical_false_prunes = int(audit.get("false_prunes", 0) or 0)
    historical_audits = int(audit.get("full_search_audits", 0) or 0)
    profiles = int(memory.get("profiles_learned_from", 0) or 0)
    ranker_samples = int(ranker.get("samples", 0) or 0)
    ranker_updates = int(ranker.get("updates", 0) or 0)
    nonzero = sum(abs(value) > 1e-12 for value in weights)
    saturated = sum(abs(value) >= 0.999999 for value in weights)
    blockers: list[str] = []
    warnings: list[str] = []
    if not profiles:
        blockers.append("assumption chart has no learned profiles")
    if historical_false_prunes:
        blockers.append(
            f"hard pruning safety latch is active because persisted audit history contains {historical_false_prunes} false prune(s); safe reordering remains allowed"
        )
    elif historical_audits < MIN_HARD_PRUNE_AUDITS:
        blockers.append(
            f"hard pruning requires {MIN_HARD_PRUNE_AUDITS} safe audits; {historical_audits} are available; safe reordering remains allowed"
        )
    if not ranker_samples or not ranker_updates:
        blockers.append("online ranker has no successful pairwise observations")
    if weights and not nonzero:
        blockers.append("online ranker weights are all zero")
    if saturated:
        warnings.append(
            f"{saturated} ranker weight(s) are saturated; global planner caps the learned chain bonus so it remains a reordering signal"
        )
    latest_usage = float(metrics.get("learned_pruning_usage_percent", 0.0) or 0.0)
    if latest_usage == 0.0 and profiles > 0 and historical_false_prunes > 0:
        latest_zero_diagnosis = (
            "The recorded run loaded learning, but its lifetime false-prune latch returned before bucket matching. "
            "Current code keeps hard pruning blocked and consumes the same evidence for safe reordering."
        )
    elif latest_usage == 0.0 and profiles > 0:
        latest_zero_diagnosis = "The recorded run has learned samples but no counted prior decisions; inspect latest_usage.diagnostics after the next short run."
    else:
        latest_zero_diagnosis = "Learning usage was recorded."
    return {
        "chart": {
            "path": str(CHART_PATH), "exists": CHART_PATH.exists(),
            "bytes": CHART_PATH.stat().st_size if CHART_PATH.exists() else 0,
            "profiles_learned_from": profiles,
            "profile_bucket_count": int(memory.get("profile_bucket_count", 0) or 0),
            "archetype_bucket_count": int(memory.get("archetype_bucket_count", 0) or 0),
            "loaded_in_latest_run": bool(metrics.get("learned_memory_loaded_from_disk", False)),
        },
        "audit": {
            "full_search_audits": historical_audits,
            "false_prunes": historical_false_prunes,
            "false_prune_rate": float(audit.get("false_prune_rate", 0.0) or 0.0),
            "hard_pruning_allowed": historical_false_prunes == 0 and historical_audits >= MIN_HARD_PRUNE_AUDITS,
            "safe_reordering_allowed": profiles > 0,
        },
        "ranker": {
            "path": str(RANKER_PATH), "exists": RANKER_PATH.exists(),
            "samples": ranker_samples, "updates": ranker_updates,
            "feature_count": len(FEATURE_COLUMNS), "stored_weight_count": len(weights),
            "nonzero_weights": nonzero, "saturated_weights": saturated,
            "loaded_in_latest_run": bool(ranker.get("loaded_from_checkpoint", False)),
        },
        "latest_usage": {
            "learned_pruning_usage_percent": latest_usage,
            "learned_reordered_profiles": int(metrics.get("learned_reordered_profiles", 0) or 0),
            "learned_pruned_profiles": int(metrics.get("learned_pruned_profiles", 0) or 0),
            "diagnostics": metrics.get("learned_usage_diagnostics", {}),
            "diagnosis": latest_zero_diagnosis,
        },
        "blockers": blockers,
        "warnings": warnings,
        "healthy_for_safe_reordering": bool(profiles > 0 and ranker_updates > 0 and nonzero > 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect persisted optimizer learning state.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON instead of the readable report.")
    args = parser.parse_args()
    report = inspect_learning()
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["healthy_for_safe_reordering"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
