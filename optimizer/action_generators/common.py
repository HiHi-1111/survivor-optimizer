"""Shared action generator helpers."""

from __future__ import annotations

from typing import Any

from optimizer.action_types import OptimizerAction


def record_value(record: Any, field: str, default: Any = None) -> Any:
    if isinstance(record, dict):
        return record.get(field, default)
    return getattr(record, field, default)


def record_id(record: Any) -> str:
    return str(record_value(record, "id", "unknown"))


def record_name(record: Any) -> str:
    return str(record_value(record, "name", record_id(record)))


def source_refs(record: Any) -> list[str]:
    source = record_value(record, "source", "")
    return [str(source)] if source else []


def confidence(record: Any) -> str:
    return str(record_value(record, "confidence", "low"))


def tags(record: Any) -> set[str]:
    raw = record_value(record, "tags", []) or []
    return {str(item).lower() for item in raw}


def description(record: Any) -> str:
    return str(record_value(record, "description", ""))


def inventory_count(player_state: Any, item_id: str) -> float:
    resources = getattr(player_state, "resources", None)
    inventory = getattr(player_state, "inventory", None)
    if resources is not None and hasattr(resources, item_id):
        return float(getattr(resources, item_id, 0) or 0)
    if inventory is not None:
        if item_id == "core_selector_chest":
            return float(getattr(inventory, "core_selector_chests", 0) or 0)
        items = getattr(inventory, "items", {}) or {}
        if item_id in items:
            value = items[item_id]
            if isinstance(value, dict):
                return float(value.get("count", 0) or 0)
            return float(value or 0)
        selectors = getattr(inventory, "selector_chests", {}) or {}
        if item_id in selectors:
            return float(selectors[item_id] or 0)
    return 0.0


def base_action(
    *,
    action_type: str,
    system: str,
    item_id: str,
    name: str,
    explanation: str,
    record: Any | None = None,
    supported: bool = True,
    warnings: list[str] | None = None,
) -> OptimizerAction:
    return OptimizerAction(
        action_id=f"{system}:{action_type}:{item_id}",
        action_type=action_type,
        system=system,
        confidence=confidence(record) if record is not None else "low",
        source_refs=source_refs(record) if record is not None else [],
        warnings=warnings or [],
        explanation=explanation,
        supported=supported,
        metadata={"item_id": item_id, "name": name},
    )


def save_hold_action(system: str, item_id: str, name: str, reason: str, record: Any | None = None) -> OptimizerAction:
    action = base_action(
        action_type="save_hold",
        system=system,
        item_id=item_id,
        name=name,
        explanation=f"Hold {name}: {reason}",
        record=record,
        supported=True,
        warnings=[],
    )
    # Holds are only legal while the referenced item is still owned. This also
    # lets the planner reuse compiled templates after an earlier spend.
    action.required_items[item_id] = 1
    return action
