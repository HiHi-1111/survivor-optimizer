"""Apply inventory actions to player states."""

from __future__ import annotations

import time
from typing import Any

from optimizer.player_state import PlayerState, validate_player_state


_STATE_COPY_CALLS = 0
_STATE_COPY_SECONDS = 0.0
_STATE_REBUILD_CALLS = 0


def _copy_for_action(source: PlayerState) -> PlayerState:
    """Copy only state branches mutated by ``apply_action``.

    Planner actions never mutate build/gear/pet/tech/collectible models. Sharing
    those immutable branches avoids rebuilding the complete validated model for
    every beam candidate while resources, inventory, owned items, and mutable
    metadata remain isolated.
    """
    state = source.model_copy()
    state.resources = source.resources.model_copy()
    state.inventory = source.inventory.model_copy(update={"items": dict(source.inventory.items)})
    state.owned_items = list(source.owned_items)
    metadata = dict(getattr(source, "metadata", {}) or {})
    if "progress" in metadata:
        metadata["progress"] = dict(metadata.get("progress", {}) or {})
    if "reached_breakpoints" in metadata:
        metadata["reached_breakpoints"] = list(metadata.get("reached_breakpoints", []) or [])
    state.metadata = metadata
    return state


def _get_resource(state: PlayerState, item_id: str) -> float:
    if hasattr(state.resources, item_id):
        return float(getattr(state.resources, item_id, 0) or 0)
    value = state.inventory.items.get(item_id, 0)
    if isinstance(value, dict):
        return float(value.get("count", 0) or 0)
    return float(value or 0)


def _set_resource(state: PlayerState, item_id: str, value: float) -> None:
    value = max(0.0, value)
    stored = int(value) if value.is_integer() else value
    if hasattr(state.resources, item_id):
        setattr(state.resources, item_id, stored)
    else:
        state.inventory.items[item_id] = stored


def _can_apply_validated(state: PlayerState, action: dict[str, Any]) -> bool:
    for item_id, required in (action.get("required_items") or {}).items():
        if _get_resource(state, str(item_id)) < float(required):
            return False
    return True


def can_apply_action(player_state: Any, action: dict[str, Any]) -> bool:
    return _can_apply_validated(validate_player_state(player_state), action)


def apply_action(player_state: Any, action: dict[str, Any]) -> PlayerState:
    global _STATE_COPY_CALLS, _STATE_COPY_SECONDS, _STATE_REBUILD_CALLS
    source = validate_player_state(player_state)
    copy_started = time.perf_counter()
    state = _copy_for_action(source)
    _STATE_COPY_CALLS += 1
    _STATE_REBUILD_CALLS += 1
    _STATE_COPY_SECONDS += time.perf_counter() - copy_started
    if not hasattr(state, "metadata") or getattr(state, "metadata", None) is None:
        setattr(state, "metadata", {})
    metadata = getattr(state, "metadata")
    if not _can_apply_validated(state, action):
        return state
    for item_id, amount in (action.get("consumed_items") or {}).items():
        _set_resource(state, str(item_id), _get_resource(state, str(item_id)) - float(amount))
    for item_id, amount in (action.get("produced_items") or {}).items():
        _set_resource(state, str(item_id), _get_resource(state, str(item_id)) + float(amount))
    action_id = str(action.get("action_id", ""))
    if action_id and action.get("supported", True):
        state.owned_items.append(action_id)
    action_metadata = action.get("metadata", {}) or {}
    for key, amount in (action_metadata.get("adds_progress") or {}).items():
        progress = metadata.setdefault("progress", {})
        progress[str(key)] = float(progress.get(str(key), 0.0) or 0.0) + float(amount)
    for key, required in (action_metadata.get("breakpoint_requirements") or {}).items():
        progress = metadata.setdefault("progress", {})
        if float(progress.get(str(key), 0.0) or 0.0) >= float(required):
            metadata.setdefault("reached_breakpoints", [])
            if str(key) not in metadata["reached_breakpoints"]:
                metadata["reached_breakpoints"].append(str(key))
    for breakpoint_id in action_metadata.get("sets_breakpoints", []) or []:
        metadata.setdefault("reached_breakpoints", [])
        if str(breakpoint_id) not in metadata["reached_breakpoints"]:
            metadata["reached_breakpoints"].append(str(breakpoint_id))
    for flag, value in (action_metadata.get("set_flags") or {}).items():
        metadata[str(flag)] = value
    if action.get("action_type") == "save_hold":
        metadata["save_value"] = float(metadata.get("save_value", 0.0) or 0.0) + 1.0
    return state


def clear_state_transition_stats() -> None:
    global _STATE_COPY_CALLS, _STATE_COPY_SECONDS, _STATE_REBUILD_CALLS
    _STATE_COPY_CALLS = 0
    _STATE_COPY_SECONDS = 0.0
    _STATE_REBUILD_CALLS = 0


def state_transition_stats() -> dict[str, float | int]:
    return {
        "state_copies": _STATE_COPY_CALLS,
        "state_rebuilds": _STATE_REBUILD_CALLS,
        "state_copy_seconds": round(_STATE_COPY_SECONDS, 6),
        "seconds_per_copy": round(_STATE_COPY_SECONDS / max(1, _STATE_COPY_CALLS), 9),
    }
