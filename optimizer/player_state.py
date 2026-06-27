"""Player state models used by the optimizer runtime."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FlexibleStateModel(BaseModel):
    model_config = {"extra": "allow"}


class PlayerBuildStats(FlexibleStateModel):
    atk: float = 0
    crit_rate: float = 0
    crit_damage: float = 0
    skill_damage: float = 0
    vulnerability: float = 0
    shield_damage: float = 0
    damage_to_chilled: float = 0
    damage_to_poisoned: float = 0
    boss_damage: float = 0
    all_damage: float = 0
    final_damage: float = 0


class PlayerGear(FlexibleStateModel):
    weapon: dict[str, Any] | None = None
    necklace: dict[str, Any] | None = None
    gloves: dict[str, Any] | None = None
    armor: dict[str, Any] | None = None
    belt: dict[str, Any] | None = None
    boots: dict[str, Any] | None = None


class PlayerSurvivor(FlexibleStateModel):
    id: str | None = None
    awakening: int = 0
    passives: list[str] = Field(default_factory=list)


class PlayerPetSetup(FlexibleStateModel):
    main_pet: str | None = None
    assisting_pets: list[str] = Field(default_factory=list)
    awakened: dict[str, int] = Field(default_factory=dict)


class PlayerTechSetup(FlexibleStateModel):
    equipped: list[str] = Field(default_factory=list)
    resonance: dict[str, Any] = Field(default_factory=dict)


class PlayerCollectibles(FlexibleStateModel):
    owned: dict[str, Any] = Field(default_factory=dict)
    salvage: dict[str, Any] = Field(default_factory=dict)


class PlayerResources(FlexibleStateModel):
    astral_core: int = 0
    xeno_core: int = 0
    resonance_chip: int = 0
    relic_core: int = 0
    gems: int = 0
    keys: int = 0


class PlayerInventory(FlexibleStateModel):
    core_selector_chests: int = 0
    selector_chests: dict[str, int] = Field(default_factory=dict)
    items: dict[str, Any] = Field(default_factory=dict)


class PlayerState(FlexibleStateModel):
    build_stats: PlayerBuildStats = Field(default_factory=PlayerBuildStats)
    gear: PlayerGear = Field(default_factory=PlayerGear)
    survivor: PlayerSurvivor = Field(default_factory=PlayerSurvivor)
    pets: PlayerPetSetup = Field(default_factory=PlayerPetSetup)
    tech_parts: PlayerTechSetup = Field(default_factory=PlayerTechSetup)
    collectibles: PlayerCollectibles = Field(default_factory=PlayerCollectibles)
    resources: PlayerResources = Field(default_factory=PlayerResources)
    inventory: PlayerInventory = Field(default_factory=PlayerInventory)
    owned_items: list[str] = Field(default_factory=list)
    goal_scenario: str = "scenario_1"
    notes: str = ""


def validate_player_state(data: dict[str, Any] | PlayerState) -> PlayerState:
    if isinstance(data, PlayerState):
        return data
    return PlayerState(**data)
