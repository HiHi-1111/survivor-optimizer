"""Stable hashes for pruning equivalent states."""

from __future__ import annotations

import json
from typing import Any


_SEARCH_MUTABLE_BRANCHES = {"resources", "inventory", "owned_items", "metadata"}


def state_fingerprint(player_state: Any) -> str:
    if hasattr(player_state, "model_dump_json"):
        # This runs inside every beam-search branch; use Pydantic's native
        # serializer instead of rebuilding and sorting a Python dictionary.
        return player_state.model_dump_json(exclude_none=True)
    if hasattr(player_state, "model_dump"):
        data = player_state.model_dump()
    elif hasattr(player_state, "dict"):
        data = player_state.dict()
    else:
        data = player_state
    return json.dumps(data, sort_keys=True, default=str)


def search_state_fingerprint(player_state: Any) -> str:
    """Serialize only state branches that optimizer actions can mutate.

    This key is valid for comparing states inside one search, where every
    candidate shares the same immutable build, gear, survivor, pet, tech, and
    collectible branches.  General caches must continue to use
    :func:`state_fingerprint`, which includes the complete player state.
    """
    if hasattr(player_state, "model_dump_json"):
        return player_state.model_dump_json(include=_SEARCH_MUTABLE_BRANCHES, exclude_none=True)
    if hasattr(player_state, "model_dump"):
        data = player_state.model_dump(include=_SEARCH_MUTABLE_BRANCHES)
    elif hasattr(player_state, "dict"):
        data = player_state.dict(include=_SEARCH_MUTABLE_BRANCHES)
    elif isinstance(player_state, dict):
        data = {key: player_state.get(key) for key in _SEARCH_MUTABLE_BRANCHES if key in player_state}
    else:
        return state_fingerprint(player_state)
    return json.dumps(data, sort_keys=True, default=str)


def prune_dominated_states(chains: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_state: dict[str, dict[str, Any]] = {}
    for chain in chains:
        key = str(chain.get("state_hash", ""))
        spent = float(chain.get("rare_resources_spent", 0.0))
        score = float(chain.get("score", 0.0))
        current = best_by_state.get(key)
        if current is None or spent < float(current.get("rare_resources_spent", 0.0)) or (
            spent == float(current.get("rare_resources_spent", 0.0)) and score > float(current.get("score", 0.0))
        ):
            best_by_state[key] = chain
    return list(best_by_state.values())
