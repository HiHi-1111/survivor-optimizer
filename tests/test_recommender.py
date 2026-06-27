from optimizer.recommender import rank_recommendations
from optimizer.scorer import ScoredAction


def test_recommender_returns_top_options_and_avoid_options():
    scored = [
        ScoredAction(
            action_id="a",
            action_type="use_core_selector_chest",
            allocation={"astral_core": 1},
            total_score=10,
            sub_scores={},
        ),
        ScoredAction(
            action_id="b",
            action_type="use_core_selector_chest",
            allocation={"xeno_core": 1},
            total_score=5,
            sub_scores={},
        ),
    ]

    result = rank_recommendations(scored)
    assert result["best"].action_id == "a"
    assert result["top_options"]
    assert result["avoid"]
