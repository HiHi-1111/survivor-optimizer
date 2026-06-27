from __future__ import annotations

from typing import Any

from optimizer.action_generators.generic import generate_known_record_actions
from optimizer.action_generators.common import description, record_id, tags


def generate_actions(player_state: Any, knowledge: dict[str, Any], options: dict[str, Any] | None = None):
    records = list(knowledge.get("xeno_pets", [])) + [record for record in knowledge.get("resources", []) if "xeno" in f"{record_id(record)} {description(record)} {' '.join(tags(record))}".lower()]
    return generate_known_record_actions(
        player_state=player_state,
        records=records,
        system="xeno_pets",
        action_type="upgrade_or_unlock_xeno_pet",
        affected_field="affected_pet",
        include_saves=(options or {}).get("include_saves", True),
    )
