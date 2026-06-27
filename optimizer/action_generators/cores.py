from __future__ import annotations

from typing import Any

from optimizer.action_generators.generic import generate_known_record_actions
from optimizer.action_generators.common import record_id, tags


def generate_actions(player_state: Any, knowledge: dict[str, Any], options: dict[str, Any] | None = None):
    records = [record for record in knowledge.get("resources", []) if "core" in record_id(record).lower() or "core" in tags(record)]
    actions = generate_known_record_actions(player_state=player_state, records=records, system="cores", action_type="spend_core", include_saves=(options or {}).get("include_saves", True))
    for action in actions:
        if action.action_type == "save_hold":
            continue
        item_id = str(action.metadata.get("item_id", "")).lower()
        if "astral" in item_id or "relic" in item_id:
            action.metadata.setdefault("adds_progress", {})["astral_forge_breakpoint"] = 1
            action.metadata.setdefault("breakpoint_requirements", {})["astral_forge_breakpoint"] = 2
        elif "xeno" in item_id:
            action.metadata.setdefault("adds_progress", {})["xeno_breakpoint"] = 1
            action.metadata.setdefault("breakpoint_requirements", {})["xeno_breakpoint"] = 2
    return actions
