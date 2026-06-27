"""Optional CUDA batch scoring for optimizer training.

This module scores numeric feature vectors only. Rules, JSON, explanations, and
candidate generation stay on CPU.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any

from optimizer.numeric_features import FEATURE_COLUMNS
from optimizer.gpu_batch_engine import GpuBatchEngine
from optimizer.profile_to_matrix_adapter import damage_from_matrix_cpu, profiles_to_matrix
from tools.device_utils import cuda_available

MIN_GPU_CANDIDATES = 512


def score_profile_damage_rows(
    profiles: list[dict[str, Any]],
    *,
    categories: list[str] | None = None,
    use_gpu: bool = False,
    hard_example_weights: dict[str, float] | None = None,
) -> tuple[list[float], dict[str, Any]]:
    batch = profiles_to_matrix(profiles, failure_categories=categories, hard_example_weights=hard_example_weights)
    if not use_gpu:
        scores = damage_from_matrix_cpu(batch.matrix).tolist()
        return scores, {
            "cuda_detected": cuda_available(),
            "gpu_used": False,
            "gpu_rows_submitted": len(profiles),
            "gpu_rows_scored": 0,
            "gpu_batches_scored": 0,
            "matrix_columns": list(batch.columns),
            "adapter_rows": int(batch.matrix.shape[0]),
            "metadata": batch.metadata,
        }
    engine = GpuBatchEngine("cuda")
    scores, stats = engine.score_profile_damage(batch.matrix)
    gpu_used = bool(stats.get("gpu_used"))
    return scores, {
        "cuda_detected": bool(stats.get("cuda_detected")),
        "gpu_used": gpu_used,
        "gpu_rows_submitted": len(profiles),
        "gpu_rows_scored": int(stats.get("rows_scored", 0)) if gpu_used else 0,
        "gpu_batches_scored": int(stats.get("batches", 0)) if gpu_used else 0,
        "gpu_seconds": float(stats.get("elapsed_seconds", 0.0) or 0.0),
        "matrix_columns": list(batch.columns),
        "adapter_rows": int(batch.matrix.shape[0]),
        "metadata": batch.metadata,
        **stats,
    }


def feature_from_action(action: dict[str, Any], chain_steps_applied: int = 0) -> dict[str, float]:
    sub_scores = action.get("sub_scores", {}) if isinstance(action, dict) else {}
    allocation = action.get("allocation", {}) if isinstance(action, dict) else {}
    allocated_resources = sum(float(value) for value in allocation.values() if value)
    features = {column: 0.0 for column in FEATURE_COLUMNS}
    features.update({
        "immediate_damage": float(sub_scores.get("damage_score", 0.0)),
        "long_term_damage": float(sub_scores.get("long_term_score", 0.0)),
        "breakpoint_value": float(sub_scores.get("breakpoint_score", 0.0)),
        "resource_efficiency": float(sub_scores.get("resource_efficiency_score", 0.0)),
        "rarity_value": allocated_resources,
        "confidence": float(sub_scores.get("confidence_score", 0.0)),
        "mode_relevance": 1.0,
        "chain_reaction_value": float(chain_steps_applied),
        "damage_gain_estimate": float(sub_scores.get("damage_score", 0.0)),
        "long_term_value_estimate": float(sub_scores.get("long_term_score", 0.0)),
        "confidence_score": float(sub_scores.get("confidence_score", 0.0)),
        "source_confidence": float(sub_scores.get("confidence_score", 0.0)),
    })
    return features


def candidate_rows_from_recommendation(recommendation: dict[str, Any], chain_steps_applied: int = 0) -> list[dict[str, Any]]:
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    candidates = []
    if recommendation.get("best"):
        candidates.append(recommendation["best"])
    candidates.extend(recommendation.get("top_options", []))
    candidates.extend(recommendation.get("avoid", []))
    for candidate in candidates:
        action_id = str(candidate.get("action_id", ""))
        if not action_id or action_id in seen:
            continue
        seen.add(action_id)
        rows.append({"action_id": action_id, "row_type": "action", "features": feature_from_action(candidate, chain_steps_applied)})
    return rows


def feature_matrix(rows: list[dict[str, Any]]) -> list[list[float]]:
    return [[float(row.get("features", {}).get(column, 0.0)) for column in FEATURE_COLUMNS] for row in rows]


def weight_vector(scoring_weights: dict[str, Any]) -> list[float]:
    default = scoring_weights.get("default", {})
    return [float(default.get(column, 1.0)) for column in FEATURE_COLUMNS]


def score_cpu(rows: list[dict[str, Any]], scoring_weights: dict[str, Any]) -> list[float]:
    matrix = feature_matrix(rows)
    weights = weight_vector(scoring_weights)
    return [sum(value * weight for value, weight in zip(features, weights)) for features in matrix]


def score_cuda(rows: list[dict[str, Any]], scoring_weights: dict[str, Any], batch_size: int) -> tuple[list[float], dict[str, Any]]:
    import torch

    matrix = feature_matrix(rows)
    weights = torch.tensor(weight_vector(scoring_weights), dtype=torch.float32, device="cuda")
    scores: list[float] = []
    batches = 0
    started = time.perf_counter()
    for start in range(0, len(matrix), batch_size):
        batch = torch.tensor(matrix[start : start + batch_size], dtype=torch.float32, device="cuda")
        batch_scores = batch @ weights
        scores.extend(float(value) for value in batch_scores.detach().cpu().tolist())
        batches += 1
    torch.cuda.synchronize()
    elapsed = max(time.perf_counter() - started, 0.000001)
    return scores, {
        "scoring_batches": batches,
        "scoring_elapsed_seconds": elapsed,
        "scoring_batches_per_second": round(batches / elapsed, 3),
        "average_batch_size": round(len(rows) / batches, 3) if batches else 0,
    }


def score_rows(
    rows: list[dict[str, Any]],
    scoring_weights: dict[str, Any],
    selected_device: str,
    gpu_score: bool,
    batch_size: int,
    min_gpu_candidates: int = MIN_GPU_CANDIDATES,
) -> tuple[list[float], dict[str, Any]]:
    cuda_detected = cuda_available()
    if not rows:
        return [], {
            "cuda_detected": cuda_detected,
            "selected_device": selected_device,
            "gpu_acceleration_enabled": False,
            "gpu_acceleration_reason": "No numeric candidates were available to score.",
            "gpu_operations_planned": [],
            "gpu_used": False,
            "scoring_batches": 0,
            "scoring_batches_per_second": 0,
            "average_batch_size": 0,
        }

    wants_cuda = selected_device == "cuda" and gpu_score
    if not wants_cuda:
        scores = score_cpu(rows, scoring_weights)
        return scores, {
            "cuda_detected": cuda_detected,
            "selected_device": selected_device,
            "gpu_acceleration_enabled": False,
            "gpu_acceleration_reason": "GPU scoring was not requested.",
            "gpu_operations_planned": [],
            "gpu_used": False,
            "scoring_batches": 0,
            "scoring_batches_per_second": 0,
            "average_batch_size": 0,
        }

    if not cuda_detected:
        scores = score_cpu(rows, scoring_weights)
        return scores, {
            "cuda_detected": False,
            "selected_device": selected_device,
            "gpu_acceleration_enabled": False,
            "gpu_acceleration_reason": "CUDA is unavailable; used CPU scoring.",
            "gpu_operations_planned": [],
            "gpu_used": False,
            "scoring_batches": 0,
            "scoring_batches_per_second": 0,
            "average_batch_size": 0,
        }

    if len(rows) < min_gpu_candidates:
        scores = score_cpu(rows, scoring_weights)
        return scores, {
            "cuda_detected": True,
            "selected_device": selected_device,
            "gpu_acceleration_enabled": False,
            "gpu_acceleration_reason": f"Only {len(rows)} candidates; minimum GPU batch threshold is {min_gpu_candidates}.",
            "gpu_operations_planned": [],
            "gpu_used": False,
            "scoring_batches": 0,
            "scoring_batches_per_second": 0,
            "average_batch_size": 0,
        }

    scores, stats = score_cuda(rows, scoring_weights, batch_size)
    return scores, {
        "cuda_detected": True,
        "selected_device": selected_device,
        "gpu_acceleration_enabled": True,
        "gpu_acceleration_reason": "CUDA numeric batch scoring enabled.",
        "gpu_operations_planned": ["numeric_feature_batch_scoring"],
        "gpu_used": True,
        **stats,
    }


class AsyncGpuScorer:
    """Background CUDA scorer fed by CPU workers.

    CPU workers produce candidate feature rows. This scorer consumes those rows
    on a separate thread and runs CUDA batches as soon as enough numeric work is
    available. Scores are not used for explanations yet; the purpose is to keep
    the GPU doing the numeric scoring work concurrently with CPU simulation.
    """

    def __init__(
        self,
        scoring_weights: dict[str, Any],
        selected_device: str,
        gpu_score: bool,
        batch_size: int,
        min_gpu_candidates: int = MIN_GPU_CANDIDATES,
        max_flush_latency: float = 1.0,
    ) -> None:
        self.scoring_weights = scoring_weights
        self.selected_device = selected_device
        self.gpu_score = gpu_score
        self.batch_size = max(1, batch_size)
        self.min_gpu_candidates = min_gpu_candidates
        self.max_flush_latency = max(0.05, float(max_flush_latency))
        # The disabled shadow scorer must not probe CUDA in the coordinator.
        self.cuda_detected = bool(gpu_score and selected_device == "cuda" and cuda_available())
        self.enabled = bool(gpu_score and selected_device == "cuda" and self.cuda_detected)
        self._queue: queue.Queue[list[dict[str, Any]] | None] = queue.Queue(maxsize=64)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._submitted_rows = 0
        self._scored_rows = 0
        self._gpu_scored_rows = 0
        self._chain_rows_submitted = 0
        self._chain_rows_scored = 0
        self._batches = 0
        self._elapsed = 0.0
        self._gpu_used = False
        self._errors: list[str] = []
        self._cpu_wait_seconds = 0.0
        self._started_at: float | None = None
        self._initialized = False
        self._weight_tensor: Any = None
        self._learned_weights = [0.0] * len(FEATURE_COLUMNS)
        self._queue_high_water = 0
        self._queue_samples = 0
        self._queue_fill_total = 0.0
        self._gpu_idle_seconds = 0.0
        self._topk_selections = 0

    def start(self) -> None:
        if not self.enabled:
            return
        self._started_at = time.perf_counter()
        self._thread = threading.Thread(target=self._run, name="gpu-numeric-scorer", daemon=True)
        self._thread.start()
        self._initialized = True

    def submit(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        if self.enabled:
            with self._lock:
                self._submitted_rows += len(rows)
                self._chain_rows_submitted += sum(1 for row in rows if row.get("row_type") == "chain")
            started = time.perf_counter()
            self._queue.put(rows)
            with self._lock:
                self._cpu_wait_seconds += time.perf_counter() - started
                queue_size = self._queue.qsize()
                self._queue_high_water = max(self._queue_high_water, queue_size)
                self._queue_samples += 1
                self._queue_fill_total += queue_size / self._queue.maxsize

    def set_learned_weights(self, weights: list[float]) -> None:
        if len(weights) != len(FEATURE_COLUMNS):
            return
        with self._lock:
            self._learned_weights = [float(value) for value in weights]
            self._weight_tensor = None

    def _score_gpu_batch(self, rows: list[dict[str, Any]]) -> None:
        import torch

        started = time.perf_counter()
        matrix = torch.tensor(feature_matrix(rows), dtype=torch.float32)
        if matrix.numel() and torch.cuda.is_available():
            try:
                matrix = matrix.pin_memory()
            except RuntimeError:
                pass
        with self._lock:
            learned_weights = list(self._learned_weights)
            weight_tensor = self._weight_tensor
        if weight_tensor is None:
            combined = [base + learned for base, learned in zip(weight_vector(self.scoring_weights), learned_weights)]
            weight_tensor = torch.tensor(combined, dtype=torch.float32, device="cuda")
            with self._lock:
                self._weight_tensor = weight_tensor
        with torch.inference_mode():
            values = matrix.to("cuda", non_blocking=True)
            scores = values @ weight_tensor
            # Keep ranking and top-k selection on CUDA. Only aggregate metrics
            # cross back to the CPU; the training loop does not need every score.
            top_k = min(64, len(rows))
            if top_k:
                torch.topk(scores, top_k, sorted=False)
        torch.cuda.synchronize()
        elapsed = max(time.perf_counter() - started, 0.000001)
        batches = max(1, (len(rows) + self.batch_size - 1) // self.batch_size)
        with self._lock:
            self._scored_rows += len(rows)
            self._gpu_scored_rows += len(rows)
            self._chain_rows_scored += sum(1 for row in rows if row.get("row_type") == "chain")
            self._batches += batches
            self._elapsed += elapsed
            self._topk_selections += int(bool(rows))
            self._gpu_used = True

    def _run(self) -> None:
        buffer: list[dict[str, Any]] = []
        buffer_started: float | None = None
        while True:
            wait_started = time.perf_counter()
            try:
                item = self._queue.get(timeout=0.05)
            except queue.Empty:
                with self._lock:
                    self._gpu_idle_seconds += time.perf_counter() - wait_started
                old_enough = buffer_started is not None and time.perf_counter() - buffer_started >= self.max_flush_latency
                if len(buffer) >= self.min_gpu_candidates and old_enough:
                    batch = buffer[: self.batch_size]
                    del buffer[: len(batch)]
                    try:
                        self._score_gpu_batch(batch)
                    except Exception as exc:
                        with self._lock:
                            self._errors.append(str(exc))
                        return
                    buffer_started = time.perf_counter() if buffer else None
                continue
            if item is None:
                break
            if not buffer:
                buffer_started = time.perf_counter()
            buffer.extend(item)
            while len(buffer) >= self.batch_size:
                batch = buffer[: self.batch_size]
                del buffer[: self.batch_size]
                try:
                    self._score_gpu_batch(batch)
                except Exception as exc:
                    with self._lock:
                        self._errors.append(str(exc))
                    return
                buffer_started = time.perf_counter() if buffer else None

        if buffer:
            try:
                if self._gpu_used or len(buffer) >= self.min_gpu_candidates:
                    self._score_gpu_batch(buffer)
                else:
                    score_cpu(buffer, self.scoring_weights)
                    with self._lock:
                        self._scored_rows += len(buffer)
            except Exception as exc:
                with self._lock:
                    self._errors.append(str(exc))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            submitted = self._submitted_rows
            scored = self._scored_rows
            gpu_scored = self._gpu_scored_rows
            chain_submitted = self._chain_rows_submitted
            chain_scored = self._chain_rows_scored
            batches = self._batches
            elapsed = max(self._elapsed, 0.000001)
            gpu_used = self._gpu_used
            errors = list(self._errors)
            cpu_wait_seconds = self._cpu_wait_seconds
            queue_high_water = self._queue_high_water
            queue_samples = self._queue_samples
            queue_fill_total = self._queue_fill_total
            gpu_idle_seconds = self._gpu_idle_seconds
            topk_selections = self._topk_selections
            wall_elapsed = max(time.perf_counter() - self._started_at, 0.000001) if self._started_at else 0.000001

        average_batch = 0 if not batches else gpu_scored / batches

        return {
            "gpu_requested": self.gpu_score,
            "cuda_available": self.cuda_detected,
            "cuda_detected": self.cuda_detected,
            "selected_device": self.selected_device,
            "gpu_initialized": self._initialized,
            "gpu_pipeline_active": self.enabled,
            "gpu_actually_used": gpu_used,
            "gpu_acceleration_enabled": gpu_used,
            "gpu_used": gpu_used,
            "async_pipeline": self.enabled,
            "submitted_rows": submitted,
            "scored_rows": scored,
            "gpu_rows_submitted": submitted,
            "gpu_rows_scored": gpu_scored,
            "gpu_chain_rows_submitted": chain_submitted,
            "gpu_chain_rows_scored": chain_scored,
            "queued_rows": max(0, submitted - scored),
            "gpu_queue_size": self._queue.qsize() if self.enabled else 0,
            "gpu_queue_capacity": self._queue.maxsize,
            "gpu_queue_high_water": queue_high_water,
            "gpu_queue_fill_rate": round(queue_fill_total / max(1, queue_samples), 6),
            "scoring_batches": batches,
            "gpu_batches_completed": batches,
            "scoring_elapsed_seconds": 0.0 if not gpu_used else self._elapsed,
            "rows_per_second": 0 if not gpu_used else round(gpu_scored / elapsed, 3),
            "gpu_active_compute_rows_per_sec": 0 if not gpu_used else round(gpu_scored / elapsed, 3),
            "gpu_wall_rows_per_sec": 0 if not gpu_used else round(gpu_scored / wall_elapsed, 3),
            "scoring_batches_per_second": 0 if not gpu_used else round(batches / elapsed, 3),
            "average_batch_size": round(average_batch, 3),
            "average_gpu_batch_size": round(average_batch, 3),
            "gpu_batch_utilization": round(min(100.0, average_batch / self.batch_size * 100.0), 3),
            "gpu_idle_percentage": round(min(100.0, gpu_idle_seconds / wall_elapsed * 100.0), 3),
            "gpu_topk_selections": topk_selections,
            "gpu_idle_reason": "waiting_for_cpu_candidates" if self.enabled and submitted == scored else ("gpu_disabled" if not self.enabled else "scoring_or_queued"),
            "cpu_waiting_on_gpu": bool(cpu_wait_seconds > 0.01 and self._queue.full()),
            "cpu_gpu_queue_wait_seconds": round(cpu_wait_seconds, 6),
            "gpu_waiting_on_cpu": bool(self.enabled and submitted == scored),
            "errors": errors,
        }

    def close(self) -> dict[str, Any]:
        if self.enabled:
            self._queue.put(None)
            if self._thread:
                self._thread.join()

        with self._lock:
            submitted = self._submitted_rows
            scored = self._scored_rows
            gpu_scored = self._gpu_scored_rows
            chain_submitted = self._chain_rows_submitted
            chain_scored = self._chain_rows_scored
            batches = self._batches
            elapsed = max(self._elapsed, 0.000001)
            gpu_used = self._gpu_used
            errors = list(self._errors)
            cpu_wait_seconds = self._cpu_wait_seconds
            queue_high_water = self._queue_high_water
            queue_samples = self._queue_samples
            queue_fill_total = self._queue_fill_total
            gpu_idle_seconds = self._gpu_idle_seconds
            topk_selections = self._topk_selections
            wall_elapsed = max(time.perf_counter() - self._started_at, 0.000001) if self._started_at else 0.000001

        average_batch = 0 if not batches else gpu_scored / batches

        if not self.gpu_score:
            reason = "GPU scoring was not requested."
        elif self.selected_device != "cuda":
            reason = "GPU scoring requested but selected device is CPU."
        elif not self.cuda_detected:
            reason = "CUDA is unavailable; used CPU scoring."
        elif errors:
            reason = f"GPU scoring failed: {errors[0]}"
        elif gpu_used:
            reason = "CUDA numeric batch scoring ran concurrently with CPU simulation."
        elif submitted < self.min_gpu_candidates:
            reason = f"Only {submitted} candidates; minimum GPU batch threshold is {self.min_gpu_candidates}."
        else:
            reason = "No GPU batch was executed."

        return {
            "gpu_requested": self.gpu_score,
            "cuda_available": self.cuda_detected,
            "cuda_detected": self.cuda_detected,
            "selected_device": self.selected_device,
            "gpu_initialized": self._initialized,
            "gpu_pipeline_active": self.enabled,
            "gpu_actually_used": gpu_used,
            "gpu_acceleration_enabled": gpu_used,
            "gpu_acceleration_reason": reason,
            "gpu_operations_planned": ["async_numeric_feature_batch_scoring"] if self.enabled else [],
            "gpu_used": gpu_used,
            "async_pipeline": self.enabled,
            "submitted_rows": submitted,
            "scored_rows": scored,
            "gpu_rows_submitted": submitted,
            "gpu_rows_scored": gpu_scored,
            "gpu_chain_rows_submitted": chain_submitted,
            "gpu_chain_rows_scored": chain_scored,
            "gpu_queue_size": 0,
            "gpu_queue_capacity": self._queue.maxsize,
            "gpu_queue_high_water": queue_high_water,
            "gpu_queue_fill_rate": round(queue_fill_total / max(1, queue_samples), 6),
            "scoring_batches": batches,
            "gpu_batches_completed": batches,
            "scoring_elapsed_seconds": 0.0 if not gpu_used else self._elapsed,
            "rows_per_second": 0 if not gpu_used else round(gpu_scored / elapsed, 3),
            "gpu_active_compute_rows_per_sec": 0 if not gpu_used else round(gpu_scored / elapsed, 3),
            "gpu_wall_rows_per_sec": 0 if not gpu_used else round(gpu_scored / wall_elapsed, 3),
            "scoring_batches_per_second": 0 if not gpu_used else round(batches / elapsed, 3),
            "average_batch_size": round(average_batch, 3),
            "average_gpu_batch_size": round(average_batch, 3),
            "gpu_batch_utilization": round(min(100.0, average_batch / self.batch_size * 100.0), 3),
            "gpu_idle_percentage": round(min(100.0, gpu_idle_seconds / wall_elapsed * 100.0), 3),
            "gpu_topk_selections": topk_selections,
            "gpu_idle_reason": reason if not gpu_used else "completed_cuda_batches",
            "cpu_gpu_queue_wait_seconds": round(cpu_wait_seconds, 6),
            "cpu_gpu_wait_fraction": round(cpu_wait_seconds / wall_elapsed, 6),
            "cpu_waiting_on_gpu": bool(cpu_wait_seconds / wall_elapsed > 0.01),
            "gpu_waiting_on_cpu": False,
            "errors": errors,
        }
