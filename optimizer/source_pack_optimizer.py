"""Profile-gated numeric ranking for exact source-pack actions.

This intentionally does not use learned pruning. Every template is either
scored or returned with its deterministic rejection reason, which makes false
prunes impossible in this path and keeps coverage auditable.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from optimizer.gpu_batch_engine import GpuBatchEngine
from optimizer.numeric_tables import action_candidate_matrix, inventory_feature_matrix, profile_feature_matrix
from optimizer.player_state import PlayerState


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PACK_DIR = ROOT / "knowledge" / "source_pack"
RARITIES = ["Base", "Y1", "Y2", "Y3", "Y4", "R1", "R2", "R3", "R4"]


@lru_cache(maxsize=1)
def load_source_pack_actions() -> list[dict[str, Any]]:
    path = SOURCE_PACK_DIR / "action_templates.json"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        actions = json.load(handle)
    return [action for action in actions if action.get("enabled")]


def clear_source_pack_cache() -> None:
    load_source_pack_actions.cache_clear()


def _profile_data(profile: PlayerState | dict[str, Any]) -> dict[str, Any]:
    if isinstance(profile, PlayerState):
        return profile.model_dump()
    return profile


def _resource_counts(profile: dict[str, Any]) -> dict[str, float]:
    counts: dict[str, float] = {}
    for section in (profile.get("resources", {}), profile.get("inventory", {}).get("items", {})):
        for key, value in section.items():
            if isinstance(value, (int, float)):
                counts[str(key)] = counts.get(str(key), 0.0) + float(value)
            elif isinstance(value, dict) and isinstance(value.get("quantity"), (int, float)):
                counts[str(key)] = counts.get(str(key), 0.0) + float(value["quantity"])
    return counts


def _mount_state(profile: dict[str, Any], mount_id: str) -> dict[str, Any] | None:
    mounts = profile.get("mounts", {})
    state = mounts.get(mount_id) if isinstance(mounts, dict) else None
    if isinstance(state, str):
        return {"rarity": state}
    if isinstance(state, dict):
        return state
    if mount_id in profile.get("owned_items", []):
        return {"rarity": "Base"}
    return None


def _gate(action: dict[str, Any], profile: dict[str, Any], resources: dict[str, float]) -> tuple[bool, str | None]:
    requirements = action.get("requirements", {})
    mount_id = requirements.get("mount_owned")
    if mount_id:
        state = _mount_state(profile, str(mount_id))
        if state is None:
            return False, f"mount_not_owned:{mount_id}"
        target = str(requirements.get("current_rarity_precedes"))
        current = str(state.get("rarity", "Base"))
        target_index = RARITIES.index(target) if target in RARITIES else -1
        required = RARITIES[target_index - 1] if target_index > 0 else None
        if current != required:
            return False, f"rarity_gate:requires_{required}:has_{current}"

    required_multiplier = requirements.get("current_multiplier")
    if required_multiplier is not None:
        resonance = profile.get("tech_parts", {}).get("resonance", {})
        current = float(resonance.get("multiplier", 1.0))
        if abs(current - float(required_multiplier)) > 1e-6:
            return False, f"multiplier_gate:requires_{required_multiplier}:has_{current}"

    for cost in action.get("costs", []):
        resource_id = str(cost["resource_id"])
        required = float(cost["amount"])
        available = resources.get(resource_id, 0.0)
        if available < required:
            return False, f"insufficient_{resource_id}:requires_{required:g}:has_{available:g}"
    return True, None


def _effect_gain(action: dict[str, Any], profile: dict[str, Any]) -> float:
    gain = 0.0
    for effect in action.get("effects", []):
        value = float(effect.get("value", 0.0))
        if effect.get("effect_type") == "attack_sync_rate_target":
            state = _mount_state(profile, action["target_id"]) or {}
            value = max(0.0, value - float(state.get("sync_rate", 0.0))) * 100.0
        elif effect.get("unit") == "multiplier":
            value *= 100.0
        gain += value
    return round(gain, 9)


def _resource_penalty(action: dict[str, Any], resources: dict[str, float]) -> float:
    penalty = 0.0
    for cost in action.get("costs", []):
        resource_id = str(cost["resource_id"])
        amount = float(cost["amount"])
        available = max(amount, resources.get(resource_id, amount))
        rarity_weight = 1.0 if any(token in resource_id for token in ("core", "chip", "selector")) else 0.25
        penalty += rarity_weight * amount / available
    return round(penalty, 9)


def _prepare_profile(profile: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[dict[str, str]]]:
    resources = _resource_counts(profile)
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    deduplicated: list[dict[str, str]] = []
    by_effect: dict[tuple[Any, ...], dict[str, Any]] = {}
    for action in load_source_pack_actions():
        allowed, reason = _gate(action, profile, resources)
        if not allowed:
            rejected.append({"action_id": action["action_id"], "reason": str(reason)})
            continue
        gain = _effect_gain(action, profile)
        rare_cost = sum(float(cost["amount"]) for cost in action.get("costs", []))
        resource_penalty = _resource_penalty(action, resources)
        candidate = {
            **action,
            "expected_dps_gain": gain,
            "estimated_dps_value": gain,
            "total_cost_units": rare_cost,
            "resource_cost_penalty": resource_penalty,
            "breakpoint_distance": (
                float(action["unlock_target"]) - float(action["requirements"]["current_multiplier"])
                if action.get("action_type") == "upgrade_resonance_multiplier" else 1.0
            ),
        }
        effect_key = (
            action["system"], action["target_id"],
            tuple(sorted((cost["resource_id"], float(cost["amount"])) for cost in action.get("costs", []))),
            tuple(sorted((effect["effect_type"], gain, effect.get("damage_bucket")) for effect in action.get("effects", []))),
            action.get("unlock_target"),
        )
        existing = by_effect.get(effect_key)
        if existing is not None:
            existing.setdefault("aliases", []).append(action["action_id"])
            deduplicated.append({"action_id": action["action_id"], "kept": existing["action_id"]})
            continue
        by_effect[effect_key] = candidate
        candidates.append(candidate)
    return candidates, rejected, deduplicated


def optimize_source_pack_batch(
    player_states: list[PlayerState | dict[str, Any]], *, top_k: int = 10, device: str = "auto"
) -> dict[str, Any]:
    """Gate many profiles, then score and top-k them in one numeric device batch."""
    profiles = [_profile_data(state) for state in player_states]
    prepared = [_prepare_profile(profile) for profile in profiles]
    candidate_groups = [item[0] for item in prepared]
    matrices = action_candidate_matrix(candidate_groups)
    profile_matrix = profile_feature_matrix(profiles)
    inventory_matrix = inventory_feature_matrix(profiles)
    engine = GpuBatchEngine(device=device)
    ranked_indices, stats = engine.rank_grouped(matrices, [1.0, -1.0], top_k)
    results = []
    for candidates, rejected, deduplicated, indices, matrix in zip(
        candidate_groups,
        [item[1] for item in prepared],
        [item[2] for item in prepared],
        ranked_indices,
        matrices,
    ):
        ranked = []
        for index in indices:
            candidate = candidates[index]
            ranked.append({**candidate, "score": round(matrix[index][0] - matrix[index][1], 6)})
        results.append({
            "best": ranked[0] if ranked else None,
            "ranked_actions": ranked,
            "ranked_alternatives": ranked[1:],
            "templates_considered": len(load_source_pack_actions()),
            "actionable_count": len(candidates),
            "rejected_count": len(rejected),
            "rejected_actions": rejected,
            "deduplicated_actions": deduplicated,
            "false_prunes": [],
            "pruning_policy": "disabled; deterministic profile gates and exact-effect dedupe only",
            "warnings": [],
            "explanation": (
                f"{ranked[0]['action_id']} has the highest exact-source DPS feature score after resource cost penalty."
                if ranked else "No exact-source action passes the current profile and resource gates."
            ),
        })
    return {
        "profiles": results,
        "numeric_backend": stats,
        "profile_feature_matrix_shape": [len(profile_matrix), len(profile_matrix[0]) if profile_matrix else 0],
        "inventory_feature_matrix_shape": [len(inventory_matrix), len(inventory_matrix[0]) if inventory_matrix else 0],
    }


def optimize_source_pack_actions(
    player_state: PlayerState | dict[str, Any], *, top_k: int = 10, device: str = "auto"
) -> dict[str, Any]:
    batch = optimize_source_pack_batch([player_state], top_k=top_k, device=device)
    result = batch["profiles"][0]
    result["numeric_backend"] = batch["numeric_backend"]
    return result
