"""Adaptive optimizer simulation trainer.

This trains scoring behavior from synthetic player-state stress tests. It does
not invent game stats and does not train a neural network.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import multiprocessing as mp
import os
from pathlib import Path
import random
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from optimizer.action_generator import generate_core_selector_splits
from optimizer.action_registry import action_cache_stats, generate_inventory_actions, registry_systems
from optimizer.chain_simulator import simulate_action_chains
from optimizer.coverage import MAJOR_SYSTEMS, coverage_audit_state, coverage_report
from optimizer.global_planner import plan_global_inventory
from optimizer.knowledge_loader import load_knowledge
from optimizer.learned_ranker import OnlineLinearRanker
from optimizer.main import optimize
from optimizer.numeric_features import FEATURE_COLUMNS as NUMERIC_FEATURE_COLUMNS, action_features
from optimizer.paths import LOGS_DIR, REPORTS_DIR, TRAINING_OUTPUTS_DIR, TRAINING_RAW_DIR, TRAINING_STATE_DIR
from optimizer.player_state import validate_player_state
from optimizer.preprune_ranker import GpuRankerStartupError, SharedPrePruneGpuService, initialize_preprune_worker
from optimizer.profile_priors import add_observation, build_chart, load_chart, profile_tags, recommend_training_plan, record_audit, recover_chart_from_report, save_chart, write_reports
from optimizer.scoring_weights import load_scoring_weights
from optimizer.simulator import simulate_upgrade_chain
from optimizer.state_transition import state_transition_stats
from optimizer.state_value import state_value_cache_stats
from optimizer.training_cache import JsonlCache, stable_hash
from optimizer.training_memory import atomic_write_json, learning_memory_snapshot, optimizer_checkpoint
from tools.device_utils import detect_npu
from tools.generate_synthetic_profiles import generate_profiles, write_profiles
from tools.gpu_scoring import AsyncGpuScorer, candidate_rows_from_recommendation
from tools.profile_batch_generator import AsyncProfileProducer, ProfileBatchGenerator
from tools.training_startup import (
    RECOVERY_NOTE,
    TrainingRunLock,
    TrainingStartupError,
    cuda_preflight,
    gpu_process_diagnostics,
    write_startup_failure,
)

try:
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
except Exception:  # pragma: no cover - rich is optional at import time
    Live = None
    Panel = None
    Table = None


PROFILES_PATH = TRAINING_RAW_DIR / "synthetic_profiles.jsonl"
RESULTS_PATH = TRAINING_RAW_DIR / "simulation_results.jsonl"
WEIGHTS_PATH = ROOT / "knowledge" / "scoring_weights.json"
TRAINING_LOG = LOGS_DIR / "training" / "optimizer_training_log.jsonl"
ASSUMPTION_CHART_PATH = TRAINING_STATE_DIR / "profile_assumption_chart.json"
PRIOR_REPORT_JSON = REPORTS_DIR / "training" / "profile_prior_report.json"
PRIOR_REPORT_MD = REPORTS_DIR / "training" / "profile_prior_report.md"
STATE_VALUE_CACHE_PATH = TRAINING_STATE_DIR / "cache" / "state_value_cache.jsonl"
ACTION_RESULT_CACHE_PATH = TRAINING_STATE_DIR / "cache" / "action_result_cache.jsonl"
CHAIN_RESULT_CACHE_PATH = TRAINING_STATE_DIR / "cache" / "chain_result_cache.jsonl"
PROFILE_FEATURE_CACHE_PATH = TRAINING_STATE_DIR / "cache" / "profile_feature_cache.jsonl"
ACTION_GENERATION_CACHE_PATH = TRAINING_STATE_DIR / "cache" / "action_generation_cache.jsonl"
GPU_SCORE_CACHE_PATH = TRAINING_STATE_DIR / "cache" / "gpu_score_cache.jsonl"
OPTIMIZER_CHECKPOINT_PATH = TRAINING_STATE_DIR / "checkpoints" / "optimizer_latest.json"
LEARNING_MEMORY_PATH = TRAINING_STATE_DIR / "learning_memory.json"
LEARNED_RANKER_PATH = TRAINING_STATE_DIR / "checkpoints" / "learned_ranker.json"
LATEST_SUMMARY_PATH = TRAINING_OUTPUTS_DIR / "latest_summary.json"
LATEST_FINAL_SUMMARY_PATH = TRAINING_OUTPUTS_DIR / "latest_final_summary.json"
LATEST_METRICS_PATH = TRAINING_OUTPUTS_DIR / "latest_metrics.json"
LATEST_COVERAGE_PATH = TRAINING_OUTPUTS_DIR / "latest_coverage_report.json"
LATEST_LEARNING_PATH = TRAINING_OUTPUTS_DIR / "latest_learning_report.json"
LATEST_HARDWARE_PATH = TRAINING_OUTPUTS_DIR / "latest_hardware_report.json"
LATEST_DEBUG_PATH = LOGS_DIR / "training" / "latest_debug.log"
FALSE_PRUNE_LOG_PATH = LOGS_DIR / "training" / "false_prune_events.jsonl"


def selected_workers(workers: str, *, gpu_pipeline: bool = False) -> int:
    cpu_count = os.cpu_count() or 1
    if workers == "auto":
        # GPU-ranked workers spend part of each profile waiting for a shared
        # response, so modest oversubscription keeps all logical CPUs and the
        # GPU producer queue busy without creating one CUDA context per worker.
        return max(1, int(round(cpu_count * (1.5 if gpu_pipeline else 1.0))))
    return max(1, int(workers))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _rate(count: int | float, elapsed: float) -> float:
    return float(count) / max(elapsed, 0.000001)


def stable_metrics_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    """Return the stable, user-facing trainer result schema."""
    gpu = metrics.get("gpu_scoring", {}) or {}
    covered = metrics.get("systems_covered", []) or []
    if isinstance(covered, int):
        covered_count = covered
    else:
        covered_count = len(covered)
    systems_not_observed = metrics.get("systems_not_observed")
    if systems_not_observed is None:
        observable = set(metrics.get("observable_real_data_systems", metrics.get("real_data_systems", [])) or [])
        systems_not_observed = sorted(observable - set(covered if isinstance(covered, list) else []))
    preflight = metrics.get("cuda_preflight")
    return {
        "profiles_tested": int(metrics.get("profiles_tested", metrics.get("profiles_processed", 0)) or 0),
        "profiles_per_second": float(metrics.get("profiles_per_second", 0.0) or 0.0),
        "benchmark_valid": bool(metrics.get("benchmark_valid", False)),
        "startup_failed": bool(metrics.get("startup_failed", False)),
        "runtime_failed": bool(metrics.get("runtime_failed", False)),
        "failure_stage": metrics.get("failure_stage"),
        "failure_reason": metrics.get("failure_reason"),
        "partial_results_valid": bool(metrics.get("partial_results_valid", False)),
        "requested_device": str(metrics.get("requested_device", "unknown")),
        "selected_device": str(metrics.get("selected_device", metrics.get("device", "unknown"))),
        "cuda_preflight_passed": bool(preflight.get("passed")) if isinstance(preflight, dict) else metrics.get("cuda_preflight_passed"),
        "gpu_owner_pid": gpu.get("gpu_owner_pid"),
        "worker_count": int(metrics.get("worker_count", metrics.get("workers", 0)) or 0),
        "gpu_rows_submitted": int(gpu.get("gpu_rows_submitted", 0) or 0),
        "gpu_rows_scored": int(gpu.get("gpu_rows_scored", 0) or 0),
        "false_prune_rate": float(metrics.get("false_prune_rate", 0.0) or 0.0),
        "preprune_false_prune_rate": float(metrics.get("preprune_false_prune_rate", 0.0) or 0.0),
        "learned_pruning_usage_percent": float(metrics.get("learned_pruning_usage_percent", 0.0) or 0.0),
        "learned_reordered_profiles": int(metrics.get("learned_reordered_profiles", 0) or 0),
        "learned_pruned_profiles": int(metrics.get("learned_pruned_profiles", 0) or 0),
        "learned_ranker_samples": int(metrics.get("learned_ranker_samples", 0) or 0),
        "learned_ranker_updates": int(metrics.get("learned_ranker_updates", 0) or 0),
        "learned_usage_diagnostics": metrics.get("learned_usage_diagnostics", {}),
        "systems_covered_count": covered_count,
        "systems_not_observed": list(systems_not_observed or []),
        "gpu_idle_percentage": float(gpu.get("gpu_idle_percentage", metrics.get("gpu_idle_percentage", 0.0)) or 0.0),
        "gpu_idle_reason": str(gpu.get("gpu_idle_reason", metrics.get("gpu_idle_reason", "unknown"))),
        "gpu_batch_utilization": float(gpu.get("gpu_batch_utilization", metrics.get("gpu_batch_utilization", 0.0)) or 0.0),
        "avg_gpu_batch_size": float(gpu.get("average_gpu_batch_size", metrics.get("avg_gpu_batch_size", 0.0)) or 0.0),
        "gpu_queue_fill": float(gpu.get("gpu_queue_fill_rate", metrics.get("gpu_queue_fill", 0.0)) or 0.0),
        "gpu_wall_rows_per_sec": float(gpu.get("gpu_wall_rows_per_sec", metrics.get("gpu_wall_rows_per_sec", 0.0)) or 0.0),
        "gpu_waiting_on_cpu": bool(gpu.get("gpu_waiting_on_cpu", metrics.get("gpu_waiting_on_cpu", False))),
        "cpu_waiting_on_gpu": bool(gpu.get("cpu_waiting_on_gpu", metrics.get("cpu_waiting_on_gpu", False))),
        "cpu_candidate_seconds": float(metrics.get("cpu_candidate_seconds", 0.0) or 0.0),
        "global_planner_seconds": float(metrics.get("global_planner_seconds", 0.0) or 0.0),
        "state_copy_count": int(metrics.get("state_copy_count", 0) or 0),
        "state_rebuild_count": int(metrics.get("state_rebuild_count", 0) or 0),
        "learned_candidates_removed": int(metrics.get("learned_candidates_removed", 0) or 0),
        "learned_candidates_reordered": int(metrics.get("learned_candidates_reordered", 0) or 0),
        "learned_candidates_saved": int(metrics.get("learned_candidates_saved", 0) or 0),
        "learning_hit_rate": float(metrics.get("learning_hit_rate", 0.0) or 0.0),
        "main_bottleneck": str(metrics.get("main_bottleneck", metrics.get("hardware_bottleneck", "unknown"))),
    }


def learning_decision_usage(
    prior_decision: dict[str, Any], learned_systems: list[str] | None,
) -> tuple[bool, bool, str, str]:
    """Classify one prior decision for stable reordering/pruning counters."""
    pruning_applied = bool(prior_decision.get("pruning_applied", prior_decision.get("pruned_systems")))
    reordering_applied = bool(not pruning_applied and (
        prior_decision.get("reordering_applied", learned_systems is not None and not pruning_applied)
    ))
    kind = "pruned" if pruning_applied else ("reordered" if reordering_applied else "not_used")
    blocked_reason = str(
        prior_decision.get("hard_pruning_blocked_reason")
        or prior_decision.get("learning_blocked_reason")
        or ""
    )
    return pruning_applied, reordering_applied, kind, blocked_reason


def classify_gpu_ranker_failure(gpu_summary: dict[str, Any]) -> str:
    """Classify CUDA ranker failure from completed device work, not profiles."""
    successful = int(
        gpu_summary.get("successful_cuda_scoring_batches", gpu_summary.get("gpu_batches_completed", 0)) or 0
    )
    return "gpu_ranker_runtime_failed" if successful > 0 else "gpu_ranker_startup"


def gpu_work_ownership(*, gpu_score: bool, resolved_device: str, gpu_profile_features: bool) -> dict[str, Any]:
    """Choose one CUDA owner for a training run."""
    shared_ranker = bool(gpu_score and resolved_device == "cuda")
    profile_features = False
    return {
        "shared_ranker": shared_ranker,
        "gpu_profile_features": profile_features,
        "cuda_owner": "shared_preprune_ranker" if shared_ranker else "none",
    }


def _time_bar(elapsed: float, total: float, width: int = 32) -> str:
    if total <= 0:
        return "[" + ("#" * width) + "] 100.0%"
    percent = min(1.0, max(0.0, elapsed / total))
    filled = int(round(percent * width))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + f"] {percent * 100:5.1f}%"


def _hardware_snapshot() -> dict[str, Any]:
    try:
        import psutil
        process = psutil.Process()
        children = process.children(recursive=True)
        rss = process.memory_info().rss + sum(child.memory_info().rss for child in children if child.is_running())
        return {
            "cpu_utilization_percent": psutil.cpu_percent(interval=0.1),
            "memory_rss_mb": round(rss / (1024 * 1024), 3),
            "system_memory_percent": psutil.virtual_memory().percent,
        }
    except Exception as exc:
        if os.name == "nt":
            try:
                import ctypes
                from ctypes import wintypes

                class MemoryStatus(ctypes.Structure):
                    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD), ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

                class ProcessMemory(ctypes.Structure):
                    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

                status = MemoryStatus(); status.dwLength = ctypes.sizeof(status)
                counters = ProcessMemory(); counters.cb = ctypes.sizeof(counters)
                ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
                ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
                ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
                process_handle = ctypes.windll.kernel32.GetCurrentProcess()
                ctypes.windll.psapi.GetProcessMemoryInfo(process_handle, ctypes.byref(counters), counters.cb)

                def cpu_times() -> tuple[int, int]:
                    idle = wintypes.FILETIME(); kernel = wintypes.FILETIME(); user = wintypes.FILETIME()
                    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
                    convert = lambda value: (value.dwHighDateTime << 32) | value.dwLowDateTime
                    return convert(idle), convert(kernel) + convert(user)

                idle_before, total_before = cpu_times(); time.sleep(0.1); idle_after, total_after = cpu_times()
                total_delta = max(1, total_after - total_before)
                cpu_percent = max(0.0, min(100.0, 100.0 * (total_delta - (idle_after - idle_before)) / total_delta))
                return {"cpu_utilization_percent": round(cpu_percent, 3), "memory_rss_mb": round(counters.WorkingSetSize / (1024 * 1024), 3), "peak_memory_rss_mb": round(counters.PeakWorkingSetSize / (1024 * 1024), 3), "system_memory_percent": float(status.dwMemoryLoad), "reason": f"Windows API fallback used because psutil is unavailable: {exc}"}
            except Exception as fallback_exc:
                return {"cpu_utilization_percent": None, "memory_rss_mb": None, "system_memory_percent": None, "reason": f"{exc}; Windows fallback failed: {fallback_exc}"}
        return {"cpu_utilization_percent": None, "memory_rss_mb": None, "system_memory_percent": None, "reason": str(exc)}


def _render_live_dashboard(
    *,
    started: float,
    deadline: float,
    processed: int,
    actions: int,
    inventory_actions_generated: int,
    chain_actions_simulated: int,
    global_chains_considered: int,
    proposal_rows_created: int,
    chain_states_produced: int,
    chains_pruned: int,
    chains_scored: int,
    chains_skipped: int,
    standalone_chain_runs_skipped: int,
    chain_simulation_reason: str,
    dominated_states_removed: int,
    systems_covered: set[str],
    gpu_snapshot: dict[str, Any],
    worker_count: int,
    keep_generating: bool,
    combo_mode: bool,
    chain_profile_interval: int,
    global_profile_interval: int,
    learned_pruning_mode: str,
    learned_chart_samples: int,
    learned_pruned_profiles: int,
    full_search_audits: int,
    false_prunes: int,
    profile_queue_size: int = 0,
    actions_by_system: dict[str, int] | None = None,
    real_data_systems: list[str] | None = None,
    placeholder_only_systems: list[str] | None = None,
    npu_status: dict[str, Any] | None = None,
    runtime_failure: TrainingStartupError | None = None,
    requested_device: str = "unknown",
    selected_device: str = "unknown",
    cuda_preflight_passed: bool | None = None,
) -> Any:
    if Table is None or Panel is None:
        return None
    now = time.monotonic()
    elapsed = max(0.0, now - started)
    total = max(0.001, deadline - started)
    gpu_rows = int(gpu_snapshot.get("scored_rows", 0))
    gpu_submitted = int(gpu_snapshot.get("submitted_rows", 0))
    waiting_on_cpu = bool(gpu_snapshot.get("gpu_waiting_on_cpu", False))
    waiting_on_gpu = bool(gpu_snapshot.get("cpu_waiting_on_gpu", False))
    queued_rows = int(gpu_snapshot.get("queued_rows", 0))
    if waiting_on_cpu or (processed and queued_rows == 0):
        main_bottleneck = "CPU action generation"
        next_fix = "reduce candidate objects/state rebuilds and refill GPU batches sooner"
    elif waiting_on_gpu:
        main_bottleneck = "GPU scoring throughput"
        next_fix = "increase scoring throughput or reduce GPU-ranked rows"
    else:
        main_bottleneck = "GPU batch fill"
        next_fix = "batch numeric candidates across more profiles"

    table = Table(title="Optimizer training live", expand=True)
    table.add_column("Metric", no_wrap=True)
    table.add_column("Value")
    table.add_row("Time", f"{elapsed:,.1f}s / {total:,.1f}s")
    table.add_row("Progress", _time_bar(elapsed, total))
    table.add_row("Stop behavior", "finish active CPU batch, then stop when time is up")
    table.add_row("Requested device", requested_device)
    table.add_row("Selected device", selected_device)
    table.add_row("CUDA preflight passed", str(cuda_preflight_passed).lower() if cuda_preflight_passed is not None else "not requested")
    table.add_row("GPU owner PID", str(gpu_snapshot.get("gpu_owner_pid") or "not initialized"))
    table.add_row("CPU workers", str(worker_count))
    table.add_row("Time-only mode", "on" if keep_generating else "off")
    table.add_row("Combo chain planner", "on" if combo_mode else "off")
    table.add_row("Chain simulator interval", f"every {chain_profile_interval} profile(s)")
    table.add_row("Global planner interval", f"every {global_profile_interval} profile(s)")
    table.add_row("Learned pruning", learned_pruning_mode)
    table.add_row("Assumption chart samples", f"{learned_chart_samples:,}")
    if runtime_failure is not None:
        hardware_bottleneck = "GPU ranker runtime failure" if runtime_failure.stage == "gpu_ranker_runtime_failed" else "GPU ranker startup failure"
    elif processed == 0:
        table.add_row("Profile status", "No profiles completed yet")
        table.add_row("Benchmark status", "not valid until at least one profile completes")
        table.add_row("GPU pipeline active", str(bool(gpu_snapshot.get("gpu_pipeline_active", False))).lower())
        table.add_row("GPU actually used", "false")
        table.add_row("Successful CUDA scoring batches", str(int(gpu_snapshot.get("successful_cuda_scoring_batches", 0))))
        if gpu_snapshot.get("failure_reason"):
            table.add_row("GPU startup failure", str(gpu_snapshot["failure_reason"]))
        return Panel(table, title="Survivor optimizer trainer", border_style="yellow")
    table.add_row("Profiles using learned system pruning", f"{learned_pruned_profiles:,}")
    table.add_row("Full-search audits", f"{full_search_audits:,}")
    table.add_row("False prunes found", f"{false_prunes:,}")
    table.add_row("Profile queue size", f"{profile_queue_size:,}")
    table.add_row("Action/GPU queue size", f"{int(gpu_snapshot.get('queued_rows', 0)):,}")
    table.add_row("CPU profiles tested", f"{processed:,}")
    table.add_row("CPU profiles/sec", f"{_rate(processed, elapsed):,.2f}")
    table.add_row("Actions generated/sec", f"{_rate(inventory_actions_generated, elapsed):,.2f}")
    table.add_row("Chain states/sec", f"{_rate(chain_states_produced, elapsed):,.2f}")
    table.add_row("Main bottleneck", main_bottleneck)
    table.add_row("Next fix", next_fix)
    table.add_row("Core selector actions tested", f"{actions:,}")
    table.add_row("Inventory actions generated", f"{inventory_actions_generated:,}")
    table.add_row("Deep chain transitions simulated", f"{chain_actions_simulated:,}")
    table.add_row("Global chains considered", f"{global_chains_considered:,}")
    table.add_row("Chain states produced", f"{chain_states_produced:,}")
    table.add_row("Chains scored", f"{chains_scored:,}")
    table.add_row("Chains pruned", f"{chains_pruned:,}")
    table.add_row("Chains skipped", f"{chains_skipped:,}")
    table.add_row("Duplicate standalone chain runs skipped", f"{standalone_chain_runs_skipped:,}")
    table.add_row("Chain simulation source", chain_simulation_reason)
    table.add_row("Dominated states removed", f"{dominated_states_removed:,}")
    table.add_row("Systems covered", f"{len(systems_covered):,}")
    real_observed = set(real_data_systems or []) & systems_covered
    real_missing = set(real_data_systems or []) - systems_covered
    table.add_row("Real-data systems observed", f"{len(real_observed):,}/{len(real_data_systems or []):,}")
    table.add_row("Real-data systems not observed", ", ".join(sorted(real_missing)) or "none")
    if actions_by_system:
        top_generators = sorted(actions_by_system.items(), key=lambda item: item[1], reverse=True)[:8]
        table.add_row("Top action generators", ", ".join(f"{key}={value:,}" for key, value in top_generators))
    table.add_row("Real-data systems", f"{len(real_data_systems or []):,}")
    table.add_row("Placeholder-only systems", f"{len(placeholder_only_systems or []):,}")
    table.add_row("GPU pipeline active", str(bool(gpu_snapshot.get("async_pipeline", False))).lower())
    table.add_row("GPU actually used", str(bool(gpu_snapshot.get("gpu_used", False))).lower())
    table.add_row("GPU rows submitted", f"{gpu_submitted:,}")
    table.add_row("GPU rows scored", f"{gpu_rows:,}")
    gpu_phase_rows = gpu_snapshot.get("preprune_rows_by_phase", {}) or {}
    gpu_proposal_rows = int(gpu_phase_rows.get("proposal", gpu_snapshot.get("gpu_chain_rows_scored", 0)))
    gpu_final_state_rows = int(gpu_phase_rows.get("final_state", 0))
    table.add_row("GPU proposal scoring coverage", f"{min(100.0, gpu_proposal_rows / proposal_rows_created * 100.0) if proposal_rows_created else 0.0:,.2f}%")
    table.add_row("GPU final chain-state coverage", f"{min(100.0, gpu_final_state_rows / chain_states_produced * 100.0) if chain_states_produced else 0.0:,.2f}%")
    table.add_row("GPU queued rows", f"{int(gpu_snapshot.get('queued_rows', 0)):,}")
    table.add_row("GPU wall rows/sec", f"{float(gpu_snapshot.get('gpu_wall_rows_per_sec', 0)):,.2f}")
    table.add_row("GPU active compute rows/sec", f"{float(gpu_snapshot.get('gpu_active_compute_rows_per_sec', 0)):,.2f}")
    table.add_row("GPU scoring batches", f"{int(gpu_snapshot.get('scoring_batches', 0)):,}")
    table.add_row("GPU batch utilization", f"{float(gpu_snapshot.get('gpu_batch_utilization', 0)):,.1f}%")
    table.add_row("Average GPU batch size", f"{float(gpu_snapshot.get('average_gpu_batch_size', 0)):,.1f}")
    table.add_row("GPU queue fill rate", f"{float(gpu_snapshot.get('gpu_queue_fill_rate', 0)) * 100.0:,.1f}%")
    table.add_row("GPU idle", f"{float(gpu_snapshot.get('gpu_idle_percentage', 0)):,.1f}%")
    table.add_row("GPU idle reason", str(gpu_snapshot.get("gpu_idle_reason", "unknown")))
    table.add_row("CPU waiting on GPU", str(bool(gpu_snapshot.get("cpu_waiting_on_gpu", False))).lower())
    table.add_row("GPU waiting on CPU", str(bool(gpu_snapshot.get("gpu_waiting_on_cpu", False))).lower())
    if npu_status:
        table.add_row("NPU status", str(npu_status.get("reason", "unknown")))
        table.add_row("NPU idle reason", str(npu_status.get("idle_reason", "backend_unavailable")))
    return Panel(table, title="Survivor optimizer trainer", border_style="cyan")


def _chain_steps_for(best: dict[str, Any]) -> list[dict[str, Any]]:
    allocation = best.get("allocation", {}) if isinstance(best, dict) else {}
    steps: list[dict[str, Any]] = []
    for resource_id, amount in allocation.items():
        if amount:
            steps.append({"action": "add_resource", "resource": resource_id, "amount": amount})
    if allocation.get("astral_core"):
        steps.append({"action": "unlock_breakpoint", "id": "synthetic_astral_core_chain", "requirements": {"astral_core": 2}})
    if allocation.get("xeno_core"):
        steps.append({"action": "unlock_breakpoint", "id": "synthetic_xeno_core_chain", "requirements": {"xeno_core": 2}})
    if allocation.get("resonance_chip"):
        steps.append({"action": "unlock_breakpoint", "id": "synthetic_resonance_chain", "requirements": {"resonance_chip": 3}})
    return steps


def simulate_profile(profile: dict[str, Any]) -> dict[str, Any]:
    start = time.perf_counter()
    profile_id = str(profile.get("id", "unknown"))
    player_state_dict = profile.get("player_state", {})
    player_state = validate_player_state(player_state_dict)
    recommendation = optimize(player_state_dict, include_global_plan=False)
    chest_count = int(getattr(player_state.inventory, "core_selector_chests", 0))
    actions_tested = len(generate_core_selector_splits(chest_count))
    best = recommendation.get("best") or {}
    chain = simulate_upgrade_chain(player_state, _chain_steps_for(best))
    applied_chain_steps = sum(1 for step in chain["trace"] if step.get("applied"))
    breakpoint_reason = any("breakpoint" in str(reason).lower() for reason in best.get("reasons", []))
    avoid_scores = [float(item.get("total_score", 0)) for item in recommendation.get("avoid", [])]
    best_score = float(best.get("total_score", 0))
    candidate_features = candidate_rows_from_recommendation(recommendation, applied_chain_steps)

    return {
        "profile_id": profile_id,
        "stage": profile.get("stage", "unknown"),
        "goal_scenario": player_state.goal_scenario,
        "actions_tested": actions_tested,
        "best_action_id": best.get("action_id", ""),
        "best_score": best_score,
        "best_allocation": best.get("allocation", {}),
        "best_sub_scores": best.get("sub_scores", {}),
        "best_reasons": best.get("reasons", []),
        "avoid_max_score": max(avoid_scores) if avoid_scores else 0.0,
        "self_consistent": best_score >= (max(avoid_scores) if avoid_scores else best_score),
        "breakpoint_reason": breakpoint_reason,
        "chain_steps_applied": applied_chain_steps,
        "chain_trace": chain["trace"],
        "candidate_features": candidate_features,
        "profile_tags": profile_tags(profile),
        "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
    }


def simulate_profile_actions(
    profile: dict[str, Any],
    chain_depth: int,
    beam_size: int,
    max_actions_per_profile: int,
    include_saves: bool,
    include_random_ev: bool,
    combo_mode: bool = False,
    run_chain_simulator: bool = True,
    run_global_planner: bool = True,
    learned_systems: list[str] | None = None,
    prior_decision: dict[str, Any] | None = None,
    audit_learned_systems: list[str] | None = None,
    learned_ranker_weights: list[float] | None = None,
    gpu_preprune: bool = False,
    preprune_oversample: int = 4,
    preprune_audit: bool = False,
    total_proposal_budget: int = 24,
) -> dict[str, Any]:
    profile_started = time.perf_counter()
    if audit_learned_systems is not None:
        learned_result = simulate_profile_actions(
            profile, chain_depth, beam_size, max_actions_per_profile, include_saves, include_random_ev,
            combo_mode, True, True, audit_learned_systems, prior_decision, None, learned_ranker_weights,
            gpu_preprune, preprune_oversample, preprune_audit,
            total_proposal_budget,
        )
        full_result = simulate_profile_actions(
            profile, chain_depth, beam_size, max_actions_per_profile, include_saves, include_random_ev,
            combo_mode, True, True, None, prior_decision, None, learned_ranker_weights,
            gpu_preprune, preprune_oversample, preprune_audit,
            total_proposal_budget,
        )
        learned_plan = learned_result.get("global_plan", {}) or {}
        full_plan = full_result.get("global_plan", {}) or {}
        full_result["audit_comparison"] = {
            "learned_systems": audit_learned_systems,
            "learned_best_system": learned_plan.get("best_system") or str(learned_result.get("best_action_id", "")).split(":", 1)[0],
            "full_best_system": full_plan.get("best_system") or str(full_result.get("best_action_id", "")).split(":", 1)[0],
            "learned_best_action_id": learned_plan.get("best_action_id") or learned_result.get("best_action_id", ""),
            "full_best_action_id": full_plan.get("best_action_id") or full_result.get("best_action_id", ""),
            "learned_score": float(learned_plan.get("best_score", learned_result.get("best_score", 0.0)) or 0.0),
            "full_score": float(full_plan.get("best_score", full_result.get("best_score", 0.0)) or 0.0),
        }
        learned_perf = learned_result.get("performance", {}) or {}
        full_perf = full_result.setdefault("performance", {})
        for key, value in learned_perf.items():
            if isinstance(value, (int, float)):
                full_perf[key] = float(full_perf.get(key, 0.0) or 0.0) + float(value)
        full_perf["audited_searches"] = int(full_perf.get("audited_searches", 0)) + 1
        return full_result
    action_cache_before = action_cache_stats()
    value_cache_before = state_value_cache_stats()
    transition_before = state_transition_stats()
    shallow_started = time.perf_counter()
    result = simulate_profile(profile)
    shallow_seconds = time.perf_counter() - shallow_started
    knowledge = load_knowledge()
    player_state_dict = profile.get("player_state", {})
    action_started = time.perf_counter()
    actions = generate_inventory_actions(
        player_state_dict,
        knowledge,
        systems=learned_systems,
        include_saves=include_saves,
        include_random_ev=include_random_ev,
        max_actions=max_actions_per_profile,
        include_missing_placeholders=False,
        proposal_budget=True,
        scoreable_only=True,
    )
    action_generation_seconds = time.perf_counter() - action_started
    chain_search_reused_global = bool(combo_mode and run_chain_simulator and run_global_planner)
    if run_chain_simulator and not chain_search_reused_global:
        chain_result = simulate_action_chains(
            player_state_dict,
            knowledge,
            chain_depth=chain_depth,
            beam_size=beam_size,
            max_actions_per_profile=max_actions_per_profile,
            include_saves=include_saves,
            include_random_ev=include_random_ev,
            systems=learned_systems,
        )
    else:
        chain_result = {
            "actions_generated": len(actions), "actions_simulated": 0, "states_produced": 0,
            "chains_scored": 0, "chains_pruned": 0, "systems_covered": [],
        }
    result["inventory_actions_generated"] = len(actions)
    result["action_systems_covered"] = sorted({str(action.get("system", "unknown")) for action in actions})
    result["actions_by_system"] = dict(Counter(str(action.get("system", "unknown")) for action in actions))
    result["candidate_features"].extend(
        {
            "action_id": str(action.get("action_id", "")),
            "row_type": "action",
            "features": dict(zip(NUMERIC_FEATURE_COLUMNS, action_features(action))),
        }
        for action in actions
    )
    result["chain_actions_generated"] = chain_result["actions_generated"]
    result["chain_actions_simulated"] = chain_result["actions_simulated"]
    result["chain_systems_covered"] = chain_result["systems_covered"]
    result["chain_actions_by_system"] = chain_result.get("actions_by_system", {})
    result["chains_by_system"] = chain_result.get("chains_by_system", {})
    result["chain_states_produced"] = int(chain_result.get("states_produced", 0))
    result["chains_scored"] = int(chain_result.get("chains_scored", 0))
    result["chains_pruned"] = int(chain_result.get("chains_pruned", 0))
    result["chains_skipped"] = 0
    result["standalone_chain_runs_skipped"] = int(bool(run_chain_simulator and chain_search_reused_global))
    result["chain_simulation_reason"] = (
        "global planner reused as the deeper chain simulator"
        if chain_search_reused_global else ("standalone chain simulator ran" if run_chain_simulator else "chain simulation disabled by interval")
    )
    result["chain_simulator_ran"] = bool(run_chain_simulator and not chain_search_reused_global)
    result["chain_search_reused_global_planner"] = chain_search_reused_global
    result["global_planner_ran"] = False
    global_planner_seconds = 0.0
    planner_performance: dict[str, Any] = {}
    if combo_mode and run_global_planner:
        planner_started = time.perf_counter()
        global_plan = plan_global_inventory(
            player_state_dict,
            knowledge,
            chain_depth=chain_depth,
            beam_size=beam_size,
            max_actions_per_profile=max_actions_per_profile,
            include_saves=include_saves,
            include_random_ev=include_random_ev,
            prune_dominated_states_enabled=True,
            systems=learned_systems,
            learned_ranker_weights=learned_ranker_weights,
            gpu_preprune=gpu_preprune,
            preprune_oversample=preprune_oversample,
            preprune_audit=preprune_audit,
            total_proposal_budget=total_proposal_budget,
            prebuilt_root_actions=actions if not preprune_audit else None,
        )
        global_planner_seconds = time.perf_counter() - planner_started
        planner_performance = global_plan.get("performance", {}) or {}
        result["candidate_features"].extend(
            {
                "action_id": str(action.get("action_id", "")),
                "row_type": "chain",
                "features": dict(zip(NUMERIC_FEATURE_COLUMNS, action_features(
                    action, chain_value=float((action.get("metadata", {}) or {}).get("chain_depth", 0)),
                    profile_stage=str(profile.get("stage", "unknown")), scenario_id=str(player_state_dict.get("goal_scenario", "unknown")),
                ))),
            }
            for action in global_plan.get("numeric_chain_candidates", [])
        )
        ordered_steps = global_plan["best_action_chain"]["ordered_steps"]
        result["global_plan"] = {
            "actions_considered": global_plan["actions_considered"],
            "chains_considered": global_plan["chains_considered"],
            "raw_proposal_rows": global_plan.get("raw_proposal_rows", global_plan["chains_considered"]),
            "proposal_rows_created": global_plan.get("proposal_rows_created", global_plan["chains_considered"]),
            "proposal_rows_budget_removed": global_plan.get("proposal_rows_budget_removed", 0),
            "states_materialized": global_plan.get("states_materialized", global_plan["chains_considered"]),
            "actions_pruned": global_plan["actions_pruned"],
            "dominated_states_removed": global_plan["dominated_states_removed"],
            "systems_covered": global_plan["systems_covered"],
            "actions_by_system": global_plan.get("actions_by_system", {}),
            "chains_by_system": global_plan.get("chains_by_system", {}),
            "search_mode": global_plan["search_mode"],
            "best_action_count": len(global_plan["best_action_chain"]["ordered_steps"]),
            "best_score": float(global_plan["best_action_chain"]["marginal_value"]["delta"]),
            "best_system": str(ordered_steps[0].get("system", "")) if ordered_steps else "save_hold",
            "best_action_type": str(ordered_steps[0].get("action_type", "")) if ordered_steps else "save_hold",
            "best_action_id": str(ordered_steps[0].get("action_id", "")) if ordered_steps else "",
            "best_chain_signature": ">".join(str(step.get("action_type", "unknown")) for step in ordered_steps),
            "save_hold_recommended": any(step["action_type"] == "save_hold" for step in global_plan["best_action_chain"]["ordered_steps"]),
            "learned_ranker_applied": bool(global_plan.get("learned_ranker_applied", False)),
            "gpu_preprune": global_plan.get("gpu_preprune", {}),
        }
        result["learning_best_system"] = result["global_plan"]["best_system"]
        result["learning_best_score"] = result["global_plan"]["best_score"]
        result["global_planner_ran"] = True
        if chain_search_reused_global:
            result["chain_systems_covered"] = global_plan["systems_covered"]
            result["chain_actions_generated"] = global_plan["actions_considered"]
            result["chain_actions_simulated"] = global_plan["chains_considered"]
            result["chain_states_produced"] = global_plan.get("states_materialized", global_plan["chains_considered"])
            result["chains_scored"] = global_plan["chains_considered"]
            result["chains_pruned"] = global_plan["actions_pruned"] + global_plan["dominated_states_removed"]
            result["chain_actions_by_system"] = global_plan.get("actions_by_system", {})
            result["chains_by_system"] = global_plan.get("chains_by_system", {})
    result["learned_pruning"] = {
        "enabled": learned_systems is not None or prior_decision is not None,
        "systems": learned_systems,
        "decision": prior_decision or {},
    }
    action_cache_after = action_cache_stats()
    value_cache_after = state_value_cache_stats()
    result["runtime_cache"] = {
        "action_hits": action_cache_after["hits"] - action_cache_before["hits"],
        "action_misses": action_cache_after["misses"] - action_cache_before["misses"],
        "state_value_hits": value_cache_after["hits"] - value_cache_before["hits"],
        "state_value_misses": value_cache_after["misses"] - value_cache_before["misses"],
    }
    transition_after = state_transition_stats()
    result["performance"] = {
        "profile_total_seconds": round(time.perf_counter() - profile_started, 6),
        "shallow_recommendation_seconds": round(shallow_seconds, 6),
        "action_generation_seconds": round(action_generation_seconds, 6),
        "cpu_candidate_seconds": float(planner_performance.get("cpu_candidate_seconds", action_generation_seconds) or 0.0),
        "global_planner_seconds": round(global_planner_seconds, 6),
        "candidate_row_creation_seconds": float(planner_performance.get("candidate_row_creation_seconds", 0.0) or 0.0),
        "numeric_feature_creation_seconds": float(planner_performance.get("numeric_feature_creation_seconds", 0.0) or 0.0),
        "state_transition_and_hashing_seconds": float(planner_performance.get("state_transition_and_hashing_seconds", 0.0) or 0.0),
        "gpu_queue_wait_seconds": float(planner_performance.get("gpu_queue_wait_seconds", 0.0) or 0.0),
        "state_copy_count": int(transition_after.get("state_copies", 0)) - int(transition_before.get("state_copies", 0)),
        "state_rebuild_count": int(transition_after.get("state_rebuilds", 0)) - int(transition_before.get("state_rebuilds", 0)),
        "state_copy_seconds": round(float(transition_after.get("state_copy_seconds", 0.0)) - float(transition_before.get("state_copy_seconds", 0.0)), 6),
        "duplicate_candidates_removed": int(planner_performance.get("duplicate_candidates_removed", 0) or 0),
        "useful_topk_rate": float(planner_performance.get("useful_topk_rate", 0.0) or 0.0),
        "waste_by_system": planner_performance.get("waste_by_system", {}),
    }
    return result


def _clamp(value: float, low: float = 0.8, high: float = 1.2) -> float:
    return round(min(high, max(low, value)), 6)


def tune_scoring_weights(results: list[dict[str, Any]], weights_path: Path = WEIGHTS_PATH) -> dict[str, Any]:
    if not results:
        return {"updated": False, "reason": "no results"}

    weights = load_scoring_weights(weights_path.parent)
    default = weights.setdefault("default", {})
    for key in [
        "immediate_damage",
        "long_term_damage",
        "breakpoint_value",
        "resource_efficiency",
        "rarity_value",
        "confidence",
        "mode_relevance",
        "chain_reaction_value",
    ]:
        default.setdefault(key, 1.0)

    breakpoint_rate = sum(1 for row in results if row.get("breakpoint_reason")) / len(results)
    chain_rate = sum(1 for row in results if row.get("chain_steps_applied", 0) > 1) / len(results)
    consistency_rate = sum(1 for row in results if row.get("self_consistent")) / len(results)

    before = {
        "breakpoint_value": float(default["breakpoint_value"]),
        "chain_reaction_value": float(default["chain_reaction_value"]),
        "confidence": float(default["confidence"]),
    }
    default["breakpoint_value"] = _clamp(before["breakpoint_value"] + (0.005 if breakpoint_rate >= 0.35 else -0.002))
    default["chain_reaction_value"] = _clamp(before["chain_reaction_value"] + (0.005 if chain_rate >= 0.35 else -0.002))
    default["confidence"] = _clamp(before["confidence"] + (0.002 if consistency_rate >= 0.8 else -0.002))

    # Keep legacy aliases aligned enough for the current scorer.
    default["breakpoint_score"] = default["breakpoint_value"]
    default["confidence_score"] = default["confidence"]

    log_entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results_used": len(results),
        "breakpoint_rate": round(breakpoint_rate, 4),
        "chain_rate": round(chain_rate, 4),
        "consistency_rate": round(consistency_rate, 4),
        "before": before,
        "after": {
            "breakpoint_value": default["breakpoint_value"],
            "chain_reaction_value": default["chain_reaction_value"],
            "confidence": default["confidence"],
        },
    }
    weights["last_tuned"] = log_entry["timestamp"]
    weights.setdefault("tuning_log", []).append(log_entry)
    weights["tuning_log"] = weights["tuning_log"][-50:]
    weights_path.write_text(json.dumps(weights, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tuning_log_path = TRAINING_LOG if weights_path.resolve() == WEIGHTS_PATH.resolve() else weights_path.with_suffix(".tuning.jsonl")
    append_jsonl(tuning_log_path, [log_entry])
    return {"updated": True, **log_entry}


def _run_training_impl(
    minutes: float,
    workers: str,
    device: str,
    resume: bool,
    seed: int,
    batch_size: int,
    gpu_score: bool = False,
    fresh: bool = False,
    chain_depth: int = 2,
    beam_size: int = 100,
    max_actions_per_profile: int = 500,
    include_saves: bool = True,
    include_random_ev: bool = True,
    coverage_report_enabled: bool = False,
    combo_mode: bool = False,
    allow_exhaustive_small_inventory: bool = False,
    prune_dominated_states_enabled: bool = True,
    keep_generating: bool = False,
    stop_when_exhausted: bool = False,
    live: bool = False,
    fast_throughput: bool = False,
    chain_profile_interval: int = 1,
    global_profile_interval: int = 1,
    learned_pruning: str | bool = "off",
    exploration_rate: float = 0.08,
    audit_full_search_interval: int = 0,
    audit_full_search_rate: float = 0.0,
    prior_min_samples: int = 20,
    profile_batch_size: int = 65536,
    materialize_profiles: str = "all",
    gpu_profile_features: bool = False,
    tune_weights: bool = True,
    checkpoint_interval_seconds: float = 30.0,
    max_profiles: int | None = None,
    checkpoint_path: Path | None = None,
    assumption_chart_path: Path = ASSUMPTION_CHART_PATH,
    profiles_path: Path = PROFILES_PATH,
    results_path: Path = RESULTS_PATH,
    weights_path: Path = WEIGHTS_PATH,
    logging_mode: str = "normal",
    learned_ranker_enabled: bool = True,
    ranker_checkpoint_path: Path | None = None,
    allow_cpu_fallback: bool = False,
    requested_device: str | None = None,
    cuda_preflight_passed: bool | None = None,
) -> dict[str, Any]:
    logging_mode = logging_mode if logging_mode in {"quiet", "normal", "debug", "json-log-to-file"} else "normal"
    debug_messages: list[str] = []

    def debug(message: str) -> None:
        debug_messages.append(message)
        if logging_mode == "debug":
            print(message)

    # run_training resolves this once after strict preflight. Never probe CUDA
    # again in the coordinator or any CPU worker.
    resolved_device = "cuda" if device in {"cuda", "gpu"} else "cpu"
    requested_device = requested_device or device
    device_warnings: list[str] = []
    worker_count = selected_workers(workers, gpu_pipeline=bool(gpu_score and resolved_device == "cuda"))
    if fast_throughput:
        chain_profile_interval = max(chain_profile_interval, 5)
        global_profile_interval = max(global_profile_interval, 25)
    chain_profile_interval = max(1, int(chain_profile_interval))
    global_profile_interval = max(1, int(global_profile_interval))
    if isinstance(learned_pruning, bool):
        learned_pruning_mode = "normal" if learned_pruning else "off"
    else:
        learned_pruning_mode = str(learned_pruning).lower()
    if learned_pruning_mode not in {"off", "soft", "normal", "aggressive"}:
        learned_pruning_mode = "normal"
    learned_pruning_enabled = learned_pruning_mode != "off"
    exploration_rate = max(0.0, min(1.0, float(exploration_rate)))
    audit_full_search_interval = max(0, int(audit_full_search_interval))
    audit_full_search_rate = max(0.0, min(1.0, float(audit_full_search_rate)))
    profile_batch_size = max(1, int(profile_batch_size))
    max_profiles = max(1, int(max_profiles)) if max_profiles is not None else None
    materialize_profiles = materialize_profiles if materialize_profiles in {"all", "on_demand"} else "all"
    primary_results = results_path.resolve() == RESULTS_PATH.resolve()
    latest_debug_alias: Path | None = None
    if primary_results:
        summary_output_path = LATEST_SUMMARY_PATH
        metrics_output_path = LATEST_METRICS_PATH
        coverage_output_path = LATEST_COVERAGE_PATH
        learning_output_path = LATEST_LEARNING_PATH
        hardware_output_path = LATEST_HARDWARE_PATH
        run_stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        run_nonce = time.time_ns() % 1_000_000_000
        debug_output_path = LOGS_DIR / "training" / "runs" / f"trainer_{run_stamp}_{os.getpid()}_{run_nonce:09d}.log"
        latest_debug_alias = LATEST_DEBUG_PATH
    else:
        artifact_stem = results_path.stem
        summary_output_path = results_path.with_name(f"{artifact_stem}_summary.json")
        metrics_output_path = results_path.with_name(f"{artifact_stem}_metrics.json")
        coverage_output_path = results_path.with_name(f"{artifact_stem}_coverage_report.json")
        learning_output_path = results_path.with_name(f"{artifact_stem}_learning_report.json")
        hardware_output_path = results_path.with_name(f"{artifact_stem}_hardware_report.json")
        debug_output_path = results_path.with_name(f"{artifact_stem}_debug.log")
    if not primary_results and assumption_chart_path.resolve() == ASSUMPTION_CHART_PATH.resolve():
        assumption_chart_path = results_path.with_name("profile_assumption_chart.json")
    primary_chart = assumption_chart_path.resolve() == ASSUMPTION_CHART_PATH.resolve()
    optimizer_checkpoint_path = checkpoint_path or (OPTIMIZER_CHECKPOINT_PATH if primary_results else results_path.with_suffix(".checkpoint.json"))
    ranker_checkpoint_path = ranker_checkpoint_path or (LEARNED_RANKER_PATH if primary_results else results_path.with_name("learned_ranker.json"))
    prior_report_json = PRIOR_REPORT_JSON if primary_chart else assumption_chart_path.with_name(f"{assumption_chart_path.stem}_report.json")
    prior_report_md = PRIOR_REPORT_MD if primary_chart else assumption_chart_path.with_name(f"{assumption_chart_path.stem}_report.md")
    learning_memory_path = LEARNING_MEMORY_PATH if primary_chart else assumption_chart_path.with_name(f"{assumption_chart_path.stem}_memory.json")
    gpu_ownership = gpu_work_ownership(
        gpu_score=gpu_score, resolved_device=resolved_device, gpu_profile_features=gpu_profile_features,
    )
    shared_ranker_requested = bool(gpu_ownership["shared_ranker"])
    gpu_profile_features_effective = bool(gpu_ownership["gpu_profile_features"])
    profile_generator = ProfileBatchGenerator(
        seed, stage="mixed", gpu_features=gpu_profile_features_effective,
        device="cpu" if shared_ranker_requested else resolved_device,
        allow_cpu_fallback=allow_cpu_fallback or device not in {"cuda", "gpu"},
    )
    profile_producer = AsyncProfileProducer(profile_generator, profile_batch_size) if materialize_profiles == "on_demand" and keep_generating else None
    compact_profile_batch: dict[str, Any] | None = None
    compact_profile_offset = 0
    profiles = read_jsonl(profiles_path)
    if not profiles and materialize_profiles == "on_demand":
        initial_numeric = profile_generator.numeric_batch(max(worker_count * 4, 100))
        profiles = profile_generator.materialize(initial_numeric)
    if not profiles:
        raise FileNotFoundError(f"No profiles found at {profiles_path}")
    if fresh and results_path.exists():
        fresh_archive = results_path.with_suffix(f".fresh_backup_{int(time.time())}.jsonl")
        results_path.replace(fresh_archive)

    if fresh:
        resume = False
    existing_results = read_jsonl(results_path) if results_path.exists() and (resume or learned_pruning_enabled) else []
    completed = {row.get("profile_id") for row in existing_results} if resume else set()
    completed_rows = existing_results if learned_pruning_enabled else []
    pending = [profile for profile in profiles if profile.get("id") not in completed]
    random.Random(seed).shuffle(pending)
    if keep_generating and not stop_when_exhausted and not pending:
        extra_profiles = generate_profiles(count=max(batch_size, 100), seed=seed + len(completed), stage="mixed")
        write_profiles(extra_profiles, profiles_path)
        profiles.extend(extra_profiles)
        pending.extend(extra_profiles)
    learned_memory_loaded_from_disk = bool(learned_pruning_enabled and assumption_chart_path.exists())
    assumption_chart = load_chart(assumption_chart_path) if learned_pruning_enabled else {"total_samples": 0, "buckets": {}}
    if learned_pruning_enabled and primary_chart:
        assumption_chart = recover_chart_from_report(assumption_chart, PRIOR_REPORT_JSON)
    if learned_pruning_enabled and completed_rows and int(assumption_chart.get("total_samples", 0)) == 0:
        rebuilt_chart = build_chart(profiles, completed_rows)
        if int(rebuilt_chart.get("total_samples", 0)) > 0:
            assumption_chart = rebuilt_chart
            save_chart(assumption_chart, assumption_chart_path)

    cuda_detected = resolved_device == "cuda"
    npu_status = detect_npu()
    startup = {
        "detected_cpu_count": os.cpu_count() or 1, "selected_workers": worker_count,
        "cuda_detected": cuda_detected, "npu": npu_status,
        "requested_device": requested_device, "selected_device": resolved_device,
        "cuda_preflight_passed": cuda_preflight_passed,
        "batch_size": batch_size, "resume": resume, "fresh": fresh, "minutes": minutes,
        "chain_depth": chain_depth, "beam_size": beam_size, "max_actions_per_profile": max_actions_per_profile,
        "combo_mode": combo_mode, "fast_throughput": fast_throughput,
        "chain_profile_interval": chain_profile_interval, "global_profile_interval": global_profile_interval,
        "learned_pruning": learned_pruning_mode, "exploration_rate": exploration_rate,
        "audit_full_search_interval": audit_full_search_interval, "audit_full_search_rate": audit_full_search_rate,
        "prior_min_samples": prior_min_samples, "assumption_chart_path": str(assumption_chart_path),
        "assumption_chart_samples_at_start": assumption_chart.get("total_samples", 0),
        "learned_memory_loaded_from_disk": learned_memory_loaded_from_disk, "keep_generating": keep_generating,
        "max_profiles": max_profiles, "profile_batch_size": profile_batch_size,
        "materialize_profiles": materialize_profiles, "gpu_profile_features_requested": gpu_profile_features,
        "gpu_profile_features_effective": gpu_profile_features_effective,
        "cuda_owner": gpu_ownership["cuda_owner"],
        "live_dashboard": live, "gpu_score_requested": gpu_score,
    }
    debug("startup: " + json.dumps(startup, ensure_ascii=False, sort_keys=True))
    startup_gpu_ops = ["numeric_feature_batch_scoring"] if gpu_score and resolved_device == "cuda" else []
    if startup_gpu_ops:
        startup_gpu_reason = "CUDA scoring requested; actual use depends on candidate batch size."
    elif gpu_score:
        startup_gpu_reason = "GPU scoring requested but selected device is CPU."
    else:
        startup_gpu_reason = "GPU scoring was not requested; CPU scoring path will be used."
    debug("gpu_startup: " + json.dumps({"requested": gpu_score, "cuda_available": resolved_device == "cuda", "initialized": bool(startup_gpu_ops), "reason": startup_gpu_reason, "operations": startup_gpu_ops}, sort_keys=True))
    for warning in device_warnings:
        debug(f"warning: {warning}")

    started = time.monotonic()
    deadline = started + max(minutes, 0.01) * 60
    processed = 0
    actions = 0
    inventory_actions_generated = 0
    chain_actions_simulated = 0
    chain_states_produced = 0
    chains_pruned = 0
    chains_scored = 0
    chains_skipped = 0
    standalone_chain_runs_skipped = 0
    chain_simulation_reason = "no profiles completed yet"
    action_generation_time = 0.0
    log_write_time = 0.0
    systems_covered: set[str] = set()
    global_chains_considered = 0
    raw_proposal_rows = 0
    proposal_rows_created = 0
    proposal_rows_budget_removed = 0
    dominated_states_removed = 0
    save_hold_recommendations = 0
    profiles_submitted = 0
    chain_simulator_profiles = 0
    global_planner_profiles = 0
    learned_pruned_profiles = 0
    learned_reordered_profiles = 0
    learned_prior_decisions: Counter[str] = Counter()
    learned_block_reasons: Counter[str] = Counter()
    learned_memory_writes = 0
    full_search_audits = 0
    false_prunes = 0
    preprune_full_search_audits = 0
    preprune_false_prunes = 0
    preprune_corrections = 0
    preprune_candidates_removed = 0
    equivalent_actions_removed_before_gpu = 0
    prebuilt_root_actions_reused = 0
    proposal_budget_raw_candidates = 0
    proposal_budget_selected_candidates = 0
    proposal_budget_candidates_removed = 0
    proposal_budget_audits = 0
    proposal_budget_false_prunes = 0
    proposal_row_budget_audits = 0
    proposal_row_budget_false_prunes = 0
    preprune_worker_wait_seconds = 0.0
    profile_batches_generated = 0
    gpu_profile_features_used = False
    actions_by_system: Counter[str] = Counter()
    chains_by_system: Counter[str] = Counter()
    runtime_cache_totals: Counter[str] = Counter()
    performance_totals: Counter[str] = Counter()
    candidate_waste_by_system: Counter[str] = Counter()
    learned_candidates_reordered = 0
    chart_checkpoint_seconds = max(5.0, float(checkpoint_interval_seconds))
    last_chart_checkpoint = started
    batch_results: list[dict[str, Any]] = []
    all_new_results: list[dict[str, Any]] = []
    scoring_weights = load_scoring_weights(ROOT / "knowledge")
    learned_ranker = OnlineLinearRanker(ranker_checkpoint_path, enabled=learned_ranker_enabled)
    preprune_enabled = bool(gpu_score and resolved_device == "cuda" and cuda_detected)
    mp_context = mp.get_context("spawn")
    preprune_manager = mp_context.Manager() if preprune_enabled else None
    preprune_queue_capacity = max(64, worker_count * 8)
    preprune_request_queue = preprune_manager.Queue(maxsize=preprune_queue_capacity) if preprune_manager else None
    preprune_response_map = preprune_manager.dict() if preprune_manager else None
    preprune_service = SharedPrePruneGpuService(
        preprune_request_queue, preprune_response_map,
        enabled=preprune_enabled, batch_size=batch_size, max_batch_rows=batch_size * 4, fill_timeout=0.02,
        queue_capacity=preprune_queue_capacity,
        allow_cpu_fallback=allow_cpu_fallback,
    ) if preprune_enabled else None
    if preprune_service:
        try:
            preprune_service.start()
        except BaseException:
            preprune_service.close()
            if preprune_manager:
                preprune_manager.shutdown()
            raise
    async_gpu_scorer = AsyncGpuScorer(scoring_weights, resolved_device, gpu_score and not preprune_enabled, batch_size)
    async_gpu_scorer.set_learned_weights(learned_ranker.snapshot_weights())
    async_gpu_scorer.start()
    if profile_producer:
        profile_producer.start()
    # Large historical JSONL files are append-only evidence. Loading hundreds
    # of MB into RAM caused paging while producing no cache hits; hot-loop reuse
    # is handled by process-local action/state memoization.
    cache_options = {"flush_every": 1000, "load_existing": False, "max_file_bytes": 64 * 1024 * 1024, "retain_entries": 50000}
    state_value_cache = JsonlCache(STATE_VALUE_CACHE_PATH, **cache_options)
    action_result_cache = JsonlCache(ACTION_RESULT_CACHE_PATH, **cache_options)
    chain_result_cache = JsonlCache(CHAIN_RESULT_CACHE_PATH, **cache_options)
    profile_feature_cache = JsonlCache(PROFILE_FEATURE_CACHE_PATH, **cache_options)
    action_generation_cache = JsonlCache(ACTION_GENERATION_CACHE_PATH, **cache_options)
    gpu_score_cache = JsonlCache(GPU_SCORE_CACHE_PATH, **cache_options)
    knowledge_version_hash = stable_hash({"knowledge_metadata": load_knowledge().get("metadata", {}), "weights": scoring_weights, "chain_depth": chain_depth, "beam_size": beam_size, "max_actions_per_profile": max_actions_per_profile})
    live_knowledge = load_knowledge()
    live_coverage = coverage_report(live_knowledge, coverage_audit_state(live_knowledge))
    live_enabled = bool(live and Live is not None and Table is not None and Panel is not None)
    if live and not live_enabled:
        debug("warning: live dashboard requested but rich is unavailable; continuing with normal output.")

    runtime_failure: TrainingStartupError | None = None

    def current_gpu_snapshot() -> dict[str, Any]:
        return preprune_service.snapshot() if preprune_service else async_gpu_scorer.snapshot()

    def render_dashboard() -> Any:
        return _render_live_dashboard(
            started=started,
            deadline=deadline,
            processed=processed,
            actions=actions,
            inventory_actions_generated=inventory_actions_generated,
            chain_actions_simulated=chain_actions_simulated,
            global_chains_considered=global_chains_considered,
            proposal_rows_created=proposal_rows_created,
            chain_states_produced=chain_states_produced,
            chains_pruned=chains_pruned,
            chains_scored=chains_scored,
            chains_skipped=chains_skipped,
            standalone_chain_runs_skipped=standalone_chain_runs_skipped,
            chain_simulation_reason=chain_simulation_reason,
            dominated_states_removed=dominated_states_removed,
            systems_covered=systems_covered,
            gpu_snapshot=current_gpu_snapshot(),
            worker_count=worker_count,
            keep_generating=keep_generating,
            combo_mode=combo_mode,
            chain_profile_interval=chain_profile_interval,
            global_profile_interval=global_profile_interval,
            learned_pruning_mode=learned_pruning_mode,
            learned_chart_samples=int(assumption_chart.get("total_samples", 0)),
            learned_pruned_profiles=learned_pruned_profiles,
            full_search_audits=full_search_audits,
            false_prunes=false_prunes,
            profile_queue_size=max(0, len(pending) - index),
            actions_by_system=dict(actions_by_system),
            real_data_systems=live_coverage.get("observable_real_data_systems", live_coverage.get("real_data_systems", [])),
            placeholder_only_systems=live_coverage.get("placeholder_only_systems", []),
            npu_status=npu_status,
            runtime_failure=runtime_failure,
            requested_device=requested_device,
            selected_device=resolved_device,
            cuda_preflight_passed=cuda_preflight_passed,
        )

    index = 0
    if resume and not pending:
        debug("warning: No profiles were trained because resume skipped everything.")
    live_view = Live(render_dashboard(), refresh_per_second=2) if live_enabled else None
    if live_view:
        live_view.start()
    profile_producer_summary = {"queue_size": 0, "batches_produced": 0, "gpu_used": False, "idle_reason": "on_demand_generation_disabled", "errors": []}
    interrupted = False
    execution_error: BaseException | None = None

    def write_checkpoint(*, completed: bool = False) -> None:
        nonlocal learned_memory_writes
        if learned_pruning_enabled:
            save_chart(assumption_chart, assumption_chart_path)
            atomic_write_json(learning_memory_path, learning_memory_snapshot(assumption_chart))
            learned_memory_writes += 1
        atomic_write_json(
            optimizer_checkpoint_path,
            optimizer_checkpoint(
                processed=processed, submitted=profiles_submitted, elapsed_seconds=time.monotonic() - started,
                chart=assumption_chart, systems_covered=systems_covered, results_path=results_path,
                profiles_path=profiles_path, device=resolved_device, workers=worker_count,
                interrupted=interrupted, completed=completed,
            ),
        )

    try:
        with ProcessPoolExecutor(
            max_workers=worker_count,
            mp_context=mp_context,
            initializer=initialize_preprune_worker,
            initargs=(preprune_request_queue, preprune_response_map, preprune_enabled, 60.0),
        ) as executor:
            while index < len(pending) and (max_profiles is None or processed < max_profiles):
                cpu_batch_size = max(1, min(batch_size, worker_count * 4))
                if max_profiles is not None:
                    cpu_batch_size = min(cpu_batch_size, max_profiles - processed)
                batch = pending[index : index + cpu_batch_size]
                if not batch:
                    break
                index += len(batch)
                jobs = []
                ranker_weights_for_batch = learned_ranker.snapshot_weights()
                preprune_audit_denominator = max(1, preprune_full_search_audits)
                observed_preprune_false_rate = preprune_false_prunes / preprune_audit_denominator
                adaptive_preprune_oversample = 12 if observed_preprune_false_rate > 0.10 else (8 if observed_preprune_false_rate > 0.03 else 1)
                proposal_audit_denominator = max(1, proposal_budget_audits + proposal_row_budget_audits)
                observed_proposal_false_rate = (proposal_budget_false_prunes + proposal_row_budget_false_prunes) / proposal_audit_denominator
                adaptive_proposal_budget = 48 if observed_proposal_false_rate > 0.10 else (32 if observed_proposal_false_rate > 0.03 else 24)
                for profile in batch:
                    profiles_submitted += 1
                    if learned_pruning_enabled:
                        prior_decision = recommend_training_plan(
                            profile,
                            assumption_chart,
                            sequence=profiles_submitted,
                            base_chain_interval=chain_profile_interval,
                            base_global_interval=global_profile_interval,
                            min_samples=prior_min_samples,
                            pruning_mode=learned_pruning_mode,
                            exploration_rate=exploration_rate,
                            rng=random.Random(seed + profiles_submitted),
                        )
                        run_chain_simulator = bool(prior_decision["run_chain_simulator"])
                        run_global_planner = bool(prior_decision["run_global_planner"])
                        if not fast_throughput:
                            # Full-search mode may use learned system ordering/pruning,
                            # but it never skips a profile's deep planners.
                            run_chain_simulator = True
                            run_global_planner = True
                        learned_systems = prior_decision.get("systems")
                        pruning_applied, reordering_applied, decision_kind, blocked_reason = learning_decision_usage(
                            prior_decision, learned_systems,
                        )
                        learned_pruned_profiles += int(pruning_applied)
                        learned_reordered_profiles += int(reordering_applied)
                        learned_prior_decisions[decision_kind] += 1
                        if blocked_reason:
                            learned_block_reasons[blocked_reason] += 1
                    else:
                        prior_decision = None
                        learned_systems = None
                        run_chain_simulator = ((profiles_submitted - 1) % chain_profile_interval) == 0
                        run_global_planner = ((profiles_submitted - 1) % global_profile_interval) == 0
                    audit_profile = bool(
                        learned_pruning_enabled
                        and (
                            (audit_full_search_interval and profiles_submitted % audit_full_search_interval == 0)
                            or (audit_full_search_rate and random.Random(seed + profiles_submitted * 17).random() < audit_full_search_rate)
                        )
                    )
                    audit_learned_systems = learned_systems
                    if audit_profile:
                        run_chain_simulator = True
                        run_global_planner = True
                        learned_systems = None
                    jobs.append((profile, run_chain_simulator, run_global_planner, learned_systems, prior_decision, audit_profile, audit_learned_systems))
                # Finish the current batch even if the deadline passes during it.
                future_to_profile = {}
                for profile, run_chain_simulator, run_global_planner, learned_systems, prior_decision, audit_profile, audit_learned_systems in jobs:
                    future = executor.submit(
                        simulate_profile_actions,
                        profile,
                        chain_depth,
                        beam_size,
                        max_actions_per_profile,
                        include_saves,
                        include_random_ev,
                        combo_mode,
                        run_chain_simulator,
                        run_global_planner,
                        learned_systems,
                        prior_decision,
                        audit_learned_systems if audit_profile and audit_learned_systems is not None else None,
                        ranker_weights_for_batch,
                        preprune_enabled,
                        adaptive_preprune_oversample,
                        audit_profile,
                        adaptive_proposal_budget,
                    )
                    future_to_profile[future] = (profile, audit_profile, audit_learned_systems)
                for future in as_completed(future_to_profile):
                    result = future.result()
                    source_profile, audit_profile, audit_learned_systems = future_to_profile[future]
                    batch_results.append(result)
                    all_new_results.append(result)
                    if learned_pruning_enabled:
                        add_observation(assumption_chart, source_profile, result)
                        if audit_profile:
                            comparison = result.get("audit_comparison", {}) or {}
                            audit_result = record_audit(
                                assumption_chart,
                                source_profile,
                                learned_systems=audit_learned_systems,
                                full_best_system=str(comparison.get("full_best_system") or str(result.get("best_action_id", "")).split(":", 1)[0]),
                                full_score=float(comparison.get("full_score", result.get("best_score", 0.0)) or 0.0),
                                learned_score=float(comparison.get("learned_score", result.get("best_score", 0.0)) or 0.0),
                                full_best_action_id=str(comparison.get("full_best_action_id", "")),
                                learned_best_action_id=str(comparison.get("learned_best_action_id", "")),
                            )
                            full_search_audits += 1
                            false_prunes += int(bool(audit_result.get("false_prune", False)))
                            if audit_result.get("false_prune", False):
                                event = {
                                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                    "profile_id": str(source_profile.get("id", "")),
                                    "bucket": audit_result.get("bucket", ""),
                                    "missed_system": audit_result.get("action_system_missed", ""),
                                    "missed_action_id": audit_result.get("action_id_missed", ""),
                                    "selected_action_id": audit_result.get("selected_action_id", ""),
                                    "learned_systems": comparison.get("learned_systems", []),
                                    "score_difference": audit_result.get("score_difference", 0.0),
                                }
                                append_jsonl(FALSE_PRUNE_LOG_PATH, [event])
                                debug("false_prune: " + json.dumps(event, ensure_ascii=False, sort_keys=True))
                    player_state_for_cache = source_profile.get("player_state", {})
                    state_key = stable_hash({"state": player_state_for_cache, "knowledge": knowledge_version_hash})
                    profile_feature_cache.set(state_key, {"knowledge_version_hash": knowledge_version_hash, "scenario_id": player_state_for_cache.get("goal_scenario", "scenario_1"), "features": source_profile.get("numeric_profile_features", [])})
                    action_generation_cache.set(stable_hash({"state": state_key, "systems": result.get("action_systems_covered", [])}), {"knowledge_version_hash": knowledge_version_hash, "state_hash": state_key, "actions_by_system": result.get("actions_by_system", {})})
                    state_value_cache.set(
                        state_key,
                        {
                            "state_hash": state_key,
                            "knowledge_version_hash": knowledge_version_hash,
                            "profile_id": result.get("profile_id"),
                            "best_score": result.get("best_score", 0),
                            "breakpoint_reason": result.get("breakpoint_reason", False),
                        },
                    )
                    action_key = stable_hash({"state": state_key, "action": result.get("best_action_id"), "knowledge": knowledge_version_hash})
                    action_result_cache.set(
                        action_key,
                        {
                            "state_hash": state_key,
                            "action_hash": stable_hash(result.get("best_action_id", "")),
                            "knowledge_version_hash": knowledge_version_hash,
                            "best_action_id": result.get("best_action_id"),
                            "best_score": result.get("best_score", 0),
                        },
                    )
                    chain_key = stable_hash({"state": state_key, "chain": result.get("chain_trace", []), "knowledge": knowledge_version_hash})
                    chain_result_cache.set(
                        chain_key,
                        {
                            "state_hash": state_key,
                            "chain_hash": chain_key,
                            "knowledge_version_hash": knowledge_version_hash,
                            "chain_steps_applied": result.get("chain_steps_applied", 0),
                            "chain_actions_simulated": result.get("chain_actions_simulated", 0),
                        },
                    )
                    candidate_rows = result.get("candidate_features", [])
                    ranker_winners = {str(result.get("best_action_id", ""))}
                    if any(row.get("row_type") == "chain" for row in candidate_rows):
                        ranker_winners.add(next(str(row.get("action_id")) for row in candidate_rows if row.get("row_type") == "chain"))
                    if learned_ranker.observe(candidate_rows, ranker_winners):
                        async_gpu_scorer.set_learned_weights(learned_ranker.snapshot_weights())
                    gpu_score_cache.set(
                        stable_hash({"weights": knowledge_version_hash, "state": state_key, "actions": [row.get("action_id") for row in candidate_rows]}),
                        {"knowledge_version_hash": knowledge_version_hash, "state_hash": state_key, "row_count": len(candidate_rows)},
                    )
                    processed += 1
                    actions += int(result.get("actions_tested", 0))
                    inventory_actions_generated += int(result.get("inventory_actions_generated", 0))
                    chain_actions_simulated += int(result.get("chain_actions_simulated", 0))
                    chain_states_produced += int(result.get("chain_states_produced", 0))
                    chains_pruned += int(result.get("chains_pruned", 0))
                    chains_scored += int(result.get("chains_scored", 0))
                    chains_skipped += int(result.get("chains_skipped", 0))
                    standalone_chain_runs_skipped += int(result.get("standalone_chain_runs_skipped", 0))
                    chain_simulation_reason = str(result.get("chain_simulation_reason", chain_simulation_reason))
                    chain_simulator_profiles += int(bool(result.get("chain_simulator_ran", False)))
                    global_planner_profiles += int(bool(result.get("global_planner_ran", False)))
                    systems_covered.update(result.get("action_systems_covered", []))
                    systems_covered.update(result.get("chain_systems_covered", []))
                    actions_by_system.update({str(key): int(value) for key, value in result.get("actions_by_system", {}).items()})
                    actions_by_system.update({str(key): int(value) for key, value in result.get("chain_actions_by_system", {}).items()})
                    chains_by_system.update({str(key): int(value) for key, value in result.get("chains_by_system", {}).items()})
                    runtime_cache_totals.update({str(key): int(value) for key, value in result.get("runtime_cache", {}).items()})
                    performance = result.get("performance", {}) or {}
                    performance_totals.update({
                        str(key): float(value)
                        for key, value in performance.items()
                        if isinstance(value, (int, float))
                    })
                    candidate_waste_by_system.update({
                        str(key): int(value)
                        for key, value in (performance.get("waste_by_system", {}) or {}).items()
                    })
                    if result.get("global_plan"):
                        global_plan = result["global_plan"]
                        global_chains_considered += int(global_plan.get("chains_considered", 0))
                        raw_proposal_rows += int(global_plan.get("raw_proposal_rows", global_plan.get("chains_considered", 0)))
                        proposal_rows_created += int(global_plan.get("proposal_rows_created", global_plan.get("chains_considered", 0)))
                        proposal_rows_budget_removed += int(global_plan.get("proposal_rows_budget_removed", 0))
                        dominated_states_removed += int(global_plan.get("dominated_states_removed", 0))
                        save_hold_recommendations += int(bool(global_plan.get("save_hold_recommended", False)))
                        systems_covered.update(global_plan.get("systems_covered", []))
                        actions_by_system.update({str(key): int(value) for key, value in global_plan.get("actions_by_system", {}).items()})
                        chains_by_system.update({str(key): int(value) for key, value in global_plan.get("chains_by_system", {}).items()})
                        preprune = global_plan.get("gpu_preprune", {}) or {}
                        preprune_full_search_audits += int(preprune.get("full_search_audits", 0))
                        preprune_false_prunes += int(preprune.get("false_prunes", 0))
                        preprune_corrections += int(preprune.get("corrections", 0))
                        preprune_candidates_removed += int(preprune.get("candidates_removed_before_state_transition", 0))
                        equivalent_actions_removed_before_gpu += int(preprune.get("equivalent_actions_removed_before_gpu", 0))
                        prebuilt_root_actions_reused += int(preprune.get("prebuilt_root_actions_reused", 0))
                        proposal_stats = preprune.get("proposal_budget", {}) or {}
                        proposal_budget_raw_candidates += int(proposal_stats.get("raw_candidates", 0))
                        proposal_budget_selected_candidates += int(proposal_stats.get("selected_candidates", 0))
                        proposal_budget_candidates_removed += sum(int(proposal_stats.get(key, 0)) for key in ("unsupported_removed", "save_aliases_removed", "effect_duplicates_removed", "over_budget_removed"))
                        proposal_budget_audits += int(bool(preprune.get("proposal_budget_audited", False)))
                        proposal_budget_false_prunes += int(preprune.get("proposal_budget_false_prunes", 0))
                        proposal_row_budget_audits += int(preprune.get("proposal_row_budget_audits", 0))
                        proposal_row_budget_false_prunes += int(preprune.get("proposal_row_budget_false_prunes", 0))
                        preprune_worker_wait_seconds += float(preprune.get("wait_seconds", 0.0))
                        if global_plan.get("learned_ranker_applied"):
                            learned_candidates_reordered += int(global_plan.get("proposal_rows_created", 0))
                    if not preprune_enabled:
                        async_gpu_scorer.submit(candidate_rows)
                    result["numeric_candidates_submitted"] = len(candidate_rows)
                    result.pop("candidate_features", None)
                    if live_view:
                        live_view.update(render_dashboard())
                write_start = time.perf_counter()
                append_jsonl(results_path, batch_results)
                now = time.monotonic()
                if learned_pruning_enabled and now - last_chart_checkpoint >= chart_checkpoint_seconds:
                    write_checkpoint()
                    last_chart_checkpoint = now
                log_write_time += time.perf_counter() - write_start
                batch_results = []
                if live_view:
                    live_view.update(render_dashboard())
                if time.monotonic() >= deadline:
                    break
                if index >= len(pending) and keep_generating and not stop_when_exhausted and time.monotonic() < deadline and (max_profiles is None or processed < max_profiles):
                    if materialize_profiles == "on_demand":
                        if compact_profile_batch is None or compact_profile_offset >= len(compact_profile_batch.get("seeds", [])):
                            compact_profile_batch = profile_producer.get() if profile_producer else profile_generator.numeric_batch(profile_batch_size)
                            compact_profile_offset = 0
                            profile_batches_generated += 1
                            gpu_profile_features_used = gpu_profile_features_used or bool(compact_profile_batch.get("gpu_used"))
                        materialize_count = min(max(worker_count * 4, 100), len(compact_profile_batch["seeds"]) - compact_profile_offset)
                        slice_end = compact_profile_offset + materialize_count
                        compact_slice = {"seeds": compact_profile_batch["seeds"][compact_profile_offset:slice_end], "features": compact_profile_batch["features"][compact_profile_offset:slice_end]}
                        compact_profile_offset = slice_end
                        extra_profiles = profile_generator.materialize(compact_slice)
                    else:
                        extra_profiles = generate_profiles(count=max(batch_size, 100), seed=seed + index + processed, stage="mixed")
                        write_profiles(extra_profiles, profiles_path)
                    profiles.extend(extra_profiles)
                    pending.extend(extra_profiles)
                    if live_view:
                        live_view.update(render_dashboard())
    except KeyboardInterrupt:
        interrupted = True
        debug("interrupt received: finished active worker shutdown; saving optimizer checkpoint")
    except BaseException as exc:
        execution_error = exc
        debug(f"training failure: {type(exc).__name__}: {exc}")
    finally:
        if live_view:
            live_view.update(render_dashboard())
            live_view.stop()
        if profile_producer:
            profile_producer_summary = profile_producer.close()

    if batch_results:
        write_start = time.perf_counter()
        append_jsonl(results_path, batch_results)
        log_write_time += time.perf_counter() - write_start
        debug(f"preserved {len(batch_results)} completed result(s) from the interrupted batch")
        batch_results = []

    shadow_gpu_summary = async_gpu_scorer.close()
    gpu_summary = preprune_service.close() if preprune_service else shadow_gpu_summary
    successful_cuda_batches = int(gpu_summary.get("successful_cuda_scoring_batches", gpu_summary.get("gpu_batches_completed", 0)) or 0)
    actual_gpu_use = bool(successful_cuda_batches > 0 and int(gpu_summary.get("gpu_rows_scored", 0) or 0) > 0)
    gpu_summary["successful_cuda_scoring_batches"] = successful_cuda_batches
    gpu_summary["gpu_actually_used"] = actual_gpu_use
    gpu_summary["gpu_used"] = actual_gpu_use
    gpu_summary["gpu_acceleration_enabled"] = actual_gpu_use
    cpu_gpu_wait_fraction = preprune_worker_wait_seconds / max(0.000001, (time.monotonic() - started) * max(1, worker_count))
    gpu_summary["cpu_gpu_wait_fraction"] = round(min(1.0, cpu_gpu_wait_fraction), 6)
    gpu_summary["cpu_waiting_on_gpu"] = bool(cpu_gpu_wait_fraction >= 0.05)
    if preprune_manager:
        preprune_manager.shutdown()
    learned_ranker.save()
    for cache in [state_value_cache, action_result_cache, chain_result_cache, profile_feature_cache, action_generation_cache, gpu_score_cache]:
        cache.close()
    debug("gpu_final: " + json.dumps(gpu_summary, ensure_ascii=False, sort_keys=True))

    runtime_gpu_diagnostics: dict[str, Any] | None = None
    if execution_error is not None:
        gpu_failed = bool(gpu_summary.get("service_failed"))
        if gpu_failed:
            stage = classify_gpu_ranker_failure(gpu_summary)
            reason = str(gpu_summary.get("failure_reason") or execution_error)
            runtime_gpu_diagnostics = gpu_process_diagnostics()
            runtime_failure = TrainingStartupError(stage, reason, details={
                "profiles_completed": processed,
                "gpu_scoring": gpu_summary,
                "error_type": type(execution_error).__name__,
                "active_gpu_processes": runtime_gpu_diagnostics,
            })
            debug(f"classified CUDA failure as {stage} after {successful_cuda_batches} successful batch(es)")
            debug("gpu_process_diagnostics: " + json.dumps(runtime_gpu_diagnostics, ensure_ascii=False, sort_keys=True))
        else:
            raise execution_error

    tuning = (
        tune_scoring_weights(all_new_results, weights_path)
        if tune_weights and runtime_failure is None else
        {"updated": False, "reason": "runtime_failure" if runtime_failure else "disabled"}
    )
    if learned_pruning_enabled:
        save_chart(assumption_chart, assumption_chart_path)
        write_reports(assumption_chart, prior_report_json, prior_report_md)
    write_checkpoint(completed=bool(not interrupted and runtime_failure is None))
    elapsed_seconds = max(time.monotonic() - started, 0.000001)
    gpu_rows_scored = int(gpu_summary.get("gpu_rows_scored", 0))
    unsupported_count = None
    knowledge = load_knowledge()
    coverage: dict[str, Any] = coverage_report(knowledge, coverage_audit_state(knowledge))
    unsupported_count = len(coverage.get("unsupported_ids", []))
    coverage["systems_observed_this_run"] = sorted(systems_covered)
    coverage["systems_not_observed_this_run"] = sorted(set(MAJOR_SYSTEMS) - systems_covered)
    coverage["systems_not_observed_count"] = len(set(MAJOR_SYSTEMS) - systems_covered)
    observable_real_systems = set(coverage.get("observable_real_data_systems", coverage.get("real_data_systems", [])))
    coverage["real_data_systems_observed_this_run"] = sorted(observable_real_systems & systems_covered)
    coverage["real_data_systems_not_observed_this_run"] = sorted(observable_real_systems - systems_covered)
    if coverage_report_enabled:
        coverage_json = REPORTS_DIR / "coverage" / "inventory_action_coverage.json"
        coverage_md = REPORTS_DIR / "coverage" / "inventory_action_coverage.md"
        coverage_json.parent.mkdir(parents=True, exist_ok=True)
        coverage_json.write_text(json.dumps(coverage, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        coverage_md.write_text(
            "# Inventory Action Coverage\n\n"
            f"- Total known inventory item ids: {coverage['total_known_inventory_item_ids']}\n"
            f"- Supported by action generator: {coverage['total_supported_by_action_generator']}\n"
            f"- Unsupported ids: {len(coverage['unsupported_ids'])}\n"
            f"- Actions generated for audit state: {coverage['actions_generated_for_audit_state']}\n"
            f"- Fully supported systems: {', '.join(coverage['systems_fully_supported']) or 'none'}\n"
            f"- Partially supported systems: {', '.join(coverage['systems_partially_supported']) or 'none'}\n"
            f"- Unsupported systems: {', '.join(coverage['unsupported_systems']) or 'none'}\n",
            encoding="utf-8",
        )
        debug("coverage: " + json.dumps(coverage, ensure_ascii=False, sort_keys=True))
    summary = {
        "profiles_available": len(profiles),
        "profiles_pending_at_start": len(pending),
        "profiles_processed": processed,
        "profile_count_limit": max_profiles,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "cpu_profiles_per_second": round(_rate(processed, elapsed_seconds), 3),
        "profiles_per_second": round(_rate(processed, elapsed_seconds), 3),
        "full_mode_profiles_per_second": round(_rate(processed, elapsed_seconds), 3) if not fast_throughput else 0.0,
        "actions_tested": actions,
        "actions_per_second": round(_rate(actions, elapsed_seconds), 3),
        "actions_generated_per_second": round(_rate(inventory_actions_generated, elapsed_seconds), 3),
        "total_actions_generated": inventory_actions_generated,
        "total_actions_simulated": chain_actions_simulated,
        "deep_chain_transitions_simulated": chain_actions_simulated,
        "chain_states_produced": chain_states_produced,
        "chain_states_per_second": round(_rate(chain_states_produced, elapsed_seconds), 3),
        "chains_scored": chains_scored,
        "chains_pruned": chains_pruned,
        "chains_skipped": chains_skipped,
        "standalone_chain_runs_skipped": standalone_chain_runs_skipped,
        "chain_simulation_reason": chain_simulation_reason,
        "chains_per_second": round(_rate(chain_actions_simulated, elapsed_seconds), 3),
        "global_chains_considered": global_chains_considered,
        "raw_proposal_rows": raw_proposal_rows,
        "proposal_rows_created": proposal_rows_created,
        "proposal_rows_budget_removed": proposal_rows_budget_removed,
        "state_rebuilds_avoided_before_transition": max(0, raw_proposal_rows - chain_states_produced),
        "cpu_candidate_seconds": round(float(performance_totals.get("cpu_candidate_seconds", 0.0)), 6),
        "global_planner_seconds": round(float(performance_totals.get("global_planner_seconds", 0.0)), 6),
        "state_copy_count": int(performance_totals.get("state_copy_count", 0)),
        "state_rebuild_count": int(performance_totals.get("state_rebuild_count", 0)),
        "state_copy_seconds": round(float(performance_totals.get("state_copy_seconds", 0.0)), 6),
        "candidate_row_creation_seconds": round(float(performance_totals.get("candidate_row_creation_seconds", 0.0)), 6),
        "numeric_feature_creation_seconds": round(float(performance_totals.get("numeric_feature_creation_seconds", 0.0)), 6),
        "state_transition_and_hashing_seconds": round(float(performance_totals.get("state_transition_and_hashing_seconds", 0.0)), 6),
        "duplicate_candidates_removed": int(performance_totals.get("duplicate_candidates_removed", 0)),
        "useful_top_k_rate": round(float(performance_totals.get("useful_topk_rate", 0.0)) / max(1, global_planner_profiles), 6),
        "candidate_waste_by_system": dict(candidate_waste_by_system.most_common()),
        "dominated_states_removed": dominated_states_removed,
        "save_hold_recommendations": save_hold_recommendations,
        "chain_simulator_profiles": chain_simulator_profiles,
        "global_planner_profiles": global_planner_profiles,
        "deep_searches_per_second": round(_rate(chain_simulator_profiles + global_planner_profiles, elapsed_seconds), 3),
        "shallow_searches": max(0, processed - max(chain_simulator_profiles, global_planner_profiles)),
        "shallow_searches_per_second": round(_rate(max(0, processed - max(chain_simulator_profiles, global_planner_profiles)), elapsed_seconds), 3),
        "average_actions_per_profile": round(inventory_actions_generated / processed, 3) if processed else 0,
        "average_chains_per_profile": round(chain_actions_simulated / processed, 3) if processed else 0,
        "average_chain_depth": chain_depth,
        "chain_depth": chain_depth,
        "fast_throughput": fast_throughput,
        "chain_profile_interval": chain_profile_interval,
        "global_profile_interval": global_profile_interval,
        "learned_pruning": learned_pruning_mode,
        "learned_ranker": learned_ranker_enabled,
        "ranker_device": resolved_device if learned_ranker_enabled else "not_used",
        "learned_ranker_updates": learned_ranker.updates,
        "learned_ranker_samples": learned_ranker.samples,
        "learned_ranker_checkpoint_path": str(ranker_checkpoint_path),
        "exploration_rate": exploration_rate,
        "audit_full_search_interval": audit_full_search_interval,
        "audit_full_search_rate": audit_full_search_rate,
        "full_search_audits": full_search_audits,
        "false_prunes": false_prunes,
        "false_prune_rate": round(false_prunes / full_search_audits, 6) if full_search_audits else 0,
        "preprune_full_search_audits": preprune_full_search_audits,
        "preprune_false_prunes": preprune_false_prunes,
        "preprune_false_prune_rate": round(preprune_false_prunes / max(1, preprune_full_search_audits + preprune_corrections), 6),
        "preprune_corrections": preprune_corrections,
        "preprune_candidates_removed_before_state_transition": preprune_candidates_removed,
        "equivalent_actions_removed_before_gpu": equivalent_actions_removed_before_gpu,
        "prebuilt_root_actions_reused": prebuilt_root_actions_reused,
        "proposal_budget_raw_candidates": proposal_budget_raw_candidates,
        "proposal_budget_selected_candidates": proposal_budget_selected_candidates,
        "proposal_budget_candidates_removed": proposal_budget_candidates_removed,
        "proposal_budget_audits": proposal_budget_audits,
        "proposal_budget_false_prunes": proposal_budget_false_prunes,
        "proposal_row_budget_audits": proposal_row_budget_audits,
        "proposal_row_budget_false_prunes": proposal_row_budget_false_prunes,
        "cpu_worker_waiting_on_gpu_seconds": round(preprune_worker_wait_seconds, 6),
        "prior_min_samples": prior_min_samples,
        "assumption_chart_path": str(assumption_chart_path),
        "profile_prior_report_json": str(prior_report_json),
        "profile_prior_report_md": str(prior_report_md),
        "assumption_chart_samples": int(assumption_chart.get("total_samples", 0)),
        "learned_pruned_profiles": learned_pruned_profiles,
        "learned_reordered_profiles": learned_reordered_profiles,
        "actions_reordered_by_learned_priors": learned_reordered_profiles,
        "actions_pruned_by_learned_priors": learned_pruned_profiles,
        "learned_candidates_removed": preprune_candidates_removed,
        "learned_candidates_reordered": learned_candidates_reordered,
        "learned_candidates_saved": preprune_candidates_removed + proposal_budget_candidates_removed + proposal_rows_budget_removed,
        "learning_hit_rate": round((learned_pruned_profiles + learned_reordered_profiles) / max(1, processed), 6),
        "profiles_using_learned_priors": learned_pruned_profiles + learned_reordered_profiles,
        "learned_pruning_usage_percent": round((learned_pruned_profiles + learned_reordered_profiles) / processed * 100.0, 3) if processed else 0.0,
        "learned_reordering_usage_percent": round(learned_reordered_profiles / processed * 100.0, 3) if processed else 0.0,
        "learned_hard_pruning_usage_percent": round(learned_pruned_profiles / processed * 100.0, 3) if processed else 0.0,
        "learned_usage_diagnostics": {
            "decision_counts": dict(sorted(learned_prior_decisions.items())),
            "blocked_reasons": dict(sorted(learned_block_reasons.items())),
            "chart_loaded": learned_memory_loaded_from_disk,
            "chart_samples": int(assumption_chart.get("total_samples", 0)),
            "ranker_weights_nonzero": sum(1 for value in learned_ranker.weights if abs(float(value)) > 1e-12),
            "ranker_observe_updates": learned_ranker.updates,
        },
        "pruning_strength": learned_pruning_mode,
        "learned_memory_loaded_from_disk": learned_memory_loaded_from_disk,
        "learned_memory_writes": learned_memory_writes,
        "profiles_learned_from": int(assumption_chart.get("total_samples", 0)),
        "profile_buckets_covered": len(assumption_chart.get("buckets", {}) or {}),
        "archetype_buckets_covered": len(assumption_chart.get("archetype_buckets", {}) or {}),
        "checkpoint_path": str(optimizer_checkpoint_path),
        "learning_memory_path": str(learning_memory_path),
        "interrupted": interrupted,
        "systems_covered": sorted(systems_covered),
        "systems_implemented": sorted(MAJOR_SYSTEMS),
        "actions_by_system": dict(sorted(actions_by_system.items())),
        "chains_by_system": dict(sorted(chains_by_system.items())),
        "systems_not_covered": sorted(set(MAJOR_SYSTEMS) - systems_covered),
        "unsupported_item_count": unsupported_count,
        "inventory_action_coverage_percent": coverage.get("inventory_action_coverage_percent"),
        "item_affordance_coverage_percent": coverage.get("item_affordance_coverage_percent"),
        "systems_missing_data": coverage.get("systems_missing_data", []),
        "systems_fully_supported": coverage.get("systems_fully_supported", []),
        "systems_partially_supported": coverage.get("systems_partially_supported", []),
        "real_data_systems": coverage.get("real_data_systems", []),
        "observable_real_data_systems": coverage.get("observable_real_data_systems", coverage.get("real_data_systems", [])),
        "catalog_only_systems": coverage.get("catalog_only_systems", []),
        "unobservable_system_reasons": coverage.get("unobservable_system_reasons", {}),
        "placeholder_only_systems": coverage.get("placeholder_only_systems", []),
        "missing_item_names": coverage.get("missing_item_names", []),
        "missing_costs": coverage.get("missing_costs", []),
        "missing_unlock_requirements": coverage.get("missing_unlock_requirements", []),
        "missing_chest_contents": coverage.get("missing_chest_contents", []),
        "needs_review_by_system": coverage.get("needs_review_by_system", {}),
        "unsupported_systems": coverage.get("unsupported_systems", []),
        "actions_scored_by_system": coverage.get("actions_scored_by_system", {}),
        "actions_skipped_by_system": coverage.get("actions_skipped_by_system", {}),
        "missing_data_by_system": coverage.get("missing_data_by_system", {}),
        "next_data_needed": coverage.get("next_data_needed", {}),
        "systems_simulated": sorted(systems_covered),
        "systems_scored": sorted(actions_by_system) if gpu_rows_scored else [],
        "combo_mode": combo_mode,
        "allow_exhaustive_small_inventory": allow_exhaustive_small_inventory,
        "prune_dominated_states": prune_dominated_states_enabled,
        "workers": worker_count,
        "device": resolved_device,
        "requested_device": requested_device,
        "selected_device": resolved_device,
        "worker_count": worker_count,
        "cuda_detected": gpu_summary["cuda_detected"],
        "npu": npu_status,
        "profile_generation": {
            "materialize_profiles": materialize_profiles,
            "profile_batch_size": profile_batch_size,
            "numeric_batches_generated": max(profile_batches_generated, int(profile_producer_summary.get("batches_produced", 0))),
            "gpu_profile_features_requested": gpu_profile_features,
            "gpu_profile_features_effective": gpu_profile_features_effective,
            "gpu_profile_features_used": bool(gpu_profile_features_used or profile_producer_summary.get("gpu_used")),
            "cuda_owner": gpu_ownership["cuda_owner"],
            "producer": profile_producer_summary,
        },
        "gpu_acceleration_enabled": gpu_summary["gpu_acceleration_enabled"],
        "gpu_acceleration_reason": gpu_summary["gpu_acceleration_reason"],
        "gpu_operations_planned": gpu_summary["gpu_operations_planned"],
        "gpu_scoring": gpu_summary,
        "gpu_proposal_scoring_coverage_percent": round(min(100.0, int((gpu_summary.get("preprune_rows_by_phase", {}) or {}).get("proposal", gpu_summary.get("gpu_chain_rows_scored", 0))) / proposal_rows_created * 100.0), 3) if proposal_rows_created else 0.0,
        "gpu_scored_chain_coverage_percent": round(min(100.0, int((gpu_summary.get("preprune_rows_by_phase", {}) or {}).get("final_state", 0)) / chain_states_produced * 100.0), 3) if chain_states_produced else 0.0,
        "resume": resume,
        "tuning": tuning,
        "timing": {
            "time_spent_generating_actions": "included in worker elapsed time",
            "time_spent_simulating_chains": "included in worker elapsed time",
            "time_spent_scoring": gpu_summary.get("scoring_elapsed_seconds", 0),
            "time_spent_writing_logs": round(log_write_time, 6),
        },
        "hardware": _hardware_snapshot(),
        "runtime_cache": {
            **dict(runtime_cache_totals),
            "hit_rate": round((runtime_cache_totals["action_hits"] + runtime_cache_totals["state_value_hits"]) / max(1, sum(runtime_cache_totals.values())), 6),
        },
        "persistent_caches": {
            "state_value_cache": state_value_cache.summary(),
            "action_result_cache": action_result_cache.summary(),
            "chain_result_cache": chain_result_cache.summary(),
            "profile_feature_cache": profile_feature_cache.summary(),
            "action_generation_cache": action_generation_cache.summary(),
            "gpu_score_cache": gpu_score_cache.summary(),
        },
        "benchmark_command": (
            f".\\tools\\run_training.ps1 -Minutes {minutes:g} -ProfileCount {max_profiles or 0} -Workers {workers} "
            f"-Device {device} -BatchSize {batch_size} -GpuScore -ComboMode -ChainDepth {chain_depth} "
            f"-BeamSize {beam_size} -MaxActionsPerProfile {max_actions_per_profile} -KeepGenerating "
            f"-LearnedPruning {learned_pruning_mode} -ExplorationRate {exploration_rate:g} "
            f"-AuditFullSearchInterval {audit_full_search_interval} -CoverageReport"
        ),
        "benchmark_configuration": {
            "minutes": minutes, "profile_count": max_profiles, "workers": workers, "selected_workers": worker_count,
            "device": resolved_device, "batch_size": batch_size, "gpu_score": gpu_score,
            "combo_mode": combo_mode, "chain_depth": chain_depth, "beam_size": beam_size,
            "max_actions_per_profile": max_actions_per_profile, "learned_pruning": learned_pruning_mode,
            "exploration_rate": exploration_rate, "audit_full_search_interval": audit_full_search_interval,
        },
    }
    active_rate = float(gpu_summary.get("gpu_active_compute_rows_per_sec", 0.0))
    wall_rate = float(gpu_summary.get("gpu_wall_rows_per_sec", 0.0))
    if processed == 0:
        hardware_bottleneck = "No profiles completed; bottleneck analysis unavailable"
    elif gpu_score and active_rate > max(1.0, wall_rate) * 5:
        hardware_bottleneck = "CPU candidate generation/search is starving the GPU"
    elif float(gpu_summary.get("cpu_gpu_wait_fraction", 0.0)) > 0.05:
        hardware_bottleneck = "GPU scoring throughput"
    else:
        hardware_bottleneck = "balanced or insufficient evidence"
    summary["hardware_bottleneck"] = hardware_bottleneck
    summary["startup_failed"] = False
    summary["runtime_failed"] = bool(runtime_failure is not None and runtime_failure.stage == "gpu_ranker_runtime_failed")
    summary["failure_stage"] = runtime_failure.stage if runtime_failure else None
    summary["failure_reason"] = runtime_failure.reason if runtime_failure else None
    summary["failure_details"] = runtime_failure.details if runtime_failure else {}
    summary["active_gpu_processes"] = runtime_gpu_diagnostics
    summary["recovery_note"] = RECOVERY_NOTE if runtime_failure is not None else None
    summary["partial_results_valid"] = bool(runtime_failure is not None and processed > 0)
    summary["partial_metrics_preserved"] = bool(runtime_failure is not None)
    summary["benchmark_valid"] = bool(processed > 0 and runtime_failure is None)
    summary["profile_status"] = (
        f"{processed} profiles completed before GPU ranker runtime failure"
        if runtime_failure is not None and processed > 0 else
        ("Profiles completed" if processed > 0 else "No profiles completed yet")
    )
    summary["training_eta_seconds"] = max(0.0, round(deadline - time.monotonic(), 3))
    summary["best_chains_found"] = global_planner_profiles
    summary["checkpoint_saves"] = learned_memory_writes + 1
    summary["test_result_status"] = "not_run_by_trainer"
    summary["profiles_tested"] = processed
    summary["systems_covered_count"] = len(systems_covered)
    summary["systems_not_observed"] = sorted(observable_real_systems - systems_covered)
    summary["main_bottleneck"] = hardware_bottleneck
    summary["gpu_idle_percentage"] = gpu_summary.get("gpu_idle_percentage", 0)
    summary["gpu_idle_reason"] = gpu_summary.get("gpu_idle_reason", "unknown")
    summary["gpu_batch_utilization"] = gpu_summary.get("gpu_batch_utilization", 0)
    summary["gpu_wall_rows_per_sec"] = gpu_summary.get("gpu_wall_rows_per_sec", 0)
    summary["gpu_waiting_on_cpu"] = bool(gpu_summary.get("gpu_waiting_on_cpu", False))
    summary["cpu_waiting_on_gpu"] = bool(gpu_summary.get("cpu_waiting_on_gpu", False))
    summary["debug_log_path"] = str(debug_output_path)

    compact_summary = {
        **stable_metrics_summary(summary),
        "full_mode_profiles_per_second": summary["full_mode_profiles_per_second"],
        "systems_covered": len(systems_covered),
        "systems_not_observed_count": len(observable_real_systems - systems_covered),
        "real_data_systems": len(observable_real_systems),
        "catalog_only_systems": coverage.get("catalog_only_systems", []),
        "actions_generated_per_second": summary["actions_generated_per_second"],
        "chain_states_per_second": summary["chain_states_per_second"],
        "gpu_actually_used": bool(gpu_summary.get("gpu_actually_used")),
        "gpu_proposal_scoring_coverage_percent": summary["gpu_proposal_scoring_coverage_percent"],
        "gpu_scored_chain_coverage_percent": summary["gpu_scored_chain_coverage_percent"],
        "best_checkpoint_path": str(optimizer_checkpoint_path),
        "coverage_report_path": str(coverage_output_path),
        "detailed_metrics_file_path": str(metrics_output_path),
        "hardware_report_path": str(hardware_output_path),
        "debug_log_path": str(debug_output_path),
        "test_result_status": summary["test_result_status"],
    }
    atomic_write_json(summary_output_path, compact_summary)
    final_summary_output_path = (
        LATEST_FINAL_SUMMARY_PATH if primary_results
        else summary_output_path.with_name(f"{summary_output_path.stem.replace('_summary', '')}_final_summary.json")
    )
    atomic_write_json(final_summary_output_path, stable_metrics_summary(summary))
    atomic_write_json(metrics_output_path, summary)
    atomic_write_json(coverage_output_path, coverage)
    atomic_write_json(learning_output_path, {"profile_memory": learning_memory_snapshot(assumption_chart), "ranker": learned_ranker.report()})
    atomic_write_json(hardware_output_path, {
        **summary["hardware"], "bottleneck": hardware_bottleneck,
        "gpu": gpu_summary, "npu": npu_status,
        "active_gpu_processes": runtime_gpu_diagnostics,
    })
    debug_output_path.parent.mkdir(parents=True, exist_ok=True)
    debug_text = "\n".join(debug_messages) + "\n"
    debug_output_path.write_text(debug_text, encoding="utf-8")
    if latest_debug_alias is not None:
        latest_debug_alias.parent.mkdir(parents=True, exist_ok=True)
        latest_debug_alias.write_text(debug_text, encoding="utf-8")

    if runtime_failure is not None and logging_mode != "quiet":
        print(f"Optimizer training runtime failed [{runtime_failure.stage}] after {processed} completed profile(s)")
        print(f"Partial metrics: {metrics_output_path}")
        print(f"Run debug log: {debug_output_path}")
    elif logging_mode == "debug":
        print("optimizer training detailed summary")
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    elif logging_mode == "normal":
        print("Optimizer training complete")
        for key, value in compact_summary.items():
            print(f"{key}: {value}")
    if runtime_failure is not None:
        raise runtime_failure
    return summary


def _training_artifact_paths(results_path: Path) -> tuple[Path, Path]:
    if results_path.resolve() == RESULTS_PATH.resolve():
        return LATEST_METRICS_PATH, LATEST_SUMMARY_PATH
    stem = results_path.stem
    return (
        results_path.with_name(f"{stem}_metrics.json"),
        results_path.with_name(f"{stem}_summary.json"),
    )


def run_training(
    *args: Any,
    force_stale_lock: bool = False,
    allow_cpu_fallback: bool = False,
    lock_path: Path | None = None,
    prepare_profile_count: int | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run training behind a strict single-run lock and CUDA preflight."""
    positional = list(args)
    requested_device = str(kwargs.get("device", positional[2] if len(positional) > 2 else "auto"))
    results_path = Path(kwargs.get("results_path", RESULTS_PATH))
    metrics_path, summary_path = _training_artifact_paths(results_path)
    lock = TrainingRunLock(lock_path or (TRAINING_OUTPUTS_DIR / "training.lock"), force_stale=force_stale_lock)
    preflight: dict[str, Any] | None = None
    run_started_wall = time.time()
    try:
        lock.acquire()
        selected_device = requested_device
        if requested_device in {"cuda", "gpu", "auto"}:
            preflight = cuda_preflight()
            if not preflight["passed"]:
                if allow_cpu_fallback:
                    selected_device = "cpu"
                    kwargs["gpu_score"] = False
                else:
                    raise TrainingStartupError("cuda_preflight", str(preflight["message"]), details=preflight)
            else:
                selected_device = "cuda"
        if len(positional) > 2:
            positional[2] = selected_device
        else:
            kwargs["device"] = selected_device
        kwargs["requested_device"] = requested_device
        kwargs["cuda_preflight_passed"] = bool(preflight and preflight.get("passed")) if preflight else None
        if prepare_profile_count is not None:
            profiles_path = Path(kwargs.get("profiles_path", PROFILES_PATH))
            seed = int(kwargs.get("seed", positional[4] if len(positional) > 4 else 20260618))
            generated_profiles = generate_profiles(count=max(1, int(prepare_profile_count)), seed=seed, stage="mixed")
            write_profiles(generated_profiles, profiles_path)
        try:
            result = _run_training_impl(
                *positional,
                **kwargs,
                allow_cpu_fallback=allow_cpu_fallback,
            )
        except GpuRankerStartupError as exc:
            raise TrainingStartupError("gpu_ranker_startup", str(exc), details={"preflight": preflight}) from None
        except TrainingStartupError:
            raise
        except BaseException as exc:
            message = str(exc)
            if requested_device in {"cuda", "gpu", "auto"} and "cuda" in message.lower():
                raise TrainingStartupError(
                    "gpu_ranker_runtime_failed", message,
                    details={"preflight": preflight, "active_gpu_processes": gpu_process_diagnostics()},
                ) from None
            raise

        result["cuda_preflight"] = preflight
        result["cuda_preflight_passed"] = bool(preflight and preflight.get("passed")) if preflight else None
        result["requested_device"] = requested_device
        result["selected_device"] = selected_device
        result["training_lock"] = {"path": str(lock.path), "pid": os.getpid(), "released_on_exit": True}
        atomic_write_json(metrics_path, result)
        if summary_path.exists():
            compact = json.loads(summary_path.read_text(encoding="utf-8"))
            stable = stable_metrics_summary(result)
            for key in (
                "requested_device", "selected_device", "gpu_owner_pid", "worker_count",
                "gpu_rows_submitted", "gpu_rows_scored", "gpu_batch_utilization",
                "gpu_idle_reason", "cpu_waiting_on_gpu", "gpu_waiting_on_cpu",
            ):
                compact[key] = stable[key]
            compact["cuda_preflight_passed"] = bool(preflight and preflight.get("passed")) if preflight else None
            compact["startup_failed"] = False
            compact["benchmark_valid"] = bool(result.get("profiles_processed", 0) > 0)
            compact["profile_status"] = result.get("profile_status")
            atomic_write_json(summary_path, compact)
        final_summary_path = (
            LATEST_FINAL_SUMMARY_PATH if summary_path.resolve() == LATEST_SUMMARY_PATH.resolve()
            else summary_path.with_name(f"{summary_path.stem.replace('_summary', '')}_final_summary.json")
        )
        atomic_write_json(final_summary_path, stable_metrics_summary(result))
        return result
    except TrainingStartupError as exc:
        if exc.stage == "gpu_ranker_runtime_failed":
            if metrics_path.exists() and metrics_path.stat().st_mtime >= run_started_wall:
                failure_record = json.loads(metrics_path.read_text(encoding="utf-8"))
            else:
                failure_record = {
                    "profiles_tested": int(exc.details.get("profiles_completed", 0) or 0),
                    "profiles_processed": int(exc.details.get("profiles_completed", 0) or 0),
                    "gpu_scoring": exc.details.get("gpu_scoring", {}),
                }
            failure_record.update({
                "startup_failed": False,
                "runtime_failed": True,
                "failure_stage": exc.stage,
                "failure_reason": exc.reason,
                "failure_details": exc.details,
                "benchmark_valid": False,
                "partial_results_valid": int(failure_record.get("profiles_processed", 0) or 0) > 0,
                "partial_metrics_preserved": True,
                "cuda_preflight": preflight,
                "cuda_preflight_passed": bool(preflight and preflight.get("passed")),
                "requested_device": requested_device,
                "selected_device": selected_device,
                "active_gpu_processes": exc.details.get("active_gpu_processes") or gpu_process_diagnostics(),
                "recovery_note": RECOVERY_NOTE,
            })
            debug_path_value = failure_record.get("debug_log_path")
            if debug_path_value:
                runtime_debug_path = Path(str(debug_path_value))
            elif results_path.resolve() == RESULTS_PATH.resolve():
                runtime_debug_path = LOGS_DIR / "training" / "runs" / f"trainer_failure_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.log"
            else:
                runtime_debug_path = results_path.with_name(f"{results_path.stem}_debug.log")
            runtime_debug_path.parent.mkdir(parents=True, exist_ok=True)
            if not runtime_debug_path.exists():
                runtime_debug_path.write_text(
                    f"failure_stage: {exc.stage}\nfailure_reason: {exc.reason}\n"
                    + "active_gpu_processes: " + json.dumps(failure_record["active_gpu_processes"], ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
            failure_record["debug_log_path"] = str(runtime_debug_path)
            atomic_write_json(metrics_path, failure_record)
            compact = stable_metrics_summary(failure_record)
            compact.update({
                "detailed_metrics_file_path": str(metrics_path),
                "profile_status": failure_record.get("profile_status", "GPU ranker runtime failure; partial profiles preserved"),
            })
            atomic_write_json(summary_path, compact)
            final_summary_path = (
                LATEST_FINAL_SUMMARY_PATH if summary_path.resolve() == LATEST_SUMMARY_PATH.resolve()
                else summary_path.with_name(f"{summary_path.stem.replace('_summary', '')}_final_summary.json")
            )
            atomic_write_json(final_summary_path, stable_metrics_summary(failure_record))
        elif exc.stage == "run_lock":
            atomic_write_json(metrics_path.with_name(f"{metrics_path.stem}_startup_failure.json"), {
                "startup_failed": True,
                "failure_stage": exc.stage,
                "failure_reason": exc.reason,
                "failure_details": exc.details,
            })
        else:
            failure_record = write_startup_failure(
                metrics_path=metrics_path,
                summary_path=summary_path,
                failure=exc,
                requested_device=requested_device,
                preflight=preflight,
            )
            final_summary_path = (
                LATEST_FINAL_SUMMARY_PATH if summary_path.resolve() == LATEST_SUMMARY_PATH.resolve()
                else summary_path.with_name(f"{summary_path.stem.replace('_summary', '')}_final_summary.json")
            )
            atomic_write_json(final_summary_path, stable_metrics_summary(failure_record))
        raise
    finally:
        lock.release()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train optimizer scoring through synthetic simulations.")
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--workers", default="auto")
    parser.add_argument("--device", choices=["cpu", "cuda", "gpu", "auto"], default="auto")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--seed", type=int, default=20260618)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--gpu-score", action="store_true", help="Use CUDA for numeric batch scoring when enough candidates are available.")
    parser.add_argument("--allow-cpu-fallback", action="store_true", help="Explicitly allow CPU fallback when an explicitly requested CUDA device fails preflight.")
    parser.add_argument("--force-stale-lock", action="store_true", help="Remove an inactive stale training lock after confirming no trainer is running.")
    parser.add_argument("--fresh", action="store_true", help="Ignore previous simulation results and archive old results.")
    parser.add_argument("--no-resume", action="store_true", help="Disable resume skipping.")
    parser.add_argument("--chain-depth", type=int, default=2)
    parser.add_argument("--beam-size", type=int, default=100)
    parser.add_argument("--max-actions-per-profile", type=int, default=500)
    parser.add_argument("--include-saves", action="store_true", default=True)
    parser.add_argument("--include-random-ev", action="store_true", default=True)
    parser.add_argument("--coverage-report", action="store_true")
    parser.add_argument("--combo-mode", action="store_true")
    parser.add_argument("--allow-exhaustive-small-inventory", action="store_true")
    parser.add_argument("--prune-dominated-states", action="store_true", default=True)
    parser.add_argument("--keep-generating", action="store_true")
    parser.add_argument("--fast-throughput", action="store_true", help="Run deep chain/global planning on intervals so profile throughput stays high.")
    parser.add_argument("--chain-profile-interval", type=int, default=1, help="Run chain simulation every N profiles.")
    parser.add_argument("--global-profile-interval", type=int, default=1, help="Run global combo planning every N profiles when combo mode is enabled.")
    parser.add_argument("--learned-pruning", choices=["off", "soft", "normal", "aggressive"], default="off", help="Use the learned profile assumption chart to prune likely bad deep-search paths.")
    parser.add_argument("--exploration-rate", type=float, default=0.08)
    parser.add_argument("--audit-full-search-interval", type=int, default=0)
    parser.add_argument("--audit-full-search-rate", type=float, default=0.0)
    parser.add_argument("--prior-min-samples", type=int, default=20, help="Similar-profile samples needed before learned pruning becomes aggressive.")
    parser.add_argument("--profile-batch-size", type=int, default=65536)
    parser.add_argument("--materialize-profiles", choices=["on_demand", "all"], default="all")
    parser.add_argument("--gpu-profile-features", action="store_true")
    parser.add_argument("--no-tune", action="store_true", help="Do not update scoring weights (useful for profiling/smoke tests).")
    parser.add_argument("--checkpoint-interval", type=float, default=30.0, help="Seconds between atomic optimizer checkpoints.")
    parser.add_argument("--profile-count", type=int, default=None, help="Optional maximum profiles for this run in addition to the time limit.")
    parser.add_argument("--prepare-profile-count", type=int, default=None, help="Generate this many initial profiles after acquiring the training lock and passing preflight.")
    parser.add_argument("--checkpoint-path", type=Path, default=None)
    parser.add_argument("--assumption-chart", type=Path, default=ASSUMPTION_CHART_PATH)
    parser.add_argument("--learned-ranker", dest="learned_ranker", action="store_true", default=True, help="Enable the persistent online linear ranker (default).")
    parser.add_argument("--no-learned-ranker", dest="learned_ranker", action="store_false", help="Disable the persistent online ranker.")
    parser.add_argument("--ranker-device", choices=["cpu", "cuda", "auto"], default="auto")
    parser.add_argument("--live", action="store_true", help="Show a live terminal dashboard while training.")
    parser.add_argument("--no-live", action="store_true", help="Disable the live terminal dashboard.")
    parser.add_argument("--target-duration", type=float, default=None, help="Alias for --minutes when provided.")
    parser.add_argument("--stop-when-exhausted", action="store_true")
    parser.add_argument("--profiles", type=Path, default=PROFILES_PATH)
    parser.add_argument("--results", type=Path, default=RESULTS_PATH)
    parser.add_argument("--logging-mode", choices=["quiet", "normal", "debug", "json-log-to-file"], default="normal")
    parser.add_argument("--verbose", "--debug-logs", action="store_true", help="Print detailed startup and final metrics instead of the compact summary.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    profiles = args.profiles if args.profiles.is_absolute() else ROOT / args.profiles
    results = args.results if args.results.is_absolute() else ROOT / args.results
    try:
        run_training(
            minutes=args.target_duration if args.target_duration is not None else args.minutes,
            workers=args.workers,
            device=args.device,
            resume=args.resume and not args.no_resume,
            seed=args.seed,
            batch_size=args.batch_size,
            gpu_score=args.gpu_score,
            fresh=args.fresh,
            chain_depth=args.chain_depth,
            beam_size=args.beam_size,
            max_actions_per_profile=args.max_actions_per_profile,
            include_saves=args.include_saves,
            include_random_ev=args.include_random_ev,
            coverage_report_enabled=args.coverage_report,
            combo_mode=args.combo_mode,
            allow_exhaustive_small_inventory=args.allow_exhaustive_small_inventory,
            prune_dominated_states_enabled=args.prune_dominated_states,
            keep_generating=args.keep_generating or (args.target_duration is not None),
            stop_when_exhausted=args.stop_when_exhausted,
            live=args.live and not args.no_live,
            fast_throughput=args.fast_throughput,
            chain_profile_interval=args.chain_profile_interval,
            global_profile_interval=args.global_profile_interval,
            learned_pruning=args.learned_pruning,
            exploration_rate=args.exploration_rate,
            audit_full_search_interval=args.audit_full_search_interval,
            audit_full_search_rate=args.audit_full_search_rate,
            prior_min_samples=args.prior_min_samples,
            profile_batch_size=args.profile_batch_size,
            materialize_profiles=args.materialize_profiles,
            gpu_profile_features=args.gpu_profile_features,
            tune_weights=not args.no_tune,
            checkpoint_interval_seconds=args.checkpoint_interval,
            max_profiles=args.profile_count,
            checkpoint_path=(args.checkpoint_path if args.checkpoint_path is None or args.checkpoint_path.is_absolute() else ROOT / args.checkpoint_path),
            assumption_chart_path=args.assumption_chart if args.assumption_chart.is_absolute() else ROOT / args.assumption_chart,
            profiles_path=profiles,
            results_path=results,
            logging_mode="debug" if args.verbose else args.logging_mode,
            learned_ranker_enabled=args.learned_ranker,
            allow_cpu_fallback=args.allow_cpu_fallback,
            force_stale_lock=args.force_stale_lock,
            prepare_profile_count=args.prepare_profile_count,
        )
    except TrainingStartupError as exc:
        if exc.stage == "gpu_ranker_runtime_failed":
            print(f"optimizer training runtime failed [{exc.stage}]: {exc.reason}")
        else:
            print(f"optimizer training startup failed [{exc.stage}]: {exc.reason}")
        if exc.stage in {"cuda_preflight", "gpu_ranker_startup", "gpu_ranker_runtime_failed"}:
            print(f"Recovery: {RECOVERY_NOTE}")
        return 1
    except Exception as exc:
        print(f"optimizer training failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
