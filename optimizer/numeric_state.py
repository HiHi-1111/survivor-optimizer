"""Compact indexed player-state representation for training-time batching."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from optimizer.player_state import validate_player_state


INDEX_SECTIONS = ("resources", "items", "chests", "gear", "pets", "xeno_pets", "tech_parts", "collectibles", "survivors")


@dataclass(frozen=True)
class KnowledgeIndex:
    ids: tuple[str, ...]
    by_id: dict[str, int]

    @classmethod
    def from_knowledge(cls, knowledge: dict[str, Any]) -> "KnowledgeIndex":
        ids = sorted({str(getattr(record, "id", record.get("id", "") if isinstance(record, dict) else "")) for section in INDEX_SECTIONS for record in knowledge.get(section, [])} - {""})
        return cls(tuple(ids), {item_id: index for index, item_id in enumerate(ids)})


@dataclass
class NumericState:
    inventory: list[float]
    stats: list[float]
    breakpoint_flags: list[float]
    scenario_id: str


def to_numeric_state(player_state: Any, index: KnowledgeIndex) -> NumericState:
    state = validate_player_state(player_state)
    inventory = [0.0] * len(index.ids)
    for item_id, position in index.by_id.items():
        if hasattr(state.resources, item_id):
            inventory[position] = float(getattr(state.resources, item_id, 0) or 0)
            continue
        value = state.inventory.items.get(item_id, 0)
        inventory[position] = float(value.get("count", 0) if isinstance(value, dict) else value or 0)
    metadata = getattr(state, "metadata", {}) or {}
    reached = set(str(value) for value in metadata.get("reached_breakpoints", []) or [])
    return NumericState(
        inventory=inventory,
        stats=[float(getattr(state.build_stats, name, 0.0) or 0.0) for name in ("atk", "crit_rate", "crit_damage", "skill_damage", "boss_damage", "all_damage", "final_damage")],
        breakpoint_flags=[float(bool(metadata.get("xeno_unlocked"))), float("astral_forge_breakpoint" in reached), float("tech_resonance_breakpoint" in reached), float("collectible_set_breakpoint" in reached), float("survivor_breakpoint" in reached)],
        scenario_id=state.goal_scenario,
    )


def state_vector(state: NumericState) -> list[float]:
    return [*state.stats, *state.breakpoint_flags, *state.inventory]
