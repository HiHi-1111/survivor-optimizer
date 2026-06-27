"""Benchmark optimizer simulation throughput."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from optimizer.scoring_weights import load_scoring_weights
from tools.device_utils import resolve_device
from tools.gpu_scoring import score_rows
from tools.train_optimizer import PROFILES_PATH, read_jsonl, selected_workers, simulate_profile


def benchmark(
    profiles_path: Path = PROFILES_PATH,
    count: int = 100,
    workers: str = "auto",
    device: str = "cpu",
    gpu_score: bool = False,
    batch_size: int = 4096,
) -> dict:
    profiles = read_jsonl(profiles_path)[:count]
    if not profiles:
        raise FileNotFoundError(f"No profiles found at {profiles_path}")
    worker_count = selected_workers(workers)
    selected_device, device_warnings = resolve_device(device)
    started = time.perf_counter()
    results = []
    with ProcessPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(simulate_profile, profile) for profile in profiles]
        for future in as_completed(futures):
            results.append(future.result())
    elapsed = max(time.perf_counter() - started, 0.000001)
    actions = sum(int(row.get("actions_tested", 0)) for row in results)
    candidate_rows = [candidate for result in results for candidate in result.get("candidate_features", [])]
    weights = load_scoring_weights(ROOT / "knowledge")

    cpu_started = time.perf_counter()
    _, cpu_scoring_summary = score_rows(candidate_rows, weights, "cpu", False, batch_size)
    cpu_scoring_elapsed = max(time.perf_counter() - cpu_started, 0.000001)

    _, scoring_summary = score_rows(candidate_rows, weights, selected_device, gpu_score, batch_size)
    scoring_elapsed = float(scoring_summary.get("scoring_elapsed_seconds") or cpu_scoring_elapsed)
    speedup = round(cpu_scoring_elapsed / scoring_elapsed, 3) if scoring_summary.get("gpu_used") else None
    summary = {
        "profiles_per_second": round(len(results) / elapsed, 3),
        "actions_simulated_per_second": round(actions / elapsed, 3),
        "scoring_batches_per_second": scoring_summary.get("scoring_batches_per_second", 0),
        "average_batch_size": scoring_summary.get("average_batch_size", 0),
        "gpu_used": scoring_summary.get("gpu_used", False),
        "gpu_acceleration_reason": scoring_summary.get("gpu_acceleration_reason", ""),
        "speedup_vs_cpu_scoring": speedup,
        "average_recommendation_time_ms": round(sum(float(row.get("elapsed_ms", 0)) for row in results) / len(results), 3),
        "total_profiles_processed": len(results),
        "total_actions_tested": actions,
        "total_numeric_candidates_scored": len(candidate_rows),
        "multiprocessing_working": worker_count > 1 and len(results) > 1,
        "detected_cpu_count": os.cpu_count() or 1,
        "selected_workers": worker_count,
        "selected_device": selected_device,
        "device_warnings": device_warnings,
    }
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark optimizer recommendation throughput.")
    parser.add_argument("--profiles", type=Path, default=PROFILES_PATH)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--workers", default="auto")
    parser.add_argument("--device", choices=["cpu", "cuda", "gpu", "auto"], default="cpu")
    parser.add_argument("--gpu-score", action="store_true")
    parser.add_argument("--batch-size", type=int, default=4096)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    profiles = args.profiles if args.profiles.is_absolute() else ROOT / args.profiles
    try:
        summary = benchmark(profiles, args.count, args.workers, args.device, args.gpu_score, args.batch_size)
    except Exception as exc:
        print(f"benchmark failed: {exc}")
        return 1
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
