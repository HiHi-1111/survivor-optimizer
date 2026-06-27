from __future__ import annotations

from typing import Any

from optimizer.action_generators.generic import generate_known_record_actions


def generate_actions(player_state: Any, knowledge: dict[str, Any], options: dict[str, Any] | None = None):
    actions = generate_known_record_actions(player_state=player_state, records=knowledge.get("survivor_awakenings", []), system="survivor_awakening", action_type="awaken_survivor", affected_field="affected_survivor", include_saves=(options or {}).get("include_saves", True))
    for action in actions:
        if action.action_type != "save_hold":
            action.metadata.setdefault("sets_breakpoints", []).append("survivor_breakpoint")
    return actions
