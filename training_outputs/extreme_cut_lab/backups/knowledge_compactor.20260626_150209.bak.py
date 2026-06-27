from __future__ import annotations

from copy import deepcopy
from typing import Any


KEEP_KEYS_EXACT = {
    # identity / user state
    "id", "item_id", "name", "type", "system", "category",
    "rarity", "tier", "level", "star", "stars", "awakening", "awakened",
    "equipped", "selected", "active", "slotted", "unlocked", "owned",

    # damage math
    "base_damage", "attack", "atk", "damage", "damage_multiplier",
    "final_damage_multiplier", "total_damage", "dps", "multiplier_breakdown",

    # blocker/progression math
    "relic_core", "relic cores", "awakening_core", "awakening core",
    "s_awakening_core", "shard", "shards", "survivor_shard", "yang_shard",
    "resonance_chip", "core", "cores", "missing", "needed", "required",
    "adds_progress", "sets_breakpoints", "bridge_reason", "blocker_analysis",
    "true_blockers", "priority_blockers", "rare_blockers_to_prioritize",

    # timing math
    "battle_duration_seconds", "fight_duration_seconds", "round_duration_seconds",
    "active_seconds", "duration_seconds", "on_seconds", "off_seconds",
    "cooldown_seconds", "charge_seconds", "cycle_seconds",
    "trigger_interval_seconds", "proc_interval_seconds",
    "uptime", "uptime_percent",

    # scenario
    "goal_scenario", "progression_stage", "steamroll_unlocked", "player_stage",
}

DROP_KEYS_EXACT = {
    "catalog", "source_catalog", "reference", "source", "sources", "preview",
    "locked_preview", "future", "wishlist", "candidate", "candidates",
    "shop_preview", "unowned_preview", "description_long", "raw_text",
    "wiki_text", "notes_dump",
}

DROP_TEXT_MARKERS = {
    "locked", "preview", "future", "catalog", "source/reference",
}


def _is_false_state(row: dict[str, Any]) -> bool:
    # Drop inactive/irrelevant rows. This is the whole point of the optimizer:
    # count current active damage only, not every possible game object.
    for key in ("equipped", "selected", "active", "slotted", "unlocked", "owned"):
        if key in row and row.get(key) is False:
            return True

    text = " ".join(str(v).lower() for v in row.values() if isinstance(v, str))
    if "locked preview" in text or "future preview" in text:
        return True

    return False


def _keep_key(key: str) -> bool:
    lower = key.lower()
    if lower in DROP_KEYS_EXACT:
        return False
    if lower in KEEP_KEYS_EXACT:
        return True
    if any(term in lower for term in ("damage", "multiplier", "blocker", "core", "shard", "resonance", "awakening", "uptime", "cycle", "cooldown", "charge")):
        return True
    return False


def compact_player_state(value: Any) -> Any:
    """Remove dead/reference data but keep all current damage/blocker/timing facts."""
    if isinstance(value, list):
        compacted = []
        for item in value:
            c = compact_player_state(item)
            if c not in ({}, [], None):
                compacted.append(c)
        return compacted

    if isinstance(value, dict):
        if _is_false_state(value):
            return {}

        out: dict[str, Any] = {}
        for key, child in value.items():
            key_text = str(key)
            lower = key_text.lower()

            if lower in DROP_KEYS_EXACT:
                continue

            child_compact = compact_player_state(child)

            if _keep_key(key_text) or child_compact not in ({}, [], None):
                out[key] = child_compact

        return out

    return value


def profile_size_score(profile: dict[str, Any]) -> int:
    return len(str(profile))


def compact_case(case: dict[str, Any]) -> dict[str, Any]:
    copied = deepcopy(case)
    for key in ("clean_profile", "challenged_profile"):
        if isinstance(copied.get(key), dict):
            copied[key] = compact_player_state(copied[key])
    copied["compression_applied"] = True
    return copied
