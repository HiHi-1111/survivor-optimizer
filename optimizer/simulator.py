"""Apply candidate actions to player state copies."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from optimizer.action_generator import CoreSelectorSplit
from optimizer.player_state import PlayerState


def apply_core_selector_split(
    player_state: PlayerState,
    split: CoreSelectorSplit,
) -> PlayerState:
    future_state = deepcopy(player_state)
    for resource_id, count in split.allocation.items():
        current = getattr(future_state.resources, resource_id, 0)
        setattr(future_state.resources, resource_id, current + count)
    future_state.inventory.core_selector_chests = max(
        0,
        future_state.inventory.core_selector_chests - sum(split.allocation.values()),
    )
    return future_state


DAMAGE_STATS = {
    "atk",
    "crit_rate",
    "crit_damage",
    "skill_damage",
    "vulnerability",
    "shield_damage",
    "damage_to_chilled",
    "damage_to_poisoned",
    "boss_damage",
    "all_damage",
    "final_damage",
}


def _get_resource(state: PlayerState, resource_id: str) -> float:
    return float(getattr(state.resources, resource_id, 0))


def _set_resource(state: PlayerState, resource_id: str, value: float) -> None:
    setattr(state.resources, resource_id, int(value) if float(value).is_integer() else value)


def _requirements_met(state: PlayerState, requirements: dict[str, Any]) -> bool:
    for resource_id, required in requirements.items():
        if _get_resource(state, resource_id) < float(required):
            return False
    return True


def simulate_upgrade_chain(player_state: PlayerState, steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply a simple explainable multi-step upgrade chain."""
    future_state = deepcopy(player_state)
    trace: list[dict[str, Any]] = []
    unlocked: set[str] = set(future_state.owned_items)

    for index, step in enumerate(steps, start=1):
        action = step.get("action")
        result: dict[str, Any] = {"step": index, "action": action, "applied": False, "notes": ""}

        if action == "add_resource":
            resource_id = str(step["resource"])
            amount = float(step.get("amount", 0))
            _set_resource(future_state, resource_id, _get_resource(future_state, resource_id) + amount)
            result.update({"applied": True, "resource": resource_id, "amount": amount})

        elif action == "spend_resource":
            resource_id = str(step["resource"])
            amount = float(step.get("amount", 0))
            current = _get_resource(future_state, resource_id)
            if current >= amount:
                _set_resource(future_state, resource_id, current - amount)
                result.update({"applied": True, "resource": resource_id, "amount": amount})
            else:
                result["notes"] = "Not enough resource."

        elif action == "unlock_breakpoint":
            breakpoint_id = str(step["id"])
            requirements = dict(step.get("requirements", {}))
            if _requirements_met(future_state, requirements):
                unlocked.add(breakpoint_id)
                if breakpoint_id not in future_state.owned_items:
                    future_state.owned_items.append(breakpoint_id)
                result.update({"applied": True, "unlocked": breakpoint_id})
            else:
                result["notes"] = "Requirements not met."

        elif action == "apply_damage_effect":
            required_unlock = step.get("requires_unlocked")
            stat = str(step["stat"])
            if required_unlock and required_unlock not in unlocked:
                result["notes"] = "Required breakpoint is not unlocked."
            elif stat not in DAMAGE_STATS:
                result["notes"] = "Ignored non-damage stat in damage-first simulator."
            else:
                amount = float(step.get("amount", 0))
                operation = step.get("operation", "add")
                current = float(getattr(future_state.build_stats, stat, 0))
                new_value = current * amount if operation == "multiply" else current + amount
                setattr(future_state.build_stats, stat, new_value)
                result.update({"applied": True, "stat": stat, "before": current, "after": new_value})

        else:
            result["notes"] = "Unknown chain action."

        trace.append(result)

    return {"state": future_state, "trace": trace}
