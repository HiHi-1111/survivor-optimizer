from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import sys
from types import ModuleType, SimpleNamespace

import pytest

from optimizer.preprune_ranker import GpuRankerStartupError, SharedPrePruneGpuService
from tools import training_startup
from tools.train_optimizer import classify_gpu_ranker_failure, gpu_work_ownership, run_training
from tools.training_startup import TrainingRunLock, TrainingStartupError, cuda_preflight, gpu_process_diagnostics


class FakeTensor:
    shape = (8, 8)

    def __matmul__(self, _other):
        return self


class GoodCuda:
    def is_available(self): return True
    def device_count(self): return 1
    def set_device(self, _index): return None
    def get_device_name(self, _index): return "Test CUDA Device"
    def synchronize(self, _device=None): return None


def test_cuda_preflight_checks_all_required_operations() -> None:
    fake = SimpleNamespace(
        __version__="test",
        cuda=GoodCuda(),
        float32="float32",
        device=lambda value: value,
        ones=lambda *args, **kwargs: FakeTensor(),
        eye=lambda *args, **kwargs: FakeTensor(),
    )
    result = cuda_preflight(torch_module=fake)
    assert result["passed"] is True
    assert result["device_count"] == 1
    assert result["selected_device_name"] == "Test CUDA Device"
    assert result["tensor_allocation_ok"] is True
    assert result["matrix_operation_ok"] is True


def test_cuda_preflight_reports_busy_allocation_cleanly() -> None:
    fake = SimpleNamespace(
        __version__="test",
        cuda=GoodCuda(),
        float32="float32",
        device=lambda value: value,
        ones=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("CUDA-capable device(s) is/are busy or unavailable")),
    )
    result = cuda_preflight(torch_module=fake)
    assert result["passed"] is False
    assert result["tensor_allocation_ok"] is False
    assert "busy or unavailable" in result["error"]


def test_training_lock_blocks_active_process_and_cleans_up(tmp_path: Path) -> None:
    path = tmp_path / "training.lock"
    path.write_text(json.dumps({"pid": os.getpid(), "start_time": "now"}), encoding="utf-8")
    with pytest.raises(TrainingStartupError, match="Another trainer appears active"):
        TrainingRunLock(path).acquire()
    path.unlink()
    with TrainingRunLock(path):
        assert path.exists()
    assert not path.exists()


def test_force_flag_only_removes_inactive_stale_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "training.lock"
    path.write_text(json.dumps({"pid": 999999, "start_time": "old"}), encoding="utf-8")
    monkeypatch.setattr(training_startup, "_pid_is_active", lambda _pid: False)
    with pytest.raises(TrainingStartupError, match="Stale training lock"):
        TrainingRunLock(path).acquire()
    with TrainingRunLock(path, force_stale=True):
        assert path.exists()
    assert not path.exists()


def test_ranker_startup_handshake_does_not_claim_gpu_use(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_torch = ModuleType("torch")
    fake_torch.cuda = GoodCuda()
    fake_torch.float32 = "float32"
    fake_torch.tensor = lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("CUDA-capable device(s) is/are busy or unavailable")
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    class ImmediateThread:
        def __init__(self, *, target, **_kwargs): self.target = target
        def start(self): self.target()
        def join(self, timeout=None): return None
        def is_alive(self): return False
    service = SharedPrePruneGpuService(queue.Queue(), {}, enabled=True, thread_factory=ImmediateThread)
    with pytest.raises(GpuRankerStartupError):
        service.start()
    snapshot = service.close()
    assert "busy or unavailable" in " ".join(snapshot["errors"])
    assert snapshot["gpu_initialized"] is False
    assert snapshot["gpu_pipeline_active"] is False
    assert snapshot["gpu_actually_used"] is False
    assert snapshot["successful_cuda_scoring_batches"] == 0


def test_failed_preflight_writes_minimal_non_benchmark_metrics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profiles = tmp_path / "profiles.jsonl"
    results = tmp_path / "results.jsonl"
    lock = tmp_path / "training.lock"
    profiles.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr("tools.train_optimizer.cuda_preflight", lambda: {
        "passed": False,
        "message": "CUDA preflight failed: device busy",
        "error": "device busy",
    })
    with pytest.raises(TrainingStartupError) as caught:
        run_training(
            minutes=0.01, workers="1", device="cuda", resume=False, seed=1, batch_size=1,
            profiles_path=profiles, results_path=results, lock_path=lock,
        )
    assert caught.value.stage == "cuda_preflight"
    metrics = json.loads((tmp_path / "results_metrics.json").read_text(encoding="utf-8"))
    assert metrics["startup_failed"] is True
    assert metrics["failure_stage"] == "cuda_preflight"
    assert metrics["profiles_tested"] == 0
    assert metrics["profile_status"] == "No profiles completed yet"
    assert metrics["benchmark_valid"] is False
    assert metrics["gpu_scoring"]["gpu_actually_used"] is False
    assert "hardware_bottleneck" not in metrics
    assert "coverage" not in metrics
    final_summary = json.loads((tmp_path / "results_final_summary.json").read_text(encoding="utf-8"))
    assert final_summary["startup_failed"] is True
    assert final_summary["profiles_tested"] == 0
    assert final_summary["cuda_preflight_passed"] is False
    assert not lock.exists()


def test_auto_requires_preflight_or_explicit_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    results = tmp_path / "auto_results.jsonl"
    lock = tmp_path / "training.lock"
    monkeypatch.setattr("tools.train_optimizer.cuda_preflight", lambda: {
        "passed": False, "message": "CUDA preflight failed: unavailable",
    })
    with pytest.raises(TrainingStartupError) as caught:
        run_training(
            minutes=0.01, workers="1", device="auto", resume=False, seed=1, batch_size=1,
            results_path=results, lock_path=lock,
        )
    assert caught.value.stage == "cuda_preflight"
    metrics = json.loads((tmp_path / "auto_results_metrics.json").read_text(encoding="utf-8"))
    assert metrics["requested_device"] == "auto"
    assert metrics["selected_device"] == "unavailable"
    assert metrics["cuda_preflight_passed"] is False


def test_auto_explicit_fallback_selects_cpu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr("tools.train_optimizer.cuda_preflight", lambda: {
        "passed": False, "message": "CUDA preflight failed: unavailable",
    })

    def fake_run(*_args, **kwargs):
        captured.update(kwargs)
        return {"profiles_processed": 0, "gpu_scoring": {}, "device": kwargs["device"]}

    monkeypatch.setattr("tools.train_optimizer._run_training_impl", fake_run)
    result = run_training(
        minutes=0.01, workers="1", device="auto", resume=False, seed=1, batch_size=1,
        results_path=tmp_path / "fallback.jsonl", lock_path=tmp_path / "training.lock",
        gpu_score=True, allow_cpu_fallback=True,
    )
    assert captured["device"] == "cpu"
    assert captured["gpu_score"] is False
    assert result["requested_device"] == "auto"
    assert result["selected_device"] == "cpu"


def test_ranker_startup_failure_writes_clean_failure_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    results = tmp_path / "results.jsonl"
    lock = tmp_path / "training.lock"
    monkeypatch.setattr("tools.train_optimizer.cuda_preflight", lambda: {
        "passed": True, "message": "ok", "selected_device_name": "test",
    })
    monkeypatch.setattr(
        "tools.train_optimizer._run_training_impl",
        lambda *args, **kwargs: (_ for _ in ()).throw(GpuRankerStartupError("CUDA ranker startup failed: device busy")),
    )
    with pytest.raises(TrainingStartupError) as caught:
        run_training(
            minutes=0.01, workers="1", device="cuda", resume=False, seed=1, batch_size=1,
            results_path=results, lock_path=lock,
        )
    assert caught.value.stage == "gpu_ranker_startup"
    metrics = json.loads((tmp_path / "results_metrics.json").read_text(encoding="utf-8"))
    assert metrics["failure_stage"] == "gpu_ranker_startup"
    assert metrics["gpu_scoring"]["gpu_pipeline_active"] is False
    assert metrics["gpu_scoring"]["gpu_actually_used"] is False
    assert metrics["gpu_scoring"]["successful_cuda_scoring_batches"] == 0
    assert "Close other training runs" in metrics["recovery_note"]
    assert not lock.exists()


def test_gpu_failure_after_successful_batch_is_runtime_failure() -> None:
    assert classify_gpu_ranker_failure({"successful_cuda_scoring_batches": 1}) == "gpu_ranker_runtime_failed"
    assert classify_gpu_ranker_failure({"successful_cuda_scoring_batches": 0}) == "gpu_ranker_startup"


def test_shared_ranker_is_the_only_cuda_owner() -> None:
    ownership = gpu_work_ownership(gpu_score=True, resolved_device="cuda", gpu_profile_features=True)
    assert ownership == {
        "shared_ranker": True,
        "gpu_profile_features": False,
        "cuda_owner": "shared_preprune_ranker",
    }


def test_runtime_failure_preserves_partial_metrics_and_gpu_processes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    results = tmp_path / "results.jsonl"
    metrics_path = tmp_path / "results_metrics.json"
    lock = tmp_path / "training.lock"
    gpu = {
        "gpu_actually_used": True,
        "gpu_rows_submitted": 23697,
        "gpu_rows_scored": 23697,
        "successful_cuda_scoring_batches": 45,
        "service_failed": True,
        "failure_reason": "CUDA device busy",
    }
    diagnostics = {
        "nvidia_smi_available": True,
        "compute_processes": ["1234, emulator.exe, 2048 MiB"],
        "gpu_summary": ["0, NVIDIA RTX 4070, 4096, 12282, 20"],
    }

    def fail_after_work(*_args, **_kwargs):
        metrics_path.write_text(json.dumps({
            "profiles_processed": 89,
            "profiles_tested": 89,
            "profiles_per_second": 2.0,
            "systems_covered": ["pets"],
            "gpu_scoring": gpu,
            "startup_failed": False,
            "runtime_failed": True,
            "benchmark_valid": False,
            "partial_results_valid": True,
        }), encoding="utf-8")
        raise TrainingStartupError(
            "gpu_ranker_runtime_failed", "CUDA device busy",
            details={"profiles_completed": 89, "gpu_scoring": gpu, "active_gpu_processes": diagnostics},
        )

    monkeypatch.setattr("tools.train_optimizer.cuda_preflight", lambda: {"passed": True, "message": "ok"})
    monkeypatch.setattr("tools.train_optimizer.gpu_process_diagnostics", lambda: diagnostics)
    monkeypatch.setattr("tools.train_optimizer._run_training_impl", fail_after_work)
    with pytest.raises(TrainingStartupError) as caught:
        run_training(
            minutes=0.01, workers="1", device="cuda", resume=False, seed=1, batch_size=1,
            results_path=results, lock_path=lock,
        )
    assert caught.value.stage == "gpu_ranker_runtime_failed"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert metrics["startup_failed"] is False
    assert metrics["runtime_failed"] is True
    assert metrics["profiles_tested"] == 89
    assert metrics["gpu_scoring"]["successful_cuda_scoring_batches"] == 45
    assert metrics["gpu_scoring"]["gpu_rows_scored"] == 23697
    assert metrics["active_gpu_processes"]["compute_processes"]
    assert Path(metrics["debug_log_path"]).exists()
    final_summary = json.loads((tmp_path / "results_final_summary.json").read_text(encoding="utf-8"))
    assert final_summary["runtime_failed"] is True
    assert final_summary["profiles_tested"] == 89
    assert not lock.exists()


def test_gpu_process_diagnostics_captures_process_table(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command, **_kwargs):
        joined = " ".join(command)
        if "--query-gpu" in joined:
            stdout = "0, NVIDIA RTX 4070, 4096, 12282, 20\n"
        elif "--query-compute-apps" in joined:
            stdout = "1234, emulator.exe, 2048 MiB\n"
        else:
            stdout = "NVIDIA-SMI process snapshot"
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(training_startup.subprocess, "run", fake_run)
    report = gpu_process_diagnostics()
    assert report["nvidia_smi_available"] is True
    assert report["gpu_summary"]
    assert report["compute_processes"] == ["1234, emulator.exe, 2048 MiB"]
    assert "process snapshot" in report["nvidia_smi_snapshot"]
