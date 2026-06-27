from pathlib import Path
import json
import subprocess
import sys

from optimizer.action_registry import generate_inventory_actions, generator_manifests, registry_systems
from optimizer.chain_simulator import simulate_action_chains
from optimizer.coverage import coverage_report
from optimizer.knowledge_loader import load_knowledge
from optimizer.player_state import PlayerState
from optimizer.state_hash import prune_dominated_states
from optimizer.state_transition import apply_action


ROOT = Path(__file__).resolve().parents[1]


def fake_knowledge():
    base = {key: [] for key in [
        "items", "resources", "chests", "gear", "pets", "xeno_pets", "tech_parts",
        "collectibles", "survivors", "survivor_awakenings", "events", "event_shops",
        "warnings"
    ]}
    base["resources"] = [{"id": "test_core", "name": "Test Core", "description": "core upgrade", "tags": ["core"], "confidence": "low"}]
    base["chests"] = [{"id": "test_selector", "name": "Test Selector", "description": "selector chest", "choices": ["test_core"], "confidence": "low"}]
    base["gear"] = [{"id": "test_weapon", "name": "Test Weapon", "description": "gear upgrade", "confidence": "low"}]
    base["pets"] = [{"id": "test_pet", "name": "Test Pet", "description": "pet upgrade", "confidence": "low"}]
    base["tech_parts"] = [{"id": "test_tech", "name": "Test Tech", "description": "tech resonance", "confidence": "low"}]
    base["collectibles"] = [{"id": "test_collectible", "name": "Test Collectible", "description": "collectible upgrade", "confidence": "low"}]
    base["survivors"] = [{"id": "test_survivor", "name": "Test Survivor", "description": "survivor upgrade", "confidence": "low"}]
    base["event_shops"] = [{"id": "test_shop_item", "name": "Test Shop Item", "description": "shop item", "confidence": "low"}]
    return base


def fake_state():
    return PlayerState(
        resources={"test_core": 2},
        inventory={"items": {
            "test_selector": 1,
            "test_weapon": 1,
            "test_pet": 1,
            "test_tech": 1,
            "test_collectible": 1,
            "test_survivor": 1,
            "test_shop_item": 1,
        }},
    )


def test_action_registry_loads():
    systems = registry_systems()
    assert "resources" in systems
    assert "chests" in systems
    assert "save_hold" in systems


def test_generator_plugins_expose_capabilities_and_missing_data():
    manifests = generator_manifests(fake_state(), fake_knowledge())
    assert set(registry_systems()) == set(manifests)
    for manifest in manifests.values():
        assert "supported_action_types" in manifest
        assert "required_resources" in manifest
        assert "generated_candidate_actions" in manifest
        assert manifest["disposition"] in {"evaluate", "skip_with_warning", "evaluate_later"}


def test_generators_return_valid_structured_actions():
    actions = generate_inventory_actions(fake_state(), fake_knowledge(), max_actions=100)
    assert actions
    for action in actions:
        assert action["action_id"]
        assert action["action_type"]
        assert action["system"]
        assert "warnings" in action
        assert "explanation" in action


def test_chest_selector_and_save_hold_actions_are_generated():
    actions = generate_inventory_actions(fake_state(), fake_knowledge(), max_actions=100)
    action_types = {action["action_type"] for action in actions}
    assert "select_from_chest" in action_types
    assert "save_hold" in action_types


def test_system_generators_work_on_fake_known_data():
    actions = generate_inventory_actions(fake_state(), fake_knowledge(), max_actions=200)
    systems = {action["system"] for action in actions}
    assert {"gear", "pets", "tech_parts", "collectibles", "survivors", "shops"} <= systems


def test_unknown_missing_data_does_not_crash():
    state = PlayerState(inventory={"items": {"unknown_item": 1}})
    actions = generate_inventory_actions(state, fake_knowledge(), max_actions=50)
    assert isinstance(actions, list)


def test_missing_systems_emit_non_scoreable_review_placeholders():
    empty = {key: [] for key in fake_knowledge()}
    actions = generate_inventory_actions(PlayerState(), empty, max_actions=200)
    placeholders = [action for action in actions if action.get("metadata", {}).get("placeholder")]
    assert {action["system"] for action in placeholders} <= set(registry_systems())
    assert set(registry_systems()) <= {action["system"] for action in actions}
    assert all(not action["supported"] and action["confidence"] == "missing" for action in placeholders)


def test_state_transition_consumes_and_produces_inventory():
    state = fake_state()
    action = {
        "action_id": "test:convert",
        "required_items": {"test_core": 1},
        "consumed_items": {"test_core": 1},
        "produced_items": {"new_resource": 2},
        "supported": True,
    }
    future = apply_action(state, action)
    assert future.resources.test_core == 1
    assert future.inventory.items["new_resource"] == 2


def test_chain_simulator_depth_two_and_pruning():
    result = simulate_action_chains(fake_state(), fake_knowledge(), chain_depth=2, beam_size=10, max_actions_per_profile=20)
    assert result["actions_generated"] > 0
    assert result["actions_simulated"] > 0
    assert result["states_produced"] == result["actions_simulated"]
    assert result["chains_scored"] == result["actions_simulated"]
    assert result["chains_pruned"] >= 0
    assert result["action_templates_reused"] > 0
    pruned = prune_dominated_states([
        {"state_hash": "a", "rare_resources_spent": 2, "score": 10},
        {"state_hash": "a", "rare_resources_spent": 1, "score": 8},
    ])
    assert pruned[0]["rare_resources_spent"] == 1


def test_coverage_report_and_tool_runs():
    report = coverage_report(fake_knowledge(), fake_state())
    assert report["total_known_inventory_item_ids"] > 0
    assert "unsupported_ids" in report
    assert set(report["systems_fully_supported"]) | set(report["systems_partially_supported"]) == set(report["systems_implemented"])
    assert report["unsupported_systems"] == []
    assert "real_data_systems" in report
    assert "placeholder_only_systems" in report
    assert "missing_costs" in report
    assert "missing_unlock_requirements" in report
    assert "missing_chest_contents" in report
    assert "needs_review_by_system" in report

    result = subprocess.run(
        [sys.executable, "tools/audit_inventory_actions.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (ROOT / "reports" / "coverage" / "inventory_action_coverage.json").exists()


def test_real_knowledge_generates_actions_without_crashing():
    knowledge = load_knowledge()
    state = PlayerState(resources={record.id: 1 for record in knowledge["resources"]}, inventory={"core_selector_chests": 1})
    actions = generate_inventory_actions(state, knowledge, max_actions=100)
    assert actions


def test_catalog_records_do_not_create_unowned_hold_candidates():
    knowledge = fake_knowledge()
    actions = generate_inventory_actions(
        PlayerState(), knowledge, systems=["survivors"], include_missing_placeholders=False, use_cache=False,
    )
    assert actions == []


def test_incomplete_clan_catalog_is_coverage_only():
    knowledge = fake_knowledge()
    knowledge["clan_shop"] = [{
        "id": "unknown_clan_row", "name": "Unknown Clan Row", "cost": {},
        "tags": ["placeholder", "missing_costs", "needs_review"], "confidence": "low",
    }]
    actions = generate_inventory_actions(
        PlayerState(), knowledge, systems=["clan_shop"], include_missing_placeholders=False, use_cache=False,
    )
    assert actions == []


def test_budgeted_generation_is_scoreable_and_bounded():
    actions = generate_inventory_actions(
        fake_state(), fake_knowledge(), max_actions=200, proposal_budget=True,
        scoreable_only=True, include_missing_placeholders=False, use_cache=False,
    )
    assert len(actions) <= 24
    assert all(action["supported"] and action["confidence"] != "missing" for action in actions)
    assert sum(action["action_type"] == "save_hold" for action in actions) <= 1
    assert all(action["system"] != "clan_shop" for action in actions)
