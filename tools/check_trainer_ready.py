"""Read-only trainer readiness check for locks, knowledge, CUDA, and learning."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from optimizer.knowledge_loader import load_knowledge
from optimizer.paths import TRAINING_OUTPUTS_DIR
from tools.inspect_learning import inspect_learning
from tools.training_startup import cuda_preflight


def check_ready(device: str) -> dict:
    checks: dict[str, dict] = {}
    lock_path = TRAINING_OUTPUTS_DIR / "training.lock"
    lock_detail = None
    if lock_path.exists():
        try:
            lock_detail = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            lock_detail = {"error": "lock file is unreadable"}
    checks["training_lock"] = {
        "passed": not lock_path.exists(), "path": str(lock_path), "detail": lock_detail,
        "message": "no overlapping trainer lock" if not lock_path.exists() else "trainer lock exists; verify the recorded process before starting",
    }
    try:
        knowledge = load_knowledge()
        required = {"resources", "survivor_awakenings", "scoring_weights", "scenarios"}
        missing = sorted(required - set(knowledge))
        checks["knowledge"] = {
            "passed": not missing, "missing_sections": missing,
            "message": "required knowledge loaded" if not missing else f"missing knowledge sections: {', '.join(missing)}",
        }
    except Exception as exc:
        checks["knowledge"] = {"passed": False, "message": f"knowledge load failed: {exc}"}
    if device == "cuda":
        checks["cuda"] = cuda_preflight()
    else:
        checks["cuda"] = {"passed": True, "skipped": True, "message": "CUDA preflight skipped for CPU training"}
    learning = inspect_learning()
    checks["learning"] = {
        "passed": bool(learning["healthy_for_safe_reordering"]),
        "message": "learning state is usable for safe reordering" if learning["healthy_for_safe_reordering"] else "learning state is incomplete; inspect tools/inspect_learning.py output",
        "audit": learning["audit"], "ranker": learning["ranker"],
    }
    ready = all(bool(value.get("passed")) for value in checks.values())
    return {"ready": ready, "device": device, "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether optimizer training can start safely.")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cuda")
    args = parser.parse_args()
    report = check_ready(args.device)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
