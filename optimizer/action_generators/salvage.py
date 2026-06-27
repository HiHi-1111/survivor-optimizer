from __future__ import annotations

from typing import Any

from optimizer.action_generators.common import base_action, inventory_count, record_id, record_name, record_value, save_hold_action, tags


def generate_actions(player_state: Any, knowledge: dict[str, Any], options: dict[str, Any] | None = None):
    actions = []
    include_saves = (options or {}).get("include_saves", True)
    for section in ["gear", "pets", "tech_parts", "collectibles", "items"]:
        for record in knowledge.get(section, []):
            item_id = record_id(record)
            count = inventory_count(player_state, item_id)
            if count <= 0:
                continue
            metadata = record_value(record, "metadata", {}) or {}
            safe = bool(metadata.get("salvage_safe")) or "salvage_safe" in tags(record)
            action = base_action(
                action_type="salvage_item" if safe else "check_salvage_safety",
                system="salvage",
                item_id=item_id,
                name=record_name(record),
                explanation=f"Evaluate salvage safety for {record_name(record)} without assuming unknown returns.",
                record=record,
                supported=safe,
                warnings=[] if safe else ["No source-backed safe salvage rule; destructive action is blocked."],
            )
            action.required_items[item_id] = 1
            if safe:
                action.consumed_items[item_id] = 1
            action.reversibility = "irreversible"
            action.metadata.update({"source_section": section, "salvage_safe": safe})
            actions.append(action)
            if include_saves:
                actions.append(save_hold_action("salvage", item_id, record_name(record), "salvage safety or future breakpoint value is uncertain.", record))
    return actions
