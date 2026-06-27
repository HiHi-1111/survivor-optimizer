"""Verify that PyTorch can run a real CUDA operation."""

from __future__ import annotations

import sys
import time


def main() -> int:
    try:
        import torch
    except Exception as exc:
        print(f"failure: torch import failed: {exc}")
        return 1

    print(f"torch_version: {torch.__version__}")
    cuda_available = bool(torch.cuda.is_available())
    print(f"cuda_available: {cuda_available}")
    if not cuda_available:
        print("failure: CUDA is not available to PyTorch")
        return 1

    device = torch.device("cuda")
    print(f"gpu_name: {torch.cuda.get_device_name(0)}")
    try:
        start = time.perf_counter()
        a = torch.randn((2048, 2048), device=device)
        b = torch.randn((2048, 2048), device=device)
        c = a @ b
        checksum = float(c.mean().detach().cpu())
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - start
    except Exception as exc:
        print(f"failure: CUDA tensor operation failed: {exc}")
        return 1

    print(f"success: CUDA matrix multiply completed in {elapsed:.3f}s")
    print(f"checksum: {checksum:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
