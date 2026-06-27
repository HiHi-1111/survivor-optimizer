from __future__ import annotations

from typing import Any

from optimizer.action_generators.generic import generate_known_record_actions
from optimizer.action_generators.common import base_action, description, inventory_count, record_id, record_name, tags


def generate_actions(player_state: Any, knowledge: dict[str, Any], options: dict[str, Any] | None = None):
    if (options or {}).get("proposal_budget"):
        chip_count = inventory_count(player_state, "resonance_chip")
        if chip_count <= 0:
            return []
        actions = []
        for record in knowledge.get("tech_resonance", []):
            item_id = record_id(record)
            action = base_action(
                action_type="advance_resonance", system="resonance", item_id=item_id, name=record_name(record),
                explanation=f"Advance {record_name(record)} with a resonance chip toward its next reviewed breakpoint.", record=record,
            )
            action.required_items["resonance_chip"] = 1
            action.consumed_items["resonance_chip"] = 1
            action.affected_tech.append(item_id)
            action.metadata.update({"inventory_count": chip_count, "adds_progress": {"tech_resonance_breakpoint": 1}, "breakpoint_requirements": {"tech_resonance_breakpoint": 2}})
            actions.append(action)
        return actions
    records = list(knowledge.get("tech_parts", [])) + list(knowledge.get("tech_resonance", [])) + list(knowledge.get("tech_resonance_costs", [])) + list(knowledge.get("resources", []))
    records = [record for record in records if "resonance" in f"{record_id(record)} {description(record)} {' '.join(tags(record))}".lower()]
    actions = generate_known_record_actions(player_state=player_state, records=records, system="resonance", action_type="advance_resonance", affected_field="affected_tech", include_saves=(options or {}).get("include_saves", True))
    for action in actions:
        if action.action_type != "save_hold":
            action.metadata.setdefault("adds_progress", {})["tech_resonance_breakpoint"] = 1
            action.metadata.setdefault("breakpoint_requirements", {})["tech_resonance_breakpoint"] = 2
    return actions
