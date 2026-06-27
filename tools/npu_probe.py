"""Print usable ONNX/NPU providers without claiming unsupported acceleration."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from optimizer.npu_backend import NpuBackend


def probe() -> dict:
    return NpuBackend().status()


def main() -> int:
    status = probe()
    print(json.dumps(status, indent=2))
    if not status["available"]:
        print("NPU unavailable for this Python path; using CPU/GPU.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
