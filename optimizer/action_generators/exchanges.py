from __future__ import annotations

from typing import Any

from optimizer.action_generators.common import base_action, inventory_count, record_id, record_name


def generate_actions(player_state: Any, knowledge: dict[str, Any], options: dict[str, Any] | None = None):
    actions = []
    records = list(knowledge.get("universal_exchange", [])) + list(knowledge.get("conversions", [])) + list(knowledge.get("resources", []))
    for record in records:
        item_id = record_id(record)
        count = inventory_count(player_state, item_id)
        text = f"{item_id} {record_name(record)}".lower()
        if count > 0 and any(term in text for term in ["exchange", "currency", "event", "clan", "gem"]):
            action = base_action(
                action_type="exchange_resource",
                system="exchanges",
                item_id=item_id,
                name=record_name(record),
                explanation=f"Evaluate exchange options for {record_name(record)} if shop/exchange data exists.",
                record=record,
                supported=record in knowledge.get("universal_exchange", []),
                warnings=[] if record in knowledge.get("universal_exchange", []) else ["No concrete exchange table is known yet; action is coverage-only."],
            )
            action.required_items[item_id] = 1
            actions.append(action)
    return actions
