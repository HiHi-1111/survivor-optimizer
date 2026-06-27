"""Convert profile dictionaries into numeric damage matrices for CPU/CUDA batches."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import numpy as np

from optimizer.damage_engine import estimate_damage_totals
from optimizer.numeric_features import category_code


PROFILE_MATRIX_COLUMNS = (
    "base_damage",
    "gear_multiplier",
    "survivor_multiplier",
    "tech_multiplier",
    "pet_multiplier",
    "collectibles_multiplier",
    "other_multiplier",
    "failure_category_id",
    "hard_example_weight",
)


@dataclass(frozen=True)
class ProfileMatrixBatch:
    matrix: np.ndarray
    columns: tuple[str, ...]
    row_ids: list[str]
    metadata: list[dict[str, Any]]


def _row_id(profile: dict[str, Any], index: int) -> str:
    name = str(profile.get("profile_name") or profile.get("case_id") or index)
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]
    return f"{index}:{digest}"


def _weight_for(profile: dict[str, Any], hard_example_weights: dict[str, float] | None) -> float:
    if not hard_example_weights:
        return 1.0
    category = str(profile.get("failure_category") or profile.get("category") or "")
    case_id = str(profile.get("case_id") or profile.get("profile_name") or "")
    return max(1.0, float(hard_example_weights.get(case_id, hard_example_weights.get(category, 1.0)) or 1.0))


def profiles_to_matrix(
    profiles: list[dict[str, Any]],
    *,
    failure_categories: list[str] | None = None,
    hard_example_weights: dict[str, float] | None = None,
) -> ProfileMatrixBatch:
    """Flatten active player-state damage into one rectangular numeric batch.

    The expensive JSON cleanup and active/equipped/unlocked filtering happens
    here once. CUDA receives only numeric columns and never sees profile strings.
    """
    rows: list[list[float]] = []
    metadata: list[dict[str, Any]] = []
    row_ids: list[str] = []
    categories = failure_categories or []
    for index, profile in enumerate(profiles):
        report = estimate_damage_totals(profile)
        breakdown = report.get("multiplier_breakdown") or {}
        category = categories[index] if index < len(categories) else str(profile.get("category") or profile.get("failure_category") or "unknown")
        weight = _weight_for(profile, hard_example_weights)
        gear = float(breakdown.get("gear", 1.0) or 1.0)
        survivor = float(breakdown.get("survivor", 1.0) or 1.0)
        tech = float(breakdown.get("tech", 1.0) or 1.0)
        pet = float(breakdown.get("pet", 1.0) or 1.0)
        collectibles = float(breakdown.get("collectibles", 1.0) or 1.0)
        known_product = gear * survivor * tech * pet * collectibles
        final_multiplier = float(report.get("final_damage_multiplier", 1.0) or 1.0)
        other = final_multiplier / known_product if known_product > 0 else 1.0
        rows.append(
            [
                float(report.get("base_damage", 1.0) or 1.0),
                gear,
                survivor,
                tech,
                pet,
                collectibles,
                other,
                float(category_code(category)),
                weight,
            ]
        )
        row_ids.append(_row_id(profile, index))
        metadata.append(
            {
                "category": category,
                "total_damage": float(report.get("total_damage", 0.0) or 0.0),
                "final_damage_multiplier": float(report.get("final_damage_multiplier", 1.0) or 1.0),
                "ignored_rows": list(report.get("ignored_inactive_or_future_damage_rows") or []),
            }
        )
    matrix = np.asarray(rows, dtype=np.float64)
    if matrix.size == 0:
        matrix = np.zeros((0, len(PROFILE_MATRIX_COLUMNS)), dtype=np.float64)
    return ProfileMatrixBatch(matrix=matrix, columns=PROFILE_MATRIX_COLUMNS, row_ids=row_ids, metadata=metadata)


def damage_from_matrix_cpu(matrix: np.ndarray) -> np.ndarray:
    if matrix.size == 0:
        return np.zeros((0,), dtype=np.float64)
    values = np.asarray(matrix, dtype=np.float64)
    return values[:, 0] * np.prod(values[:, 1:7], axis=1)
