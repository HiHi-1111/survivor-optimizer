"""Optional real CUDA tensor work for numeric optimizer batches."""

from __future__ import annotations

import time
from typing import Any

from optimizer.numeric_features import score_matrix_cpu
from optimizer.profile_to_matrix_adapter import damage_from_matrix_cpu


class GpuBatchEngine:
    def __init__(self, device: str = "auto") -> None:
        self.requested_device = device
        self.device = "cpu"
        self.cuda_available = False
        self.rows_scored = 0
        self.batches = 0
        self.elapsed_seconds = 0.0
        try:
            import torch
            self.cuda_available = bool(torch.cuda.is_available())
        except Exception:
            self.cuda_available = False
        if device in {"auto", "cuda", "gpu"} and self.cuda_available:
            self.device = "cuda"

    def score(self, matrix: list[list[float]], weights: list[float]) -> tuple[list[float], dict[str, Any]]:
        if not matrix:
            return [], self.stats("empty_batch")
        started = time.perf_counter()
        if self.device == "cuda":
            import torch
            values = torch.tensor(matrix, dtype=torch.float32, device="cuda")
            weight_tensor = torch.tensor(weights, dtype=torch.float32, device="cuda")
            scores = torch.matmul(values, weight_tensor)
            torch.cuda.synchronize()
            output = scores.cpu().tolist()
            used = True
        else:
            output = score_matrix_cpu(matrix, weights)
            used = False
        elapsed = time.perf_counter() - started
        self.rows_scored += len(matrix)
        self.batches += 1
        self.elapsed_seconds += elapsed
        return output, {**self.stats("completed"), "gpu_used": used}

    def score_profile_damage(self, matrix: Any) -> tuple[list[float], dict[str, Any]]:
        """Vectorized current-damage scoring for profile matrix batches."""
        row_count = int(getattr(matrix, "shape", [len(matrix)])[0]) if matrix is not None else 0
        if row_count <= 0:
            return [], self.stats("empty_profile_damage_batch")
        started = time.perf_counter()
        if self.device == "cuda":
            import torch

            values = torch.as_tensor(matrix, dtype=torch.float64, device="cuda")
            damage = values[:, 0] * torch.prod(values[:, 1:7], dim=1)
            torch.cuda.synchronize()
            output = damage.detach().cpu().tolist()
            used = True
        else:
            output = damage_from_matrix_cpu(matrix).tolist()
            used = False
        elapsed = time.perf_counter() - started
        self.rows_scored += row_count
        self.batches += 1
        self.elapsed_seconds += elapsed
        return output, {**self.stats("completed_profile_damage"), "gpu_used": used}

    def rank(self, matrix: list[list[float]], weights: list[float], top_k: int) -> tuple[list[int], dict[str, Any]]:
        scores, stats = self.score(matrix, weights)
        ranked = sorted(range(len(scores)), key=scores.__getitem__, reverse=True)[:max(0, top_k)]
        return ranked, stats

    def rank_grouped(
        self, matrices: list[list[list[float]]], weights: list[float], top_k: int
    ) -> tuple[list[list[int]], dict[str, Any]]:
        """Score and top-k many profile/action matrices in one device batch."""
        row_count = sum(len(matrix) for matrix in matrices)
        max_rows = max((len(matrix) for matrix in matrices), default=0)
        if not matrices or max_rows == 0:
            return [[] for _ in matrices], self.stats("empty_grouped_batch")
        started = time.perf_counter()
        if self.device == "cuda":
            import torch

            zero = [0.0] * len(weights)
            padded = [matrix + [zero] * (max_rows - len(matrix)) for matrix in matrices]
            mask_rows = [[False] * len(matrix) + [True] * (max_rows - len(matrix)) for matrix in matrices]
            values = torch.tensor(padded, dtype=torch.float32, device="cuda")
            mask = torch.tensor(mask_rows, dtype=torch.bool, device="cuda")
            weight_tensor = torch.tensor(weights, dtype=torch.float32, device="cuda")
            scores = torch.matmul(values, weight_tensor).masked_fill(mask, float("-inf"))
            indices = torch.topk(scores, min(max(1, top_k), max_rows), dim=1, sorted=True).indices.cpu().tolist()
            ranked = [[index for index in group if index < len(matrices[p])][:top_k]
                      for p, group in enumerate(indices)]
            torch.cuda.synchronize()
            used = True
        else:
            ranked = []
            for matrix in matrices:
                scores = score_matrix_cpu(matrix, weights)
                ranked.append(sorted(range(len(scores)), key=scores.__getitem__, reverse=True)[:max(0, top_k)])
            used = False
        elapsed = time.perf_counter() - started
        self.rows_scored += row_count
        self.batches += 1
        self.elapsed_seconds += elapsed
        return ranked, {**self.stats("completed"), "gpu_used": used, "profiles_scored": len(matrices)}

    def similarity(self, queries: list[list[float]], buckets: list[list[float]]) -> tuple[list[int], dict[str, Any]]:
        if not queries or not buckets:
            return [], self.stats("empty_similarity_batch")
        started = time.perf_counter()
        if self.device == "cuda":
            import torch
            q = torch.tensor(queries, dtype=torch.float32, device="cuda")
            b = torch.tensor(buckets, dtype=torch.float32, device="cuda")
            similarity = torch.nn.functional.normalize(q, dim=1) @ torch.nn.functional.normalize(b, dim=1).T
            indices = similarity.argmax(dim=1)
            torch.cuda.synchronize()
            result = indices.cpu().tolist()
            used = True
        else:
            result = []
            for query in queries:
                scores = [sum(left * right for left, right in zip(query, bucket)) for bucket in buckets]
                result.append(max(range(len(scores)), key=scores.__getitem__))
            used = False
        self.rows_scored += len(queries)
        self.batches += 1
        self.elapsed_seconds += time.perf_counter() - started
        return result, {**self.stats("completed"), "gpu_used": used}

    def stats(self, idle_reason: str = "waiting_for_numeric_batch") -> dict[str, Any]:
        elapsed = max(self.elapsed_seconds, 1e-9)
        return {
            "selected_device": self.device, "cuda_detected": self.cuda_available,
            "gpu_used": self.device == "cuda" and self.batches > 0,
            "rows_scored": self.rows_scored, "batches": self.batches,
            "elapsed_seconds": round(self.elapsed_seconds, 6),
            "rows_per_second": round(self.rows_scored / elapsed, 3) if self.batches else 0.0,
            "batches_per_second": round(self.batches / elapsed, 3) if self.batches else 0.0,
            "average_batch_size": round(self.rows_scored / self.batches, 3) if self.batches else 0.0,
            "gpu_idle_reason": idle_reason,
        }
