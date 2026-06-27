"""Strict CUDA preflight, single-run locking, and startup-failure metrics."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import multiprocessing as mp
import os
from pathlib import Path
import socket
import subprocess
import sys
import uuid
from typing import Any

from optimizer.training_memory import atomic_write_json


RECOVERY_NOTE = (
    "Close other training runs, check Task Manager or nvidia-smi for GPU users, "
    "restart the terminal if needed, then rerun."
)


def gpu_process_diagnostics(*, timeout: float = 5.0) -> dict[str, Any]:
    """Capture current NVIDIA GPU/process state without changing GPU state."""
    result: dict[str, Any] = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "nvidia_smi_available": False,
        "gpu_summary": [],
        "compute_processes": [],
        "nvidia_smi_snapshot": "",
        "errors": [],
    }

    def run(arguments: list[str]) -> str:
        completed = subprocess.run(
            ["nvidia-smi", *arguments], capture_output=True, text=True,
            timeout=max(1.0, float(timeout)), check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or f"nvidia-smi exited {completed.returncode}")
        result["nvidia_smi_available"] = True
        return completed.stdout.strip()

    try:
        gpu_rows = run([
            "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
            "--format=csv,noheader,nounits",
        ])
        result["gpu_summary"] = [line.strip() for line in gpu_rows.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        result["errors"].append(f"gpu query: {exc}")
    try:
        process_rows = run([
            "--query-compute-apps=pid,process_name,used_gpu_memory",
            "--format=csv,noheader,nounits",
        ])
        result["compute_processes"] = [line.strip() for line in process_rows.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        result["errors"].append(f"process query: {exc}")
    try:
        result["nvidia_smi_snapshot"] = run([])[-16000:]
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        result["errors"].append(f"full snapshot: {exc}")
    return result


class TrainingStartupError(RuntimeError):
    def __init__(self, stage: str, reason: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.stage = stage
        self.reason = reason
        self.details = details or {}


def _cuda_preflight_local(*, device_index: int = 0, torch_module: Any | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "requested_device": "cuda",
        "device_index": int(device_index),
        "torch_imported": False,
        "cuda_available": False,
        "device_count": 0,
        "selected_device_name": None,
        "tensor_allocation_ok": False,
        "matrix_operation_ok": False,
        "passed": False,
    }
    try:
        torch = torch_module
        if torch is None:
            import torch as imported_torch
            torch = imported_torch
        result["torch_imported"] = True
        result["torch_version"] = str(getattr(torch, "__version__", "unknown"))
        result["cuda_available"] = bool(torch.cuda.is_available())
        if not result["cuda_available"]:
            raise RuntimeError("torch.cuda.is_available() returned false")
        result["device_count"] = int(torch.cuda.device_count())
        if result["device_count"] <= device_index:
            raise RuntimeError(f"CUDA device {device_index} is unavailable; detected {result['device_count']} device(s)")
        torch.cuda.set_device(device_index)
        result["selected_device_name"] = str(torch.cuda.get_device_name(device_index))
        device = torch.device(f"cuda:{device_index}")
        left = torch.ones((8, 8), dtype=torch.float32, device=device)
        result["tensor_allocation_ok"] = True
        right = torch.eye(8, dtype=torch.float32, device=device)
        output = left @ right
        torch.cuda.synchronize(device)
        if tuple(output.shape) != (8, 8):
            raise RuntimeError(f"CUDA matrix smoke operation returned unexpected shape {tuple(output.shape)}")
        result["matrix_operation_ok"] = True
        result["passed"] = True
        result["message"] = f"CUDA preflight passed on {result['selected_device_name']}"
        return result
    except BaseException as exc:
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)
        result["message"] = f"CUDA preflight failed: {exc}"
        return result


def _cuda_preflight_process(connection: Any, device_index: int) -> None:
    """Run all CUDA discovery/allocation in an isolated preflight process."""
    try:
        connection.send(_cuda_preflight_local(device_index=device_index))
    except BaseException as exc:
        connection.send({
            "requested_device": "cuda", "device_index": int(device_index),
            "passed": False, "error_type": type(exc).__name__, "error": str(exc),
            "message": f"CUDA preflight failed: {exc}",
        })
    finally:
        connection.close()


def cuda_preflight(
    *, device_index: int = 0, torch_module: Any | None = None, timeout: float = 30.0,
) -> dict[str, Any]:
    """Strict CUDA smoke test without initializing CUDA in the coordinator."""
    if torch_module is not None:
        return _cuda_preflight_local(device_index=device_index, torch_module=torch_module)
    context = mp.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(
        target=_cuda_preflight_process, args=(child, int(device_index)),
        name="cuda-preflight", daemon=False,
    )
    process.start()
    child.close()
    if parent.poll(max(1.0, float(timeout))):
        result = parent.recv()
    else:
        process.terminate()
        result = {
            "requested_device": "cuda", "device_index": int(device_index),
            "passed": False, "error_type": "TimeoutError",
            "error": f"CUDA preflight exceeded {timeout:g} seconds",
            "message": f"CUDA preflight failed: exceeded {timeout:g} seconds",
        }
    parent.close()
    process.join(timeout=5.0)
    result["preflight_pid"] = process.pid
    result["isolated_process"] = True
    return result


def _pid_is_active(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
        if handle:
            kernel32.CloseHandle(handle)
            return True
        # Access denied still means the process exists; invalid parameter means
        # the PID is not active. Unlike os.kill(pid, 0), this is non-signalling.
        return ctypes.get_last_error() == 5
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


class TrainingRunLock:
    def __init__(self, path: Path, *, force_stale: bool = False) -> None:
        self.path = path
        self.force_stale = bool(force_stale)
        self.run_id = uuid.uuid4().hex
        self.acquired = False
        self.payload: dict[str, Any] = {}

    def acquire(self) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, Any] | None = None
        if self.path.exists():
            try:
                existing = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                existing = {"pid": 0, "error": "lock file is unreadable"}
            active = _pid_is_active(int(existing.get("pid", 0) or 0))
            if active:
                raise TrainingStartupError(
                    "run_lock",
                    f"Another trainer appears active (PID {existing.get('pid')}); refusing to overlap runs.",
                    details={"lock_path": str(self.path), "existing_lock": existing, "active": True},
                )
            if not self.force_stale:
                raise TrainingStartupError(
                    "run_lock",
                    f"Stale training lock exists at {self.path}. Remove it or rerun with --force-stale-lock after confirming no trainer is active.",
                    details={"lock_path": str(self.path), "existing_lock": existing, "active": False},
                )
            self.path.unlink(missing_ok=True)

        self.payload = {
            "pid": os.getpid(),
            "run_id": self.run_id,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "hostname": socket.gethostname(),
            "command": [sys.executable, *sys.argv],
        }
        try:
            descriptor = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self.payload, handle, indent=2)
                handle.write("\n")
        except FileExistsError as exc:
            raise TrainingStartupError("run_lock", f"Another trainer acquired {self.path} during startup.") from exc
        self.acquired = True
        return self.payload

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            current = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
            if current.get("run_id") == self.run_id and int(current.get("pid", 0) or 0) == os.getpid():
                self.path.unlink(missing_ok=True)
        finally:
            self.acquired = False

    def __enter__(self) -> "TrainingRunLock":
        self.acquire()
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.release()


def write_startup_failure(
    *, metrics_path: Path, summary_path: Path, failure: TrainingStartupError,
    requested_device: str, preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = {
        "status": "failed",
        "startup_failed": True,
        "failure_stage": failure.stage,
        "failure_reason": failure.reason,
        "failure_details": failure.details,
        "recovery_note": RECOVERY_NOTE if failure.stage in {"cuda_preflight", "gpu_ranker_startup"} else None,
        "requested_device": requested_device,
        "selected_device": "unavailable",
        "cuda_preflight_passed": bool(preflight and preflight.get("passed")),
        "gpu_owner_pid": None,
        "worker_count": 0,
        "gpu_rows_submitted": 0,
        "gpu_rows_scored": 0,
        "profiles_tested": 0,
        "profiles_processed": 0,
        "profile_status": "No profiles completed yet",
        "benchmark_valid": False,
        "cuda_preflight": preflight,
        "gpu_scoring": {
            "gpu_requested": requested_device in {"cuda", "gpu"},
            "gpu_initialized": False,
            "gpu_pipeline_active": False,
            "gpu_actually_used": False,
            "gpu_used": False,
            "successful_cuda_scoring_batches": 0,
            "gpu_owner_pid": None,
            "gpu_rows_submitted": 0,
            "gpu_rows_scored": 0,
            "gpu_batch_utilization": 0.0,
            "gpu_idle_reason": "cuda_preflight_failed",
            "cpu_waiting_on_gpu": False,
            "gpu_waiting_on_cpu": False,
            "errors": [failure.reason],
        },
    }
    atomic_write_json(metrics_path, record)
    atomic_write_json(summary_path, {
        "startup_failed": True,
        "failure_stage": failure.stage,
        "failure_reason": failure.reason,
        "profiles_tested": 0,
        "profile_status": "No profiles completed yet",
        "benchmark_valid": False,
        "detailed_metrics_file_path": str(metrics_path),
    })
    return record
