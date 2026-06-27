from __future__ import annotations

from typing import Any

from optimizer.action_generators.generic import generate_known_record_actions


def generate_actions(player_state: Any, knowledge: dict[str, Any], options: dict[str, Any] | None = None):
    return generate_known_record_actions(
        player_state=player_state,
        records=knowledge.get("events", []),
        system="events",
        action_type="event_action",
        include_saves=(options or {}).get("include_saves", True),
    )
