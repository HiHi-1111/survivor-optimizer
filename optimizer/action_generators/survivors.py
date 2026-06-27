from __future__ import annotations

from typing import Any

from optimizer.action_generators.generic import generate_known_record_actions
from optimizer.action_generators.common import description, record_id, tags


def generate_actions(player_state: Any, knowledge: dict[str, Any], options: dict[str, Any] | None = None):
    records = list(knowledge.get("survivors", [])) + list(knowledge.get("survivor_awakenings", [])) + list(knowledge.get("survivor_energy_essence_costs", []))
    records += [record for record in knowledge.get("resources", []) if "survivor" in f"{record_id(record)} {description(record)} {' '.join(tags(record))}".lower()]
    if (options or {}).get("proposal_budget"):
        # Catalog survivor ownership is not an upgrade action. Until a record
        # has a source-backed cost, do not consume a survivor/currency merely
        # because it exists in knowledge.
        records = [record for record in records if getattr(record, "cost", None) or getattr(record, "upgrade_cost", None)]
    return generate_known_record_actions(
        player_state=player_state,
        records=records,
        system="survivors",
        action_type="upgrade_or_switch_survivor",
        affected_field="affected_survivor",
        include_saves=(options or {}).get("include_saves", True),
    )
