"""Breadth/beam chain simulation over generated inventory actions."""

from __future__ import annotations

from typing import Any
from collections import Counter

from optimizer.action_registry import generate_inventory_actions
from optimizer.state_hash import prune_dominated_states, search_state_fingerprint
from optimizer.state_transition import apply_action, can_apply_action


def action_score(action: dict[str, Any]) -> float:
    return (
        float(action.get("expected_damage_delta", 0.0))
        + float(action.get("long_term_value", 0.0))
        + float(action.get("breakpoint_value", 0.0))
        + float(action.get("resource_efficiency", 0.0))
        + (0.1 if action.get("supported", True) else -1.0)
    )


def simulate_action_chains(
    player_state: Any,
    knowledge: dict[str, Any],
    *,
    chain_depth: int = 2,
    beam_size: int = 100,
    max_actions_per_profile: int = 500,
    include_saves: bool = True,
    include_random_ev: bool = True,
    systems: list[str] | None = None,
) -> dict[str, Any]:
    frontier = [{"state": player_state, "actions": [], "score": 0.0, "rare_resources_spent": 0.0}]
    systems_covered: set[str] = set()
    actions_generated = 0
    actions_simulated = 0
    states_produced = 0
    dominated_removed = 0
    beam_pruned = 0
    all_chains: list[dict[str, Any]] = []
    actions_by_system: Counter[str] = Counter()
    chains_by_system: Counter[str] = Counter()
    root_actions: list[dict[str, Any]] | None = None
    action_templates_reused = 0
    action_generator_refreshes = 0

    for _depth in range(max(1, chain_depth)):
        next_frontier: list[dict[str, Any]] = []
        for chain in frontier:
            prior_produced_items = any(step.get("produced_items") for step in chain.get("actions", []))
            if root_actions is not None and not prior_produced_items:
                actions = [action for action in root_actions if can_apply_action(chain["state"], action)]
                action_templates_reused += len(actions)
            else:
                actions = generate_inventory_actions(
                    chain["state"], knowledge, systems=systems, include_saves=include_saves,
                    include_random_ev=include_random_ev, max_actions=max_actions_per_profile,
                    include_missing_placeholders=False if root_actions is not None else True,
                    use_cache=False if root_actions is not None else True, proposal_budget=True,
                    scoreable_only=True,
                )
                action_generator_refreshes += int(root_actions is not None)
                if root_actions is None:
                    root_actions = actions
            actions_generated += len(actions)
            for action in actions:
                system = str(action.get("system", "unknown"))
                systems_covered.add(system)
                actions_by_system[system] += 1
                if not action.get("supported", True):
                    continue
                future = apply_action(chain["state"], action)
                spent = sum(float(value) for value in (action.get("consumed_items") or {}).values())
                new_chain = {
                    "state": future,
                    "state_hash": search_state_fingerprint(future),
                    "actions": [*chain["actions"], action],
                    "score": chain["score"] + action_score(action),
                    "rare_resources_spent": chain["rare_resources_spent"] + spent,
                }
                next_frontier.append(new_chain)
                actions_simulated += 1
                states_produced += 1
                chains_by_system[system] += 1
        if not next_frontier:
            break
        before_dominated = len(next_frontier)
        next_frontier = prune_dominated_states(next_frontier)
        dominated_removed += before_dominated - len(next_frontier)
        next_frontier.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
        beam_pruned += max(0, len(next_frontier) - beam_size)
        frontier = next_frontier[:beam_size]
        all_chains.extend(frontier)

    best_chain = max(all_chains, key=lambda item: float(item.get("score", 0.0)), default=None)
    return {
        "best_chain": best_chain,
        "chains_kept": len(frontier),
        "actions_generated": actions_generated,
        "actions_simulated": actions_simulated,
        "states_produced": states_produced,
        "chains_scored": actions_simulated,
        "chains_pruned": dominated_removed + beam_pruned,
        "dominated_states_removed": dominated_removed,
        "beam_pruned": beam_pruned,
        "systems_covered": sorted(systems_covered),
        "actions_by_system": dict(actions_by_system),
        "chains_by_system": dict(chains_by_system),
        "action_templates_reused": action_templates_reused,
        "action_generator_refreshes": action_generator_refreshes,
    }
