"""Generic knowledge-driven action generators."""

from __future__ import annotations

from typing import Any

from optimizer.action_generators.common import (
    base_action,
    description,
    inventory_count,
    record_id,
    record_name,
    save_hold_action,
    tags,
)
from optimizer.action_types import OptimizerAction


def _known_use_terms(record: Any) -> bool:
    text = " ".join([record_id(record), record_name(record), description(record), " ".join(tags(record))]).lower()
    terms = [
        "selector",
        "choice",
        "chest",
        "core",
        "upgrade",
        "merge",
        "forge",
        "awakening",
        "resonance",
        "shop",
        "exchange",
        "shard",
        "collectible",
        "pet",
        "survivor",
        "tech",
    ]
    return any(term in text for term in terms)


def generate_known_record_actions(
    *,
    player_state: Any,
    records: list[Any],
    system: str,
    action_type: str,
    affected_field: str | None = None,
    include_saves: bool = True,
) -> list[OptimizerAction]:
    actions: list[OptimizerAction] = []
    for record in records:
        item_id = record_id(record)
        name = record_name(record)
        count = inventory_count(player_state, item_id)
        has_known_use = _known_use_terms(record)

        if count > 0 and has_known_use:
            action = base_action(
                action_type=action_type,
                system=system,
                item_id=item_id,
                name=name,
                explanation=f"Evaluate {action_type.replace('_', ' ')} for {name} using known guide/knowledge data.",
                record=record,
                supported=True,
            )
            action.required_items[item_id] = 1
            action.consumed_items[item_id] = 0 if action_type.startswith(("equip", "switch")) else 1
            if affected_field:
                getattr(action, affected_field).append(item_id)
            action.metadata["inventory_count"] = count
            if system == "pets":
                action.metadata.setdefault("adds_progress", {})["xeno_unlock"] = 1
                action.metadata.setdefault("breakpoint_requirements", {})["xeno_unlock"] = 2
            elif system == "xeno_pets":
                action.metadata.setdefault("sets_breakpoints", []).append("xeno_breakpoint")
            elif system == "tech_parts" or "resonance" in item_id.lower():
                action.metadata.setdefault("adds_progress", {})["tech_resonance_breakpoint"] = 1
                action.metadata.setdefault("breakpoint_requirements", {})["tech_resonance_breakpoint"] = 2
            elif system == "collectibles" or "collectible" in item_id.lower():
                action.metadata.setdefault("adds_progress", {})["collectible_set_breakpoint"] = 1
                action.metadata.setdefault("breakpoint_requirements", {})["collectible_set_breakpoint"] = 2
            elif system == "survivors":
                action.metadata.setdefault("adds_progress", {})["survivor_breakpoint"] = 1
                action.metadata.setdefault("breakpoint_requirements", {})["survivor_breakpoint"] = 2
            if not description(record):
                action.warnings.append("Known id has no description/effect text; score is low-confidence.")
            actions.append(action)
        elif count > 0:
            action = base_action(
                action_type=f"unsupported_{action_type}",
                system=system,
                item_id=item_id,
                name=name,
                explanation=f"{name} is in inventory, but no known use/effect path exists in knowledge.",
                record=record,
                supported=False,
                warnings=["Missing known use/effect path; not recommended except as save/hold."],
            )
            action.required_items[item_id] = 1
            action.metadata["inventory_count"] = count
            actions.append(action)

        # Catalog knowledge is not inventory.  Emitting a hold for every known
        # record made newly-ingested data create expensive fake candidates.
        if include_saves and count > 0:
            actions.append(save_hold_action(system, item_id, name, "data may be incomplete or a later breakpoint may be better.", record))
    return actions


def generate_selector_actions(player_state: Any, chest_records: list[Any], include_saves: bool = True) -> list[OptimizerAction]:
    actions: list[OptimizerAction] = []
    for chest in chest_records:
        chest_id = record_id(chest)
        name = record_name(chest)
        choices = getattr(chest, "choices", None)
        if choices is None and isinstance(chest, dict):
            choices = chest.get("choices", [])
        choices = list(choices or [])
        count = inventory_count(player_state, chest_id)
        lower = f"{chest_id} {name} {description(chest)} {' '.join(tags(chest))}".lower()
        is_selector = any(term in lower for term in ["selector", "choice", "choose"]) or bool(choices)
        is_random = any(term in lower for term in ["random", "odds", "probability"])

        if count > 0 and is_selector:
            if choices:
                for choice in choices:
                    action = base_action(
                        action_type="select_from_chest",
                        system="selectors",
                        item_id=f"{chest_id}_{choice}",
                        name=f"{name} -> {choice}",
                        explanation=f"Select {choice} from {name}.",
                        record=chest,
                    )
                    action.required_items[chest_id] = 1
                    action.consumed_items[chest_id] = 1
                    action.produced_items[str(choice)] = 1
                    action.metadata.update({"item_id": chest_id, "chest_id": chest_id, "choice": choice, "inventory_count": count})
                    choice_text = str(choice).lower()
                    if "pet" in choice_text:
                        action.metadata.setdefault("adds_progress", {})["xeno_unlock"] = 1
                        action.metadata.setdefault("breakpoint_requirements", {})["xeno_unlock"] = 2
                    if "collectible" in choice_text:
                        action.metadata.setdefault("adds_progress", {})["collectible_set_breakpoint"] = 1
                        action.metadata.setdefault("breakpoint_requirements", {})["collectible_set_breakpoint"] = 2
                    if "tech" in choice_text or "resonance" in choice_text:
                        action.metadata.setdefault("adds_progress", {})["tech_resonance_breakpoint"] = 1
                        action.metadata.setdefault("breakpoint_requirements", {})["tech_resonance_breakpoint"] = 2
                    actions.append(action)
            else:
                actions.append(
                    base_action(
                        action_type="unsupported_selector",
                        system="selectors",
                        item_id=chest_id,
                        name=name,
                        explanation=f"{name} looks like a selector, but no options are known.",
                        record=chest,
                        supported=False,
                        warnings=["Selector chest has no known choices."],
                    )
                )

        if count > 0 and is_random:
            for mode in ["expected_value", "best_case", "worst_case", "conservative"]:
                action = base_action(
                    action_type=f"open_random_chest_{mode}",
                    system="chests",
                    item_id=f"{chest_id}_{mode}",
                    name=name,
                    explanation=f"Evaluate {mode.replace('_', ' ')} opening for {name}; randomness is not guaranteed.",
                    record=chest,
                    warnings=["Random chest odds may be unknown; do not treat outcome as guaranteed."],
                )
                action.required_items[chest_id] = 1
                action.consumed_items[chest_id] = 1
                action.metadata.update({"chest_id": chest_id, "random_mode": mode, "inventory_count": count})
                actions.append(action)

        if include_saves and count > 0:
            actions.append(save_hold_action("chests", chest_id, name, "selector/random value may improve near a breakpoint.", chest))
    return actions
