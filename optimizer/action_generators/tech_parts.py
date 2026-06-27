from __future__ import annotations

from typing import Any

from optimizer.action_generators.generic import generate_known_record_actions
from optimizer.action_generators.common import description, record_id, tags


def generate_actions(player_state: Any, knowledge: dict[str, Any], options: dict[str, Any] | None = None):
    records = list(knowledge.get("tech_parts", []))
    return generate_known_record_actions(
        player_state=player_state,
        records=records,
        system="tech_parts",
        action_type="upgrade_or_equip_tech",
        affected_field="affected_tech",
        include_saves=(options or {}).get("include_saves", True),
    )
