from optimizer.models import Scenario
from optimizer.scorer import should_score_stat


def test_survival_only_stats_are_not_scored_by_default():
    scenario = Scenario(id="scenario_1", name="Damage", weights={"damage_score": 1.0})
    assert should_score_stat(["survival", "ignored_by_default"], scenario) is False


def test_damage_stats_are_scored_by_default():
    scenario = Scenario(id="scenario_1", name="Damage", weights={"damage_score": 1.0})
    assert should_score_stat(["damage"], scenario) is True


def test_survival_stats_can_be_enabled_by_future_scenario():
    scenario = Scenario(id="survival", name="Survival", weights={"survival_score": 1.0})
    assert should_score_stat(["survival", "ignored_by_default"], scenario) is True
