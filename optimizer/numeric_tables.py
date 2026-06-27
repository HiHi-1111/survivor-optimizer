"""Load compiled source data and encode profiles/actions as rectangular arrays."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
NUMERIC_PATH = ROOT / "knowledge" / "gpu_tables" / "source_pack" / "numeric_tables.json"


@lru_cache(maxsize=1)
def load_numeric_tables() -> dict[str, Any]:
    if not NUMERIC_PATH.exists():
        return {}
    return json.loads(NUMERIC_PATH.read_text(encoding="utf-8"))


def profile_feature_matrix(profiles: list[dict[str, Any]]) -> list[list[float]]:
    matrix = []
    for profile in profiles:
        stats = profile.get("build_stats", {})
        matrix.append([
            float(profile.get("chapter", 0) or 0),
            float(stats.get("atk", 0) or 0),
            float(stats.get("crit_rate", 0) or 0),
            float(stats.get("crit_damage", 0) or 0),
            float(stats.get("skill_damage", 0) or 0),
        ])
    return matrix


def inventory_feature_matrix(profiles: list[dict[str, Any]]) -> list[list[float]]:
    resources = list(load_numeric_tables().get("id_maps", {}).get("resources", {}))
    matrix = []
    for profile in profiles:
        counts: dict[str, float] = {}
        for section in (profile.get("resources", {}), profile.get("inventory", {}).get("items", {})):
            for key, value in section.items():
                if isinstance(value, (int, float)):
                    counts[str(key)] = counts.get(str(key), 0.0) + float(value)
                elif isinstance(value, dict) and isinstance(value.get("quantity"), (int, float)):
                    counts[str(key)] = counts.get(str(key), 0.0) + float(value["quantity"])
        matrix.append([counts.get(resource, 0.0) for resource in resources])
    return matrix


def action_candidate_matrix(candidate_groups: list[list[dict[str, Any]]]) -> list[list[list[float]]]:
    return [
        [[float(candidate["estimated_dps_value"]), float(candidate["resource_cost_penalty"])] for candidate in candidates]
        for candidates in candidate_groups
    ]
