"""Rule-based recommendation explanations."""

from __future__ import annotations

from optimizer.scorer import ScoredAction


def explain_recommendations(recommendation: dict) -> dict:
    best: ScoredAction | None = recommendation.get("best")
    avoid: list[ScoredAction] = recommendation.get("avoid", [])

    if best is None:
        return {
            "best_move": "No valid action found.",
            "why_it_wins": [],
            "what_it_unlocks": "Unknown.",
            "what_to_avoid": [],
            "confidence": "low",
            "next_focus": "Add inventory and knowledge data.",
        }

    avoid_text = [
        f"Avoid {action.allocation}; score {action.total_score}."
        for action in avoid
        if action.action_id != best.action_id
    ]
    return {
        "best_move": f"Use core selector chests as {best.allocation}.",
        "why_it_wins": best.reasons,
        "what_it_unlocks": "Potential breakpoint value is estimated from current placeholder rules.",
        "what_to_avoid": avoid_text,
        "confidence": best.confidence,
        "next_focus": "Replace placeholder scoring with real Survivor.io breakpoints and stat bucket math.",
    }


# GLOBAL_PLAN_GUARDRAILS_AUTO_PATCH_V1
try:
    import sys as _survivor_sys
    from optimizer.global_plan_guardrails import patch_module_functions as _survivor_patch_module_functions
    _survivor_patch_module_functions(_survivor_sys.modules[__name__])
except Exception:
    pass

