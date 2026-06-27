"""Rank scored optimizer actions."""

from __future__ import annotations

from optimizer.scorer import ScoredAction


def rank_recommendations(scored_actions: list[ScoredAction], top_n: int = 5) -> dict:
    ranked = sorted(scored_actions, key=lambda item: item.total_score, reverse=True)
    avoid = sorted(scored_actions, key=lambda item: item.total_score)[: min(3, len(ranked))]
    return {
        "best": ranked[0] if ranked else None,
        "top_options": ranked[:top_n],
        "avoid": avoid,
        "short_term_winner": ranked[0] if ranked else None,
        "long_term_winner": ranked[0] if ranked else None,
    }
