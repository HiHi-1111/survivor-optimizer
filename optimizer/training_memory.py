"""Atomic optimizer checkpoints and compact explainable learning memory."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def learning_memory_snapshot(chart: dict[str, Any]) -> dict[str, Any]:
    archetypes = chart.get("archetype_buckets", {}) or {}
    buckets = chart.get("buckets", {}) or {}
    top_archetypes = sorted(archetypes.values(), key=lambda value: int(value.get("samples", 0)), reverse=True)[:100]
    top_buckets = sorted(buckets.values(), key=lambda value: int(value.get("samples", 0)), reverse=True)[:100]
    return {
        "version": 1,
        "updated_at": utc_now(),
        "profiles_learned_from": int(chart.get("total_samples", 0)),
        "profile_bucket_count": len(buckets),
        "archetype_bucket_count": len(archetypes),
        "audit": chart.get("audit", {}),
        "legacy_memory": chart.get("legacy_memory", {}),
        "scenario_stats": chart.get("scenario_stats", {}),
        "resource_bottlenecks": chart.get("resource_bottlenecks", {}),
        "top_action_priors": sorted(
            (chart.get("action_priors", {}) or {}).items(), key=lambda item: int(item[1].get("samples", 0)), reverse=True
        )[:100],
        "best_chain_priors": sorted(
            (chart.get("best_chain_priors", {}) or {}).items(), key=lambda item: int(item[1].get("samples", 0)), reverse=True
        )[:100],
        "top_archetypes": [
            {
                "tag": entry.get("tag"), "samples": entry.get("samples", 0),
                "confidence": entry.get("confidence", "low"), "top_systems": entry.get("top_systems", []),
                "average_score": entry.get("average_score", 0), "breakpoint_rate": entry.get("breakpoint_rate", 0),
            }
            for entry in top_archetypes
        ],
        "high_value_chain_examples": [
            {
                "bucket": entry.get("bucket"), "samples": entry.get("samples", 0),
                "average_best_score": entry.get("avg_best_score", 0), "top_systems": entry.get("top_systems", []),
            }
            for entry in top_buckets[:25]
        ],
    }


def optimizer_checkpoint(
    *, processed: int, submitted: int, elapsed_seconds: float, chart: dict[str, Any],
    systems_covered: set[str], results_path: Path, profiles_path: Path,
    device: str, workers: int, interrupted: bool, completed: bool,
) -> dict[str, Any]:
    return {
        "version": 1, "updated_at": utc_now(), "processed_this_run": processed,
        "profiles_submitted": submitted, "elapsed_seconds": round(elapsed_seconds, 3),
        "profiles_learned_from": int(chart.get("total_samples", 0)),
        "full_search_audits": int((chart.get("audit", {}) or {}).get("full_search_audits", 0)),
        "false_prune_rate": float((chart.get("audit", {}) or {}).get("false_prune_rate", 0.0)),
        "systems_covered": sorted(systems_covered), "results_path": str(results_path),
        "profiles_path": str(profiles_path), "device": device, "workers": workers,
        "interrupted": interrupted, "completed": completed,
    }
