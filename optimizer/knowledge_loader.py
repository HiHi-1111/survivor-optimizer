"""Load and validate structured knowledge JSON files."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from optimizer.models import (
    Breakpoint,
    Chest,
    Collectible,
    CollectibleSet,
    Event,
    EventShopItem,
    Gear,
    GearSet,
    HiddenInteraction,
    Item,
    Pet,
    Resource,
    Rule,
    Scenario,
    StatBucket,
    Survivor,
    SurvivorAwakening,
    TechPart,
    WarningRecord,
    XenoPet,
)
from optimizer.scoring_weights import load_scoring_weights

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = ROOT / "knowledge"

KNOWLEDGE_MODELS = {
    "scenarios": Scenario,
    "stat_buckets": StatBucket,
    "items": Item,
    "item_effects": Item,
    "gear": Gear,
    "gear_sets": GearSet,
    "weapons": Gear,
    "skills": Item,
    "survivors": Survivor,
    "survivor_awakenings": SurvivorAwakening,
    "survivor_energy_essence_costs": Item,
    "pets": Pet,
    "pet_merging": Item,
    "pet_awakenings": Item,
    "xeno_pets": XenoPet,
    "tech_parts": TechPart,
    "tech_resonance": TechPart,
    "tech_resonance_costs": Item,
    "collectibles": Collectible,
    "collectible_sets": CollectibleSet,
    "collectible_chest_odds": Item,
    "resources": Resource,
    "chests": Chest,
    "chest_odds": Item,
    "events": Event,
    "event_shops": EventShopItem,
    "clan_shop": EventShopItem,
    "universal_exchange": Item,
    "conversions": Item,
    "crit_stats": Item,
    "source_confidence": Item,
    "breakpoints": Breakpoint,
    "rules": Rule,
    "hidden_interactions": HiddenInteraction,
    "warnings": WarningRecord,
}


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=8)
def _load_knowledge_cached(knowledge_dir: str) -> dict[str, Any]:
    knowledge_path = Path(knowledge_dir)
    loaded: dict[str, Any] = {}

    metadata_path = knowledge_path / "metadata.json"
    loaded["metadata"] = _load_json(metadata_path) if metadata_path.exists() else {}
    loaded["scoring_weights"] = load_scoring_weights(knowledge_path)

    for section, model in KNOWLEDGE_MODELS.items():
        path = knowledge_path / f"{section}.json"
        if not path.exists():
            raise FileNotFoundError(f"Missing knowledge file: {path}")
        raw_records = _load_json(path)
        if not isinstance(raw_records, list):
            raise ValueError(f"{path} must contain a JSON array")
        loaded[section] = [model(**record) for record in raw_records]

    return loaded


def load_knowledge(knowledge_dir: Path | str = KNOWLEDGE_DIR) -> dict[str, Any]:
    return _load_knowledge_cached(str(Path(knowledge_dir).resolve()))


def clear_knowledge_cache() -> None:
    _load_knowledge_cached.cache_clear()


def knowledge_counts(knowledge: dict[str, Any]) -> dict[str, int]:
    return {
        key: len(value)
        for key, value in knowledge.items()
        if isinstance(value, list)
    }
