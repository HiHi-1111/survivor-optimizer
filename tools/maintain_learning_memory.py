"""Safely compact generated optimizer learning artifacts.

Only generated state under training_outputs is eligible by default. Source
knowledge and imported source material are explicitly rejected.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from optimizer.paths import REPORTS_DIR, TRAINING_STATE_DIR
from optimizer.profile_priors import save_chart
from optimizer.training_memory import atomic_write_json, learning_memory_snapshot


DEFAULT_CHART = TRAINING_STATE_DIR / "profile_assumption_chart.json"
DEFAULT_MEMORY = TRAINING_STATE_DIR / "learning_memory.json"
DEFAULT_RANKER = TRAINING_STATE_DIR / "checkpoints" / "learned_ranker.json"
DEFAULT_CACHE_DIR = TRAINING_STATE_DIR / "cache"
DEFAULT_BACKUP_ROOT = TRAINING_STATE_DIR / "backups"
DEFAULT_REPORT = REPORTS_DIR / "training" / "learning_memory_maintenance.json"
SOURCE_TRUTH_ROOTS = (ROOT / "knowledge", ROOT / "data_sources")


def _utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def assert_generated_path(path: Path) -> None:
    if any(_is_within(path, root) for root in SOURCE_TRUTH_ROOTS):
        raise ValueError(f"refusing to edit source truth: {path}")


def _file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() and path.is_file() else 0


def _dedupe_examples(rows: list[Any]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        unique[key] = row
    return list(unique.values())


def _general_bucket(bucket: str) -> str:
    return re.sub(r"current_best_action_system:[^|]+", "current_best_action_system:*", bucket)


def compact_learning_chart(chart: dict[str, Any], *, max_buckets: int = 5000) -> dict[str, Any]:
    buckets = chart.get("buckets", {}) or {}
    audit = chart.setdefault("audit", {})
    failure_examples = _dedupe_examples(list(audit.get("false_prune_examples", []) or []))
    audit["false_prune_examples"] = failure_examples
    failed_buckets = {
        str(row.get("bucket", "")) for row in failure_examples if row.get("bucket")
    } | {str(value) for value in audit.get("downgraded_buckets", []) or []}

    high_value = sorted(
        buckets,
        key=lambda key: (
            float(buckets[key].get("avg_best_score", buckets[key].get("average_score", 0.0)) or 0.0),
            int(buckets[key].get("samples", 0) or 0),
        ),
        reverse=True,
    )[:100]
    by_evidence = sorted(
        buckets,
        key=lambda key: int(buckets[key].get("samples", 0) or 0),
        reverse=True,
    )[:max(1, int(max_buckets))]
    keep = set(by_evidence) | set(high_value) | failed_buckets

    summaries: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"retired_bucket_count": 0, "samples": 0, "winner_counts": Counter()}
    )
    retired = 0
    for key in list(buckets):
        entry = buckets[key]
        if key in keep:
            if int(entry.get("false_prunes", 0) or 0) > 0 or key in failed_buckets:
                entry["confidence"] = "low"
                entry["prune_hint"] = "retired_bad_prior"
                entry["needs_more_deep_exploration"] = True
                entry["retired_bad_prior"] = True
            continue
        summary = summaries[_general_bucket(str(key))]
        summary["retired_bucket_count"] += 1
        summary["samples"] += int(entry.get("samples", 0) or 0)
        summary["winner_counts"].update(entry.get("winner_counts", {}) or {})
        del buckets[key]
        retired += 1

    retired_summaries = []
    for bucket, summary in summaries.items():
        retired_summaries.append({
            "bucket": bucket,
            "retired_bucket_count": int(summary["retired_bucket_count"]),
            "samples": int(summary["samples"]),
            "top_systems": [name for name, _count in summary["winner_counts"].most_common(5)],
        })
    retired_summaries.sort(key=lambda row: row["samples"], reverse=True)
    chart["retired_bucket_summaries"] = retired_summaries[:2000]
    chart["memory_maintenance"] = {
        "updated_at": _utc_stamp(),
        "retired_low_confidence_buckets": retired,
        "retained_buckets": len(buckets),
        "retained_failure_examples": len(failure_examples),
        "retained_high_value_examples": len(set(high_value)),
        "retired_bad_priors": sum(1 for entry in buckets.values() if entry.get("retired_bad_prior")),
    }
    return chart["memory_maintenance"]


def _scan_or_compact_jsonl(path: Path, *, keep_entries: int, apply: bool) -> dict[str, Any]:
    before = _file_size(path)
    kept: deque[str] = deque(maxlen=max(1, int(keep_entries)))
    rows = malformed = 0
    if path.exists():
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                rows += 1
                try:
                    json.loads(line)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                kept.append(line if line.endswith("\n") else line + "\n")
    estimated_after = sum(len(line.encode("utf-8")) for line in kept)
    if apply and path.exists():
        temporary = path.with_suffix(path.suffix + f".{os.getpid()}.compact.tmp")
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            handle.writelines(kept)
        temporary.replace(path)
    return {
        "path": str(path), "rows_before": rows, "rows_after": len(kept),
        "malformed_rows_removed": malformed, "bytes_before": before,
        "bytes_after": _file_size(path) if apply else estimated_after,
    }


def _backup_file(source: Path, backup_root: Path, relative_name: Path) -> str:
    destination = backup_root / relative_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
        method = "hardlink"
    except OSError:
        shutil.copy2(source, destination)
        method = "copy"
    return method


def maintain_learning_memory(
    *, apply: bool, chart_path: Path = DEFAULT_CHART, memory_path: Path = DEFAULT_MEMORY,
    ranker_path: Path = DEFAULT_RANKER, cache_dir: Path = DEFAULT_CACHE_DIR,
    backup_root: Path = DEFAULT_BACKUP_ROOT, max_buckets: int = 5000,
    cache_entries: int = 50000, report_path: Path = DEFAULT_REPORT,
) -> dict[str, Any]:
    generated_files = [chart_path, memory_path, ranker_path]
    cache_files = sorted(cache_dir.glob("*.jsonl")) if cache_dir.exists() else []
    for path in [*generated_files, *cache_files, backup_root, report_path]:
        assert_generated_path(path)

    existing = [path for path in generated_files if path.exists()]
    bytes_before = sum(_file_size(path) for path in [*existing, *cache_files])
    backup_path: Path | None = None
    backup_methods: dict[str, str] = {}
    if apply:
        backup_path = backup_root / f"learning_{_utc_stamp()}_{os.getpid()}"
        for path in [*existing, *cache_files]:
            relative = Path("cache") / path.name if path in cache_files else Path(path.name)
            backup_methods[str(relative)] = _backup_file(path, backup_path, relative)
        atomic_write_json(backup_path / "backup_manifest.json", {
            "created_at": _utc_stamp(), "files": backup_methods,
            "source_truth_included": False,
        })

    chart: dict[str, Any] = {}
    chart_stats: dict[str, Any] = {
        "retired_low_confidence_buckets": 0, "retained_buckets": 0,
        "retained_failure_examples": 0, "retired_bad_priors": 0,
    }
    if chart_path.exists():
        chart = json.loads(chart_path.read_text(encoding="utf-8"))
        chart_stats = compact_learning_chart(chart, max_buckets=max_buckets)
        if apply:
            save_chart(chart, chart_path)
            atomic_write_json(memory_path, learning_memory_snapshot(chart))

    cache_reports = [
        _scan_or_compact_jsonl(path, keep_entries=cache_entries, apply=apply)
        for path in cache_files
    ]
    bytes_after = (
        sum(_file_size(path) for path in [*generated_files, *cache_files] if path.exists())
        if apply else
        sum(report["bytes_after"] for report in cache_reports)
        + (len(json.dumps(chart, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) if chart else 0)
        + _file_size(ranker_path)
    )
    report = {
        "mode": "apply" if apply else "dry-run",
        "applied": apply,
        "source_truth_paths_touched": [],
        "backup_path": str(backup_path) if backup_path else None,
        "bytes_before": bytes_before,
        "bytes_after": bytes_after,
        "memory_size_before": bytes_before,
        "memory_size_after": bytes_after,
        "compaction_ratio": round(bytes_after / max(1, bytes_before), 6),
        "bytes_saved": max(0, bytes_before - bytes_after),
        "chart": chart_stats,
        "caches": cache_reports,
    }
    if apply:
        atomic_write_json(report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Compact generated optimizer learning memory safely.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Report changes without writing files.")
    mode.add_argument("--apply", action="store_true", help="Back up and atomically apply compaction.")
    parser.add_argument("--max-buckets", type=int, default=5000)
    parser.add_argument("--cache-entries", type=int, default=50000)
    args = parser.parse_args()
    report = maintain_learning_memory(
        apply=bool(args.apply), max_buckets=max(100, args.max_buckets),
        cache_entries=max(100, args.cache_entries),
    )
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
