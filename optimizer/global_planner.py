"""Whole-inventory combination planner."""

from __future__ import annotations

import hashlib
import heapq
import time
from typing import Any
from collections import Counter

from optimizer.action_registry import generate_inventory_actions, last_proposal_budget_stats, registry_systems
from optimizer.state_hash import prune_dominated_states, search_state_fingerprint
from optimizer.state_transition import apply_action, can_apply_action
from optimizer.state_value import BREAKPOINT_VALUES, marginal_value, reached_breakpoints
from optimizer.numeric_features import category_code
from optimizer.preprune_ranker import rank_shared_candidates


def _learned_chain_bonus(action: dict[str, Any], chain_depth: int, weights: list[float] | None) -> float:
    """Score hot-path numeric fields without hashing categorical strings."""
    if not weights:
        return 0.0
    metadata = action.get("metadata", {}) or {}
    spent = sum(float(value) for value in (action.get("consumed_items") or {}).values())
    confidence = {"missing": 0.0, "low": 0.25, "medium": 0.6, "high": 0.9, "confirmed": 1.0}.get(str(action.get("confidence", "low")), 0.25)
    warnings = action.get("warnings", []) or []
    damage = float(action.get("expected_damage_delta", 0.0))
    long_term = float(action.get("long_term_value", 0.0))
    breakpoint = float(action.get("breakpoint_value", 0.0))
    # Indices correspond to numeric_features.FEATURE_COLUMNS. Categorical
    # codes are intentionally excluded here; they are still present in the
    # batched ranker rows used by CUDA and online learning.
    raw_bonus = (
        weights[0] * damage + weights[1] * long_term + weights[2] * breakpoint
        + weights[3] * float(action.get("resource_efficiency", 0.0)) - weights[4] * spent
        + weights[5] * confidence + weights[7] * float(chain_depth) - weights[12] * spent
        + weights[13] * damage + weights[16] * long_term
        - weights[17] * float(metadata.get("breakpoint_distance", 0.0))
        + weights[18] * float(metadata.get("resource_bottleneck_score", 0.0))
        + weights[19] * float(metadata.get("synergy_score", 0.0))
        + weights[20] * float(action.get("action_type") == "save_hold")
        + weights[21] * float(metadata.get("chest_expected_value", 0.0))
        + weights[22] * confidence + weights[23] * (-1.0 if warnings or not action.get("supported", True) else 0.0)
        + weights[24] * confidence
    )
    # Learned weights are a tie-break/reordering signal, not a replacement for
    # exact marginal value. Persisted online weights may saturate, so cap their
    # influence before proposal top-k.
    return max(-25.0, min(25.0, raw_bonus))


def _compact_preprune_features(
    chain: dict[str, Any], action: dict[str, Any], *, estimated_value: float,
    scenario_code: float, profile_bucket_code: float,
) -> list[float]:
    metadata = action.get("metadata", {}) or {}
    spent = sum(float(value) for value in (action.get("consumed_items") or {}).values())
    confidence = {"missing": 0.0, "low": 0.25, "medium": 0.6, "high": 0.9, "confirmed": 1.0}.get(str(action.get("confidence", "low")), 0.25)
    warnings = action.get("warnings", []) or []
    action_type = str(action.get("action_type", "unknown"))
    irreversible = action_type in {"open_chest", "salvage_item", "exchange_resource", "universal_exchange", "buy_event_shop_item", "buy_clan_shop_item"}
    systems = chain.get("systems", ())
    action_system = str(action.get("system", "unknown"))
    system_count = len(systems) + int(action_system not in systems)
    synergy = max(float(metadata.get("synergy_score", 0.0)), float(system_count - 1))
    return [
        float(estimated_value), float(action.get("expected_damage_delta", 0.0)),
        float(action.get("long_term_value", 0.0)), float(action.get("breakpoint_value", 0.0)),
        float(action.get("resource_efficiency", 0.0)), spent, float(metadata.get("rarity_value", 0.0)),
        float(metadata.get("breakpoint_distance", 0.0)), confidence,
        1.0 if action.get("supported", True) and not warnings else 0.0, synergy,
        1.0 if action_type == "save_hold" else 0.0, 0.0 if irreversible else 1.0,
        category_code(action.get("system")), scenario_code, profile_bucket_code,
    ]


def _preprune_category_context(original_state: Any) -> tuple[float, float]:
    """Extract invariant categorical features once per planner invocation."""
    if isinstance(original_state, dict):
        state = original_state
    else:
        state = original_state.model_dump(include={"metadata", "goal_scenario"})
    metadata = state.get("metadata", {}) or {}
    profile_bucket = metadata.get("archetype", metadata.get("stage", "unknown"))
    return category_code(state.get("goal_scenario", "unknown")), category_code(profile_bucket)


def _hard_budget_indices(
    proposed: list[tuple[dict[str, Any], dict[str, Any], str, float, float]], limit: int,
) -> list[int]:
    """Keep the best proposal per system, then fill remaining slots globally."""
    if len(proposed) <= limit:
        return list(range(len(proposed)))

    # CPython's C-backed Timsort wins for ordinary planner batches. Use it
    # until the candidate set is large enough, relative to the retained top-k,
    # for a bounded heap to provide a measured benefit.
    if len(proposed) < max(4096, limit * 16):
        ranked = sorted(
            range(len(proposed)),
            key=lambda index: (
                proposed[index][3], proposed[index][2],
                str(proposed[index][1].get("action_id", "")),
            ),
            reverse=True,
        )
        diverse: list[int] = []
        seen_systems: set[str] = set()
        for index in ranked:
            system = proposed[index][2]
            if system not in seen_systems:
                diverse.append(index)
                seen_systems.add(system)
        diverse_set = set(diverse)
        return (diverse + [index for index in ranked if index not in diverse_set])[:limit]

    # Extract each key once. ``-index`` preserves Python's stable-sort behavior
    # when all content keys tie while heapq avoids sorting every proposal.
    rank_keys = [
        (proposal[3], proposal[2], str(proposal[1].get("action_id", "")), -index)
        for index, proposal in enumerate(proposed)
    ]
    best_by_system: dict[str, int] = {}
    for index, proposal in enumerate(proposed):
        system = proposal[2]
        current = best_by_system.get(system)
        if current is None or rank_keys[index] > rank_keys[current]:
            best_by_system[system] = index

    diverse = sorted(
        best_by_system.values(), key=rank_keys.__getitem__, reverse=True,
    )[:limit]
    if len(diverse) == limit:
        return diverse

    diverse_set = set(diverse)
    remaining = heapq.nlargest(
        limit - len(diverse),
        (index for index in range(len(proposed)) if index not in diverse_set),
        key=rank_keys.__getitem__,
    )
    return diverse + remaining


def _estimated_state_value_delta(
    state: Any, action: dict[str, Any], *, reached: set[str] | None = None,
) -> float:
    """Cheap exact estimate for the fields consumed by state_value()."""
    if isinstance(state, dict):
        metadata = state.get("metadata", {}) or {}
        resources = state.get("resources", {}) or {}
    else:
        metadata = getattr(state, "metadata", {}) or {}
        resources = getattr(state, "resources", None)
    action_metadata = action.get("metadata", {}) or {}
    reached = reached if reached is not None else reached_breakpoints(state)
    delta = 1.0 if action.get("action_type") == "save_hold" else 0.0
    for breakpoint_id in action_metadata.get("sets_breakpoints", []) or []:
        if str(breakpoint_id) not in reached:
            delta += BREAKPOINT_VALUES.get(str(breakpoint_id), 25.0)
    progress = metadata.get("progress", {}) or {}
    for breakpoint_id, amount in (action_metadata.get("adds_progress") or {}).items():
        required = float((action_metadata.get("breakpoint_requirements") or {}).get(breakpoint_id, float("inf")))
        if str(breakpoint_id) not in reached and float(progress.get(str(breakpoint_id), 0.0) or 0.0) + float(amount) >= required:
            delta += BREAKPOINT_VALUES.get(str(breakpoint_id), 25.0)
    flexibility_ids = {"astral_core", "xeno_core", "resonance_chip", "relic_core"}
    for item_id, amount in (action.get("consumed_items") or {}).items():
        if str(item_id) in flexibility_ids:
            delta -= 0.05 * float(amount)
    for item_id, amount in (action.get("produced_items") or {}).items():
        if str(item_id) in flexibility_ids:
            delta += 0.05 * float(amount)
    return delta


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple, set)):
        return tuple(_freeze(item) for item in value)
    return value


def _numeric_action_signature(action: dict[str, Any]) -> tuple[Any, ...]:
    """Collapse actions that are identical to the current value/transition model."""
    if action.get("action_type") == "save_hold":
        # All holds currently perform the same transition (save_value += 1)
        # and have the same numeric value. Preserve one explanation and record
        # how many system-specific aliases were collapsed.
        return ("save_hold", _freeze(action.get("required_items", {})))
    metadata = action.get("metadata", {}) or {}
    return (
        action.get("system"), action.get("action_type"), _freeze(action.get("required_items", {})),
        _freeze(action.get("consumed_items", {})), _freeze(action.get("produced_items", {})),
        float(action.get("expected_damage_delta", 0.0)), float(action.get("long_term_value", 0.0)),
        float(action.get("breakpoint_value", 0.0)), float(action.get("resource_efficiency", 0.0)),
        bool(action.get("supported", True)), _freeze(metadata.get("adds_progress", {})),
        _freeze(metadata.get("breakpoint_requirements", {})), _freeze(metadata.get("sets_breakpoints", [])),
        _freeze(metadata.get("set_flags", {})),
    )


def _chain_explanation(chain: dict[str, Any], original_state: Any, knowledge: dict[str, Any]) -> dict[str, Any]:
    actions = chain.get("actions", [])
    marginal = marginal_value(original_state, chain.get("state"), knowledge)
    reasons = []
    after = marginal["after"]
    for breakpoint_id in after.get("breakpoints", []):
        reasons.append(f"Final state reaches {breakpoint_id}; counted once for the whole chain.")
    if any(action.get("action_type") == "save_hold" for action in actions):
        reasons.append("Chain includes save/hold to preserve flexibility where spend value is uncertain.")
    if not reasons:
        reasons.append("Chain has limited known damage breakpoint value from current knowledge.")
    return {
        "summary": reasons,
        "marginal_value": marginal,
        "ordered_steps": [
            {
                "step": index + 1,
                "action_id": action.get("action_id"),
                "action_type": action.get("action_type"),
                "system": action.get("system"),
                "required_items": action.get("required_items", {}),
                "required_resources": action.get("required_resources", action.get("required_items", {})),
                "consumed_items": action.get("consumed_items", {}),
                "consumed_resources": action.get("consumed_resources", action.get("consumed_items", {})),
                "produced_items": action.get("produced_items", {}),
                "produced_state_delta": action.get("produced_state_delta", action.get("produced_items", {})),
                "expected_damage_delta": action.get("expected_damage_delta", 0.0),
                "long_term_value": action.get("long_term_value", 0.0),
                "long_term_value_delta": action.get("long_term_value_delta", action.get("long_term_value", 0.0)),
                "breakpoint_value": action.get("breakpoint_value", 0.0),
                "explanation": action.get("explanation"),
                "warnings": action.get("warnings", []),
                "missing_data_warnings": action.get("missing_data_warnings", action.get("warnings", [])),
                "risk_uncertainty": action.get("risk_uncertainty", action.get("warnings", [])),
                "source_refs": action.get("source_refs", []),
                "confidence": action.get("confidence", "low"),
                "can_score_now": action.get("can_score_now", action.get("supported", True)),
            }
            for index, action in enumerate(actions)
        ],
    }


def plan_global_inventory(
    player_state: Any,
    knowledge: dict[str, Any],
    *,
    chain_depth: int = 3,
    beam_size: int = 200,
    max_actions_per_profile: int = 2000,
    include_saves: bool = True,
    include_random_ev: bool = True,
    allow_exhaustive_small_inventory: bool = False,
    prune_dominated_states_enabled: bool = True,
    systems: list[str] | None = None,
    learned_ranker_weights: list[float] | None = None,
    gpu_preprune: bool = False,
    preprune_oversample: int = 4,
    preprune_audit: bool = False,
    proposal_budget_enabled: bool = True,
    total_proposal_budget: int = 24,
    proposal_row_budget_multiplier: int = 2,
    min_gpu_preprune_rows: int = 64,
    prebuilt_root_actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    planner_started = time.perf_counter()
    planner_timings: Counter[str] = Counter()
    original_state = player_state
    scenario_code, profile_bucket_code = _preprune_category_context(original_state)
    budget_audit_action_ids: set[str] = set()
    reused_prebuilt_root = bool(prebuilt_root_actions is not None and not preprune_audit)
    root_actions_started = time.perf_counter()
    if reused_prebuilt_root:
        root_actions = prebuilt_root_actions or []
        root_budget_stats = last_proposal_budget_stats()
    elif preprune_audit and proposal_budget_enabled:
        budgeted_root = generate_inventory_actions(
            player_state, knowledge, systems=systems, include_saves=include_saves,
            include_random_ev=include_random_ev, max_actions=max_actions_per_profile,
            include_missing_placeholders=False, use_cache=False, proposal_budget=True,
            scoreable_only=True, total_proposal_budget=total_proposal_budget,
        )
        budget_audit_action_ids = {str(action.get("action_id", "")) for action in budgeted_root}
        root_budget_stats = last_proposal_budget_stats()
        root_actions = generate_inventory_actions(
            player_state, knowledge, systems=systems, include_saves=include_saves,
            include_random_ev=include_random_ev, max_actions=max_actions_per_profile,
            include_missing_placeholders=False, use_cache=False, scoreable_only=True,
        )
    else:
        root_actions = generate_inventory_actions(
            player_state, knowledge, systems=systems, include_saves=include_saves,
            include_random_ev=include_random_ev, max_actions=max_actions_per_profile,
            include_missing_placeholders=False, proposal_budget=proposal_budget_enabled,
            scoreable_only=True, total_proposal_budget=total_proposal_budget,
        )
        root_budget_stats = last_proposal_budget_stats()
    planner_timings["root_action_generation"] += time.perf_counter() - root_actions_started
    deep_systems = sorted({
        str(action.get("system")) for action in root_actions
        if action.get("system") and not (action.get("metadata", {}) or {}).get("placeholder")
    })
    exhaustive = bool(allow_exhaustive_small_inventory and len(root_actions) <= 8 and chain_depth <= 4)
    search_mode = "bounded_exhaustive" if exhaustive else "beam_pruned"
    frontier = [{
        "state": player_state,
        "actions": [],
        "systems": frozenset(),
        "state_hash": search_state_fingerprint(player_state),
    }]
    completed: list[dict[str, Any]] = []
    actions_considered = 0
    chains_considered = 0
    actions_pruned = 0
    dominated_removed = 0
    systems_covered: set[str] = set()
    actions_by_system: Counter[str] = Counter()
    chains_by_system: Counter[str] = Counter()
    preprune_rows_submitted = 0
    preprune_gpu_rows_scored = 0
    preprune_requests = 0
    preprune_wait_seconds = 0.0
    preprune_fallbacks: Counter[str] = Counter()
    preprune_candidates_removed = 0
    preprune_full_search_audits = 0
    preprune_false_prunes = 0
    preprune_corrections = 0
    equivalent_actions_removed = sum(int(root_budget_stats.get(key, 0)) for key in ("save_aliases_removed", "effect_duplicates_removed", "over_budget_removed", "unsupported_removed"))
    action_templates_reused = 0
    action_generator_refreshes = 0
    states_materialized = 0
    affected_system_refreshes: Counter[str] = Counter()
    raw_proposal_rows = 0
    proposal_rows_created = 0
    proposal_rows_budget_removed = 0
    proposal_row_budget_audits = 0
    proposal_row_budget_false_prunes = 0
    preprune_cpu_rows_ranked = 0

    knowledge_sections_by_id: dict[str, set[str]] = {}
    for section in ("resources", "gear", "weapons", "pets", "xeno_pets", "tech_parts", "survivors", "chests", "collectibles"):
        for record in knowledge.get(section, []):
            record_id = str(record.get("id", "")) if isinstance(record, dict) else str(getattr(record, "id", ""))
            if record_id:
                knowledge_sections_by_id.setdefault(record_id, set()).add(section)

    def affected_systems(action: dict[str, Any]) -> list[str]:
        if action.get("action_type") == "merge_duplicates":
            return []
        affected: set[str] = set()
        for item_id in (action.get("produced_items") or {}):
            sections = knowledge_sections_by_id.get(str(item_id), set())
            if "resources" in sections:
                affected.update({"cores", "ss_gear", "xeno_pets", "resonance", "survivors", "pet_awakenings", "exchanges", "save_hold"})
            if sections & {"gear", "weapons"}:
                affected.update({"gear", "ss_gear", "merge", "save_hold"})
            if sections & {"pets", "xeno_pets"}:
                affected.update({"pets", "pet_awakenings", "xeno_pets", "merge", "save_hold"})
            if "tech_parts" in sections:
                affected.update({"tech_parts", "resonance", "merge", "save_hold"})
            if "survivors" in sections:
                affected.update({"survivors", "survivor_awakening", "save_hold"})
            if "chests" in sections:
                affected.update({"chests", "selectors", "save_hold"})
            if "collectibles" in sections:
                affected.update({"collectibles", "collectible_sets", "save_hold"})
            if not sections:
                affected.update(registry_systems())
        return sorted(affected)

    def collapse_templates(actions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        unique: dict[tuple[Any, ...], dict[str, Any]] = {}
        for action in actions:
            if not action.get("supported", True):
                continue
            signature = _numeric_action_signature(action)
            if signature in unique:
                representative = unique[signature]
                metadata = representative.setdefault("metadata", {})
                metadata["collapsed_equivalent_count"] = int(metadata.get("collapsed_equivalent_count", 0)) + 1
                metadata["collapse_reason"] = "identical numeric value, legality, and state transition"
                continue
            unique[signature] = {**action, "metadata": dict(action.get("metadata", {}) or {})}
        return list(unique.values()), len(actions) - len(unique)

    root_templates, root_equivalents = collapse_templates(root_actions)
    transition_budget = min(beam_size, max(64, len(root_templates) * 2))
    systems_covered.update(str(action.get("system", "unknown")) for action in root_actions)

    for _depth in range(max(1, chain_depth)):
        proposal_started = time.perf_counter()
        proposed: list[tuple[dict[str, Any], dict[str, Any], str, float, float]] = []
        for chain in frontier:
            chain_breakpoints = reached_breakpoints(chain["state"])
            prior_action = chain.get("actions", [])[-1] if chain.get("actions") else None
            refresh_systems = affected_systems(prior_action) if prior_action else []
            if _depth == 0 or not refresh_systems:
                # Reuse root action dictionaries as compact templates. Pure
                # consumption cannot reveal a new action; legality is a cheap
                # inventory check. Regenerate only after a producing action.
                actions = [action for action in root_templates if can_apply_action(chain["state"], action)]
                action_templates_reused += len(actions)
                equivalent_actions_removed += root_equivalents
            else:
                refreshed = generate_inventory_actions(
                    chain["state"], knowledge, systems=refresh_systems, include_saves=include_saves,
                    include_random_ev=include_random_ev, max_actions=max_actions_per_profile,
                    include_missing_placeholders=False, use_cache=False, proposal_budget=proposal_budget_enabled and not preprune_audit,
                    scoreable_only=True, total_proposal_budget=total_proposal_budget,
                )
                action_generator_refreshes += 1
                affected_system_refreshes.update(refresh_systems)
                actions, refreshed_equivalents = collapse_templates(refreshed)
                equivalent_actions_removed += refreshed_equivalents
            actions_considered += len(actions)
            for action in actions:
                system = str(action.get("system", "unknown"))
                systems_covered.add(system)
                actions_by_system[system] += 1
                exact_delta = _estimated_state_value_delta(chain["state"], action, reached=chain_breakpoints)
                estimate = (
                    float(chain.get("base_score", chain.get("score", 0.0)))
                    + exact_delta
                    + _learned_chain_bonus(action, len(chain.get("actions", [])) + 1, learned_ranker_weights)
                )
                proposed.append((chain, action, system, estimate, exact_delta))
                chains_considered += 1
                chains_by_system[system] += 1
        planner_timings["candidate_row_creation"] += time.perf_counter() - proposal_started
        if not proposed:
            break

        raw_proposal_rows += len(proposed)
        hard_row_limit = max(64, beam_size * max(1, int(proposal_row_budget_multiplier)))
        hard_budget_indices = _hard_budget_indices(proposed, hard_row_limit)
        hard_budget_selected = set(hard_budget_indices)
        if len(proposed) > hard_row_limit and not preprune_audit:
            proposal_rows_budget_removed += len(proposed) - len(hard_budget_indices)
            proposed = [proposed[index] for index in hard_budget_indices]
        elif len(proposed) > hard_row_limit:
            proposal_row_budget_audits += 1
        proposal_rows_created += len(proposed)
        feature_started = time.perf_counter()
        proposal_features = [
            _compact_preprune_features(
                chain, action, estimated_value=estimate,
                scenario_code=scenario_code, profile_bucket_code=profile_bucket_code,
            )
            for chain, action, _system, estimate, _exact_delta in proposed
        ]
        planner_timings["numeric_feature_creation"] += time.perf_counter() - feature_started

        proposal_limit = (
            min(len(proposed), max(transition_budget, transition_budget * max(1, int(preprune_oversample))))
            if gpu_preprune and not exhaustive else len(proposed)
        )
        if gpu_preprune and len(proposed) > proposal_limit and len(proposed) >= min_gpu_preprune_rows:
            selected_indices, _scores, rank_stats = rank_shared_candidates(proposal_features, proposal_limit, phase="proposal")
            preprune_rows_submitted += len(proposal_features)
            preprune_gpu_rows_scored += len(proposal_features) if rank_stats.get("gpu_used") else 0
            preprune_requests += 1
            preprune_wait_seconds += float(rank_stats.get("wait_seconds", 0.0))
            if rank_stats.get("fallback"):
                preprune_fallbacks[str(rank_stats["fallback"])] += 1
        else:
            selected_indices = sorted(range(len(proposed)), key=lambda index: proposal_features[index][0], reverse=True)[:proposal_limit]
            preprune_cpu_rows_ranked += len(proposed) if gpu_preprune and len(proposed) > proposal_limit else 0

        proposal_selected = set(selected_indices)
        materialized_indices = list(range(len(proposed))) if preprune_audit else selected_indices
        preprune_candidates_removed += max(0, len(proposed) - len(selected_indices))
        next_frontier: list[dict[str, Any]] = []
        exact_by_proposal_index: dict[int, float] = {}
        transition_started = time.perf_counter()
        for proposal_index in materialized_indices:
                chain, action, _system, _estimate, exact_delta = proposed[proposal_index]
                future = apply_action(chain["state"], action)
                states_materialized += 1
                new_chain = {
                    "state": future,
                    "state_hash": search_state_fingerprint(future),
                    "actions": [*chain["actions"], action],
                    "systems": frozenset((*chain.get("systems", ()), str(action.get("system", "unknown")))),
                }
                ranker_bonus = _learned_chain_bonus(action, len(new_chain["actions"]), learned_ranker_weights)
                # _estimated_state_value_delta covers every field consumed by
                # state_value(): breakpoint flags/progress, flexible resources,
                # and save value. Reuse that exact delta instead of serializing
                # and re-valuing the complete state for every candidate.
                base_score = float(chain.get("base_score", chain.get("score", 0.0))) + exact_delta
                new_chain["base_score"] = base_score
                new_chain["learned_ranker_score"] = ranker_bonus
                new_chain["score"] = base_score + ranker_bonus
                new_chain["rare_resources_spent"] = float(chain.get("rare_resources_spent", 0.0)) + sum(
                    float(value) for value in (action.get("consumed_items") or {}).values()
                )
                new_chain["proposal_index"] = proposal_index
                next_frontier.append(new_chain)
                exact_by_proposal_index[proposal_index] = float(new_chain["score"])
        planner_timings["state_transition_and_hashing"] += time.perf_counter() - transition_started
        if preprune_audit and exact_by_proposal_index:
            preprune_full_search_audits += 1
            exact_best_proposal = max(exact_by_proposal_index, key=exact_by_proposal_index.get)
            preprune_false_prunes += int(exact_best_proposal not in proposal_selected)
            if len(proposed) > hard_row_limit:
                proposal_row_budget_false_prunes += int(exact_best_proposal not in hard_budget_selected)
        before_prune = len(next_frontier)
        if prune_dominated_states_enabled:
            next_frontier = prune_dominated_states(next_frontier)
            dominated_removed += before_prune - len(next_frontier)
        if not exhaustive and gpu_preprune and len(next_frontier) >= min_gpu_preprune_rows:
            final_features = [
                _compact_preprune_features(
                    chain, chain["actions"][-1], estimated_value=float(chain.get("score", 0.0)),
                    scenario_code=scenario_code, profile_bucket_code=profile_bucket_code,
                )
                for chain in next_frontier
            ]
            final_limit = min(beam_size, len(next_frontier))
            final_indices, final_scores, rank_stats = rank_shared_candidates(final_features, final_limit, phase="final_state")
            preprune_rows_submitted += len(final_features)
            preprune_gpu_rows_scored += len(final_features) if rank_stats.get("gpu_used") else 0
            preprune_requests += 1
            preprune_wait_seconds += float(rank_stats.get("wait_seconds", 0.0))
            if rank_stats.get("fallback"):
                preprune_fallbacks[str(rank_stats["fallback"])] += 1
            cpu_best_index = max(range(len(next_frontier)), key=lambda index: float(next_frontier[index].get("score", 0.0)))
            if cpu_best_index not in final_indices:
                preprune_false_prunes += 1
                preprune_corrections += 1
                final_indices = [cpu_best_index, *final_indices[:-1]]
                final_scores = [float(next_frontier[cpu_best_index].get("score", 0.0)), *final_scores[:-1]]
            for index, score in zip(final_indices, final_scores):
                next_frontier[index]["gpu_rank_score"] = float(score)
            actions_pruned += len(next_frontier) - len(final_indices)
            next_frontier = [next_frontier[index] for index in final_indices]
        else:
            next_frontier.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
            if not exhaustive and len(next_frontier) > beam_size:
                actions_pruned += len(next_frontier) - beam_size
                next_frontier = next_frontier[:beam_size]
        frontier = next_frontier
        completed.extend(frontier)

    completed.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
    best = completed[0] if completed else {"actions": [], "score": 0.0, "state": player_state}
    alternatives = completed[1:6]
    avoid = sorted(completed, key=lambda item: float(item.get("score", 0.0)))[:3]
    unsupported_items = []
    for action in root_actions:
        if not action.get("supported", True):
            unsupported_items.append(action.get("metadata", {}).get("item_id", action.get("action_id")))

    best_first_action_id = str((best.get("actions") or [{}])[0].get("action_id", ""))
    proposal_budget_false_prune = int(bool(budget_audit_action_ids and best_first_action_id not in budget_audit_action_ids))

    numeric_chain_candidates = []
    for index, chain in enumerate(completed[: max(256, beam_size)]):
        chain_actions = chain.get("actions", [])
        numeric_chain_candidates.append({
            "action_id": f"chain:{index}:{hashlib.sha256(str(chain.get('state_hash', '')).encode('utf-8')).hexdigest()[:16]}", "action_type": "candidate_chain",
            "system": str(chain_actions[0].get("system", "unknown")) if chain_actions else "save_hold",
            "expected_damage_delta": float(chain.get("score", 0.0)),
            "long_term_value": sum(float(action.get("long_term_value", 0.0)) for action in chain_actions),
            "breakpoint_value": sum(float(action.get("breakpoint_value", 0.0)) for action in chain_actions),
            "resource_efficiency": -float(chain.get("rare_resources_spent", 0.0)),
            "confidence": "low" if any(action.get("warnings") for action in chain_actions) else "medium",
            "supported": all(action.get("supported", True) for action in chain_actions),
            "warnings": [warning for action in chain_actions for warning in action.get("warnings", [])],
            "consumed_items": {"rare_resource_units": float(chain.get("rare_resources_spent", 0.0))},
            "metadata": {"synergy_score": max(0, len({action.get('system') for action in chain_actions}) - 1), "chain_depth": len(chain_actions)},
        })

    planner_elapsed = time.perf_counter() - planner_started
    useful_topk_rate = states_materialized / max(1, proposal_rows_created)
    waste_by_system = {
        system: max(0, int(actions_by_system[system]) - int(chains_by_system[system]))
        for system in actions_by_system
    }
    return {
        "best_action_chain": _chain_explanation(best, original_state, knowledge),
        "alternative_chains": [_chain_explanation(chain, original_state, knowledge) for chain in alternatives],
        "avoid_chains": [_chain_explanation(chain, original_state, knowledge) for chain in avoid],
        "actions_considered": actions_considered,
        "chains_considered": chains_considered,
        "raw_proposal_rows": raw_proposal_rows,
        "proposal_rows_created": proposal_rows_created,
        "proposal_rows_budget_removed": proposal_rows_budget_removed,
        "states_materialized": states_materialized,
        "actions_pruned": actions_pruned + preprune_candidates_removed,
        "dominated_states_removed": dominated_removed,
        "systems_covered": sorted(systems_covered),
        "actions_by_system": dict(actions_by_system),
        "chains_by_system": dict(chains_by_system),
        "systems_not_covered": sorted(set(registry_systems()) - systems_covered),
        "unsupported_items": sorted(set(str(item) for item in unsupported_items if item)),
        "numeric_chain_candidates": numeric_chain_candidates,
        "performance": {
            "global_planner_seconds": round(planner_elapsed, 6),
            "cpu_candidate_seconds": round(
                planner_timings["root_action_generation"]
                + planner_timings["candidate_row_creation"]
                + planner_timings["numeric_feature_creation"],
                6,
            ),
            "root_action_generation_seconds": round(planner_timings["root_action_generation"], 6),
            "candidate_row_creation_seconds": round(planner_timings["candidate_row_creation"], 6),
            "numeric_feature_creation_seconds": round(planner_timings["numeric_feature_creation"], 6),
            "state_transition_and_hashing_seconds": round(planner_timings["state_transition_and_hashing"], 6),
            "gpu_queue_wait_seconds": round(preprune_wait_seconds, 6),
            "state_copy_count": states_materialized,
            "state_rebuild_count": states_materialized,
            "duplicate_candidates_removed": equivalent_actions_removed,
            "useful_topk_rate": round(useful_topk_rate, 6),
            "waste_by_system": dict(sorted(waste_by_system.items(), key=lambda item: item[1], reverse=True)),
        },
        "learned_ranker_applied": bool(learned_ranker_weights),
        "gpu_preprune": {
            "enabled": gpu_preprune,
            "rows_submitted": preprune_rows_submitted,
            "gpu_rows_scored": preprune_gpu_rows_scored,
            "requests": preprune_requests,
            "wait_seconds": round(preprune_wait_seconds, 6),
            "fallbacks": dict(preprune_fallbacks),
            "candidates_removed_before_state_transition": preprune_candidates_removed,
            "equivalent_actions_removed_before_gpu": equivalent_actions_removed,
            "action_templates_reused": action_templates_reused,
            "prebuilt_root_actions_reused": len(root_actions) if reused_prebuilt_root else 0,
            "action_generator_refreshes": action_generator_refreshes,
            "affected_system_refreshes": dict(affected_system_refreshes),
            "proposal_budget": root_budget_stats,
            "proposal_budget_enabled": proposal_budget_enabled,
            "proposal_budget_limit": total_proposal_budget,
            "proposal_budget_audited": bool(budget_audit_action_ids),
            "proposal_budget_false_prunes": proposal_budget_false_prune,
            "proposal_budget_missed_action": best_first_action_id if proposal_budget_false_prune else "",
            "proposal_row_budget_multiplier": proposal_row_budget_multiplier,
            "proposal_row_budget_audits": proposal_row_budget_audits,
            "proposal_row_budget_false_prunes": proposal_row_budget_false_prunes,
            "transition_budget": transition_budget,
            "min_gpu_preprune_rows": min_gpu_preprune_rows,
            "preprune_cpu_rows_ranked": preprune_cpu_rows_ranked,
            "full_search_audits": preprune_full_search_audits,
            "false_prunes": preprune_false_prunes,
            "corrections": preprune_corrections,
            "false_prune_rate": round(preprune_false_prunes / max(1, preprune_full_search_audits + preprune_corrections), 6),
        },
        "confidence": "medium" if best.get("score", 0) > 0 else "low",
        "search_mode": search_mode,
        "exhaustive": exhaustive,
        "explanation": {
            "why": _chain_explanation(best, original_state, knowledge)["summary"],
            "honesty": "Search is exhaustive only for the bounded depth/action set when exhaustive=true; otherwise beam pruning is used.",
        },
    }


# GLOBAL_PLAN_GUARDRAILS_AUTO_PATCH_V1
try:
    import sys as _survivor_sys
    from optimizer.global_plan_guardrails import patch_module_functions as _survivor_patch_module_functions
    _survivor_patch_module_functions(_survivor_sys.modules[__name__])
except Exception:
    pass

