import json
from pathlib import Path

from optimizer.knowledge_loader import load_knowledge
from optimizer.main import optimize
from optimizer.profile_priors import CANONICAL_SYSTEMS, add_observation, new_chart, profile_tags, recommend_training_plan
from optimizer.training_memory import atomic_write_json, learning_memory_snapshot, optimizer_checkpoint
from optimizer.learned_ranker import OnlineLinearRanker
from tools.generate_synthetic_profiles import ARCHETYPES, generate_profiles


def test_required_scenarios_exist_and_change_weights():
    scenarios = {scenario.id: scenario for scenario in load_knowledge()["scenarios"]}
    required = {
        "scenario_1", "scenario_2", "scenario_3", "scenario_event_shop", "scenario_f2p_gems",
        "scenario_chapter_push", "scenario_clan_shop", "scenario_pet_xeno", "scenario_gear_ss",
        "scenario_collectibles",
    }
    assert required <= set(scenarios)
    assert scenarios["scenario_1"].weights != scenarios["scenario_f2p_gems"].weights


def test_synthetic_profiles_include_real_archetype_metadata():
    profiles = generate_profiles(count=50, seed=20260620, stage="mixed")
    found = {profile["player_state"]["metadata"]["archetype"] for profile in profiles}
    assert found <= set(ARCHETYPES)
    assert len(found) >= 8
    assert all(profile_tags(profile) for profile in profiles)


def test_archetype_memory_reorders_without_pruning():
    profile = generate_profiles(count=1, seed=20260621, stage="midgame")[0]
    chart = new_chart()
    for index in range(25):
        cloned = json.loads(json.dumps(profile))
        cloned["id"] = f"archetype_{index}"
        add_observation(chart, cloned, {"best_action_id": "pets:upgrade:known_pet", "best_score": 10.0})
    chart["buckets"] = {}
    plan = recommend_training_plan(profile, chart, sequence=1, base_chain_interval=1, base_global_interval=1, min_samples=20)
    assert plan["systems"] is not None
    assert plan["pruned_systems"] == []
    assert plan["systems"][0] == "pet"
    assert "reordered" in plan["reason"]


def test_high_evidence_archetype_memory_prunes_with_exploration_safety():
    profile = generate_profiles(count=1, seed=20260626, stage="midgame")[0]
    state = profile["player_state"]
    state.setdefault("metadata", {}).update({
        "near_breakpoint": False,
        "close_to_xeno_breakpoint": False,
        "close_to_astral_forge_breakpoint": False,
        "close_to_tech_resonance_breakpoint": False,
        "close_to_collectible_set_breakpoint": False,
        "close_to_survivor_breakpoint": False,
    })
    state.setdefault("resources", {}).update({"xeno_core": 0, "astral_core": 0, "resonance_chip": 0})
    chart = new_chart()
    for index in range(500):
        cloned = json.loads(json.dumps(profile))
        cloned["id"] = f"high_evidence_{index}"
        action = "pets:upgrade:known_pet" if index < 400 else "tech:upgrade:known_tech"
        add_observation(chart, cloned, {"best_action_id": action, "best_score": 10.0})
    chart["buckets"] = {}
    chart["audit"].update({"full_search_audits": 100, "false_prunes": 0, "false_prune_rate": 0.0})
    pruned = recommend_training_plan(
        profile, chart, sequence=1, base_chain_interval=1, base_global_interval=1,
        min_samples=20, pruning_mode="normal", exploration_rate=0.0,
    )
    explored = recommend_training_plan(
        profile, chart, sequence=1, base_chain_interval=1, base_global_interval=1,
        min_samples=20, pruning_mode="normal", exploration_rate=1.0,
    )
    assert pruned["confidence"] == "high"
    assert pruned["pruned_systems"]
    assert explored["pruned_systems"] == []
    assert set(explored["systems"]) == set(CANONICAL_SYSTEMS)


def test_website_api_shape_is_json_serializable():
    profile = generate_profiles(count=1, seed=20260622, stage="midgame")[0]
    result = optimize(profile["player_state"], planner_options={"chain_depth": 1, "beam_size": 20, "max_actions_per_profile": 100})
    required = {
        "recommendation", "ranked_alternatives", "action_chain", "resources_used", "resources_saved",
        "expected_damage_gain", "long_term_value", "scenario_used", "explanation", "rejected_alternatives",
        "warnings", "assumptions", "future_goals", "missing_data", "confidence_level", "next_best_action",
        "source_refs", "confidence", "next_goal",
    }
    assert required <= set(result)
    assert "numeric_chain_candidates" not in result["global_plan"]
    json.dumps(result)


def test_atomic_optimizer_checkpoint_and_learning_memory(tmp_path: Path):
    chart = new_chart()
    checkpoint = optimizer_checkpoint(
        processed=4, submitted=5, elapsed_seconds=1.2, chart=chart, systems_covered={"cores"},
        results_path=tmp_path / "results.jsonl", profiles_path=tmp_path / "profiles.jsonl",
        device="cpu", workers=2, interrupted=True, completed=False,
    )
    checkpoint_path = tmp_path / "optimizer_latest.json"
    memory_path = tmp_path / "learning_memory.json"
    atomic_write_json(checkpoint_path, checkpoint)
    atomic_write_json(memory_path, learning_memory_snapshot(chart))
    assert json.loads(checkpoint_path.read_text())["interrupted"] is True
    assert json.loads(memory_path.read_text())["profiles_learned_from"] == 0


def test_online_ranker_learns_and_resumes(tmp_path: Path):
    path = tmp_path / "ranker.json"
    rows = [
        {"action_id": "winner", "features": {"immediate_damage": 10.0, "breakpoint_value": 2.0}},
        {"action_id": "loser", "features": {"immediate_damage": 1.0, "breakpoint_value": 0.0}},
    ]
    ranker = OnlineLinearRanker(path)
    assert ranker.observe(rows, {"winner"}) is True
    assert ranker.updates == 1
    assert any(weight != 0.0 for weight in ranker.weights)
    ranker.save()
    resumed = OnlineLinearRanker(path)
    assert resumed.loaded is True
    assert resumed.weights == ranker.weights


def test_scenarios_change_explainable_recommendation():
    state = {"inventory": {"core_selector_chests": 3}, "resources": {"astral_core": 0, "xeno_core": 0, "resonance_chip": 0, "relic_core": 0}}
    boss = optimize({**state, "goal_scenario": "scenario_1"}, include_global_plan=False)
    long_term = optimize({**state, "goal_scenario": "scenario_2"}, include_global_plan=False)
    assert boss["best"]["action_id"] != long_term["best"]["action_id"]
    assert boss["explanation"]["scenario_tradeoff"] != long_term["explanation"]["scenario_tradeoff"]


def test_high_false_prune_rate_weakens_aggressive_mode():
    profile = generate_profiles(count=1, seed=20260624, stage="midgame")[0]
    chart = new_chart()
    for index in range(25):
        cloned = json.loads(json.dumps(profile)); cloned["id"] = f"audit_{index}"
        add_observation(chart, cloned, {"best_action_id": "pets:upgrade:known_pet", "best_score": 10.0})
    chart["audit"].update({"full_search_audits": 10, "false_prunes": 2, "false_prune_rate": 0.2})
    plan = recommend_training_plan(profile, chart, sequence=1, base_chain_interval=1, base_global_interval=1, min_samples=20, pruning_mode="aggressive")
    assert plan["systems"] is not None
    assert plan["pruned_systems"] == []
    assert plan["reordering_applied"] is True
    assert plan["pruning_applied"] is False
    assert plan["false_prune_safety_latch"] is True
    assert "hard pruning disabled" in plan["hard_pruning_blocked_reason"]


def test_near_breakpoint_safety_override_disables_all_pruning():
    profile = generate_profiles(count=1, seed=20260625, stage="midgame")[0]
    profile["player_state"].setdefault("metadata", {})["near_breakpoint"] = True
    profile["player_state"]["metadata"]["close_to_xeno_breakpoint"] = True
    chart = new_chart()
    for index in range(25):
        cloned = json.loads(json.dumps(profile)); cloned["id"] = f"near_{index}"
        add_observation(chart, cloned, {"best_action_id": "pets:upgrade:known_pet", "best_score": 10.0})
    plan = recommend_training_plan(profile, chart, sequence=2, base_chain_interval=1, base_global_interval=1, min_samples=20, pruning_mode="aggressive", exploration_rate=0.0)
    assert plan["systems"] is not None
    assert plan["pruned_systems"] == []
    assert plan["full_search_safety_override"] is True


def test_historical_false_prune_latch_keeps_safe_learning_usage():
    profile = generate_profiles(count=1, seed=20260627, stage="midgame")[0]
    chart = new_chart()
    for index in range(25):
        cloned = json.loads(json.dumps(profile)); cloned["id"] = f"safe_reorder_{index}"
        add_observation(chart, cloned, {"best_action_id": "pets:upgrade:known_pet", "best_score": 10.0})
    chart["audit"].update({"full_search_audits": 1000, "false_prunes": 1, "false_prune_rate": 0.001})
    plan = recommend_training_plan(
        profile, chart, sequence=2, base_chain_interval=1, base_global_interval=1,
        min_samples=20, pruning_mode="normal", exploration_rate=0.0,
    )
    assert plan["reordering_applied"] is True
    assert plan["pruning_applied"] is False
    assert plan["systems"]
    assert plan["pruned_systems"] == []


def test_hard_pruning_waits_for_safe_audit_history():
    profile = generate_profiles(count=1, seed=20260629, stage="midgame")[0]
    chart = new_chart()
    for index in range(500):
        cloned = json.loads(json.dumps(profile)); cloned["id"] = f"unaudited_{index}"
        add_observation(chart, cloned, {"best_action_id": "pets:upgrade:known_pet", "best_score": 10.0})
    chart["buckets"] = {}
    plan = recommend_training_plan(
        profile, chart, sequence=1, base_chain_interval=1, base_global_interval=1,
        min_samples=20, pruning_mode="normal", exploration_rate=0.0,
    )
    assert plan["reordering_applied"] is True
    assert plan["pruning_applied"] is False
    assert plan["pruned_systems"] == []
    assert "100 safe full-search audits" in plan["hard_pruning_blocked_reason"]
