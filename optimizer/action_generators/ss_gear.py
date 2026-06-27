from __future__ import annotations

from typing import Any

from optimizer.action_generators.common import base_action, inventory_count, record_id, record_name, save_hold_action


CORE_IDS = ["relic_core", "astral_core", "eternal_core", "void_core", "chaos_core", "xeno_core"]
SLOTS = ["weapon", "necklace", "gloves", "armor", "belt", "boots"]


def generate_actions(player_state: Any, knowledge: dict[str, Any], options: dict[str, Any] | None = None):
    actions = []
    resource_records = {record_id(record): record for record in knowledge.get("resources", [])}
    include_saves = (options or {}).get("include_saves", True)
    for core_id in CORE_IDS:
        count = inventory_count(player_state, core_id)
        record = resource_records.get(core_id)
        if count <= 0:
            continue
        # Slot-specific branches currently have identical transitions and
        # value because source-backed per-slot SS effects are unavailable.
        # Emit one numeric branch and retain every candidate slot as review
        # metadata instead of generating six equivalent deep-search states.
        action = base_action(
            action_type="allocate_ss_core",
            system="ss_gear",
            item_id=f"{core_id}_best_damage_slot",
            name=f"{record_name(record) if record else core_id} -> best damage slot",
            explanation=f"Evaluate allocating {core_id} toward the best source-backed SS damage slot; exact slot needs reviewed data.",
            record=record,
            supported=count > 0 and record is not None,
            warnings=[] if record is not None else ["Core id not present in knowledge/resources.json."],
        )
        action.required_items[core_id] = 1
        action.consumed_items[core_id] = 1
        action.affected_equipment.extend(SLOTS)
        action.metadata.update({"inventory_count": count, "item_id": core_id, "candidate_slots": list(SLOTS), "slot_selection": "needs_review"})
        actions.append(action)
        if include_saves and count > 0:
            actions.append(save_hold_action("ss_gear", core_id, record_name(record) if record else core_id, "core may be better saved for a known breakpoint.", record))
    return actions
