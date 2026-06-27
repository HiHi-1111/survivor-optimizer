from optimizer.action_generator import generate_core_selector_splits
from optimizer.main import optimize


def sample_player() -> dict:
    return {
        "build_stats": {
            "atk": 500000,
            "crit_rate": 0.75,
            "crit_damage": 2.5,
            "skill_damage": 1.2,
        },
        "inventory": {"core_selector_chests": 3},
        "resources": {
            "astral_core": 1,
            "xeno_core": 0,
            "resonance_chip": 4,
        },
        "goal_scenario": "scenario_1",
    }


def test_generate_core_selector_splits_for_three_chests_returns_ten_combinations():
    splits = generate_core_selector_splits(3)
    assert len(splits) == 10
    assert any(split.allocation == {"astral_core": 3, "xeno_core": 0, "resonance_chip": 0} for split in splits)
    assert any(split.allocation == {"astral_core": 0, "xeno_core": 0, "resonance_chip": 3} for split in splits)


def test_optimize_returns_best_top_options_and_avoid():
    result = optimize(sample_player())
    assert result["best"]
    assert result["top_options"]
    assert result["avoid"]
