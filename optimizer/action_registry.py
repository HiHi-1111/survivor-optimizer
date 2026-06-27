"""Registry for inventory action generators."""

from __future__ import annotations

from collections import Counter, defaultdict
import time
from typing import Any, Callable

from optimizer.action_generators import (
    chests,
    clan_shop,
    collectible_sets,
    collectibles,
    cores,
    events,
    event_shops,
    exchanges,
    gear,
    merge,
    pet_awakenings,
    pet_merging,
    pets,
    resources,
    resonance,
    salvage,
    save_hold,
    shops,
    ss_gear,
    selectors,
    skills,
    survivors,
    survivor_awakening,
    tech_parts,
    xeno_pets,
    universal_exchange,
)
from optimizer.action_types import OptimizerAction, action_to_dict
from optimizer.player_state import validate_player_state
from optimizer.proposal_budget import budget_proposals
from optimizer.state_hash import state_fingerprint


Generator = Callable[[Any, dict[str, Any], dict[str, Any] | None], list[OptimizerAction]]
_ACTION_CACHE: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
_ACTION_CACHE_LIMIT = 20000
_ACTION_CACHE_HITS = 0
_ACTION_CACHE_MISSES = 0
_GENERATOR_CALLS: Counter[str] = Counter()
_GENERATOR_RAW: Counter[str] = Counter()
_GENERATOR_EMITTED: Counter[str] = Counter()
_GENERATOR_DUPLICATES: Counter[str] = Counter()
_GENERATOR_SECONDS: defaultdict[str, float] = defaultdict(float)
_LAST_PROPOSAL_BUDGET_STATS: dict[str, int] = {}


ACTION_GENERATORS: dict[str, Generator] = {
    "resources": resources.generate_actions,
    "cores": cores.generate_actions,
    "chests": chests.generate_actions,
    "selectors": selectors.generate_actions,
    "skills": skills.generate_actions,
    "gear": gear.generate_actions,
    "ss_gear": ss_gear.generate_actions,
    "pets": pets.generate_actions,
    "pet_merging": pet_merging.generate_actions,
    "pet_awakenings": pet_awakenings.generate_actions,
    "xeno_pets": xeno_pets.generate_actions,
    "tech_parts": tech_parts.generate_actions,
    "resonance": resonance.generate_actions,
    "collectibles": collectibles.generate_actions,
    "collectible_sets": collectible_sets.generate_actions,
    "survivors": survivors.generate_actions,
    "survivor_awakening": survivor_awakening.generate_actions,
    "events": events.generate_actions,
    "event_shops": event_shops.generate_actions,
    "clan_shop": clan_shop.generate_actions,
    "shops": shops.generate_actions,
    "clan": shops.generate_actions,
    "special_ops": shops.generate_actions,
    "exchanges": exchanges.generate_actions,
    "universal_exchange": universal_exchange.generate_actions,
    "merge": merge.generate_actions,
    "salvage": salvage.generate_actions,
    "save_hold": save_hold.generate_actions,
}

ACTION_TYPE_MANIFEST: dict[str, tuple[str, ...]] = {
    "gear": ("upgrade_or_equip_gear",), "ss_gear": ("forge_ss_gear",),
    "cores": ("spend_core",), "resources": ("spend_resource",),
    "chests": ("open_chest",), "selectors": ("select_from_chest",),
    "pets": ("upgrade_or_awaken_pet",), "pet_merging": ("merge_pet",), "pet_awakenings": ("awaken_pet",),
    "xeno_pets": ("unlock_or_upgrade_xeno_pet",), "skills": ("equip_or_evolve_skill",),
    "tech_parts": ("upgrade_or_equip_tech",), "resonance": ("advance_resonance",),
    "collectibles": ("upgrade_collectible",), "collectible_sets": ("complete_collectible_set",),
    "survivors": ("upgrade_survivor",), "survivor_awakening": ("awaken_survivor",),
    "events": ("spend_event_resource",), "event_shops": ("buy_event_shop_item",),
    "clan_shop": ("buy_clan_shop_item",), "exchanges": ("exchange_resource",),
    "universal_exchange": ("universal_exchange",), "merge": ("merge_item",),
    "salvage": ("salvage_item",), "save_hold": ("save_hold",),
}

# Profile priors use stable domain names while generators retain their older
# module names. Expand aliases at this boundary so a retained learned system is
# never silently ignored.
SYSTEM_ALIASES: dict[str, tuple[str, ...]] = {
    "core_selector": ("resources", "cores"),
    "pet": ("pets",),
    "xeno_pet": ("xeno_pets",),
    "tech": ("tech_parts", "resonance"),
    "collectible": ("collectibles", "collectible_sets"),
    "survivor": ("survivors", "survivor_awakening"),
    "event_shop": ("events", "event_shops"),
    "clan_shop": ("clan_shop",),
    "exchange": ("exchanges", "universal_exchange"),
    "chest_opening": ("chests",),
    "selector_chest": ("selectors",),
}

SYSTEM_INPUT_SECTIONS: dict[str, tuple[str, ...]] = {
    "resources": ("resources",), "cores": ("resources",), "chests": ("chests", "chest_odds", "collectible_chest_odds"),
    "selectors": ("chests",), "skills": ("skills",), "gear": ("gear", "weapons"), "ss_gear": ("resources", "gear", "weapons"),
    "pets": ("pets", "pet_awakenings", "pet_merging"), "pet_merging": ("pet_merging",), "pet_awakenings": ("pet_awakenings",),
    "xeno_pets": ("xeno_pets", "resources"), "tech_parts": ("tech_parts", "resources"),
    "resonance": ("tech_parts", "tech_resonance", "tech_resonance_costs", "resources"),
    "collectibles": ("collectibles",), "collectible_sets": ("collectible_sets",),
    "survivors": ("survivors", "survivor_awakenings", "survivor_energy_essence_costs", "resources"),
    "survivor_awakening": ("survivor_awakenings",), "events": ("events",), "event_shops": ("event_shops",),
    "clan_shop": ("clan_shop",), "shops": ("event_shops",), "clan": ("event_shops",), "special_ops": ("event_shops",),
    "exchanges": ("universal_exchange", "conversions", "resources"), "universal_exchange": ("universal_exchange",),
    "merge": ("gear", "pets", "tech_parts"), "salvage": ("gear", "pets", "tech_parts", "collectibles", "items"),
    "save_hold": ("resources", "items", "chests"),
}


def _expand_systems(systems: list[str] | None) -> list[str]:
    if systems is None:
        return registry_systems()
    expanded: list[str] = []
    seen: set[str] = set()
    for system in systems:
        for registry_name in SYSTEM_ALIASES.get(system, (system,)):
            if registry_name not in seen:
                seen.add(registry_name)
                expanded.append(registry_name)
    return expanded


def registry_systems() -> list[str]:
    return sorted(ACTION_GENERATORS)


def _positive_inventory_ids(state: Any) -> set[str]:
    owned: set[str] = set()
    resources = getattr(state, "resources", None)
    if resources is not None:
        values = resources.model_dump() if hasattr(resources, "model_dump") else vars(resources)
        owned.update(str(key) for key, value in values.items() if isinstance(value, (int, float)) and value > 0)
    inventory = getattr(state, "inventory", None)
    if inventory is not None:
        for key, value in (getattr(inventory, "items", {}) or {}).items():
            count = value.get("count", 0) if isinstance(value, dict) else value
            if isinstance(count, (int, float)) and count > 0:
                owned.add(str(key))
        if int(getattr(inventory, "core_selector_chests", 0) or 0) > 0:
            owned.update({"core_selector", "core_selector_chest"})
        owned.update(str(key) for key, value in (getattr(inventory, "selector_chests", {}) or {}).items() if value)
    # Raw profiles commonly put items at the top level. Pydantic retains that
    # extra field after validation, so include it in the budget gate as well.
    for key, value in (getattr(state, "items", {}) or {}).items():
        count = value.get("count", 0) if isinstance(value, dict) else value
        if isinstance(count, (int, float)) and count > 0:
            owned.add(str(key))
    return owned


def _record_id(record: Any) -> str:
    return str(record.get("id", "")) if isinstance(record, dict) else str(getattr(record, "id", ""))


def _record_field(record: Any, field: str, default: Any = None) -> Any:
    return record.get(field, default) if isinstance(record, dict) else getattr(record, field, default)


def _system_may_act(system: str, state: Any, knowledge: dict[str, Any], owned: set[str]) -> bool:
    if system in {"resources", "salvage", "clan_shop"}:
        return False
    if system == "survivors" and not any(
        _record_field(record, "cost") or _record_field(record, "upgrade_cost")
        for section in ("survivors", "survivor_awakenings", "survivor_energy_essence_costs")
        for record in knowledge.get(section, [])
    ):
        return False
    if system == "exchanges":
        actionable = list(knowledge.get("universal_exchange", [])) + list(knowledge.get("conversions", []))
        return any(_record_id(record) in owned for record in actionable)
    if system == "pet_awakenings":
        return "awakening_crystal" in owned and bool(knowledge.get("pet_awakenings", []))
    if system in {"collectibles", "collectible_sets", "chests"} and owned & {
        "red_collectible_chest", "collectible_shard",
    }:
        return True
    sections = SYSTEM_INPUT_SECTIONS.get(system, ())
    records = [record for section in sections for record in knowledge.get(section, [])]
    if not records:
        return False
    record_ids = {_record_id(record) for record in records if _record_id(record)}
    if system in {"selectors", "chests"}:
        return bool(owned & record_ids) or "core_selector" in owned or "core_selector_chest" in owned
    if system == "merge":
        inventory = getattr(getattr(state, "inventory", None), "items", {}) or {}
        return any(
            item_id in record_ids and float(value.get("count", 0) if isinstance(value, dict) else value or 0) >= 2
            for item_id, value in inventory.items()
        )
    if system == "ss_gear":
        return bool(owned & {"relic_core", "astral_core", "eternal_core", "void_core", "chaos_core", "xeno_core"})
    return bool(owned & record_ids)


def generator_manifests(player_state: Any, knowledge: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Describe each plugin without pretending missing game data is scoreable."""
    state = validate_player_state(player_state)
    manifests: dict[str, dict[str, Any]] = {}
    for system in registry_systems():
        generated = [action_to_dict(action) for action in ACTION_GENERATORS[system](state, knowledge, {"include_saves": True, "include_random_ev": True})]
        scoreable = [action for action in generated if action.get("supported", True)]
        missing = sorted({warning for action in generated if not action.get("supported", True) for warning in action.get("warnings", [])})
        if not generated:
            missing = ["No source-backed records or owned resources are available for this system."]
        manifests[system] = {
            "system": system,
            "supported_action_types": list(ACTION_TYPE_MANIFEST.get(system, ())),
            "required_resources": sorted({item for action in generated for item in (action.get("required_items") or {})}),
            "generated_candidate_actions": len(generated),
            "missing_data_warnings": missing,
            "can_score_now": bool(scoreable),
            "disposition": "evaluate" if scoreable else ("skip_with_warning" if generated else "evaluate_later"),
            "plugin_module": ACTION_GENERATORS[system].__module__,
        }
    return manifests


def generate_inventory_actions(
    player_state: Any,
    knowledge: dict[str, Any],
    *,
    systems: list[str] | None = None,
    include_saves: bool = True,
    include_random_ev: bool = True,
    max_actions: int | None = None,
    include_missing_placeholders: bool = True,
    use_cache: bool = True,
    proposal_budget: bool = False,
    scoreable_only: bool = False,
    total_proposal_budget: int = 24,
) -> list[dict[str, Any]]:
    state = validate_player_state(player_state)
    selected = _expand_systems(systems)
    cache_key = (id(knowledge), state_fingerprint(state), tuple(selected), include_saves, include_random_ev, max_actions, include_missing_placeholders, proposal_budget, scoreable_only, total_proposal_budget) if use_cache else None
    global _ACTION_CACHE_HITS, _ACTION_CACHE_MISSES
    cached = _ACTION_CACHE.get(cache_key) if cache_key is not None else None
    if cached is not None:
        _ACTION_CACHE_HITS += 1
        return cached
    _ACTION_CACHE_MISSES += int(use_cache)
    options = {"include_saves": include_saves, "include_random_ev": include_random_ev, "proposal_budget": proposal_budget}
    generated_actions: list[OptimizerAction] = []
    owned = _positive_inventory_ids(state) if proposal_budget else set()
    for system in selected:
        generator = ACTION_GENERATORS.get(system)
        if generator is None:
            continue
        if proposal_budget and not _system_may_act(system, state, knowledge, owned):
            continue
        generator_started = time.perf_counter()
        generated = generator(state, knowledge, options)
        _GENERATOR_SECONDS[system] += time.perf_counter() - generator_started
        _GENERATOR_CALLS[system] += 1
        _GENERATOR_RAW[system] += len(generated)
        generated_actions.extend(generated)
        if not generated and include_missing_placeholders and not proposal_budget and not scoreable_only:
            missing_action = OptimizerAction(
                action_id=f"missing_data:{system}",
                action_type=(ACTION_TYPE_MANIFEST.get(system, ("review_missing_data",))[0]),
                system=system,
                confidence="missing",
                reversibility="reversible",
                supported=False,
                explanation=f"{system} is represented in planning, but source-backed actionable data is missing.",
                warnings=[f"Missing source-backed {system} data; this placeholder is not scoreable and cannot be recommended."],
                metadata={"missing_data": True, "placeholder": True, "system": system},
            )
            generated_actions.append(missing_action)

    global _LAST_PROPOSAL_BUDGET_STATS
    if proposal_budget:
        generated_actions, budget_stats = budget_proposals(generated_actions, state, total_budget=total_proposal_budget)
        _LAST_PROPOSAL_BUDGET_STATS = budget_stats.to_dict()
    else:
        _LAST_PROPOSAL_BUDGET_STATS = {}
    if scoreable_only:
        generated_actions = [action for action in generated_actions if action.supported and action.confidence != "missing"]

    actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for action in generated_actions:
        data = action_to_dict(action)
        action_id = str(data.get("action_id", ""))
        if not action_id or action_id in seen:
            _GENERATOR_DUPLICATES[action.system] += 1
            continue
        seen.add(action_id)
        actions.append(data)
        _GENERATOR_EMITTED[action.system] += 1
        if max_actions is not None and len(actions) >= max_actions:
            break
    if len(_ACTION_CACHE) >= _ACTION_CACHE_LIMIT:
        _ACTION_CACHE.clear()
    if cache_key is not None:
        _ACTION_CACHE[cache_key] = actions
    return actions


def clear_action_cache() -> None:
    global _ACTION_CACHE_HITS, _ACTION_CACHE_MISSES
    _ACTION_CACHE.clear()
    _ACTION_CACHE_HITS = 0
    _ACTION_CACHE_MISSES = 0


def clear_action_generator_stats() -> None:
    _GENERATOR_CALLS.clear()
    _GENERATOR_RAW.clear()
    _GENERATOR_EMITTED.clear()
    _GENERATOR_DUPLICATES.clear()
    _GENERATOR_SECONDS.clear()


def action_generator_stats() -> dict[str, dict[str, float | int]]:
    systems = set(_GENERATOR_CALLS) | set(_GENERATOR_RAW) | set(_GENERATOR_EMITTED)
    return {
        system: {
            "calls": _GENERATOR_CALLS[system],
            "object_creation_seconds": round(_GENERATOR_SECONDS[system], 6),
            "raw_candidates": _GENERATOR_RAW[system],
            "emitted_candidates": _GENERATOR_EMITTED[system],
            "generator_duplicates": _GENERATOR_DUPLICATES[system],
        }
        for system in sorted(systems)
    }


def action_cache_stats() -> dict[str, int]:
    return {"hits": _ACTION_CACHE_HITS, "misses": _ACTION_CACHE_MISSES, "entries": len(_ACTION_CACHE)}


def last_proposal_budget_stats() -> dict[str, int]:
    return dict(_LAST_PROPOSAL_BUDGET_STATS)
