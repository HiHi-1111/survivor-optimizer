"""Damage-first state valuation for whole-inventory planning.

The value model is intentionally small and explainable. It values reached
breakpoint flags once per final state, which prevents double-counting when
multiple actions contribute to the same breakpoint.
"""

from __future__ import annotations

from typing import Any

from optimizer.player_state import validate_player_state
from optimizer.state_hash import state_fingerprint


BREAKPOINT_VALUES = {
    "astral_forge_breakpoint": 120.0,
    "xeno_unlock": 90.0,
    "xeno_breakpoint": 80.0,
    "collectible_set_breakpoint": 70.0,
    "tech_resonance_breakpoint": 65.0,
    "survivor_breakpoint": 60.0,
}
_VALUE_CACHE: dict[str, dict[str, Any]] = {}
_VALUE_CACHE_LIMIT = 50000
_VALUE_CACHE_HITS = 0
_VALUE_CACHE_MISSES = 0


def _metadata(state: Any) -> dict[str, Any]:
    if hasattr(state, "metadata"):
        value = getattr(state, "metadata", {})
        return value if isinstance(value, dict) else {}
    if isinstance(state, dict):
        return state.get("metadata", {}) or {}
    return {}


def reached_breakpoints(state: Any) -> set[str]:
    metadata = _metadata(state)
    reached = set(str(item) for item in metadata.get("reached_breakpoints", []) or [])
    if metadata.get("xeno_unlocked"):
        reached.add("xeno_unlock")
    if metadata.get("astral_forge_ready"):
        reached.add("astral_forge_breakpoint")
    if metadata.get("collectible_set_ready"):
        reached.add("collectible_set_breakpoint")
    if metadata.get("tech_resonance_ready"):
        reached.add("tech_resonance_breakpoint")
    return reached


def state_value(player_state: Any, knowledge: dict[str, Any] | None = None) -> dict[str, Any]:
    global _VALUE_CACHE_HITS, _VALUE_CACHE_MISSES
    state = validate_player_state(player_state)
    cache_key = state_fingerprint(state)
    cached = _VALUE_CACHE.get(cache_key)
    if cached is not None:
        _VALUE_CACHE_HITS += 1
        return cached
    _VALUE_CACHE_MISSES += 1
    metadata = _metadata(state)
    breakpoints = reached_breakpoints(state)
    damage_value = sum(BREAKPOINT_VALUES.get(item, 25.0) for item in breakpoints)
    # Damage-first: HP/healing/armor/etc are intentionally not read here.
    resource_flexibility = 0.05 * (
        float(getattr(state.resources, "astral_core", 0) or 0)
        + float(getattr(state.resources, "xeno_core", 0) or 0)
        + float(getattr(state.resources, "resonance_chip", 0) or 0)
        + float(getattr(state.resources, "relic_core", 0) or 0)
    )
    save_value = float(metadata.get("save_value", 0.0) or 0.0)
    total = damage_value + resource_flexibility + save_value
    result = {
        "total": round(total, 6),
        "damage_value": round(damage_value, 6),
        "resource_flexibility": round(resource_flexibility, 6),
        "save_value": round(save_value, 6),
        "breakpoints": sorted(breakpoints),
    }
    if len(_VALUE_CACHE) >= _VALUE_CACHE_LIMIT:
        _VALUE_CACHE.clear()
    _VALUE_CACHE[cache_key] = result
    return result


def state_value_cache_stats() -> dict[str, int]:
    return {"hits": _VALUE_CACHE_HITS, "misses": _VALUE_CACHE_MISSES, "entries": len(_VALUE_CACHE)}


def marginal_value(original_state: Any, future_state: Any, knowledge: dict[str, Any] | None = None) -> dict[str, Any]:
    before = state_value(original_state, knowledge)
    after = state_value(future_state, knowledge)
    return {
        "delta": round(float(after["total"]) - float(before["total"]), 6),
        "before": before,
        "after": after,
    }
