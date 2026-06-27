"""Placeholder scoring for V1 recommendations."""

from __future__ import annotations

from dataclasses import dataclass, field

from optimizer.action_generator import CoreSelectorSplit
from optimizer.models import Scenario
from optimizer.player_state import PlayerState
from optimizer.scoring_weights import weight_for

IGNORED_BY_DEFAULT_RELEVANCE = {"survival", "ignored_by_default"}


@dataclass
class ScoredAction:
    action_id: str
    action_type: str
    allocation: dict[str, int]
    total_score: float
    sub_scores: dict[str, float]
    reasons: list[str] = field(default_factory=list)
    confidence: str = "medium"


def _scenario_weight(scenario: Scenario, key: str, default: float = 1.0) -> float:
    return float(scenario.weights.get(key, default))


def should_score_stat(
    scoring_relevance: list[str] | tuple[str, ...] | set[str],
    scenario: Scenario,
) -> bool:
    """Damage scenarios ignore survival-only stats unless survival weighting exists."""
    relevance = set(scoring_relevance)
    survival_weight = _scenario_weight(scenario, "survival_score", 0.0)
    if relevance and relevance <= IGNORED_BY_DEFAULT_RELEVANCE and survival_weight <= 0:
        return False
    return "ignored_by_default" not in relevance or survival_weight > 0


def score_core_selector_result(
    original_state: PlayerState,
    future_state: PlayerState,
    split: CoreSelectorSplit,
    scenario: Scenario,
    scoring_weights: dict | None = None,
) -> ScoredAction:
    allocation = split.allocation
    astral = allocation.get("astral_core", 0)
    xeno = allocation.get("xeno_core", 0)
    resonance = allocation.get("resonance_chip", 0)

    damage_score = astral * 10 + xeno * 8 + resonance * 6
    long_term_score = astral * 7 + xeno * 10 + resonance * 5
    resource_efficiency_score = astral * 6 + xeno * 6 + resonance * 4
    breakpoint_score = 0.0
    reasons: list[str] = []

    if future_state.resources.astral_core >= 2:
        breakpoint_score += 12
        reasons.append("Astral cores reach a possible SS or astral breakpoint.")
    if future_state.resources.xeno_core >= 2:
        breakpoint_score += 8
        reasons.append("Xeno cores reach a useful long-term xeno progression threshold.")
    if resonance >= 3:
        resource_efficiency_score -= 5
        reasons.append("All-in resonance may underperform if it does not unlock a breakpoint.")

    if not reasons:
        reasons.append("This is a baseline resource-value estimate until real breakpoints are added.")

    confidence_score = 5.0
    sub_scores = {
        "damage_score": damage_score,
        "long_term_score": long_term_score,
        "resource_efficiency_score": resource_efficiency_score,
        "breakpoint_score": breakpoint_score,
        "confidence_score": confidence_score,
    }
    scoring_weights = scoring_weights or {"default": {}, "scenarios": {}}
    total = sum(
        _scenario_weight(scenario, key, 0.0) * weight_for(scoring_weights, scenario.id, key) * value
        for key, value in sub_scores.items()
    )

    return ScoredAction(
        action_id=split.id,
        action_type=split.action_type,
        allocation=dict(allocation),
        total_score=round(total, 3),
        sub_scores={key: round(value, 3) for key, value in sub_scores.items()},
        reasons=reasons,
        confidence="medium",
    )


# GLOBAL_PLAN_GUARDRAILS_AUTO_PATCH_V1
try:
    import sys as _survivor_sys
    from optimizer.global_plan_guardrails import patch_module_functions as _survivor_patch_module_functions
    _survivor_patch_module_functions(_survivor_sys.modules[__name__])
except Exception:
    pass

