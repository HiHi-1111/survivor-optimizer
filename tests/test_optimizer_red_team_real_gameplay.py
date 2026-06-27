"""
Real gameplay red-team traps for Survivor.io optimizer.

These are not fake/impossible values.
They represent realistic player-data situations that can trick an optimizer:

- item exists in inventory but is not equipped
- upgrade preview exists but is locked
- next AF bonus is visible but not paid for yet
- Twinborn has two modes but only one can be active
- survivor roster has multiple survivors but only one active survivor counts
- resonance candidates exist but are not currently equipped
- collectible next-level bonus exists but is not unlocked yet
- real blocker aliases are written in normal human/game wording
"""

from __future__ import annotations

import importlib
import json
import os
from copy import deepcopy
from typing import Any


def _plain(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump())
    if hasattr(value, "dict"):
        return _plain(value.dict())
    if hasattr(value, "__dict__"):
        return _plain(vars(value))
    return str(value)


def _text(value: Any) -> str:
    return json.dumps(_plain(value), default=str, sort_keys=True).lower()


def _find_key(value: Any, wanted: set[str]) -> Any:
    value = _plain(value)

    if isinstance(value, dict):
        for k, v in value.items():
            if str(k).lower() in wanted:
                return v
        for v in value.values():
            found = _find_key(v, wanted)
            if found is not None:
                return found

    if isinstance(value, list):
        for item in value:
            found = _find_key(item, wanted)
            if found is not None:
                return found

    return None


def _num(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _optimizer():
    entry = os.environ.get("SURVIVOR_OPTIMIZER_ENTRY", "optimizer.main:optimize")
    module_name, function_name = entry.split(":", 1)
    module = importlib.import_module(module_name)
    return getattr(module, function_name)


def _run(profile: dict[str, Any]) -> dict[str, Any]:
    fn = _optimizer()
    try:
        return fn(
            profile,
            include_global_plan=True,
            planner_options={
                "chain_depth": 2,
                "beam_size": 30,
                "max_actions_per_profile": 250,
            },
        )
    except TypeError:
        return fn(profile)


def _damage(result: dict[str, Any]) -> float:
    return _num(
        _find_key(
            result,
            {"total_damage", "damage_total", "final_damage", "expected_damage", "total_dps", "dps"},
        )
    )


def _multiplier(result: dict[str, Any]) -> float:
    return _num(
        _find_key(
            result,
            {"final_damage_multiplier", "final_multiplier", "total_multiplier", "damage_multiplier", "multiplier"},
        )
    )


def _base_profile() -> dict[str, Any]:
    return {
        "profile_name": "Real_Gameplay_Base",
        "base_damage": 185000,
        "player_stage": {
            "chapter": 126,
            "steamroll_unlocked": True,
            "progression_stage": "SS progression",
            "focus": "damage_only",
            "ignore_hp_unless_direct_damage": True,
        },

        # Current equipped/current active systems.
        "gear": {
            "weapon": {
                "name": "SS Starforged Havoc",
                "slot": "weapon",
                "equipped": True,
                "rarity": "SS",
                "astral_forge": 1,
                "damage_multiplier": 2.35,
            },
            "belt": {
                "name": "SS Belt",
                "slot": "belt",
                "equipped": True,
                "rarity": "SS",
                "astral_forge": 0,
                "damage_multiplier": 1.75,
            },
            "necklace": {
                "name": "S Necklace",
                "slot": "necklace",
                "equipped": True,
                "rarity": "legendary",
                "damage_multiplier": 1.45,
            },
        },

        "survivor": {
            "active": {
                "name": "Yang",
                "selected": True,
                "rarity": "S",
                "level": 100,
                "stars": 5,
                "awakening": 0,
                "damage_multiplier": 2.10,
            },
            "near_milestone": {
                "milestone": "S Survivor Awakening 1",
                "missing": {
                    "awakening_core": 1,
                    "s_survivor_shards": 4,
                },
            },
        },

        "tech": {
            "drone": {
                "equipped": True,
                "rarity": "legendary",
                "resonance": 6000,
                "damage_multiplier": 2.25,
            },
            "lightning": {
                "equipped": True,
                "rarity": "legendary",
                "resonance": 3000,
                "damage_multiplier": 1.55,
            },
            "twinborn": {
                "unlocked": True,
                "active_pair": "Drone/Forcefield",
                "active_mode": {
                    "name": "Drone mode",
                    "active": True,
                    "damage_multiplier": 1.40,
                },
            },
        },

        "pet": {
            "main": {
                "name": "Crab",
                "active": True,
                "level": 100,
                "stars": 5,
                "awakening": 1,
                "damage_multiplier": 1.85,
            },
            "assist": [
                {"name": "Eagle", "equipped": True, "damage_multiplier": 1.18},
                {"name": "Croaky", "equipped": True, "damage_multiplier": 1.12},
            ],
        },

        "collectibles": {
            "owned_bonus": {
                "unlocked": True,
                "damage_multiplier": 2.20,
            },
        },

        "inventory": {
            "relic_cores": 0,
            "needed_relic_cores_for_next_ss_af": 1,
            "awakening_cores": 0,
            "needed_awakening_cores_for_next_survivor_awakening": 1,
            "s_survivor_shards": 46,
            "needed_s_survivor_shards_for_next_survivor_awakening": 50,

            "normal_salvage_cubes": 0,
            "basic_gear_fodder": 0,
            "purple_merge_items": 0,
            "yellow_merge_items": 0,
            "common_materials": 0,
        },

        "stats": {
            "hp": 2800000,
            "defense": 85000,
        },
    }


def test_real_gameplay_trap_unequipped_inventory_gear_does_not_count():
    clean = _base_profile()
    trapped = deepcopy(clean)

    # Realistic: player owns a strong item but it is not equipped.
    trapped["gear"]["owned_not_equipped"] = {
        "weapon_copy": {
            "name": "Void Power",
            "slot": "weapon",
            "equipped": False,
            "rarity": "legendary",
            "damage_multiplier": 1.80,
        },
        "necklace_copy": {
            "name": "Extra S Necklace",
            "slot": "necklace",
            "equipped": False,
            "rarity": "legendary",
            "damage_multiplier": 1.60,
        },
    }

    assert _damage(_run(clean)) == _damage(_run(trapped)), (
        "REAL GAMEPLAY VULNERABILITY: unequipped inventory gear counted as active damage."
    )


def test_real_gameplay_trap_locked_af_preview_does_not_count():
    clean = _base_profile()
    trapped = deepcopy(clean)

    # Realistic: next forge bonus is visible in UI but not unlocked yet.
    trapped["gear"]["weapon"]["next_astral_forge_preview"] = {
        "astral_forge": 2,
        "unlocked": False,
        "missing": {"relic_core": 1},
        "damage_multiplier": 1.25,
    }

    assert _damage(_run(clean)) == _damage(_run(trapped)), (
        "REAL GAMEPLAY VULNERABILITY: locked next Astral Forge preview counted as current damage."
    )


def test_real_gameplay_trap_unselected_survivor_roster_does_not_stack():
    clean = _base_profile()
    trapped = deepcopy(clean)

    # Realistic: account owns more survivors, but only one active survivor should count.
    trapped["survivor"]["roster"] = [
        {
            "name": "Melinda",
            "selected": False,
            "level": 80,
            "stars": 4,
            "damage_multiplier": 1.95,
        },
        {
            "name": "King",
            "selected": False,
            "level": 80,
            "stars": 4,
            "damage_multiplier": 1.55,
        },
    ]

    assert _damage(_run(clean)) == _damage(_run(trapped)), (
        "REAL GAMEPLAY VULNERABILITY: inactive survivor roster bonuses stacked into current damage."
    )


def test_real_gameplay_trap_twinborn_inactive_mode_does_not_stack():
    clean = _base_profile()
    trapped = deepcopy(clean)

    # Realistic: Twinborn pair may have multiple modes, but only active mode counts.
    trapped["tech"]["twinborn"]["inactive_mode"] = {
        "name": "Forcefield mode",
        "active": False,
        "damage_multiplier": 1.30,
    }

    assert _damage(_run(clean)) == _damage(_run(trapped)), (
        "REAL GAMEPLAY VULNERABILITY: inactive Twinborn mode stacked with active mode."
    )


def test_real_gameplay_trap_resonance_candidate_assists_do_not_count():
    clean = _base_profile()
    trapped = deepcopy(clean)

    # Realistic: possible resonance assists exist in inventory but are not slotted.
    trapped["tech"]["drone"]["candidate_resonance_assists"] = [
        {
            "name": "RPG assist tech",
            "slotted": False,
            "damage_multiplier": 1.22,
        },
        {
            "name": "Molotov assist tech",
            "slotted": False,
            "damage_multiplier": 1.18,
        },
    ]

    assert _damage(_run(clean)) == _damage(_run(trapped)), (
        "REAL GAMEPLAY VULNERABILITY: unslotted resonance assist candidates counted as active."
    )


def test_real_gameplay_trap_pet_assist_inventory_does_not_count():
    clean = _base_profile()
    trapped = deepcopy(clean)

    # Realistic: extra pets exist, but are not active main/assist pets.
    trapped["pet"]["owned_not_equipped"] = [
        {
            "name": "Murica",
            "equipped": False,
            "damage_multiplier": 1.45,
        },
        {
            "name": "Shelly",
            "equipped": False,
            "damage_multiplier": 1.20,
        },
    ]

    assert _damage(_run(clean)) == _damage(_run(trapped)), (
        "REAL GAMEPLAY VULNERABILITY: unequipped pets counted as active damage."
    )


def test_real_gameplay_trap_locked_collectible_breakpoint_does_not_count():
    clean = _base_profile()
    trapped = deepcopy(clean)

    # Realistic: player can see the next collectible breakpoint but has not reached it.
    trapped["collectibles"]["next_breakpoint_preview"] = {
        "unlocked": False,
        "missing_shards": 12,
        "damage_multiplier": 1.18,
    }

    assert _damage(_run(clean)) == _damage(_run(trapped)), (
        "REAL GAMEPLAY VULNERABILITY: locked collectible breakpoint counted as current damage."
    )


def test_real_gameplay_trap_event_shop_value_does_not_hide_milestone_blocker():
    profile = _base_profile()

    # Realistic event shop: cheap bulk items look high quantity, but rare item is the real gate.
    profile["event_shop_options"] = [
        {
            "name": "Normal Salvage Cube Bundle",
            "quantity": 50,
            "cost": 1200,
            "material_type": "cheap",
        },
        {
            "name": "Relic Core",
            "quantity": 1,
            "cost": 6000,
            "material_type": "rare_gate",
        },
        {
            "name": "Awakening Core",
            "quantity": 1,
            "cost": 6000,
            "material_type": "rare_gate",
        },
    ]

    result = _run(profile)
    text = _text(result)

    assert "relic" in text and "core" in text, (
        "REAL GAMEPLAY VULNERABILITY: event shop cheap bundle hid relic core milestone blocker."
    )
    assert "awakening" in text and "core" in text, (
        "REAL GAMEPLAY VULNERABILITY: event shop cheap bundle hid awakening core milestone blocker."
    )


def test_real_gameplay_trap_real_alias_material_names_are_detected():
    profile = _base_profile()
    inv = profile["inventory"]

    # Realistic human/game-style names instead of perfect canonical keys.
    inv.pop("relic_cores")
    inv.pop("needed_relic_cores_for_next_ss_af")
    inv.pop("awakening_cores")
    inv.pop("needed_awakening_cores_for_next_survivor_awakening")
    inv.pop("s_survivor_shards")
    inv.pop("needed_s_survivor_shards_for_next_survivor_awakening")

    inv.update(
        {
            "Relic Core": 0,
            "Relic Core needed for SS AF": 1,
            "S Awakening Core": 0,
            "S Awakening Core needed": 1,
            "Yang shard": 46,
            "Yang shard needed": 50,
        }
    )

    result = _run(profile)
    text = _text(result)

    assert "relic" in text and "core" in text, (
        "REAL GAMEPLAY VULNERABILITY: real name 'Relic Core' was not detected."
    )
    assert "awakening" in text and "core" in text, (
        "REAL GAMEPLAY VULNERABILITY: real name 'S Awakening Core' was not detected."
    )
    assert "shard" in text or "yang" in text, (
        "REAL GAMEPLAY VULNERABILITY: real survivor shard alias was not detected."
    )


def test_real_gameplay_trap_milestone_requires_both_core_and_shards():
    profile = _base_profile()

    # Realistic: player is almost there on shards but still has no awakening core.
    profile["inventory"]["s_survivor_shards"] = 49
    profile["inventory"]["needed_s_survivor_shards_for_next_survivor_awakening"] = 50
    profile["inventory"]["awakening_cores"] = 0
    profile["inventory"]["needed_awakening_cores_for_next_survivor_awakening"] = 1

    result = _run(profile)
    text = _text(result)

    assert "awakening" in text or "awaken" in text, (
        "REAL GAMEPLAY VULNERABILITY: near awakening milestone was ignored."
    )
    assert "core" in text, (
        "REAL GAMEPLAY VULNERABILITY: awakening core was not shown as mandatory."
    )
    assert "shard" in text, (
        "REAL GAMEPLAY VULNERABILITY: shard gap was not shown."
    )


def test_real_gameplay_trap_percent_and_x_input_from_source_pack_is_parsed():
    numeric = _base_profile()

    formatted = _base_profile()
    formatted["gear"]["weapon"]["damage_multiplier"] = "2.35x"
    formatted["gear"]["belt"]["damage_multiplier"] = "1.75x"
    formatted["gear"]["necklace"]["damage_multiplier"] = "1.45x"
    formatted["survivor"]["active"]["damage_multiplier"] = "2.10x"
    formatted["tech"]["drone"]["damage_multiplier"] = "2.25x"
    formatted["tech"]["lightning"]["damage_multiplier"] = "1.55x"
    formatted["tech"]["twinborn"]["active_mode"]["damage_multiplier"] = "1.40x"
    formatted["pet"]["main"]["damage_multiplier"] = "1.85x"
    formatted["pet"]["assist"][0]["damage_multiplier"] = "1.18x"
    formatted["pet"]["assist"][1]["damage_multiplier"] = "1.12x"
    formatted["collectibles"]["owned_bonus"]["damage_multiplier"] = "2.20x"

    assert _damage(_run(formatted)) == _damage(_run(numeric)), (
        "REAL GAMEPLAY VULNERABILITY: realistic source-pack multiplier strings like '2.35x' are not parsed."
    )


def test_real_gameplay_trap_same_profile_same_result():
    profile = _base_profile()

    result_1 = _run(deepcopy(profile))
    result_2 = _run(deepcopy(profile))

    assert _damage(result_1) == _damage(result_2), (
        "REAL GAMEPLAY VULNERABILITY: same profile gave different total damage."
    )
    assert _multiplier(result_1) == _multiplier(result_2), (
        "REAL GAMEPLAY VULNERABILITY: same profile gave different final multiplier."
    )
