"""Small persistent online ranker shared by CPU beam search and CUDA scoring."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from optimizer.numeric_features import FEATURE_COLUMNS
from optimizer.training_memory import atomic_write_json


class OnlineLinearRanker:
    def __init__(self, checkpoint_path: Path, *, learning_rate: float = 0.01, enabled: bool = True) -> None:
        self.checkpoint_path = checkpoint_path
        self.learning_rate = float(learning_rate)
        self.enabled = bool(enabled)
        self.weights = [0.0] * len(FEATURE_COLUMNS)
        self.samples = 0
        self.updates = 0
        self.loaded = False
        if self.enabled and checkpoint_path.exists():
            try:
                payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                stored = payload.get("weights", [])
                if len(stored) == len(self.weights):
                    self.weights = [float(value) for value in stored]
                    self.samples = int(payload.get("samples", 0))
                    self.updates = int(payload.get("updates", 0))
                    self.loaded = True
            except (OSError, ValueError, TypeError):
                pass

    def observe(self, rows: list[dict[str, Any]], winner_ids: set[str]) -> bool:
        if not self.enabled or not rows or not winner_ids:
            return False
        positives = [row for row in rows if str(row.get("action_id", "")) in winner_ids]
        negatives = [row for row in rows if str(row.get("action_id", "")) not in winner_ids]
        if not positives or not negatives:
            return False
        positive = self._mean(positives)
        negative = self._mean(negatives[:256])
        delta = [left - right for left, right in zip(positive, negative)]
        norm = math.sqrt(sum(value * value for value in delta)) or 1.0
        rate = self.learning_rate / math.sqrt(1.0 + self.updates / 1000.0)
        self.weights = [max(-1.0, min(1.0, weight + rate * value / norm)) for weight, value in zip(self.weights, delta)]
        self.samples += 1
        self.updates += 1
        return True

    def _mean(self, rows: list[dict[str, Any]]) -> list[float]:
        values = [[float(row.get("features", {}).get(column, 0.0)) for column in FEATURE_COLUMNS] for row in rows]
        return [sum(row[index] for row in values) / len(values) for index in range(len(FEATURE_COLUMNS))]

    def snapshot_weights(self) -> list[float]:
        return list(self.weights) if self.enabled else []

    def save(self) -> None:
        if not self.enabled:
            return
        atomic_write_json(self.checkpoint_path, self.report())

    def report(self) -> dict[str, Any]:
        importance = sorted(
            ({"feature": name, "weight": round(weight, 8), "importance": round(abs(weight), 8)} for name, weight in zip(FEATURE_COLUMNS, self.weights)),
            key=lambda row: row["importance"], reverse=True,
        )
        return {
            "version": 1, "model": "online_linear_pairwise_ranker", "enabled": self.enabled,
            "samples": self.samples, "updates": self.updates, "loaded_from_checkpoint": self.loaded,
            "weights": self.weights, "feature_importance": importance,
            "checkpoint_path": str(self.checkpoint_path),
        }


def learned_score(features: list[float], weights: list[float] | None) -> float:
    if not weights:
        return 0.0
    return sum(value * weight for value, weight in zip(features, weights))
