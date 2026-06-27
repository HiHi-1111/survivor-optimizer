from __future__ import annotations

from typing import Any

from optimizer.action_generators.generic import generate_selector_actions


def generate_actions(player_state: Any, knowledge: dict[str, Any], options: dict[str, Any] | None = None):
    actions = generate_selector_actions(
        player_state,
        knowledge.get("chests", []),
        include_saves=(options or {}).get("include_saves", True),
    )
    return [action for action in actions if action.action_type in {"select_from_chest", "unsupported_selector"}]
