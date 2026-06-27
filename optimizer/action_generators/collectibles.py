"""Collectible action generator."""

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


def _add_collectible_bridge_actions(player_state: Any, knowledge: dict[str, Any]) -> list[Any]:
    """Create actions from collectible chests/shards even when the exact collectible is unknown."""
    actions = []
    records = knowledge.get("collectibles", [])
    record = _first_record(records)

    red_chest_count = _owned_count(player_state, "red_collectible_chest")
    shard_count = _owned_count(player_state, "collectible_shard")

    if red_chest_count > 0:
        action = base_action(
            action_type="open_collectible_chest",
            system="collectibles",
            item_id="red_collectible_chest",
            name="Red Collectible Chest",
            explanation="Open/evaluate red collectible chest for collectible and set progress.",
            record=record,
            supported=True,
        )
        action.required_items["red_collectible_chest"] = 1
        action.consumed_items["red_collectible_chest"] = 1
        action.metadata.setdefault("adds_progress", {})["collectible_set_breakpoint"] = 1
        action.metadata.setdefault("breakpoint_requirements", {})["collectible_set_breakpoint"] = 1
        action.metadata.setdefault("sets_breakpoints", []).append("collectible_set_breakpoint")
        action.metadata["inventory_count"] = red_chest_count
        action.metadata["bridge_action"] = True
        action.metadata["bridge_reason"] = "collectible chest can create collectible/set progress before exact drop is known"
        action.affected_collectible.append("red_collectible_chest")
        actions.append(action)
        actions.append(save_hold_action(
            "collectibles",
            "red_collectible_chest",
            "Red Collectible Chest",
            "collectible chest value depends on set progress and exact drop data.",
            record,
        ))

    if shard_count > 0:
        action = base_action(
            action_type="use_collectible_shards",
            system="collectibles",
            item_id="collectible_shard",
            name="Collectible Shards",
            explanation="Use/evaluate collectible shards for collectible set progress.",
            record=record,
            supported=True,
        )
        action.required_items["collectible_shard"] = min(shard_count, 10)
        action.consumed_items["collectible_shard"] = min(shard_count, 10)
        action.metadata.setdefault("adds_progress", {})["collectible_set_breakpoint"] = 1
        action.metadata.setdefault("breakpoint_requirements", {})["collectible_set_breakpoint"] = 1
        action.metadata.setdefault("sets_breakpoints", []).append("collectible_set_breakpoint")
        action.metadata["inventory_count"] = shard_count
        action.metadata["bridge_action"] = True
        action.metadata["bridge_reason"] = "collectible shards can create collectible/set progress before exact target is known"
        action.affected_collectible.append("collectible_shard")
        actions.append(action)

    return actions


def generate_actions(player_state: Any, knowledge: dict[str, Any], options: dict[str, Any] | None = None):
    actions = generate_known_record_actions(
        player_state=player_state,
        records=knowledge.get("collectibles", []),
        system="collectibles",
        action_type="upgrade_or_unlock_collectible",
        affected_field="affected_collectible",
        include_saves=(options or {}).get("include_saves", True),
    )
    actions.extend(_add_collectible_bridge_actions(player_state, knowledge))
    return actions
