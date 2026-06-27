"""Training-time device detection.

The runtime optimizer does not depend on PyTorch. This module imports torch only
when the training pipeline asks for GPU-aware work.
"""

from __future__ import annotations

import os


def cuda_available() -> bool:
    if os.environ.get("SURVIVOR_OPTIMIZER_CPU_WORKER") == "1":
        return False
    try:
        import torch
    except Exception:
        return False
    return bool(torch.cuda.is_available())


def resolve_device(requested: str, *, allow_cpu_fallback: bool = False) -> tuple[str, list[str]]:
    """Resolve once using the same strict, isolated CUDA preflight as training."""
    warnings: list[str] = []
    if requested == "cpu":
        return "cpu", warnings
    if requested in {"gpu", "cuda", "auto"}:
        from tools.training_startup import cuda_preflight

        preflight = cuda_preflight()
        if preflight.get("passed"):
            return "cuda", warnings
        message = str(preflight.get("message", "CUDA preflight failed"))
        if allow_cpu_fallback:
            return "cpu", [f"{message}; explicit CPU fallback enabled."]
        raise RuntimeError(f"{message}; refusing CPU fallback without --allow-cpu-fallback")
    raise ValueError(f"Unknown device {requested!r}")


def detect_npu() -> dict[str, object]:
    from optimizer.npu_backend import NpuBackend
    return NpuBackend().status()
