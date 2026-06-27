from __future__ import annotations

from typing import Any

from optimizer.action_generators.common import base_action, inventory_count, record_id, record_name, save_hold_action


def generate_actions(player_state: Any, knowledge: dict[str, Any], options: dict[str, Any] | None = None):
    actions = []
    include_saves = (options or {}).get("include_saves", True)
    for section in ["gear", "pets", "tech_parts"]:
        for record in knowledge.get(section, []):
            item_id = record_id(record)
            count = inventory_count(player_state, item_id)
            if count < 2:
                continue
            action = base_action(
                action_type="merge_duplicates",
                system="merge",
                item_id=item_id,
                name=record_name(record),
                explanation=f"Merge duplicate {record_name(record)} copies using only the known inventory id.",
                record=record,
            )
            action.required_items[item_id] = 2
            action.consumed_items[item_id] = 2
            action.produced_items[item_id] = 1
            action.metadata.update({"source_section": section, "inventory_count": count, "merge_result_unknown": True})
            action.warnings.append("Exact rarity result is unknown; value is not invented.")
            actions.append(action)
            if include_saves:
                actions.append(save_hold_action("merge", item_id, record_name(record), "duplicates may enable a later breakpoint.", record))
    return actions
