from __future__ import annotations

from typing import Any

from optimizer.action_generators.generic import generate_known_record_actions
from optimizer.action_generators.common import base_action, inventory_count, record_id, record_name, record_value


def generate_actions(player_state: Any, knowledge: dict[str, Any], options: dict[str, Any] | None = None):
    if (options or {}).get("proposal_budget"):
        crystals = inventory_count(player_state, "awakening_crystal")
        if crystals <= 0:
            return []
        main_pet = str(getattr(getattr(player_state, "pets", None), "main_pet", "") or "")
        if not main_pet:
            for pet in knowledge.get("pets", []):
                pet_id = record_id(pet)
                if inventory_count(player_state, pet_id) > 0:
                    main_pet = pet_id
                    break
        if not main_pet:
            return []
        cost_record = next(iter(knowledge.get("pet_awakenings", [])), None)
        costs = record_value(cost_record, "cost_by_level", {}) or {}
        first_cost = float(costs.get("Y1", 5) or 5)
        if crystals < first_cost:
            return []
        action = base_action(
            action_type="awaken_pet", system="pet_awakenings", item_id=main_pet,
            name=f"Awaken {main_pet}", explanation=f"Spend {first_cost:g} awakening crystals on the deployed pet's next sourced awakening step.",
            record=cost_record,
        )
        action.required_items["awakening_crystal"] = first_cost
        action.consumed_items["awakening_crystal"] = first_cost
        action.affected_pet.append(main_pet)
        action.metadata.update({"inventory_count": crystals, "adds_progress": {"pet_awakening_breakpoint": 1}, "breakpoint_requirements": {"pet_awakening_breakpoint": 2}})
        return [action]
    return generate_known_record_actions(
        player_state=player_state, records=knowledge.get("pet_awakenings", []), system="pet_awakenings",
        action_type="awaken_pet", affected_field="affected_pet", include_saves=(options or {}).get("include_saves", True),
    )
