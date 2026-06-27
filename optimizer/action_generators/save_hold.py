from __future__ import annotations

from typing import Any

from optimizer.action_generators.common import inventory_count, record_id, record_name, save_hold_action


def generate_actions(player_state: Any, knowledge: dict[str, Any], options: dict[str, Any] | None = None):
    candidates = []
    for section in ["resources", "items", "chests"]:
        for record in knowledge.get(section, []):
            item_id = record_id(record)
            if inventory_count(player_state, item_id) > 0:
                priority = 2 if any(term in item_id.lower() for term in ["astral", "xeno", "resonance", "relic"]) else 1
                candidates.append((priority, item_id, record))
    if not candidates:
        return []
    _, item_id, record = max(candidates, key=lambda row: (row[0], row[1]))
    return [save_hold_action("save_hold", item_id, record_name(record), "holding preserves the highest-value uncertain resource option.", record)]
