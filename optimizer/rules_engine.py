"""Apply extracted JSON guide rules to scored actions."""

from __future__ import annotations

from typing import Any

from optimizer.scorer import ScoredAction


def _record_value(record: Any, field: str, default: Any) -> Any:
    if isinstance(record, dict):
        return record.get(field, default)
    return getattr(record, field, default)


def _action_terms(action: ScoredAction) -> set[str]:
    terms = {action.action_type}
    terms.update(key for key, value in action.allocation.items() if value)
    return terms


def apply_rules_to_action(action: ScoredAction, rules: list[Any]) -> ScoredAction:
    """Attach matching rule reasons without hiding the underlying score math."""
    terms = _action_terms(action)
    seen = set(action.reasons)
    for rule in rules:
        applies_to = set(_record_value(rule, "applies_to", []) or [])
        tags = set(_record_value(rule, "tags", []) or [])
        if not (terms & applies_to or terms & tags):
            continue
        description = _record_value(rule, "description", "")
        if not description:
            continue
        reason = f"Guide rule: {description}"
        if reason not in seen:
            action.reasons.append(reason)
            seen.add(reason)
    return action


def apply_rules(scored_actions: list[ScoredAction], rules: list[Any]) -> list[ScoredAction]:
    return [apply_rules_to_action(action, rules) for action in scored_actions]
