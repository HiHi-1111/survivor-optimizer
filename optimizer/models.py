"""Pydantic models for structured Survivor.io knowledge."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Confidence = Literal["missing", "low", "medium", "high", "confirmed"]
SourceType = Literal[
    "game",
    "discord",
    "community-tested",
    "veteran-rule",
    "inferred",
    "unknown",
]
ScoringRelevance = Literal[
    "damage",
    "resource",
    "survival",
    "utility",
    "ignored_by_default",
]


class FlexibleModel(BaseModel):
    model_config = {"extra": "allow"}


class Effect(FlexibleModel):
    id: str | None = None
    stat: str | None = None
    value: float | int | str | None = None
    operation: str | None = None
    condition: str | None = None
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class BaseKnowledgeRecord(FlexibleModel):
    id: str
    name: str
    category: str = "unknown"
    type: str | None = None
    description: str = ""
    effects: list[Effect] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source_type: SourceType = "unknown"
    source: str = ""
    date: str = ""
    confidence: Confidence = "medium"
    notes: str = ""
    scoring_relevance: list[ScoringRelevance] = Field(default_factory=lambda: ["utility"])
    metadata: dict[str, Any] = Field(default_factory=dict)


class StatBucket(BaseKnowledgeRecord):
    category: str = "stat_bucket"
    scoring_relevance: list[ScoringRelevance] = Field(default_factory=lambda: ["damage"])


class Item(BaseKnowledgeRecord):
    category: str = "item"


class Gear(BaseKnowledgeRecord):
    category: str = "gear"
    slot: str | None = None
    rarity: str | None = None
    family: str | None = None


class GearSet(BaseKnowledgeRecord):
    category: str = "gear_set"
    pieces_required: int | None = None


class Survivor(BaseKnowledgeRecord):
    category: str = "survivor"


class SurvivorAwakening(BaseKnowledgeRecord):
    category: str = "survivor_awakening"
    survivor_id: str | None = None
    level: int | None = None


class Pet(BaseKnowledgeRecord):
    category: str = "pet"


class XenoPet(BaseKnowledgeRecord):
    category: str = "xeno_pet"


class TechPart(BaseKnowledgeRecord):
    category: str = "tech_part"


class Collectible(BaseKnowledgeRecord):
    category: str = "collectible"


class CollectibleSet(BaseKnowledgeRecord):
    category: str = "collectible_set"


class Resource(BaseKnowledgeRecord):
    category: str = "resource"


class Chest(BaseKnowledgeRecord):
    category: str = "chest"
    choices: list[str] = Field(default_factory=list)


class Event(BaseKnowledgeRecord):
    category: str = "event"
    starts_at: str | None = None
    ends_at: str | None = None


class EventShopItem(BaseKnowledgeRecord):
    category: str = "event_shop_item"
    event_id: str | None = None
    cost: dict[str, int | float] = Field(default_factory=dict)


class Breakpoint(BaseKnowledgeRecord):
    category: str = "breakpoint"
    requirements: dict[str, Any] = Field(default_factory=dict)


class Rule(BaseKnowledgeRecord):
    category: str = "rule"
    applies_to: list[str] = Field(default_factory=list)


class HiddenInteraction(BaseKnowledgeRecord):
    category: str = "hidden_interaction"


class WarningRecord(BaseKnowledgeRecord):
    category: str = "warning"


class Scenario(FlexibleModel):
    id: str
    name: str
    description: str = ""
    weights: dict[str, float] = Field(default_factory=dict)
