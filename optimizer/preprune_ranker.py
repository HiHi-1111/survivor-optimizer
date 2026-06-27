"""Cross-process shared-memory ranking before CPU beam pruning.

Workers keep Python candidate objects locally. Only compact float32 matrices
cross the process boundary through shared memory; queue messages contain the
shared-memory name, shape, and an opaque request token.
"""

from __future__ import annotations

from collections import Counter
import queue
from multiprocessing import shared_memory
import os
import threading
import time
import traceback
import uuid
from typing import Any


FEATURE_NAMES = (
    "estimated_final_value", "damage_estimate", "long_term_value", "breakpoint_value",
    "resource_efficiency", "resource_cost", "rarity", "breakpoint_distance",
    "confidence", "data_completeness", "synergy", "save_value",
    "reversible", "system_type", "scenario", "profile_bucket",
)
FEATURE_COUNT = len(FEATURE_NAMES)

# Final-state value dominates. The remaining numeric signals break close ties
# and allow learned weights to influence ordering without overwhelming exact
# marginal-value scoring.
DEFAULT_WEIGHTS = (
    1.0, 0.001, 0.001, 0.001, 0.001, -0.001, 0.0005, -0.001,
    0.0005, 0.0005, 0.001, 0.001, 0.0005, 0.0, 0.0, 0.0,
)

_REQUEST_QUEUE: Any = None
_RESPONSE_MAP: Any = None
_WORKER_ENABLED = False
_WORKER_TIMEOUT = 30.0


class GpuRankerStartupError(RuntimeError):
    """Raised when the shared CUDA ranker cannot initialize cleanly."""


class GpuRankerUnavailableError(RuntimeError):
    """Raised in workers when the shared CUDA ranker stops after startup."""


def initialize_preprune_worker(request_queue: Any, response_map: Any, enabled: bool, timeout: float = 30.0) -> None:
    global _REQUEST_QUEUE, _RESPONSE_MAP, _WORKER_ENABLED, _WORKER_TIMEOUT
    # Spawned search workers are CPU-only. Hiding CUDA here prevents an
    # accidental torch import in optimizer code from creating another CUDA
    # context; the parent-owned shared ranker is the sole CUDA owner.
    os.environ["SURVIVOR_OPTIMIZER_CPU_WORKER"] = "1"
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    _REQUEST_QUEUE = request_queue
    _RESPONSE_MAP = response_map
    _WORKER_ENABLED = bool(enabled and request_queue is not None and response_map is not None)
    _WORKER_TIMEOUT = max(1.0, float(timeout))


def _cpu_topk(rows: list[list[float]], top_k: int) -> tuple[list[int], list[float]]:
    scores = [sum(float(value) * weight for value, weight in zip(row, DEFAULT_WEIGHTS)) for row in rows]
    indices = sorted(range(len(scores)), key=scores.__getitem__, reverse=True)[:top_k]
    return indices, [scores[index] for index in indices]


def rank_shared_candidates(rows: list[list[float]], top_k: int, *, phase: str) -> tuple[list[int], list[float], dict[str, Any]]:
    """Submit rows to the parent CUDA service and return local candidate indices."""
    top_k = max(0, min(int(top_k), len(rows)))
    if not rows or top_k == 0:
        return [], [], {"rows": len(rows), "gpu_used": False, "fallback": "empty", "wait_seconds": 0.0, "phase": phase}
    if not _WORKER_ENABLED:
        indices, scores = _cpu_topk(rows, top_k)
        return indices, scores, {"rows": len(rows), "gpu_used": False, "fallback": "service_disabled", "wait_seconds": 0.0, "phase": phase}

    try:
        import numpy as np
        matrix = np.asarray(rows, dtype=np.float32, order="C")
        if matrix.ndim != 2 or matrix.shape[1] != FEATURE_COUNT:
            raise ValueError(f"expected (*, {FEATURE_COUNT}) feature matrix, got {matrix.shape}")
        memory = shared_memory.SharedMemory(create=True, size=matrix.nbytes)
        shared = np.ndarray(matrix.shape, dtype=np.float32, buffer=memory.buf)
        shared[:] = matrix
        token = f"{os.getpid()}-{uuid.uuid4().hex}"
        started = time.perf_counter()
        _REQUEST_QUEUE.put((token, memory.name, matrix.shape[0], matrix.shape[1], top_k, phase), timeout=_WORKER_TIMEOUT)
        deadline = time.monotonic() + _WORKER_TIMEOUT
        response = None
        while time.monotonic() < deadline:
            response = _RESPONSE_MAP.pop(token, None)
            if response is not None:
                break
            time.sleep(0.001)
        wait_seconds = time.perf_counter() - started
        memory.close()
        memory.unlink()
        if response is None:
            raise TimeoutError("shared pre-prune GPU ranker timed out")
        if response.get("fatal_error"):
            raise GpuRankerUnavailableError(str(response["fatal_error"]))
        indices = [int(value) for value in response.get("indices", [])]
        scores = [float(value) for value in response.get("scores", [])]
        return indices, scores, {
            "rows": len(rows), "gpu_used": bool(response.get("gpu_used")),
            "fallback": response.get("fallback", ""), "wait_seconds": wait_seconds, "phase": phase,
        }
    except GpuRankerUnavailableError:
        try:
            if "memory" in locals():
                memory.close()
                memory.unlink()
        except Exception:
            pass
        raise
    except Exception as exc:
        try:
            if "memory" in locals():
                memory.close()
                memory.unlink()
        except Exception:
            pass
        indices, scores = _cpu_topk(rows, top_k)
        return indices, scores, {"rows": len(rows), "gpu_used": False, "fallback": type(exc).__name__, "wait_seconds": 0.0, "phase": phase}


class SharedPrePruneGpuService:
    """Parent-owned CUDA consumer batching matrices from all CPU workers."""

    def __init__(
        self, request_queue: Any, response_map: Any, *, enabled: bool,
        batch_size: int = 8192, max_batch_rows: int | None = None,
        fill_timeout: float = 0.04, weights: list[float] | None = None, queue_capacity: int = 128,
        allow_cpu_fallback: bool = False,
        thread_factory: Any | None = None,
    ) -> None:
        self.request_queue = request_queue
        self.response_map = response_map
        self.batch_size = max(1, int(batch_size))
        self.max_batch_rows = max(self.batch_size, int(max_batch_rows or self.batch_size * 4))
        self.fill_timeout = max(0.002, float(fill_timeout))
        self.weights = list(weights or DEFAULT_WEIGHTS)
        self.queue_capacity = max(1, int(queue_capacity))
        self.requested = bool(enabled)
        self.enabled = self.requested
        self.allow_cpu_fallback = bool(allow_cpu_fallback)
        self._thread_factory = thread_factory or threading.Thread
        # Strict device preflight has already completed in an isolated process.
        # Do not import torch or probe CUDA outside the scorer execution path.
        self.cuda_available = bool(self.enabled)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._started_at: float | None = None
        self._requests = 0
        self._rows = 0
        self._gpu_rows = 0
        self._batches = 0
        self._compute_seconds = 0.0
        self._batch_sizes: list[int] = []
        self._errors: list[str] = []
        self._phase_rows: Counter[str] = Counter()
        self._idle_seconds: Counter[str] = Counter()
        self._queue_high_water = 0
        self._queue_fill_total = 0.0
        self._queue_samples = 0
        self._queue_starvation_events = 0
        self._batch_request_counts: list[int] = []
        self._service_failed = False
        self._initialized = False
        self._failure_reason = ""
        self._start_calls = 0
        self._thread_start_count = 0
        self._cuda_initialization_count = 0
        self._cuda_owner_thread_id: int | None = None
        self._gpu_owner_pid: int | None = None
        self._cuda_released = False

    def _record_error(self, exc: BaseException) -> None:
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
        with self._lock:
            self._errors.append(detail)
            del self._errors[:-20]

    def _respond(self, token: str, payload: dict[str, Any]) -> None:
        try:
            self.response_map[token] = payload
        except (BrokenPipeError, ConnectionError, EOFError, OSError) as exc:
            self._record_error(exc)
            self._service_failed = True
            self._stop.set()

    def _fail_queued_requests(self, reason: str) -> None:
        while True:
            try:
                request = self.request_queue.get_nowait()
            except (queue.Empty, AttributeError):
                return
            except (BrokenPipeError, ConnectionError, EOFError, OSError) as exc:
                self._record_error(exc)
                return
            token = str(request[0])
            self._respond(token, {
                "indices": [], "scores": [], "gpu_used": False,
                "fallback": "cuda_service_failed", "fatal_error": reason,
            })

    def start(self) -> None:
        if not self.enabled:
            return
        with self._start_lock:
            self._start_calls += 1
            if self._thread is not None:
                if self._initialized and not self._service_failed:
                    return
                reason = self._failure_reason or "shared CUDA ranker was already started and is unavailable"
                raise GpuRankerStartupError(reason)
            self._started_at = time.perf_counter()
            self._thread = self._thread_factory(target=self._run, name="shared-preprune-gpu-ranker", daemon=True)
            self._thread_start_count += 1
            self._thread.start()
        if not self._ready.wait(timeout=15.0):
            self._failure_reason = "shared CUDA ranker initialization timed out"
            self._service_failed = True
            self._stop.set()
            raise GpuRankerStartupError(self._failure_reason)
        if not self._initialized:
            reason = self._failure_reason or "shared CUDA ranker failed to initialize"
            raise GpuRankerStartupError(reason)

    def _receive_batch(self) -> list[tuple[Any, ...]]:
        wait_started = time.perf_counter()
        try:
            first = self.request_queue.get(timeout=0.05)
        except queue.Empty:
            with self._lock:
                self._idle_seconds["waiting_for_cpu_candidates"] += time.perf_counter() - wait_started
                self._queue_starvation_events += 1
            return []
        except (BrokenPipeError, ConnectionError, EOFError, OSError) as exc:
            self._record_error(exc)
            self._service_failed = True
            self._stop.set()
            return []
        requests = [first]
        rows = int(first[2])
        try:
            queued_requests = int(self.request_queue.qsize())
        except (AttributeError, NotImplementedError):
            queued_requests = 0
        with self._lock:
            self._queue_high_water = max(self._queue_high_water, queued_requests + 1)
            self._queue_fill_total += min(1.0, (queued_requests + 1) / self.queue_capacity)
            self._queue_samples += 1
        deadline = time.perf_counter() + self.fill_timeout
        # Wait only until the configured useful batch size is reached. The old
        # loop waited for max_batch_rows (4x the target), which stalled every
        # synchronous CPU worker for the full fill timeout on most batches.
        while rows < self.batch_size:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break
            try:
                item = self.request_queue.get(timeout=remaining)
            except queue.Empty:
                with self._lock:
                    self._idle_seconds["waiting_for_batch_fill"] += max(0.0, remaining)
                break
            except (BrokenPipeError, ConnectionError, EOFError, OSError) as exc:
                self._record_error(exc)
                self._service_failed = True
                self._stop.set()
                break
            requests.append(item)
            rows += int(item[2])
        # Once a useful batch exists, absorb already-queued work without
        # waiting. This keeps batches large during bursts and returns results
        # promptly when CPU candidate generation is the limiting stage.
        while rows < self.max_batch_rows:
            try:
                item = self.request_queue.get_nowait()
            except (queue.Empty, AttributeError):
                break
            except (BrokenPipeError, ConnectionError, EOFError, OSError) as exc:
                self._record_error(exc)
                self._service_failed = True
                self._stop.set()
                break
            requests.append(item)
            rows += int(item[2])
        return requests

    def _run(self) -> None:
        try:
            import torch
            weight_tensor = torch.tensor(self.weights, dtype=torch.float32, device="cuda")
            import numpy as np
        except BaseException as exc:
            self._record_error(exc)
            self._service_failed = True
            self._failure_reason = f"CUDA ranker startup failed: {exc}"
            self._stop.set()
            # Cleanup remains on the scorer thread; the coordinator never
            # performs a CUDA operation, including failure cleanup.
            self._release_cuda()
            self._ready.set()
            return
        self._initialized = True
        self._cuda_initialization_count += 1
        self._cuda_owner_thread_id = threading.get_ident()
        self._gpu_owner_pid = os.getpid()
        self._ready.set()

        while not self._stop.is_set():
            requests = self._receive_batch()
            if not requests:
                continue
            memories: list[shared_memory.SharedMemory] = []
            arrays: list[Any] = []
            valid_requests: list[tuple[Any, ...]] = []
            try:
                for request in requests:
                    token, name, row_count, column_count, _top_k, _phase = request
                    try:
                        memory = shared_memory.SharedMemory(name=name)
                        view = np.ndarray((int(row_count), int(column_count)), dtype=np.float32, buffer=memory.buf)
                        arrays.append(np.array(view, copy=True))
                        memories.append(memory)
                        valid_requests.append(request)
                    except Exception as exc:
                        self._respond(token, {"indices": [], "scores": [], "gpu_used": False, "fallback": type(exc).__name__})
                if not arrays:
                    continue
                combined = np.concatenate(arrays, axis=0)
                compute_started = time.perf_counter()
                cpu_tensor = torch.from_numpy(combined)
                try:
                    cpu_tensor = cpu_tensor.pin_memory()
                except RuntimeError:
                    pass
                with torch.inference_mode():
                    gpu_values = cpu_tensor.to("cuda", non_blocking=True)
                    gpu_scores = gpu_values @ weight_tensor
                    offset = 0
                    responses: list[tuple[str, list[int], list[float]]] = []
                    for request, array in zip(valid_requests, arrays):
                        token, _name, row_count, _columns, top_k, phase = request
                        if str(phase) == "final_state":
                            # Exact final-state value is column zero. Keeping
                            # final beam selection exact prevents learned tie
                            # breakers from silently removing the best state.
                            segment = gpu_values[offset: offset + int(row_count), 0]
                        else:
                            segment = gpu_scores[offset: offset + int(row_count)]
                        selected_scores, selected_indices = torch.topk(segment, min(int(top_k), int(row_count)), sorted=True)
                        responses.append((token, selected_indices.cpu().tolist(), selected_scores.cpu().tolist()))
                        offset += int(row_count)
                torch.cuda.synchronize()
                elapsed = time.perf_counter() - compute_started
                for token, indices, scores in responses:
                    self._respond(token, {"indices": indices, "scores": scores, "gpu_used": True, "fallback": ""})
                with self._lock:
                    row_total = int(combined.shape[0])
                    self._requests += len(valid_requests)
                    self._rows += row_total
                    self._gpu_rows += row_total
                    self._batches += 1
                    self._compute_seconds += elapsed
                    self._batch_sizes.append(row_total)
                    self._batch_request_counts.append(len(valid_requests))
                    for request in valid_requests:
                        self._phase_rows[str(request[5])] += int(request[2])
            except Exception as exc:
                self._record_error(exc)
                self._service_failed = True
                self._failure_reason = f"CUDA ranker scoring failed: {exc}"
                for request, array in zip(valid_requests, arrays):
                    token, _name, _rows, _columns, top_k, _phase = request
                    if self.allow_cpu_fallback:
                        indices, scores = _cpu_topk(array.tolist(), int(top_k))
                        self._respond(token, {"indices": indices, "scores": scores, "gpu_used": False, "fallback": type(exc).__name__})
                    else:
                        self._respond(token, {"indices": [], "scores": [], "gpu_used": False,
                                              "fallback": type(exc).__name__, "fatal_error": self._failure_reason})
            finally:
                for memory in memories:
                    memory.close()
            if self._service_failed and self.allow_cpu_fallback and not self._stop.is_set():
                self._run_cpu_fallback(np)
                return
            if self._service_failed:
                self._fail_queued_requests(self._failure_reason or "CUDA ranker service failed")
                self._stop.set()
                self._release_cuda()
                return
        self._release_cuda()

    def _release_cuda(self) -> None:
        if self._cuda_released:
            return
        self._cuda_released = True
        try:
            import torch
            if hasattr(torch, "cuda") and hasattr(torch.cuda, "empty_cache"):
                torch.cuda.empty_cache()
        except BaseException as exc:
            self._record_error(exc)

    def _run_cpu_fallback(self, np: Any) -> None:
        """Keep workers alive if CUDA initialization fails in the service thread."""
        while not self._stop.is_set():
            requests = self._receive_batch()
            for token, name, row_count, column_count, top_k, _phase in requests:
                memory: shared_memory.SharedMemory | None = None
                try:
                    memory = shared_memory.SharedMemory(name=name)
                    view = np.ndarray((int(row_count), int(column_count)), dtype=np.float32, buffer=memory.buf)
                    indices, scores = _cpu_topk(np.array(view, copy=True).tolist(), int(top_k))
                    self._respond(token, {"indices": indices, "scores": scores, "gpu_used": False, "fallback": "cuda_service_failed"})
                except BaseException as exc:
                    self._record_error(exc)
                    self._respond(token, {"indices": [], "scores": [], "gpu_used": False, "fallback": type(exc).__name__})
                finally:
                    if memory is not None:
                        memory.close()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            rows = self._rows
            gpu_rows = self._gpu_rows
            batches = self._batches
            compute = self._compute_seconds
            sizes = list(self._batch_sizes)
            errors = list(self._errors)
            phases = dict(self._phase_rows)
            idle = dict(self._idle_seconds)
            requests = self._requests
            queue_high_water = self._queue_high_water
            queue_fill_total = self._queue_fill_total
            queue_samples = self._queue_samples
            queue_starvation_events = self._queue_starvation_events
            batch_request_counts = list(self._batch_request_counts)
        wall = max(0.000001, time.perf_counter() - self._started_at) if self._started_at else 0.000001
        average_batch = sum(sizes) / len(sizes) if sizes else 0.0
        idle_total = sum(idle.values())
        state_seconds = {
            "waiting_for_cpu_candidates": round(idle.get("waiting_for_cpu_candidates", 0.0), 6),
            "waiting_for_batch_fill": round(idle.get("waiting_for_batch_fill", 0.0), 6),
            "scoring": round(compute, 6), "queue_backpressure": 0.0,
            "cpu_pruned_before_gpu": 0.0, "gpu_disabled": round(wall, 6) if not self.enabled else 0.0,
        }
        return {
            "gpu_requested": self.requested, "cuda_available": self.cuda_available, "cuda_detected": self.cuda_available,
            "gpu_initialized": self._initialized, "gpu_pipeline_active": bool(self.enabled and self._initialized and not self._service_failed),
            "gpu_actually_used": gpu_rows > 0, "gpu_acceleration_enabled": gpu_rows > 0,
            "gpu_acceleration_reason": "shared-memory CUDA ranking before beam pruning" if gpu_rows else "pre-prune GPU service did not score rows",
            "gpu_operations_planned": ["shared_memory_preprune_ranking", "cuda_topk"] if self.enabled else [],
            "gpu_used": gpu_rows > 0 and batches > 0, "async_pipeline": bool(self.enabled and self._initialized and not self._service_failed),
            "submitted_rows": rows, "scored_rows": rows, "gpu_rows_submitted": rows,
            "gpu_rows_scored": gpu_rows, "gpu_chain_rows_submitted": rows, "gpu_chain_rows_scored": gpu_rows,
            "scoring_batches": batches, "gpu_batches_completed": batches,
            "successful_cuda_scoring_batches": batches,
            "scoring_elapsed_seconds": round(compute, 6),
            "gpu_active_compute_rows_per_sec": round(gpu_rows / max(compute, 0.000001), 3) if gpu_rows else 0.0,
            "gpu_wall_rows_per_sec": round(gpu_rows / wall, 3) if gpu_rows else 0.0,
            "average_batch_size": round(average_batch, 3), "average_gpu_batch_size": round(average_batch, 3),
            "gpu_batch_utilization": round(min(100.0, average_batch / self.batch_size * 100.0), 3),
            "gpu_idle_percentage": round(min(100.0, idle_total / wall * 100.0), 3),
            "gpu_idle_reasons_seconds": state_seconds,
            "gpu_idle_reason": max(idle, key=idle.get) if idle else ("scoring" if gpu_rows else "gpu_disabled"),
            "gpu_queue_size": int(self.request_queue.qsize()) if self.enabled else 0,
            "gpu_queue_high_water": queue_high_water,
            "gpu_queue_fill_rate": round(queue_fill_total / max(1, queue_samples), 6),
            "gpu_queue_starvation_events": queue_starvation_events,
            "average_requests_per_gpu_batch": round(sum(batch_request_counts) / max(1, len(batch_request_counts)), 3),
            "prepared_gpu_batches": max(0, queue_high_water),
            "preprune_requests": requests, "preprune_rows_by_phase": phases,
            "cpu_waiting_on_gpu": False, "gpu_waiting_on_cpu": bool(self.enabled and self.request_queue.empty()),
            "errors": errors, "service_failed": self._service_failed, "failure_reason": self._failure_reason,
            "start_calls": self._start_calls,
            "thread_start_count": self._thread_start_count,
            "cuda_initialization_count": self._cuda_initialization_count,
            "cuda_owner_thread_id": self._cuda_owner_thread_id,
            "gpu_owner_pid": self._gpu_owner_pid,
            "cuda_released": self._cuda_released,
            "cpu_workers_cuda_isolated": True,
        }

    def close(self) -> dict[str, Any]:
        self._stop.set()
        self._fail_queued_requests("shared CUDA ranker is shutting down")
        if self._thread:
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                self._record_error(RuntimeError("shared pre-prune GPU ranker did not stop within 5 seconds"))
        self._release_cuda()
        return self.snapshot()
