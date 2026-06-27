from __future__ import annotations

from typing import Any

from optimizer.action_generators.generic import generate_selector_actions


def generate_actions(player_state: Any, knowledge: dict[str, Any], options: dict[str, Any] | None = None):
    actions = generate_selector_actions(
        player_state,
        list(knowledge.get("chests", [])) + list(knowledge.get("chest_odds", [])) + list(knowledge.get("collectible_chest_odds", [])),
        include_saves=(options or {}).get("include_saves", True),
    )
    return [action for action in actions if action.action_type.startswith("open_random_chest_") or action.action_type == "save_hold"]
