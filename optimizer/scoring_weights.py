"""Load small tunable scoring weights."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCORING_WEIGHTS = {
    "version": 1,
    "default": {
        "immediate_damage": 1.0,
        "long_term_damage": 1.0,
        "breakpoint_value": 1.0,
        "resource_efficiency": 1.0,
        "rarity_value": 1.0,
        "confidence": 1.0,
        "mode_relevance": 1.0,
        "chain_reaction_value": 1.0,
        "damage_score": 1.0,
        "long_term_score": 1.0,
        "resource_efficiency_score": 1.0,
        "breakpoint_score": 1.0,
        "confidence_score": 1.0,
        "mode_relevance_score": 1.0,
        "profile_stage": 0.0,
        "scenario_id": 0.0,
        "system_type": 0.0,
        "action_type": 0.0,
        "resource_costs": 1.0,
        "damage_gain_estimate": 1.0,
        "crit_gain_estimate": 1.0,
        "attack_gain_estimate": 1.0,
        "long_term_value_estimate": 1.0,
        "breakpoint_distance": 1.0,
        "resource_bottleneck_score": 1.0,
        "synergy_score": 1.0,
        "save_value": 1.0,
        "chest_expected_value": 1.0,
        "confidence_score": 1.0,
        "missing_data_penalty": 1.0,
        "source_confidence": 0.5,
    },
    "scenarios": {},
}

WEIGHT_ALIASES = {
    "damage_score": "immediate_damage",
    "long_term_score": "long_term_damage",
    "resource_efficiency_score": "resource_efficiency",
    "breakpoint_score": "breakpoint_value",
    "confidence_score": "confidence",
    "mode_relevance_score": "mode_relevance",
}


def load_scoring_weights(knowledge_dir: Path | str = ROOT / "knowledge") -> dict[str, Any]:
    path = Path(knowledge_dir) / "scoring_weights.json"
    if not path.exists():
        return DEFAULT_SCORING_WEIGHTS.copy()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    merged = DEFAULT_SCORING_WEIGHTS.copy()
    merged.update(data)
    merged.setdefault("default", DEFAULT_SCORING_WEIGHTS["default"])
    merged.setdefault("scenarios", {})
    for key, value in DEFAULT_SCORING_WEIGHTS["default"].items():
        merged["default"].setdefault(key, value)
    for scenario_weights in merged["scenarios"].values():
        if isinstance(scenario_weights, dict):
            for key, value in DEFAULT_SCORING_WEIGHTS["default"].items():
                scenario_weights.setdefault(key, value)
    return merged


def weight_for(scoring_weights: dict[str, Any], scenario_id: str, score_key: str) -> float:
    aliased_key = WEIGHT_ALIASES.get(score_key, score_key)
    default = scoring_weights.get("default", {})
    scenario = scoring_weights.get("scenarios", {}).get(scenario_id, {})
    return float(
        scenario.get(
            score_key,
            scenario.get(aliased_key, default.get(score_key, default.get(aliased_key, 1.0))),
        )
    )
