"""Inventory/action coverage metrics with explicit implementation vs data gaps."""

from __future__ import annotations

from collections import Counter
from typing import Any

from optimizer.action_registry import generate_inventory_actions, generator_manifests, registry_systems
from optimizer.player_state import PlayerState


INVENTORY_SECTIONS = [
    "items", "resources", "chests", "chest_odds", "gear", "weapons", "skills", "pets", "pet_merging", "pet_awakenings", "xeno_pets",
    "tech_parts", "collectibles", "survivors", "survivor_awakenings",
    "events", "event_shops", "clan_shop", "universal_exchange",
]

MAJOR_SYSTEMS = [
    "gear", "ss_gear", "cores", "resources", "chests", "selectors", "skills", "pets", "pet_merging", "pet_awakenings",
    "xeno_pets", "tech_parts", "resonance", "collectibles", "collectible_sets",
    "survivors", "survivor_awakening", "events", "event_shops", "clan_shop",
    "exchanges", "universal_exchange", "merge", "salvage", "save_hold",
]

SYSTEM_SECTIONS = {
    "gear": ["gear", "weapons"], "ss_gear": ["resources", "gear", "weapons"], "cores": ["resources"], "resources": ["resources"],
    "chests": ["chests", "chest_odds", "collectible_chest_odds"], "selectors": ["chests"], "skills": ["skills"], "pets": ["pets"],
    "pet_merging": ["pet_merging"], "pet_awakenings": ["pet_awakenings"],
    "xeno_pets": ["xeno_pets", "resources"], "tech_parts": ["tech_parts", "resources"], "resonance": ["tech_parts", "tech_resonance", "tech_resonance_costs", "resources"],
    "collectibles": ["collectibles", "collectible_sets"], "collectible_sets": ["collectible_sets"],
    "survivors": ["survivors", "survivor_awakenings", "resources"], "survivor_awakening": ["survivor_awakenings"], "events": ["events"],
    "event_shops": ["event_shops"], "clan_shop": ["clan_shop"],
    "exchanges": ["universal_exchange"], "universal_exchange": ["universal_exchange"], "merge": ["gear", "pets", "tech_parts"],
    "salvage": ["gear", "pets", "tech_parts", "collectibles", "items"],
    "save_hold": ["items", "resources", "chests"],
}


def _record_id(record: Any) -> str:
    return str(getattr(record, "id", record.get("id", "") if isinstance(record, dict) else ""))


def _record_value(record: Any, field: str, default: Any = None) -> Any:
    return record.get(field, default) if isinstance(record, dict) else getattr(record, field, default)


def _record_tags(record: Any) -> set[str]:
    return {str(tag).lower() for tag in (_record_value(record, "tags", []) or [])}


def _is_placeholder(record: Any) -> bool:
    tags = _record_tags(record)
    metadata = _record_value(record, "metadata", {}) or {}
    return "placeholder" in tags or bool(metadata.get("placeholder")) or _record_value(record, "confidence", "") == "missing"


def _needs_review(record: Any) -> bool:
    return "needs_review" in _record_tags(record) or _record_value(record, "confidence", "medium") in {"missing", "low"}


def known_inventory_ids(knowledge: dict[str, Any]) -> dict[str, set[str]]:
    return {
        section: {_record_id(record) for record in knowledge.get(section, []) if _record_id(record)}
        for section in INVENTORY_SECTIONS
    }


def coverage_audit_state(knowledge: dict[str, Any]) -> PlayerState:
    known = known_inventory_ids(knowledge)
    return PlayerState(
        resources={item_id: 2 for item_id in known.get("resources", set())},
        inventory={
            "core_selector_chests": 2,
            "items": {item_id: 2 for section, ids in known.items() if section != "resources" for item_id in ids},
        },
    )


def coverage_report(knowledge: dict[str, Any], player_state: Any) -> dict[str, Any]:
    known = known_inventory_ids(knowledge)
    actions = generate_inventory_actions(player_state, knowledge, include_saves=True, max_actions=None)
    all_ids = set().union(*known.values()) if known else set()
    supported_item_ids = {
        str(action.get("metadata", {}).get("item_id", ""))
        for action in actions if action.get("supported", True)
    }
    action_item_ids = {str(action.get("metadata", {}).get("item_id", "")) for action in actions}
    unsupported = sorted(item_id for item_id in all_ids if item_id and item_id not in supported_item_ids)
    actions_by_system = Counter(str(action.get("system", "unknown")) for action in actions)
    systems_generated = sorted(system for system in MAJOR_SYSTEMS if actions_by_system.get(system, 0))
    systems_missing_data = sorted(
        system for system in MAJOR_SYSTEMS
        if not any(knowledge.get(section, []) for section in SYSTEM_SECTIONS[system])
    )
    implemented = sorted(system for system in MAJOR_SYSTEMS if system in registry_systems())
    manifests = generator_manifests(player_state, knowledge)
    actions_scored_by_system = Counter(
        str(action.get("system", "unknown")) for action in actions if action.get("supported", True)
    )
    actions_skipped_by_system = Counter(
        str(action.get("system", "unknown")) for action in actions if not action.get("supported", True)
    )
    fully_supported = sorted(
        system for system in implemented
        if system not in systems_missing_data and actions_scored_by_system.get(system, 0)
    )
    partially_supported = sorted(set(implemented) - set(fully_supported))
    unsupported_systems = sorted(set(MAJOR_SYSTEMS) - set(implemented))
    warnings = knowledge.get("warnings", [])
    real_data_systems: list[str] = []
    # Resource rows are a catalog consumed by dedicated spend generators;
    # there is intentionally no standalone "resource" action to observe.
    catalog_only_systems = {"resources"}
    placeholder_only_systems: list[str] = []
    needs_review_by_system: dict[str, list[str]] = {}
    for system in MAJOR_SYSTEMS:
        records = [record for section in SYSTEM_SECTIONS[system] for record in knowledge.get(section, [])]
        real_records = [record for record in records if not _is_placeholder(record)]
        placeholders = [record for record in records if _is_placeholder(record)]
        if real_records:
            real_data_systems.append(system)
        elif placeholders:
            placeholder_only_systems.append(system)
        review_ids = sorted({_record_id(record) for record in records if _needs_review(record) and _record_id(record)})
        if review_ids:
            needs_review_by_system[system] = review_ids

    missing_item_names = sorted({
        _record_id(record) for records in knowledge.values() if isinstance(records, list) for record in records
        if _record_id(record) and ("missing_item_names" in _record_tags(record) or not str(_record_value(record, "name", "")).strip())
    })
    missing_costs = sorted({
        _record_id(record) for section in ("event_shops", "clan_shop", "universal_exchange", "pet_awakenings", "tech_resonance_costs")
        for record in knowledge.get(section, []) if _record_id(record) and (
            "missing_costs" in _record_tags(record)
            or (section in {"event_shops", "clan_shop", "universal_exchange"} and not (_record_value(record, "cost", {}) or {}))
        )
    })
    missing_unlock_requirements = sorted({
        _record_id(record) for section in ("skills", "survivors", "survivor_awakenings", "pets", "pet_awakenings", "xeno_pets")
        for record in knowledge.get(section, []) if _record_id(record)
        and (
            "missing_unlock_requirements" in _record_tags(record)
            or (
                section in {"skills", "survivors", "pets", "pet_awakenings", "xeno_pets"}
                and not (_record_value(record, "unlock_requirement", {}) or {})
            )
        )
    })
    missing_chest_contents = sorted({
        _record_id(record) for record in knowledge.get("chests", []) if _record_id(record)
        and not (_record_value(record, "choices", []) or [])
    })
    total = len(all_ids)
    supported_count = len(all_ids & supported_item_ids)
    return {
        "total_known_items": total,
        "supported_items": supported_count,
        "unsupported_items": unsupported,
        "total_known_inventory_item_ids": total,
        "total_supported_by_action_generator": supported_count,
        "unsupported_ids": unsupported,
        "unsupported_categories": {section: sorted(ids - supported_item_ids) for section, ids in known.items() if ids - supported_item_ids},
        "systems_implemented": implemented,
        "systems_seen_in_profiles": sorted({str(action.get("system")) for action in actions if action.get("metadata", {}).get("inventory_count", 1)}),
        "systems_generated_actions": systems_generated,
        "systems_simulated": [],
        "systems_scored": [],
        "systems_missing_data": systems_missing_data,
        "systems_fully_supported": fully_supported,
        "systems_partially_supported": partially_supported,
        "real_data_systems": sorted(real_data_systems),
        "observable_real_data_systems": sorted(set(real_data_systems) - catalog_only_systems),
        "catalog_only_systems": sorted(set(real_data_systems) & catalog_only_systems),
        "unobservable_system_reasons": {
            "resources": "Resource rows are inputs to core, resonance, pet, survivor, exchange, and save/hold actions; spending a currency without a destination is not a valid action."
        },
        "placeholder_only_systems": sorted(placeholder_only_systems),
        "missing_item_names": missing_item_names,
        "missing_costs": missing_costs,
        "missing_unlock_requirements": missing_unlock_requirements,
        "missing_chest_contents": missing_chest_contents,
        "needs_review_by_system": needs_review_by_system,
        "unsupported_systems": unsupported_systems,
        "supported_systems": systems_generated,
        "systems_with_generators": registry_systems(),
        "generator_manifests": manifests,
        "actions_by_system": dict(sorted(actions_by_system.items())),
        "actions_scored_by_system": dict(sorted(actions_scored_by_system.items())),
        "actions_skipped_by_system": dict(sorted(actions_skipped_by_system.items())),
        "missing_data_by_system": {system: manifests[system]["missing_data_warnings"] for system in partially_supported},
        "next_data_needed": {system: f"Add source-backed records for: {', '.join(SYSTEM_SECTIONS[system])}." for system in systems_missing_data},
        "item_affordance_coverage_percent": round((len(all_ids & action_item_ids) / total * 100) if total else 100.0, 3),
        "inventory_action_coverage_percent": round((supported_count / total * 100) if total else 100.0, 3),
        "actions_generated_for_audit_state": len(actions),
        "items_with_unknown_effects": unsupported,
        "items_with_no_known_spend_use_action": unsupported,
        "items_with_conflicting_guide_data": [getattr(record, "id", "") for record in warnings if "conflict" in getattr(record, "id", "")],
        "destructive_actions_requiring_caution": [action["action_id"] for action in actions if action.get("reversibility") == "irreversible"],
        "random_chests_without_odds": [action["action_id"] for action in actions if "random_chest" in action.get("action_type", "") and action.get("warnings")],
        "selector_chests_without_options": [action["action_id"] for action in actions if action.get("action_type") == "unsupported_selector"],
        "event_currencies_without_active_shop_data": [item_id for item_id in known.get("resources", set()) if "event" in item_id.lower()],
    }
