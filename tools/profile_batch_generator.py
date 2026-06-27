"""CPU-only compact seed and numeric profile feature generation."""

from __future__ import annotations

import random
import queue
import threading
from typing import Any

from tools.generate_synthetic_profiles import generate_profile, load_id_pools


PROFILE_FEATURE_COLUMNS = ("stage", "atk_fraction", "crit_fraction", "resource_fraction", "inventory_fraction", "breakpoint_fraction")


class ProfileBatchGenerator:
    def __init__(
        self, seed: int, *, stage: str = "mixed", gpu_features: bool = False,
        device: str = "auto", allow_cpu_fallback: bool = True,
    ) -> None:
        self.seed = int(seed)
        self.stage = stage
        self.gpu_features = gpu_features
        self.device = device
        self.allow_cpu_fallback = bool(allow_cpu_fallback)
        self.pools = load_id_pools()
        self.generated = 0
        self.gpu_used = False

    def numeric_batch(self, count: int) -> dict[str, Any]:
        count = max(0, int(count))
        selected_device = "cpu"
        # CPU producers own profile generation. CUDA is reserved for the one
        # numeric scorer and never used to create profile features.
        feature_rows, seed_rows = self._cpu_numeric(count)
        self.generated += count
        return {"seeds": seed_rows, "features": feature_rows, "device": selected_device, "gpu_used": selected_device == "cuda"}

    def _cpu_numeric(self, count: int) -> tuple[list[list[float]], list[int]]:
        rng = random.Random(self.seed + self.generated)
        return [[rng.random() for _ in PROFILE_FEATURE_COLUMNS] for _ in range(count)], [rng.randrange(2**31 - 1) for _ in range(count)]

    def materialize(self, batch: dict[str, Any]) -> list[dict[str, Any]]:
        profiles = []
        for offset, seed in enumerate(batch.get("seeds", [])):
            rng = random.Random(int(seed))
            profile = generate_profile(self.generated + offset, rng, self.stage, self.pools)
            profile["numeric_profile_features"] = batch.get("features", [])[offset]
            profiles.append(profile)
        return profiles

    def generate(self, count: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        batch = self.numeric_batch(count)
        return self.materialize(batch), {"device": batch["device"], "gpu_used": batch["gpu_used"], "count": count}


class AsyncProfileProducer:
    """Bounded compact-batch producer; it never materializes full profiles."""

    def __init__(self, generator: ProfileBatchGenerator, batch_size: int, queue_size: int = 2) -> None:
        self.generator = generator
        self.batch_size = max(1, int(batch_size))
        self.queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=max(1, queue_size))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.batches_produced = 0
        self.errors: list[str] = []

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="profile-seed-producer", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                batch = self.generator.numeric_batch(self.batch_size)
                while not self._stop.is_set():
                    try:
                        self.queue.put(batch, timeout=0.2)
                        self.batches_produced += 1
                        break
                    except queue.Full:
                        continue
            except Exception as exc:
                self.errors.append(str(exc))
                return

    def get(self, timeout: float = 30.0) -> dict[str, Any]:
        return self.queue.get(timeout=timeout)

    def close(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        return self.stats()

    def stats(self) -> dict[str, Any]:
        return {
            "queue_size": self.queue.qsize(), "queue_capacity": self.queue.maxsize,
            "batches_produced": self.batches_produced, "gpu_used": self.generator.gpu_used,
            "errors": list(self.errors),
            "idle_reason": "queue_full_waiting_for_cpu" if self.queue.full() else "generating_or_waiting",
        }
