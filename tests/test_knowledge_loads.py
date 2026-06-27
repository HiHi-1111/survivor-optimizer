from optimizer.knowledge_loader import load_knowledge


def test_load_knowledge_works_with_starter_files():
    knowledge = load_knowledge()
    assert "scenarios" in knowledge
    assert {"scenario_1", "scenario_2", "scenario_3"} <= {scenario.id for scenario in knowledge["scenarios"]}
    assert len(knowledge["scenarios"]) >= 10
    assert "stat_buckets" in knowledge
    assert len(knowledge["stat_buckets"]) >= 19


def test_survival_buckets_are_ignored_by_default():
    knowledge = load_knowledge()
    buckets = {bucket.id: bucket for bucket in knowledge["stat_buckets"]}
    assert buckets["hp"].scoring_relevance == ["survival", "ignored_by_default"]
    assert buckets["damage_reduction"].scoring_relevance == ["survival", "ignored_by_default"]
