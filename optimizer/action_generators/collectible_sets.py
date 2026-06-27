"""Collectible set action generator."""

from typing import Any

from optimizer.action_generators.generic import generate_known_record_actions
from optimizer.action_generators.common import base_action, inventory_count, save_hold_action



def _owned_count(player_state: Any, item_id: str) -> float:
    """Count item/resource from dict, pydantic model, or existing inventory helper."""
    # Raw dict input
    if isinstance(player_state, dict):
        total = 0.0
        for key in ("items", "resources", "inventory", "currencies"):
            container = player_state.get(key, {})
            if isinstance(container, dict):
                total += float(container.get(item_id, 0) or 0)
        if total > 0:
            return total

    # Pydantic/object input
    for key in ("items", "resources", "inventory", "currencies"):
        container = getattr(player_state, key, None)
        if isinstance(container, dict):
            value = container.get(item_id, 0) or 0
            if value:
                return float(value)
        elif hasattr(container, "model_dump"):
            data = container.model_dump()
            if isinstance(data, dict):
                value = data.get(item_id, 0) or 0
                if value:
                    return float(value)
        elif container is not None and hasattr(container, item_id):
            value = getattr(container, item_id, 0) or 0
            if value:
                return float(value)

    # Existing project helper fallback
    try:
        return float(inventory_count(player_state, item_id) or 0)
    except Exception:
        return 0.0



def _first_record(records: list[Any]) -> Any | None:
    return records[0] if records else None


def _add_collectible_set_bridge_actions(player_state: Any, knowledge: dict[str, Any]) -> list[Any]:
    """Create collectible-set actions from chest/shard resources."""
    actions = []
    records = knowledge.get("collectible_sets", [])
    record = _first_record(records)

    red_chest_count = _owned_count(player_state, "red_collectible_chest")
    shard_count = _owned_count(player_state, "collectible_shard")

    if red_chest_count > 0 or shard_count > 0:
        action = base_action(
            action_type="progress_collectible_set",
            system="collectible_sets",
            item_id="collectible_set_progress",
            name="Collectible Set Progress",
            explanation="Evaluate collectible chest/shards toward collectible set breakpoint and DPS-related set bonuses.",
            record=record,
            supported=True,
        )

        if red_chest_count > 0:
            action.required_items["red_collectible_chest"] = 1
            action.consumed_items["red_collectible_chest"] = 1

        if shard_count > 0:
            action.required_items["collectible_shard"] = min(shard_count, 10)
            action.consumed_items["collectible_shard"] = min(shard_count, 10)

        action.metadata.setdefault("sets_breakpoints", []).append("collectible_set_breakpoint")
        action.metadata["inventory_count"] = red_chest_count + shard_count
        action.metadata["bridge_action"] = True
        action.metadata["bridge_reason"] = "collectible chest/shards can advance collectible set bonuses before exact target is known"
        action.affected_collectible.append("collectible_set_progress")
        actions.append(action)

    return actions


def generate_actions(player_state: Any, knowledge: dict[str, Any], options: dict[str, Any] | None = None):
    actions = generate_known_record_actions(
        player_state=player_state,
        records=knowledge.get("collectible_sets", []),
        system="collectible_sets",
        action_type="complete_collectible_set",
        affected_field="affected_collectible",
        include_saves=(options or {}).get("include_saves", True),
    )
    for action in actions:
        if action.action_type != "save_hold":
            action.metadata.setdefault("sets_breakpoints", []).append("collectible_set_breakpoint")

    actions.extend(_add_collectible_set_bridge_actions(player_state, knowledge))
    return actions
