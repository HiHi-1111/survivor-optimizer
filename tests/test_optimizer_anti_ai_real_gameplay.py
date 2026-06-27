"""
ANTI-AI real gameplay red-team tests.

Goal:
Make the optimizer fight its own bad assumptions.

This suite avoids fake/impossible values. It uses realistic Survivor.io traps:
- owned but not equipped
- visible but locked preview
- unselected roster character
- inactive Twinborn mode
- unslotted resonance assist
- unequipped pet inventory
- locked collectible breakpoint
- source/database/catalog rows mixed into the profile
- real game-style material aliases
- x/percent multiplier strings

False-positive protection:
- Inactive/locked/candidate data must NOT change current damage.
- Existing active/equipped fields MUST still change damage when upgraded.
So the optimizer cannot pass by ignoring everything.
"""

from __future__ import annotations

import importlib
import json
import os
from copy import deepcopy
from typing import Any, Callable

import pytest


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
        "profile_name": "Anti_AI_Real_Gameplay_Base",
        "base_damage": 185000,
        "player_stage": {
            "chapter": 126,
            "steamroll_unlocked": True,
            "progression_stage": "SS progression",
            "focus": "damage_only",
            "ignore_hp_unless_direct_damage": True,
        },
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


def _same_damage_after_trap(label: str, trap: Callable[[dict[str, Any]], None]) -> None:
    clean = _base_profile()
    trapped = deepcopy(clean)
    trap(trapped)

    clean_result = _run(clean)
    trapped_result = _run(trapped)

    clean_damage = _damage(clean_result)
    trapped_damage = _damage(trapped_result)

    assert clean_damage == trapped_damage, (
        f"ANTI-AI VULNERABILITY [{label}]: inactive/locked/unowned data changed current damage. "
        f"clean={clean_damage}, trapped={trapped_damage}"
    )


def _damage_increases_after_real_active_upgrade(label: str, upgrade: Callable[[dict[str, Any]], None]) -> None:
    clean = _base_profile()
    upgraded = deepcopy(clean)
    upgrade(upgraded)

    clean_result = _run(clean)
    upgraded_result = _run(upgraded)

    clean_damage = _damage(clean_result)
    upgraded_damage = _damage(upgraded_result)

    assert upgraded_damage > clean_damage, (
        f"ANTI-AI FALSE-POSITIVE GUARD [{label}]: real active/equipped upgrade did not increase damage. "
        f"clean={clean_damage}, upgraded={upgraded_damage}"
    )


ANTI_AI_TRAPS: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
    (
        "unequipped_owned_gear_inventory",
        lambda p: p["gear"].setdefault("owned_not_equipped", {}).update(
            {
                "void_power_copy": {
                    "name": "Void Power",
                    "slot": "weapon",
                    "equipped": False,
                    "owned": True,
                    "rarity": "legendary",
                    "damage_multiplier": 1.80,
                },
                "extra_s_necklace": {
                    "name": "Extra S Necklace",
                    "slot": "necklace",
                    "equipped": False,
                    "owned": True,
                    "rarity": "legendary",
                    "damage_multiplier": 1.60,
                },
            }
        ),
    ),
    (
        "locked_af_preview_on_equipped_weapon",
        lambda p: p["gear"]["weapon"].update(
            {
                "next_astral_forge_preview": {
                    "astral_forge": 2,
                    "unlocked": False,
                    "preview": True,
                    "missing": {"relic_core": 1},
                    "damage_multiplier": 1.25,
                }
            }
        ),
    ),
    (
        "future_ss_cosmic_cast_preview",
        lambda p: p["gear"]["weapon"].update(
            {
                "cosmic_cast_preview": {
                    "unlocked": False,
                    "missing": {"s_core": 1, "relic_core": 1},
                    "damage_multiplier": 1.22,
                }
            }
        ),
    ),
    (
        "unselected_survivor_roster",
        lambda p: p["survivor"].update(
            {
                "roster": [
                    {
                        "name": "Melinda",
                        "selected": False,
                        "owned": True,
                        "level": 80,
                        "stars": 4,
                        "damage_multiplier": 1.95,
                    },
                    {
                        "name": "King",
                        "selected": False,
                        "owned": True,
                        "level": 80,
                        "stars": 4,
                        "damage_multiplier": 1.55,
                    },
                ]
            }
        ),
    ),
    (
        "inactive_twinborn_mode_same_pair",
        lambda p: p["tech"]["twinborn"].update(
            {
                "inactive_mode": {
                    "name": "Forcefield mode",
                    "active": False,
                    "same_pair_as_active": True,
                    "damage_multiplier": 1.30,
                }
            }
        ),
    ),
    (
        "unslotted_resonance_assist_candidates",
        lambda p: p["tech"]["drone"].update(
            {
                "candidate_resonance_assists": [
                    {
                        "name": "RPG assist tech",
                        "owned": True,
                        "slotted": False,
                        "damage_multiplier": 1.22,
                    },
                    {
                        "name": "Molotov assist tech",
                        "owned": True,
                        "slotted": False,
                        "damage_multiplier": 1.18,
                    },
                ]
            }
        ),
    ),
    (
        "unequipped_pet_inventory",
        lambda p: p["pet"].update(
            {
                "owned_not_equipped": [
                    {
                        "name": "Murica",
                        "owned": True,
                        "equipped": False,
                        "active": False,
                        "damage_multiplier": 1.45,
                    },
                    {
                        "name": "Shelly",
                        "owned": True,
                        "equipped": False,
                        "active": False,
                        "damage_multiplier": 1.20,
                    },
                ]
            }
        ),
    ),
    (
        "locked_collectible_next_breakpoint",
        lambda p: p["collectibles"].update(
            {
                "next_breakpoint_preview": {
                    "unlocked": False,
                    "preview": True,
                    "missing_shards": 12,
                    "damage_multiplier": 1.18,
                }
            }
        ),
    ),
    (
        "source_database_catalog_rows_not_player_state",
        lambda p: p.update(
            {
                "source_database_catalog_rows": [
                    {
                        "system": "tech",
                        "name": "Twinborn Drone/Forcefield",
                        "record_type": "catalog_reference",
                        "owned": False,
                        "active": False,
                        "damage_multiplier": 1.40,
                    },
                    {
                        "system": "collectibles",
                        "name": "Collectible breakpoint from guide",
                        "record_type": "source_pack_reference",
                        "unlocked": False,
                        "damage_multiplier": 1.20,
                    },
                    {
                        "system": "pet",
                        "name": "Possible pet assist from guide",
                        "record_type": "recommendation_candidate",
                        "equipped": False,
                        "damage_multiplier": 1.15,
                    },
                ]
            }
        ),
    ),
    (
        "event_shop_options_not_owned_until_bought",
        lambda p: p.update(
            {
                "event_shop_options": [
                    {
                        "name": "Relic Core",
                        "available": True,
                        "owned_after_purchase": False,
                        "cost": 6000,
                        "damage_multiplier_if_used_later": 1.25,
                    },
                    {
                        "name": "Awakening Core",
                        "available": True,
                        "owned_after_purchase": False,
                        "cost": 6000,
                        "damage_multiplier_if_used_later": 1.30,
                    },
                ]
            }
        ),
    ),
]


@pytest.mark.parametrize("label,trap", ANTI_AI_TRAPS)
def test_anti_ai_real_inactive_locked_or_catalog_data_does_not_count(label, trap):
    _same_damage_after_trap(label, trap)


ACTIVE_UPGRADES: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
    (
        "equipped_weapon_multiplier_upgrade",
        lambda p: p["gear"]["weapon"].update({"damage_multiplier": 2.60}),
    ),
    (
        "active_survivor_multiplier_upgrade",
        lambda p: p["survivor"]["active"].update({"damage_multiplier": 2.30}),
    ),
    (
        "equipped_drone_resonance_upgrade",
        lambda p: p["tech"]["drone"].update({"damage_multiplier": 2.50}),
    ),
    (
        "active_pet_upgrade",
        lambda p: p["pet"]["main"].update({"damage_multiplier": 2.05}),
    ),
    (
        "unlocked_collectible_owned_bonus_upgrade",
        lambda p: p["collectibles"]["owned_bonus"].update({"damage_multiplier": 2.40}),
    ),
]


@pytest.mark.parametrize("label,upgrade", ACTIVE_UPGRADES)
def test_anti_ai_false_positive_guard_real_active_upgrades_still_count(label, upgrade):
    _damage_increases_after_real_active_upgrade(label, upgrade)


def test_anti_ai_real_material_aliases_are_normalized_without_canonical_keys():
    profile = _base_profile()
    inv = profile["inventory"]

    # Remove perfect code names.
    inv.pop("relic_cores")
    inv.pop("needed_relic_cores_for_next_ss_af")
    inv.pop("awakening_cores")
    inv.pop("needed_awakening_cores_for_next_survivor_awakening")
    inv.pop("s_survivor_shards")
    inv.pop("needed_s_survivor_shards_for_next_survivor_awakening")

    # Real player/source wording.
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
        "ANTI-AI VULNERABILITY: real material alias 'Relic Core' was not detected."
    )
    assert "awakening" in text and "core" in text, (
        "ANTI-AI VULNERABILITY: real material alias 'S Awakening Core' was not detected."
    )
    assert "shard" in text or "yang" in text, (
        "ANTI-AI VULNERABILITY: real shard alias 'Yang shard' was not detected."
    )


def test_anti_ai_multiplier_strings_from_source_pack_parse_same_as_numbers():
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

    numeric_result = _run(numeric)
    formatted_result = _run(formatted)

    assert _damage(formatted_result) == _damage(numeric_result), (
        "ANTI-AI VULNERABILITY: source-pack multiplier strings like '2.35x' do not parse like numeric values."
    )
    assert _multiplier(formatted_result) == _multiplier(numeric_result), (
        "ANTI-AI VULNERABILITY: source-pack multiplier strings changed final multiplier."
    )


def test_anti_ai_same_profile_same_result_even_after_many_runs():
    profile = _base_profile()

    damages = []
    multipliers = []
    for _ in range(5):
        result = _run(deepcopy(profile))
        damages.append(_damage(result))
        multipliers.append(_multiplier(result))

    assert len(set(damages)) == 1, (
        f"ANTI-AI VULNERABILITY: same profile gave different damages over repeated runs: {damages}"
    )
    assert len(set(multipliers)) == 1, (
        f"ANTI-AI VULNERABILITY: same profile gave different multipliers over repeated runs: {multipliers}"
    )


def test_anti_ai_public_output_explains_real_current_vs_future_or_locked():
    profile = _base_profile()
    profile["gear"]["weapon"]["next_astral_forge_preview"] = {
        "astral_forge": 2,
        "unlocked": False,
        "preview": True,
        "missing": {"relic_core": 1},
        "damage_multiplier": 1.25,
    }
    profile["tech"]["drone"]["candidate_resonance_assists"] = [
        {
            "name": "RPG assist tech",
            "owned": True,
            "slotted": False,
            "damage_multiplier": 1.22,
        }
    ]

    result = _run(profile)
    text = _text(result)

    # This is not asking for exact wording.
    # It just requires the optimizer to expose some evidence that it understands current vs future/locked state.
    evidence_terms = ["locked", "preview", "future", "ignored", "not_counted", "inactive", "unslotted", "candidate"]

    assert any(term in text for term in evidence_terms), (
        "ANTI-AI VULNERABILITY: output does not explain that locked/future/unslotted data was ignored or treated separately."
    )
