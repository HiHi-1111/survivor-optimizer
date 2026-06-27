"""Structured optimizer action records."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class OptimizerAction:
    action_id: str
    action_type: str
    system: str
    required_items: dict[str, int | float] = field(default_factory=dict)
    consumed_items: dict[str, int | float] = field(default_factory=dict)
    produced_items: dict[str, int | float] = field(default_factory=dict)
    affected_equipment: list[str] = field(default_factory=list)
    affected_pet: list[str] = field(default_factory=list)
    affected_survivor: list[str] = field(default_factory=list)
    affected_tech: list[str] = field(default_factory=list)
    affected_collectible: list[str] = field(default_factory=list)
    expected_damage_delta: float = 0.0
    long_term_value: float = 0.0
    breakpoint_value: float = 0.0
    resource_efficiency: float = 0.0
    confidence: str = "low"
    reversibility: str = "unknown"
    source_refs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    explanation: str = ""
    supported: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.update({
            "required_resources": dict(self.required_items),
            "consumed_resources": dict(self.consumed_items),
            "produced_state_delta": dict(self.produced_items),
            "long_term_value_delta": self.long_term_value,
            "risk_uncertainty": list(self.warnings),
            "missing_data_warnings": list(self.warnings),
            "can_score_now": bool(self.supported and self.confidence not in {"missing"}),
        })
        return data


def action_to_dict(action: OptimizerAction | dict[str, Any]) -> dict[str, Any]:
    if isinstance(action, OptimizerAction):
        return action.to_dict()
    return action
