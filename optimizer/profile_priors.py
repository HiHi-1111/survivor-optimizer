"""Explainable learned priors for training-time path pruning.

The prior chart is a statistics table, not a black-box model. It learns what
usually wins or loses for similar player buckets, then uses that evidence to
order and softly prune expensive search paths while continuing exploration and
audits.
"""

from __future__ import annotations

from collections import Counter
import json
import random
import time
from pathlib import Path
from typing import Any


CHART_VERSION = 3
DEFAULT_MIN_SAMPLES = 20
MIN_HARD_PRUNE_AUDITS = 100
RECENT_AUDIT_WINDOW = 200
SAFE_SYSTEMS = {"save_hold"}
DESTRUCTIVE_SYSTEMS = {"merge", "salvage", "exchange", "exchanges"}
CANONICAL_SYSTEMS = [
    "core_selector",
    "ss_gear",
    "gear",
    "pet",
    "xeno_pet",
    "tech",
    "collectible",
    "survivor",
    "event_shop",
    "clan_shop",
    "exchange",
    "chest_opening",
    "selector_chest",
    "merge",
    "salvage",
    "save_hold",
]
SYSTEM_ALIASES = {
    "chests": "chest_opening",
    "selectors": "selector_chest",
    "pets": "pet",
    "xeno_pets": "xeno_pet",
    "tech_parts": "tech",
    "collectibles": "collectible",
    "survivors": "survivor",
    "events": "event_shop",
    "shops": "event_shop",
    "clan": "clan_shop",
    "exchanges": "exchange",
    "resources": "core_selector",
    "cores": "core_selector",
    "resonance": "tech",
    "collectible_sets": "collectible",
    "survivor_awakening": "survivor",
    "event_shops": "event_shop",
    "clan_shop": "clan_shop",
    "universal_exchange": "exchange",
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _bucket_number(value: float, buckets: list[tuple[float, str]]) -> str:
    for limit, label in buckets:
        if value <= limit:
            return label
    return buckets[-1][1]


def _state(profile: dict[str, Any]) -> dict[str, Any]:
    return profile.get("player_state", {}) or {}


def _metadata(state: dict[str, Any]) -> dict[str, Any]:
    value = state.get("metadata", {}) or {}
    return value if isinstance(value, dict) else {}


def _count_items(state: dict[str, Any]) -> int:
    inventory = state.get("inventory", {}) or {}
    items = inventory.get("items", {}) or {}
    owned = state.get("owned_items", []) or []
    return max(len(items), len(owned))


def _resources(state: dict[str, Any]) -> dict[str, Any]:
    return state.get("resources", {}) or {}


def _inventory(state: dict[str, Any]) -> dict[str, Any]:
    return state.get("inventory", {}) or {}


def _has_any_key(container: dict[str, Any], needles: list[str]) -> bool:
    lowered = {str(key).lower(): value for key, value in container.items()}
    return any(str(key).lower() in lowered and bool(lowered[str(key).lower()]) for key in needles)


def profile_tags(profile: dict[str, Any]) -> list[str]:
    state = _state(profile)
    metadata = _metadata(state)
    resources = _resources(state)
    inventory = _inventory(state)
    item_ids = [str(key).lower() for key in (inventory.get("items", {}) or {})]
    owned_ids = [str(value).lower() for value in state.get("owned_items", []) or []]
    all_ids = item_ids + owned_ids
    tags = {str(profile.get("stage", "unknown")), str(state.get("goal_scenario", "scenario_1"))}
    archetype = str(metadata.get("archetype", ""))
    spending = str(metadata.get("spending_profile", ""))
    if archetype:
        tags.add(archetype)
    if spending and spending != "unknown":
        tags.add(spending)
    gem_count = float(resources.get("gem", resources.get("gems", 0)) or 0)
    tags.add("gem_heavy" if gem_count >= 1000 else "gem_poor")
    category_terms = {
        "pet_heavy": ("pet",), "xeno_heavy": ("xeno",), "gear_heavy": ("gear", "weapon", "necklace", "glove", "belt", "boot"),
        "ss_heavy": ("ss_", "astral"), "tech_heavy": ("tech", "resonance"), "collectible_heavy": ("collectible",),
        "event_heavy": ("event",), "clan_shop_heavy": ("clan",), "shard_heavy": ("shard",),
    }
    for tag, needles in category_terms.items():
        if sum(any(needle in item_id for needle in needles) for item_id in all_ids) >= 1:
            tags.add(tag)
    if inventory.get("core_selector_chests", 0) or inventory.get("selector_chests", {}):
        tags.update({"selector_heavy", "chest_heavy"})
    if metadata.get("near_breakpoint") or state.get("close_to_breakpoint"):
        tags.add("near_breakpoint")
    else:
        tags.add("far_from_breakpoint")
    if metadata.get("bottlenecked"):
        tags.add("bottlenecked")
    if metadata.get("close_to_xeno_breakpoint"):
        tags.add("near_pet_breakpoint")
    if metadata.get("close_to_astral_forge_breakpoint"):
        tags.add("near_gear_breakpoint")
    if metadata.get("close_to_survivor_breakpoint"):
        tags.add("near_survivor_breakpoint")
    if metadata.get("close_to_collectible_set_breakpoint"):
        tags.add("near_collectible_set_breakpoint")
    return sorted(tag for tag in tags if tag)


def profile_features(profile: dict[str, Any], current_best_system: str | None = None) -> dict[str, Any]:
    state = _state(profile)
    stats = state.get("build_stats", {}) or {}
    inventory = _inventory(state)
    resources = _resources(state)
    metadata = _metadata(state)
    selector_chests = inventory.get("selector_chests", {}) or {}
    items = inventory.get("items", {}) or {}
    resource_total = sum(float(resources.get(key, 0) or 0) for key in ["astral_core", "xeno_core", "resonance_chip", "relic_core"])
    unsupported_missing = int(metadata.get("unsupported_item_count", 0) or metadata.get("missing_data_count", 0) or 0)
    return {
        "account_stage": str(profile.get("stage", "unknown")),
        "scenario": str(state.get("goal_scenario", "scenario_1")),
        "inventory_size_bucket": _bucket_number(float(_count_items(state)), [(3, "0-3"), (8, "4-8"), (15, "9-15"), (30, "16-30"), (9999, "31+")]),
        "damage_stage": _bucket_number(float(stats.get("atk", 0) or 0), [(50_000, "0-50k"), (150_000, "50-150k"), (300_000, "150-300k"), (700_000, "300-700k"), (1_200_000, "700k-1.2m"), (5_000_000, "1.2m+")]),
        "has_xeno_unlocked": bool(metadata.get("xeno_unlocked", False)),
        "close_to_xeno_breakpoint": bool(metadata.get("close_to_xeno_breakpoint", False) or resources.get("xeno_core", 0) in [1, 2, 4, 5]),
        "close_to_astral_forge_breakpoint": bool(metadata.get("close_to_astral_forge_breakpoint", False) or resources.get("astral_core", 0) in [1, 2, 4, 5]),
        "close_to_tech_resonance_breakpoint": bool(metadata.get("close_to_tech_resonance_breakpoint", False) or resources.get("resonance_chip", 0) in [2, 3, 5, 6]),
        "close_to_collectible_set_breakpoint": bool(metadata.get("close_to_collectible_set_breakpoint", False)),
        "close_to_survivor_breakpoint": bool(metadata.get("close_to_survivor_breakpoint", False)),
        "has_major_selector_chest": bool(selector_chests or inventory.get("core_selector_chests", 0)),
        "has_pet_chest": _has_any_key(selector_chests, ["pet_chest", "pet_selector"]) or any("pet" in str(key).lower() and "chest" in str(key).lower() for key in items),
        "has_collectible_chest": _has_any_key(selector_chests, ["collectible_chest", "collectible_selector"]) or any("collectible" in str(key).lower() and "chest" in str(key).lower() for key in items),
        "has_core_selector_chest": bool(inventory.get("core_selector_chests", 0)),
        "has_event_clan_currency": any(float(resources.get(key, 0) or 0) > 0 for key in ["event_currency", "clan_currency", "gems", "keys"]),
        "has_many_rare_resources": resource_total >= 30,
        "current_best_action_system": current_best_system or "unknown",
        "unsupported_missing_data_bucket": _bucket_number(float(unsupported_missing), [(0, "0"), (3, "1-3"), (10, "4-10"), (9999, "11+")]),
    }


def profile_bucket(profile: dict[str, Any], current_best_system: str | None = None) -> str:
    features = profile_features(profile, current_best_system=current_best_system)
    ordered = [
        "account_stage",
        "scenario",
        "inventory_size_bucket",
        "damage_stage",
        "has_xeno_unlocked",
        "close_to_xeno_breakpoint",
        "close_to_astral_forge_breakpoint",
        "close_to_tech_resonance_breakpoint",
        "close_to_collectible_set_breakpoint",
        "close_to_survivor_breakpoint",
        "has_major_selector_chest",
        "has_pet_chest",
        "has_collectible_chest",
        "has_core_selector_chest",
        "has_event_clan_currency",
        "has_many_rare_resources",
        "current_best_action_system",
        "unsupported_missing_data_bucket",
    ]
    return "|".join(f"{key}:{features[key]}" for key in ordered)


def normalize_system(system: str) -> str:
    value = str(system or "unknown").strip()
    return SYSTEM_ALIASES.get(value, value)


def action_system(action_id: str) -> str:
    value = str(action_id or "unknown")
    if value.startswith("core_selector:"):
        return "core_selector"
    return normalize_system(value.split(":", 1)[0] or "unknown")


def new_chart() -> dict[str, Any]:
    return {
        "version": CHART_VERSION,
        "created_at": _now(),
        "updated_at": _now(),
        "total_samples": 0,
        "audit": {
            "full_search_audits": 0,
            "false_prunes": 0,
            "false_prune_rate": 0.0,
            "downgraded_buckets": [],
            "false_prune_examples": [],
            "recent_outcomes": [],
            "recent_false_prunes": 0,
            "recent_false_prune_rate": 0.0,
        },
        "archetype_buckets": {},
        "buckets": {},
    }


def load_chart(path: Path) -> dict[str, Any]:
    if not path.exists():
        return new_chart()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return new_chart()
    if not isinstance(data, dict):
        return new_chart()
    if data.get("version") == 2:
        migrated = new_chart()
        migrated["audit"] = data.get("audit", migrated["audit"])
        migrated["legacy_memory"] = {
            "version": 2,
            "profiles": int(data.get("total_samples", 0)),
            "bucket_count": len(data.get("buckets", {}) or {}),
            "reason": "Quarantined because V2 learned winners from the legacy core-selector recommendation instead of the global final-state planner.",
            "source": str(path),
        }
        migrated["migration"] = {"from_version": 2, "skip_report_recovery": True, "migrated_at": _now()}
        return migrated
    if data.get("version") != CHART_VERSION:
        return new_chart()
    data.setdefault("total_samples", 0)
    data.setdefault("audit", {"full_search_audits": 0, "false_prunes": 0, "false_prune_rate": 0.0, "downgraded_buckets": []})
    data.setdefault("buckets", {})
    data.setdefault("archetype_buckets", {})
    return data


def recover_chart_from_report(chart: dict[str, Any], report_path: Path) -> dict[str, Any]:
    """Recover full bucket records retained in a prior summary report.

    Older trainers could replace a valid chart with an empty rebuild when new
    profile IDs did not match old results. Reports retain the most-observed
    complete buckets, so merge only those records and report the exact number
    of samples that were actually recoverable.
    """
    if chart.get("migration", {}).get("skip_report_recovery") or not report_path.exists():
        return chart
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return chart
    report_total = int(report.get("total_samples", 0) or 0)
    if report_total <= int(chart.get("total_samples", 0) or 0):
        return chart
    buckets = chart.setdefault("buckets", {})
    recovered = 0
    for candidate in report.get("top_buckets", []) or []:
        if not isinstance(candidate, dict) or not candidate.get("bucket"):
            continue
        bucket = str(candidate["bucket"])
        existing = buckets.get(bucket)
        if existing is None or int(candidate.get("samples", 0)) > int(existing.get("samples", 0)):
            buckets[bucket] = candidate
            recovered += 1
    if recovered:
        known_samples = sum(int(entry.get("samples", 0) or 0) for entry in buckets.values())
        chart["total_samples"] = known_samples
        chart["recovery"] = {
            "source": str(report_path),
            "reported_historical_samples": report_total,
            "recoverable_samples": known_samples,
            "recovered_bucket_count": recovered,
            "note": "Only complete bucket records retained in the report were restored; missing rare buckets were not fabricated.",
        }
        chart["updated_at"] = _now()
    return finalize_chart(chart)


def _blank_action_stats(system: str) -> dict[str, Any]:
    return {
        "system": system,
        "tried_count": 0,
        "win_count": 0,
        "top_3_count": 0,
        "avoid_count": 0,
        "score_total": 0.0,
        "marginal_value_total": 0.0,
        "chain_value_total": 0.0,
        "breakpoint_hits": 0,
        "resource_waste_hits": 0,
        "save_recommendation_hits": 0,
        "destructive_action_block_hits": 0,
        "last_updated": _now(),
        "evidence_count": 0,
        "false_prune_hits": 0,
    }


def _blank_combo_stats(combo_id: str) -> dict[str, Any]:
    return {
        "combo_id": combo_id,
        "tried_count": 0,
        "win_count": 0,
        "value_total": 0.0,
        "when_good": Counter(),
        "when_bad": Counter(),
        "common_warnings": Counter(),
        "example_profile_ids": [],
        "evidence_count": 0,
        "confidence": "low",
    }


def _increment_counter(container: dict[str, Any], key: str, value: int = 1) -> None:
    """Increment counters loaded from JSON as safely as fresh Counter objects."""
    container[key] = int(container.get(key, 0)) + value


def _entry(chart: dict[str, Any], bucket: str, features: dict[str, Any]) -> dict[str, Any]:
    buckets = chart.setdefault("buckets", {})
    entry = buckets.setdefault(
        bucket,
        {
            "bucket": bucket,
            "features": features,
            "samples": 0,
            "best_score_total": 0.0,
            "breakpoint_hits": 0,
            "self_consistent_hits": 0,
            "chain_step_hits": 0,
            "chain_simulator_runs": 0,
            "global_planner_runs": 0,
            "global_save_hold_hits": 0,
            "full_search_audits": 0,
            "false_prunes": 0,
            "systems": {},
            "combos": {},
            "commonly_pruned": {},
            "pruned_but_later_good": {},
            "needs_more_deep_exploration": True,
            "confidence": "low",
            "notes": [],
        },
    )
    entry.setdefault("features", features)
    entry.setdefault("systems", {})
    entry.setdefault("combos", {})
    entry.setdefault("commonly_pruned", {})
    entry.setdefault("pruned_but_later_good", {})
    return entry


def _candidate_systems(result: dict[str, Any]) -> list[str]:
    systems = [action_system(str(result.get("best_action_id", "")))]
    for action in result.get("top_options", []) or []:
        systems.append(action_system(str(action.get("action_id", ""))))
    for action in result.get("avoid", []) or []:
        systems.append(action_system(str(action.get("action_id", ""))))
    for system in result.get("action_systems_covered", []) or []:
        systems.append(normalize_system(str(system)))
    for system in result.get("chain_systems_covered", []) or []:
        systems.append(normalize_system(str(system)))
    if result.get("global_plan"):
        for system in result["global_plan"].get("systems_covered", []) or []:
            systems.append(normalize_system(str(system)))
    return sorted(set(system for system in systems if system and system != "unknown"))


def _combo_ids(profile: dict[str, Any], result: dict[str, Any]) -> list[str]:
    state = _state(profile)
    resources = _resources(state)
    inventory = _inventory(state)
    metadata = _metadata(state)
    systems = set(_candidate_systems(result))
    combos: set[str] = set()
    if inventory.get("core_selector_chests", 0) and resources.get("xeno_core", 0):
        combos.add("pet_chest+xeno_core" if "pet" in systems else "core_selector+xeno_core")
    if "core_selector" in systems and resources.get("astral_core", 0):
        combos.add("core_selector+astral_forge")
    if "collectible" in systems and inventory.get("selector_chests", {}):
        combos.add("collectible_chest+collectible_shards")
    if "tech" in systems and resources.get("resonance_chip", 0):
        combos.add("tech_selector+resonance_chips")
    if "survivor" in systems and metadata.get("close_to_survivor_breakpoint"):
        combos.add("survivor_selector+survivor_breakpoint")
    if "event_shop" in systems and resources.get("event_currency", 0):
        combos.add("event_currency+core_purchase")
    if "clan_shop" in systems and resources.get("clan_currency", 0):
        combos.add("clan_currency+core_purchase")
    if metadata.get("xeno_unlocked") and "pet" in systems:
        combos.add("xeno_unlocked+normal_pet_copy")
    if resources.get("xeno_core", 0) in [1, 2, 4, 5] and "pet" in systems:
        combos.add("xeno_requirement_close+epic_pet_chest")
    if sum(1 for key in ["astral_core", "xeno_core", "resonance_chip"] if resources.get(key, 0)) >= 2:
        combos.add("multiple_resources+same_breakpoint")
    return sorted(combos)


def _confidence(evidence_count: int, false_prune_rate: float = 0.0) -> str:
    if evidence_count < 10:
        return "low"
    if evidence_count <= 50 or false_prune_rate > 0.05:
        return "medium"
    return "high"


def add_observation(chart: dict[str, Any], profile: dict[str, Any], result: dict[str, Any]) -> None:
    global_plan = result.get("global_plan", {}) or {}
    best_system = normalize_system(str(result.get("learning_best_system") or global_plan.get("best_system") or action_system(str(result.get("best_action_id", "")))))
    best_score_value = float(result.get("learning_best_score", global_plan.get("best_score", result.get("best_score", 0.0))) or 0.0)
    features = profile_features(profile, current_best_system=best_system)
    bucket = profile_bucket(profile, current_best_system=best_system)
    entry = _entry(chart, bucket, features)
    entry["samples"] += 1
    chart["total_samples"] = int(chart.get("total_samples", 0)) + 1
    chart["updated_at"] = _now()
    scenario = str(_state(profile).get("goal_scenario", "scenario_1"))
    scenario_entry = chart.setdefault("scenario_stats", {}).setdefault(
        scenario, {"samples": 0, "winner_counts": {}, "score_total": 0.0, "breakpoint_wins": 0, "save_hold_wins": 0}
    )
    scenario_entry["samples"] += 1
    _increment_counter(scenario_entry["winner_counts"], best_system)
    scenario_entry["score_total"] = round(float(scenario_entry["score_total"]) + best_score_value, 6)
    scenario_entry["breakpoint_wins"] += int(bool(result.get("breakpoint_reason", False)))
    scenario_entry["save_hold_wins"] += int(bool(global_plan.get("save_hold_recommended", False)) or best_system == "save_hold")
    best_action_id = str(result.get("best_action_id", "unknown"))
    action_entry = chart.setdefault("action_priors", {}).setdefault(best_action_id, {"samples": 0, "wins": 0, "score_total": 0.0, "scenarios": {}})
    action_entry["samples"] += 1
    action_entry["wins"] += 1
    action_entry["score_total"] = round(float(action_entry["score_total"]) + best_score_value, 6)
    _increment_counter(action_entry["scenarios"], scenario)
    chain_signature = str(global_plan.get("best_chain_signature", ""))
    if chain_signature:
        chain_entry = chart.setdefault("best_chain_priors", {}).setdefault(chain_signature, {"samples": 0, "score_total": 0.0, "systems": []})
        chain_entry["samples"] += 1
        chain_entry["score_total"] = round(float(chain_entry["score_total"]) + best_score_value, 6)
        chain_entry["systems"] = list(global_plan.get("systems_covered", []))
    if "bottlenecked" in profile_tags(profile):
        _increment_counter(chart.setdefault("resource_bottlenecks", {}), best_system)
    archetypes = chart.setdefault("archetype_buckets", {})
    for tag in profile_tags(profile):
        tag_entry = archetypes.setdefault(tag, {"tag": tag, "samples": 0, "winner_counts": {}, "score_total": 0.0, "breakpoint_hits": 0, "examples": []})
        tag_entry["samples"] += 1
        _increment_counter(tag_entry.setdefault("winner_counts", {}), best_system)
        tag_entry["score_total"] = round(float(tag_entry.get("score_total", 0.0)) + float(result.get("best_score", 0.0) or 0.0), 6)
        tag_entry["breakpoint_hits"] += int(bool(result.get("breakpoint_reason", False)))
        if len(tag_entry.setdefault("examples", [])) < 5:
            tag_entry["examples"].append(str(profile.get("id", "")))
    entry["best_score_total"] = round(float(entry.get("best_score_total", 0.0)) + best_score_value, 6)
    entry["breakpoint_hits"] += int(bool(result.get("breakpoint_reason", False)))
    entry["self_consistent_hits"] += int(bool(result.get("self_consistent", False)))
    entry["chain_step_hits"] += int(float(result.get("chain_steps_applied", 0) or 0) > 1)
    entry["chain_simulator_runs"] += int(bool(result.get("chain_simulator_ran", False)))
    entry["global_planner_runs"] += int(bool(result.get("global_planner_ran", False)))
    entry["global_save_hold_hits"] += int(bool(global_plan.get("save_hold_recommended", False)))

    top_systems = [action_system(str(action.get("action_id", ""))) for action in result.get("top_options", [])[:3]]
    avoid_systems = [action_system(str(action.get("action_id", ""))) for action in result.get("avoid", [])]
    systems = _candidate_systems(result)
    if best_system not in systems:
        systems.append(best_system)
    for system in sorted(set(systems)):
        stats = entry["systems"].setdefault(system, _blank_action_stats(system))
        stats["tried_count"] += 1
        stats["evidence_count"] += 1
        stats["win_count"] += int(system == best_system)
        stats["top_3_count"] += int(system in top_systems)
        stats["avoid_count"] += int(system in avoid_systems)
        stats["score_total"] = round(float(stats.get("score_total", 0.0)) + (best_score_value if system == best_system else 0.0), 6)
        stats["marginal_value_total"] = round(float(stats.get("marginal_value_total", 0.0)) + float(global_plan.get("best_action_count", 0) or 0), 6)
        stats["chain_value_total"] = round(float(stats.get("chain_value_total", 0.0)) + float(result.get("chain_steps_applied", 0) or 0), 6)
        stats["breakpoint_hits"] += int(bool(result.get("breakpoint_reason", False)) and system == best_system)
        stats["resource_waste_hits"] += int(system in avoid_systems and "save" not in system)
        stats["save_recommendation_hits"] += int(bool(global_plan.get("save_hold_recommended", False)) or system == "save_hold")
        stats["destructive_action_block_hits"] += int(system in DESTRUCTIVE_SYSTEMS and system in avoid_systems)
        stats["last_updated"] = _now()

    for combo_id in _combo_ids(profile, result):
        combo = entry["combos"].setdefault(combo_id, _blank_combo_stats(combo_id))
        combo["tried_count"] += 1
        combo["evidence_count"] += 1
        combo["win_count"] += int(best_system in combo_id or result.get("breakpoint_reason", False))
        combo["value_total"] = round(float(combo.get("value_total", 0.0)) + best_score_value, 6)
        if result.get("breakpoint_reason", False):
            _increment_counter(combo.setdefault("when_good", {}), "breakpoint_reason")
        else:
            _increment_counter(combo.setdefault("when_bad", {}), "no_breakpoint")
        for reason in result.get("best_reasons", [])[:3]:
            _increment_counter(combo.setdefault("common_warnings", {}), str(reason)[:120])
        examples = combo.setdefault("example_profile_ids", [])
        if len(examples) < 5:
            examples.append(str(profile.get("id", "")))


def _rates(entry: dict[str, Any]) -> dict[str, float]:
    samples = max(1, int(entry.get("samples", 0)))
    global_runs = max(1, int(entry.get("global_planner_runs", 0)))
    audits = max(1, int(entry.get("full_search_audits", 0)))
    return {
        "avg_best_score": round(float(entry.get("best_score_total", 0.0)) / samples, 6),
        "breakpoint_rate": round(float(entry.get("breakpoint_hits", 0)) / samples, 6),
        "self_consistency_rate": round(float(entry.get("self_consistent_hits", 0)) / samples, 6),
        "chain_step_rate": round(float(entry.get("chain_step_hits", 0)) / samples, 6),
        "global_save_hold_rate": round(float(entry.get("global_save_hold_hits", 0)) / global_runs, 6),
        "false_prune_rate": round(float(entry.get("false_prunes", 0)) / audits, 6),
    }


def _finalize_system(stats: dict[str, Any]) -> dict[str, Any]:
    tried = max(1, int(stats.get("tried_count", 0)))
    evidence = int(stats.get("evidence_count", tried))
    false_rate = float(stats.get("false_prune_hits", 0)) / max(1, evidence)
    stats["average_score"] = round(float(stats.get("score_total", 0.0)) / tried, 6)
    stats["average_marginal_value"] = round(float(stats.get("marginal_value_total", 0.0)) / tried, 6)
    stats["average_chain_value"] = round(float(stats.get("chain_value_total", 0.0)) / tried, 6)
    stats["breakpoint_hit_rate"] = round(float(stats.get("breakpoint_hits", 0)) / tried, 6)
    stats["resource_waste_rate"] = round(float(stats.get("resource_waste_hits", 0)) / tried, 6)
    stats["save_recommendation_rate"] = round(float(stats.get("save_recommendation_hits", 0)) / tried, 6)
    stats["destructive_action_block_rate"] = round(float(stats.get("destructive_action_block_hits", 0)) / tried, 6)
    stats["confidence"] = _confidence(evidence, false_rate)
    return stats


def _finalize_combo(combo: dict[str, Any]) -> dict[str, Any]:
    tried = max(1, int(combo.get("tried_count", 0)))
    evidence = int(combo.get("evidence_count", tried))
    combo["average_value"] = round(float(combo.get("value_total", 0.0)) / tried, 6)
    combo["confidence"] = _confidence(evidence)
    for key in ["when_good", "when_bad", "common_warnings"]:
        value = combo.get(key, {})
        if isinstance(value, Counter):
            combo[key] = dict(value.most_common(8))
        elif isinstance(value, dict):
            combo[key] = dict(Counter(value).most_common(8))
    return combo


def top_systems(entry: dict[str, Any], limit: int = 5) -> list[str]:
    systems = entry.get("systems", {}) or {}
    ranked = sorted(
        systems.items(),
        key=lambda item: (
            int(item[1].get("win_count", 0)),
            int(item[1].get("top_3_count", 0)),
            -float(item[1].get("resource_waste_rate", 0.0)),
            float(item[1].get("average_score", 0.0)),
        ),
        reverse=True,
    )
    return [str(system) for system, _stats in ranked[:limit] if system]


def weak_systems(entry: dict[str, Any], mode: str = "normal") -> list[str]:
    if mode == "off":
        return []
    min_evidence = {"soft": 50, "normal": 20, "aggressive": 10}.get(mode, 20)
    weak: list[str] = []
    for system, stats in (entry.get("systems", {}) or {}).items():
        stats = _finalize_system(stats)
        if system in SAFE_SYSTEMS or system in DESTRUCTIVE_SYSTEMS:
            continue
        if int(stats.get("evidence_count", 0)) < min_evidence:
            continue
        win_rate = float(stats.get("win_count", 0)) / max(1, int(stats.get("tried_count", 0)))
        top_rate = float(stats.get("top_3_count", 0)) / max(1, int(stats.get("tried_count", 0)))
        if win_rate <= 0.02 and top_rate <= 0.08 and float(stats.get("resource_waste_rate", 0.0)) >= 0.25:
            weak.append(str(system))
    return sorted(weak)


def finalize_chart(chart: dict[str, Any]) -> dict[str, Any]:
    audit = chart.setdefault("audit", {"full_search_audits": 0, "false_prunes": 0, "false_prune_rate": 0.0, "downgraded_buckets": []})
    audit["false_prune_rate"] = round(float(audit.get("false_prunes", 0)) / max(1, int(audit.get("full_search_audits", 0))), 6)
    recent_outcomes = list(audit.get("recent_outcomes", []) or [])[-RECENT_AUDIT_WINDOW:]
    audit["recent_outcomes"] = recent_outcomes
    audit["recent_false_prunes"] = sum(1 for outcome in recent_outcomes if bool(outcome.get("false_prune")))
    audit["recent_false_prune_rate"] = round(audit["recent_false_prunes"] / max(1, len(recent_outcomes)), 6)
    for entry in (chart.setdefault("archetype_buckets", {}) or {}).values():
        samples = max(1, int(entry.get("samples", 0)))
        entry["average_score"] = round(float(entry.get("score_total", 0.0)) / samples, 6)
        entry["breakpoint_rate"] = round(float(entry.get("breakpoint_hits", 0)) / samples, 6)
        entry["top_systems"] = [system for system, _count in Counter(entry.get("winner_counts", {})).most_common(8)]
        entry["confidence"] = _confidence(int(entry.get("samples", 0)))
    for entry in (chart.get("buckets", {}) or {}).values():
        rates = _rates(entry)
        entry.update(rates)
        for system, stats in list((entry.get("systems", {}) or {}).items()):
            entry["systems"][system] = _finalize_system(stats)
        for combo_id, combo in list((entry.get("combos", {}) or {}).items()):
            entry["combos"][combo_id] = _finalize_combo(combo)
        entry["top_systems"] = top_systems(entry)
        entry["weak_systems"] = weak_systems(entry, "normal")
        entry["confidence"] = _confidence(int(entry.get("samples", 0)), rates["false_prune_rate"])
        entry["needs_more_deep_exploration"] = bool(entry["confidence"] != "high" or rates["false_prune_rate"] > 0.03)
        entry["prune_hint"] = "stable" if entry["confidence"] == "high" and not entry["needs_more_deep_exploration"] else "learning"
    return chart


def save_chart(chart: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # This generated chart can contain thousands of buckets. Compact JSON cuts
    # checkpoint bytes substantially; an atomic replace prevents interruption
    # from leaving a partially written learning database.
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(finalize_chart(chart), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_chart(profiles: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {str(profile.get("id")): profile for profile in profiles}
    chart = new_chart()
    for result in results:
        profile = by_id.get(str(result.get("profile_id")))
        if profile:
            add_observation(chart, profile, result)
    return finalize_chart(chart)


def _near_breakpoint(features: dict[str, Any]) -> bool:
    return any(
        bool(features.get(key))
        for key in [
            "close_to_xeno_breakpoint",
            "close_to_astral_forge_breakpoint",
            "close_to_tech_resonance_breakpoint",
            "close_to_collectible_set_breakpoint",
            "close_to_survivor_breakpoint",
        ]
    )


def recommend_training_plan(
    profile: dict[str, Any],
    chart: dict[str, Any],
    *,
    sequence: int,
    base_chain_interval: int,
    base_global_interval: int,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    pruning_mode: str = "normal",
    exploration_rate: float = 0.08,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    rng = rng or random.Random(sequence)
    features = profile_features(profile)
    audit_state = chart.get("audit", {}) or {}
    historical_false_prunes = int(audit_state.get("false_prunes", 0) or 0)
    historical_audits = int(audit_state.get("full_search_audits", 0) or 0)
    recent_outcomes = list(audit_state.get("recent_outcomes", []) or [])[-RECENT_AUDIT_WINDOW:]
    recent_audits = len(recent_outcomes) if recent_outcomes else historical_audits
    recent_false_prunes = sum(1 for outcome in recent_outcomes if bool(outcome.get("false_prune"))) if recent_outcomes else historical_false_prunes
    # A historical false prune must disable hard pruning, but it must not make
    # all accumulated learning unusable forever. Reordering is non-destructive
    # and remains safe while full-search audits continue.
    hard_pruning_blocked = bool(
        pruning_mode != "off"
        and (recent_false_prunes > 0 or recent_audits < MIN_HARD_PRUNE_AUDITS)
    )
    if recent_false_prunes > 0:
        hard_pruning_blocked_reason = (
            f"learned priors reordered systems; hard pruning disabled by safety latch after {recent_false_prunes} false prune(s) in the recent audit window"
        )
    elif hard_pruning_blocked:
        hard_pruning_blocked_reason = (
            f"learned priors reordered systems; hard pruning requires {MIN_HARD_PRUNE_AUDITS} recent safe full-search audits and only {recent_audits} are available"
        )
    else:
        hard_pruning_blocked_reason = ""
    possible_buckets = [
        profile_bucket(profile, current_best_system="unknown"),
        *[profile_bucket(profile, current_best_system=system) for system in CANONICAL_SYSTEMS],
    ]
    buckets = chart.get("buckets", {}) or {}
    entry = next((buckets[key] for key in possible_buckets if key in buckets), None)
    if pruning_mode == "off":
        entry = None
    if not entry or int(entry.get("samples", 0)) < min_samples:
        tag_entries = [(chart.get("archetype_buckets", {}) or {}).get(tag) for tag in profile_tags(profile)]
        tag_entries = [tag_entry for tag_entry in tag_entries if tag_entry and int(tag_entry.get("samples", 0)) >= min_samples]
        if tag_entries and pruning_mode != "off":
            winner_counts: Counter[str] = Counter()
            for tag_entry in tag_entries:
                winner_counts.update(tag_entry.get("winner_counts", {}))
            ordered = [system for system, _count in winner_counts.most_common()]
            ordered.extend(system for system in CANONICAL_SYSTEMS if system not in ordered)
            archetype_evidence = max(int(value.get("samples", 0)) for value in tag_entries)
            archetype_confidence = "low" if archetype_evidence < 100 else ("medium" if archetype_evidence < 500 else "high")
            global_false_rate = float((chart.get("audit", {}) or {}).get("false_prune_rate", 0.0) or 0.0)
            mode = pruning_mode if pruning_mode in {"soft", "normal", "aggressive"} else "normal"
            if global_false_rate > 0.05:
                mode = "soft" if mode == "normal" else ("normal" if mode == "aggressive" else mode)
            force_full_search = bool(
                _near_breakpoint(features)
                or rng.random() < max(0.0, min(1.0, exploration_rate))
            )
            if archetype_confidence == "low" or mode == "soft" or force_full_search or hard_pruning_blocked:
                systems = ordered
                pruned: set[str] = set()
            else:
                limit = 10 if archetype_confidence == "medium" else {"normal": 6, "aggressive": 4}[mode]
                selected = set(ordered[:limit]) | SAFE_SYSTEMS | DESTRUCTIVE_SYSTEMS
                systems = [system for system in ordered if system in selected]
                pruned = set(CANONICAL_SYSTEMS) - selected
            return {
                "bucket": possible_buckets[0], "samples": sum(int(value.get("samples", 0)) for value in tag_entries),
                "confidence": archetype_confidence, "run_chain_simulator": ((sequence - 1) % max(1, base_chain_interval)) == 0,
                "run_global_planner": ((sequence - 1) % max(1, base_global_interval)) == 0,
                "systems": systems, "pruned_systems": sorted(pruned),
                "reason": (
                    hard_pruning_blocked_reason
                    if hard_pruning_blocked else "archetype memory reordered all systems; evidence is not strong enough to prune"
                    if not pruned else f"{archetype_confidence}-confidence archetype memory pruned weak systems"
                ),
                "archetype_tags": profile_tags(profile),
                "archetype_evidence": archetype_evidence,
                "effective_pruning_mode": mode,
                "full_search_safety_override": force_full_search,
                "false_prune_safety_latch": hard_pruning_blocked,
                "hard_pruning_blocked_reason": hard_pruning_blocked_reason,
                "reordering_applied": True,
                "pruning_applied": bool(pruned),
            }
        return {
            "bucket": possible_buckets[0],
            "samples": int(entry.get("samples", 0)) if entry else 0,
            "confidence": "low",
            "run_chain_simulator": ((sequence - 1) % max(1, base_chain_interval)) == 0,
            "run_global_planner": ((sequence - 1) % max(1, base_global_interval)) == 0,
            "systems": None,
            "pruned_systems": [],
            "reason": "learning bucket; not enough similar profiles to prune aggressively",
            "reordering_applied": False,
            "pruning_applied": False,
            "learning_blocked_reason": "insufficient_bucket_or_archetype_samples",
            "false_prune_safety_latch": hard_pruning_blocked,
            "hard_pruning_blocked_reason": hard_pruning_blocked_reason,
        }

    entry = finalize_chart({"buckets": {"x": entry}, "audit": chart.get("audit", {})})["buckets"]["x"]
    rates = _rates(entry)
    chain_interval = max(1, base_chain_interval)
    global_interval = max(1, base_global_interval)
    if rates["chain_step_rate"] < 0.15 and rates["self_consistency_rate"] >= 0.9:
        chain_interval *= 3
    if rates["global_save_hold_rate"] < 0.05 and rates["self_consistency_rate"] >= 0.9:
        global_interval *= 4
    if rates["false_prune_rate"] > 0.03 or rates["self_consistency_rate"] < 0.75:
        chain_interval = max(1, chain_interval // 2)
        global_interval = max(1, global_interval // 2)

    mode = pruning_mode if pruning_mode in {"soft", "normal", "aggressive"} else "normal"
    global_false_rate = float((chart.get("audit", {}) or {}).get("false_prune_rate", 0.0) or 0.0)
    if global_false_rate > 0.05:
        mode = "soft" if mode == "normal" else ("normal" if mode == "aggressive" else mode)
    candidate_top = set(top_systems(entry, limit={"soft": 8, "normal": 6, "aggressive": 4}[mode]))
    ordered_systems = top_systems(entry, limit=len(CANONICAL_SYSTEMS))
    ordered_systems.extend(system for system in CANONICAL_SYSTEMS if system not in ordered_systems)
    force_full_search = bool(_near_breakpoint(features) or rng.random() < max(0.0, min(1.0, exploration_rate)))
    if mode == "soft" or force_full_search or hard_pruning_blocked:
        systems = ordered_systems
        pruned: set[str] = set()
    else:
        selected = candidate_top | SAFE_SYSTEMS | DESTRUCTIVE_SYSTEMS
        systems = [system for system in ordered_systems if system in selected]
        if not systems:
            systems = ordered_systems
        pruned = set(CANONICAL_SYSTEMS) - set(systems)

    return {
        "bucket": str(entry.get("bucket", possible_buckets[0])),
        "samples": int(entry.get("samples", 0)),
        "confidence": entry.get("confidence", "low"),
        "run_chain_simulator": ((sequence - 1) % chain_interval) == 0,
        "run_global_planner": ((sequence - 1) % global_interval) == 0,
        "systems": systems,
        "pruned_systems": sorted(pruned),
        "reason": hard_pruning_blocked_reason or "learned priors applied with exploration/audit safety",
        "chain_interval_used": chain_interval,
        "global_interval_used": global_interval,
        "rates": rates,
        "effective_pruning_mode": mode,
        "archetype_tags": profile_tags(profile),
        "full_search_safety_override": force_full_search,
        "false_prune_safety_latch": hard_pruning_blocked,
        "hard_pruning_blocked_reason": hard_pruning_blocked_reason,
        "reordering_applied": True,
        "pruning_applied": bool(pruned),
    }


def record_audit(
    chart: dict[str, Any],
    profile: dict[str, Any],
    *,
    learned_systems: list[str] | None,
    full_best_system: str,
    full_score: float,
    learned_score: float,
    full_best_action_id: str = "",
    learned_best_action_id: str = "",
) -> dict[str, Any]:
    full_best_system = normalize_system(full_best_system)
    normalized_learned = {normalize_system(system) for system in learned_systems or []}
    features = profile_features(profile, current_best_system=full_best_system)
    bucket = profile_bucket(profile, current_best_system=full_best_system)
    entry = _entry(chart, bucket, features)
    missed = bool(learned_systems is not None and full_best_system not in normalized_learned)
    score_difference = round(float(full_score) - float(learned_score), 6)
    false_prune = bool(missed and score_difference > 0)
    entry["full_search_audits"] += 1
    entry["false_prunes"] += int(false_prune)
    audit = chart.setdefault("audit", {"full_search_audits": 0, "false_prunes": 0, "false_prune_rate": 0.0, "downgraded_buckets": []})
    audit["full_search_audits"] += 1
    audit["false_prunes"] += int(false_prune)
    recent_outcomes = audit.setdefault("recent_outcomes", [])
    recent_outcomes.append({
        "profile_id": str(profile.get("id", "")),
        "bucket": bucket,
        "false_prune": false_prune,
        "score_difference": score_difference,
        "timestamp": _now(),
    })
    del recent_outcomes[:-RECENT_AUDIT_WINDOW]
    audit["recent_false_prunes"] = sum(1 for outcome in recent_outcomes if bool(outcome.get("false_prune")))
    audit["recent_false_prune_rate"] = round(audit["recent_false_prunes"] / max(1, len(recent_outcomes)), 6)
    if false_prune:
        audit.setdefault("downgraded_buckets", []).append(bucket)
        examples = audit.setdefault("false_prune_examples", [])
        examples.append({
            "profile_id": str(profile.get("id", "")), "bucket": bucket,
            "missed_system": full_best_system, "missed_action_id": str(full_best_action_id),
            "selected_action_id": str(learned_best_action_id),
            "learned_systems": sorted(normalized_learned), "score_difference": score_difference,
        })
        del examples[:-100]
        for system in entry.get("systems", {}).values():
            system["false_prune_hits"] = int(system.get("false_prune_hits", 0)) + 1
    finalize_chart(chart)
    return {
        "bucket": bucket,
        "missed_best_chain": missed,
        "score_difference": score_difference,
        "action_system_missed": full_best_system if missed else "",
        "action_id_missed": str(full_best_action_id) if missed else "",
        "selected_action_id": str(learned_best_action_id),
        "false_prune": false_prune,
    }


def write_reports(chart: dict[str, Any], json_path: Path, md_path: Path) -> None:
    chart = finalize_chart(chart)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    buckets = chart.get("buckets", {}) or {}
    stable = [entry for entry in buckets.values() if entry.get("confidence") == "high"]
    learning = [entry for entry in buckets.values() if entry.get("needs_more_deep_exploration")]
    top = sorted(buckets.values(), key=lambda entry: int(entry.get("samples", 0)), reverse=True)[:15]
    report = {
        "total_samples": chart.get("total_samples", 0),
        "bucket_count": len(buckets),
        "stable_bucket_count": len(stable),
        "needs_more_deep_exploration_count": len(learning),
        "audit": chart.get("audit", {}),
        "recovery": chart.get("recovery", {}),
        "top_buckets": top,
    }
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Profile Prior Report",
        "",
        f"- Total samples: {report['total_samples']}",
        f"- Buckets: {report['bucket_count']}",
        f"- Stable high-confidence buckets: {report['stable_bucket_count']}",
        f"- Buckets needing more deep exploration: {report['needs_more_deep_exploration_count']}",
        f"- Full-search audits: {chart.get('audit', {}).get('full_search_audits', 0)}",
        f"- False prune rate: {chart.get('audit', {}).get('false_prune_rate', 0)}",
        "",
        "## Top Buckets",
    ]
    for entry in top:
        lines.append(
            f"- samples={entry.get('samples', 0)} confidence={entry.get('confidence', 'low')} "
            f"top_systems={', '.join(entry.get('top_systems', [])[:5]) or 'none'} "
            f"weak_systems={', '.join(entry.get('weak_systems', [])[:5]) or 'none'}"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
