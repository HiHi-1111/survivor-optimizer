"""Numeric action/state features shared by CPU and CUDA ranking paths."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Any


FEATURE_COLUMNS = (
    "immediate_damage", "long_term_damage", "breakpoint_value", "resource_efficiency",
    "rarity_value", "confidence", "mode_relevance", "chain_reaction_value",
    "profile_stage", "scenario_id", "system_type", "action_type", "resource_costs",
    "damage_gain_estimate", "crit_gain_estimate", "attack_gain_estimate", "long_term_value_estimate",
    "breakpoint_distance", "resource_bottleneck_score", "synergy_score", "save_value",
    "chest_expected_value", "confidence_score", "missing_data_penalty", "source_confidence",
)


@lru_cache(maxsize=512)
def _cached_category_code(value: str) -> float:
    digest = hashlib.sha256(str(value or "unknown").encode("utf-8")).digest()
    return int.from_bytes(digest[:2], "big") / 65535.0


def category_code(value: Any) -> float:
    return _cached_category_code(str(value or "unknown"))


def action_features(
    action: dict[str, Any], *, chain_value: float = 0.0, profile_stage: str = "unknown", scenario_id: str = "unknown"
) -> list[float]:
    confidence = {"missing": 0.0, "low": 0.25, "medium": 0.6, "high": 0.9, "confirmed": 1.0}.get(str(action.get("confidence", "low")), 0.25)
    spent = sum(float(value) for value in (action.get("consumed_items") or {}).values())
    metadata = action.get("metadata", {}) or {}
    warnings = action.get("warnings", []) or []
    damage = float(action.get("expected_damage_delta", 0.0))
    long_term = float(action.get("long_term_value", 0.0))
    breakpoint = float(action.get("breakpoint_value", 0.0))
    return [
        damage, long_term, breakpoint, float(action.get("resource_efficiency", 0.0)),
        -spent, confidence, 1.0, float(chain_value),
        category_code(profile_stage), category_code(scenario_id), category_code(action.get("system")),
        category_code(action.get("action_type")), -spent, damage,
        float(metadata.get("crit_gain_estimate", 0.0)), float(metadata.get("attack_gain_estimate", 0.0)), long_term,
        -float(metadata.get("breakpoint_distance", 0.0)), float(metadata.get("resource_bottleneck_score", 0.0)),
        float(metadata.get("synergy_score", 0.0)), 1.0 if action.get("action_type") == "save_hold" else 0.0,
        float(metadata.get("chest_expected_value", 0.0)), confidence,
        -1.0 if warnings or not action.get("supported", True) else 0.0, confidence,
    ]


def action_feature_matrix(actions: list[dict[str, Any]]) -> list[list[float]]:
    return [action_features(action) for action in actions]


def weight_vector(scoring_weights: dict[str, Any]) -> list[float]:
    default = scoring_weights.get("default", {})
    return [float(default.get(column, 1.0)) for column in FEATURE_COLUMNS]


def score_matrix_cpu(matrix: list[list[float]], weights: list[float]) -> list[float]:
    return [sum(value * weight for value, weight in zip(row, weights)) for row in matrix]
