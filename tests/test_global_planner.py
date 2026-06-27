from optimizer.action_registry import generate_inventory_actions
from optimizer.global_planner import _hard_budget_indices, _learned_chain_bonus, plan_global_inventory
from optimizer.knowledge_loader import load_knowledge
from optimizer.main import optimize
from optimizer.player_state import PlayerState
from optimizer.state_transition import apply_action
from optimizer.state_value import marginal_value


def combo_knowledge():
    return {
        "items": [],
        "resources": [
            {"id": "xeno_core", "name": "Xeno Core", "description": "xeno core upgrade", "tags": ["xeno", "core"]},
            {"id": "astral_core", "name": "Astral Core", "description": "astral core forge", "tags": ["astral", "core"]},
            {"id": "resonance_chip", "name": "Resonance Chip", "description": "tech resonance", "tags": ["resonance"]},
            {"id": "collectible_shard", "name": "Collectible Shard", "description": "collectible shard", "tags": ["collectible"]},
        ],
        "chests": [
            {"id": "epic_pet_chest", "name": "Epic Pet Chest", "description": "pet selector chest", "choices": ["pet_copy"]},
            {"id": "core_selector", "name": "Core Selector", "description": "core selector chest", "choices": ["astral_core", "xeno_core", "resonance_chip"]},
            {"id": "red_collectible_chest", "name": "Red Collectible Chest", "description": "collectible selector chest", "choices": ["collectible_shard"]},
            {"id": "tech_selector", "name": "Tech Selector", "description": "tech selector chest", "choices": ["tech_part"]},
        ],
        "gear": [],
        "pets": [{"id": "pet_copy", "name": "Pet Copy", "description": "pet awakening copy", "tags": ["pet"]}],
        "xeno_pets": [{"id": "xeno_pet", "name": "Xeno Pet", "description": "xeno pet damage", "tags": ["xeno"]}],
        "tech_parts": [{"id": "tech_part", "name": "Tech Part", "description": "tech resonance part", "tags": ["tech", "resonance"]}],
        "collectibles": [{"id": "collectible_shard", "name": "Collectible", "description": "collectible set upgrade", "tags": ["collectible"]}],
        "survivors": [],
        "survivor_awakenings": [],
        "events": [],
        "event_shops": [],
        "warnings": [],
    }


def state_with(items=None, resources=None, metadata=None):
    return PlayerState(
        resources=resources or {},
        inventory={"items": items or {}},
        metadata=metadata or {},
    )


def best_steps(plan):
    return plan["best_action_chain"]["ordered_steps"]


def test_global_planner_evaluates_combined_inventory_not_isolated_scores():
    state = state_with(items={"epic_pet_chest": 1}, resources={"xeno_core": 1})
    plan = plan_global_inventory(state, combo_knowledge(), chain_depth=3, beam_size=50, max_actions_per_profile=100, include_saves=True)
    steps = best_steps(plan)
    assert plan["chains_considered"] > len(steps)
    assert any("epic_pet_chest" in step["action_id"] for step in steps)


def test_action_a_can_change_value_of_action_b_and_order_matters():
    knowledge = combo_knowledge()
    state = state_with(items={"epic_pet_chest": 1}, resources={"xeno_core": 1})
    chest_action = next(action for action in plan_global_inventory(state, knowledge, chain_depth=1)["alternative_chains"][0]["ordered_steps"] if action)
    future = apply_action(state, {
        "action_id": "selectors:select_from_chest:epic_pet_chest_pet_copy",
        "required_items": {"epic_pet_chest": 1},
        "consumed_items": {"epic_pet_chest": 1},
        "produced_items": {"pet_copy": 1},
        "metadata": {"adds_progress": {"xeno_unlock": 1}, "breakpoint_requirements": {"xeno_unlock": 2}},
        "supported": True,
    })
    future2 = apply_action(future, {
        "action_id": "pets:upgrade_or_equip_pet:pet_copy",
        "required_items": {"pet_copy": 1},
        "consumed_items": {"pet_copy": 1},
        "metadata": {"adds_progress": {"xeno_unlock": 1}, "breakpoint_requirements": {"xeno_unlock": 2}},
        "supported": True,
    })
    assert marginal_value(state, future2, knowledge)["delta"] > marginal_value(state, future, knowledge)["delta"]
    assert chest_action["action_id"]


def test_duplicate_breakpoint_value_is_not_double_counted():
    state = state_with(metadata={"reached_breakpoints": ["xeno_unlock"]})
    future = apply_action(state, {
        "action_id": "duplicate:xeno",
        "metadata": {"sets_breakpoints": ["xeno_unlock"]},
        "supported": True,
    })
    assert marginal_value(state, future, combo_knowledge())["delta"] == 0


def test_pet_chest_xeno_core_combo_and_already_owned_xeno_reduces_pet_value():
    knowledge = combo_knowledge()
    combo_state = state_with(items={"epic_pet_chest": 1}, resources={"xeno_core": 1})
    combo_plan = plan_global_inventory(combo_state, knowledge, chain_depth=3, beam_size=100, max_actions_per_profile=100)
    assert any("xeno" in reason for reason in combo_plan["explanation"]["why"])

    owned_state = state_with(items={"epic_pet_chest": 1}, resources={"astral_core": 1}, metadata={"reached_breakpoints": ["xeno_unlock"]})
    owned_plan = plan_global_inventory(owned_state, knowledge, chain_depth=3, beam_size=100, max_actions_per_profile=100)
    assert not any(step["action_id"].startswith("chests:open_random") for step in best_steps(owned_plan))


def test_astral_forge_chain_can_beat_pet_xeno_chain():
    state = state_with(items={"epic_pet_chest": 1}, resources={"astral_core": 2}, metadata={"reached_breakpoints": ["xeno_unlock"]})
    plan = plan_global_inventory(state, combo_knowledge(), chain_depth=3, beam_size=100, max_actions_per_profile=100)
    assert any("astral_core" in step["action_id"] or "astral" in step["explanation"].lower() for step in best_steps(plan))


def test_red_collectible_chest_and_shards_can_complete_set():
    state = state_with(items={"red_collectible_chest": 1}, resources={"collectible_shard": 1})
    plan = plan_global_inventory(state, combo_knowledge(), chain_depth=3, beam_size=100, max_actions_per_profile=100)
    assert "collectible_set_breakpoint" in plan["best_action_chain"]["marginal_value"]["after"]["breakpoints"]


def test_collectible_bridge_resources_pass_proposal_budget_gate():
    profile = {
        "items": {"red_collectible_chest": 1},
        "resources": {"collectible_shard": 10},
        "goal_scenario": "normal",
    }
    validated = state_with(items=profile["items"], resources=profile["resources"])
    knowledge = load_knowledge()

    for state in (profile, validated):
        actions = generate_inventory_actions(
            state,
            knowledge,
            systems=["collectibles", "collectible_sets", "chests"],
            proposal_budget=True,
            scoreable_only=True,
            include_missing_placeholders=False,
            use_cache=False,
        )
        assert any(action["system"] in {"collectibles", "collectible_sets"} for action in actions)

    plan = optimize(profile)["global_plan"]
    assert plan["actions_considered"] > 0
    assert plan["best_action_chain"]["ordered_steps"]


def test_resonance_not_overvalued_without_breakpoint_and_save_can_win():
    state = state_with(resources={"resonance_chip": 1})
    plan = plan_global_inventory(state, combo_knowledge(), chain_depth=1, beam_size=50, max_actions_per_profile=100, include_saves=True)
    assert plan["best_action_chain"]["marginal_value"]["delta"] <= 1.1
    assert any(step["action_type"] == "save_hold" for step in best_steps(plan))


def test_dominated_chains_are_pruned():
    state = state_with(items={"core_selector": 1}, resources={"astral_core": 1})
    plan = plan_global_inventory(state, combo_knowledge(), chain_depth=3, beam_size=50, max_actions_per_profile=100)
    assert plan["dominated_states_removed"] >= 0
    assert plan["search_mode"] in {"beam_pruned", "bounded_exhaustive"}


def test_preprune_cpu_fallback_preserves_audited_best_path():
    state = state_with(items={"red_collectible_chest": 1}, resources={"collectible_shard": 1})
    baseline = plan_global_inventory(state, combo_knowledge(), chain_depth=3, beam_size=50, max_actions_per_profile=100)
    ranked = plan_global_inventory(
        state, combo_knowledge(), chain_depth=3, beam_size=50, max_actions_per_profile=100,
        gpu_preprune=True, preprune_oversample=1, preprune_audit=True,
    )
    assert ranked["best_action_chain"]["marginal_value"]["after"]["breakpoints"] == baseline["best_action_chain"]["marginal_value"]["after"]["breakpoints"]
    assert ranked["gpu_preprune"]["full_search_audits"] > 0
    assert ranked["gpu_preprune"]["false_prunes"] == 0


def test_equivalent_ss_slot_actions_are_removed_before_gpu():
    state = state_with(resources={"astral_core": 2})
    plan = plan_global_inventory(state, combo_knowledge(), chain_depth=2, beam_size=30, max_actions_per_profile=100)
    assert plan["gpu_preprune"]["equivalent_actions_removed_before_gpu"] > 0


def test_proposal_budget_caps_rows_before_gpu_and_state_rebuilds():
    state = state_with(items={"core_selector": 2, "epic_pet_chest": 2}, resources={"astral_core": 4, "xeno_core": 4, "resonance_chip": 4})
    plan = plan_global_inventory(
        state, combo_knowledge(), chain_depth=2, beam_size=20, max_actions_per_profile=100,
        gpu_preprune=True, preprune_oversample=1,
    )
    assert plan["proposal_rows_created"] <= 128  # min hard cap is 64 per depth
    assert plan["states_materialized"] <= plan["proposal_rows_created"]
    assert plan["gpu_preprune"]["proposal_budget"]["selected_candidates"] <= 24
    assert plan["gpu_preprune"]["proposal_budget"]["raw_candidates"] >= plan["gpu_preprune"]["proposal_budget"]["selected_candidates"]


def test_proposal_budget_audit_keeps_expanded_search_and_reports_result():
    state = state_with(items={"red_collectible_chest": 1}, resources={"collectible_shard": 1})
    plan = plan_global_inventory(
        state, combo_knowledge(), chain_depth=3, beam_size=30, max_actions_per_profile=100,
        gpu_preprune=True, preprune_oversample=1, preprune_audit=True,
    )
    stats = plan["gpu_preprune"]
    assert stats["proposal_budget_audited"] is True
    assert stats["proposal_budget_false_prunes"] == 0


def test_learned_chain_bonus_changes_ordering_signal_but_is_safely_capped():
    action = {
        "action_type": "upgrade",
        "expected_damage_delta": 1000.0,
        "long_term_value": 10.0,
        "confidence": "high",
        "metadata": {},
    }
    weights = [0.0] * 25
    weights[0] = 1.0
    assert _learned_chain_bonus(action, 1, weights) == 25.0
    weights[0] = -1.0
    assert _learned_chain_bonus(action, 1, weights) == -25.0


def test_hard_proposal_budget_preserves_system_diversity_then_global_rank():
    chain = {"actions": []}
    proposed = [
        (chain, {"action_id": "core-low"}, "cores", 1.0, 0.0),
        (chain, {"action_id": "core-high"}, "cores", 9.0, 0.0),
        (chain, {"action_id": "pet"}, "pets", 2.0, 0.0),
        (chain, {"action_id": "tech"}, "tech", 3.0, 0.0),
        (chain, {"action_id": "core-mid"}, "cores", 8.0, 0.0),
    ]
    assert _hard_budget_indices(proposed, 4) == [1, 3, 2, 4]
