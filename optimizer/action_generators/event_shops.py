from __future__ import annotations

from typing import Any

from optimizer.action_generators.common import base_action, inventory_count, record_id, record_name, record_value, save_hold_action


def generate_actions(player_state: Any, knowledge: dict[str, Any], options: dict[str, Any] | None = None):
    actions = []
    include_saves = (options or {}).get("include_saves", True)
    for record in knowledge.get("event_shops", []):
        item_id = record_id(record)
        cost = record_value(record, "cost", {}) or {}
        affordable = bool(cost) and all(inventory_count(player_state, currency) >= float(amount) for currency, amount in cost.items())
        action = base_action(
            action_type="buy_event_shop_item" if affordable else "check_event_shop_item",
            system="event_shops", item_id=item_id, name=record_name(record),
            explanation=f"Evaluate {record_name(record)} against its source-backed event-shop cost.", record=record,
            supported=affordable,
            warnings=[] if affordable else ["Missing shop cost or insufficient known currency; purchase is not assumed."],
        )
        for currency, amount in cost.items():
            action.required_items[str(currency)] = float(amount)
            if affordable:
                action.consumed_items[str(currency)] = float(amount)
        if affordable:
            action.produced_items[item_id] = 1
        action.metadata["cost"] = cost
        actions.append(action)
        if include_saves:
            for currency in cost:
                actions.append(save_hold_action("event_shops", str(currency), str(currency), "event currency may buy a stronger breakpoint later.", record))
    return actions
