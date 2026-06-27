"""
Red-team tests for the Survivor.io optimizer.

Purpose:
These tests are intentionally mean. They try to trick the optimizer into:
- overvaluing cheap/common bait materials
- missing hidden damage multipliers
- trusting visible gear too much
- ignoring near milestones
- using fake score language instead of total damage / multipliers
- letting HP/survival bait affect damage
- failing on messy or weird profiles

These are not normal happy-path tests. They are cat-and-mouse tests.
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


def _base_profile() -> dict[str, Any]:
    return {
        "profile_name": "RedTeam_Base_SS_Profile",
        "base_damage": 1000,
        "player_stage": {
            "chapter": 126,
            "steamroll_unlocked": True,
            "progression_stage": "ss_progression",
            "focus": "damage_only",
            "ignore_hp_unless_direct_damage": True,
        },
        "gear": {
            "weapon": {
                "name": "SS Starforged Havoc",
                "rarity": "SS",
                "astral_forge": 1,
                "damage_multiplier": 5.0,
            },
            "belt": {
                "name": "SS Belt",
                "rarity": "SS",
                "astral_forge": 0,
                "damage_multiplier": 2.0,
            },
        },
        "survivor": {
            "name": "S Survivor",
            "rarity": "S",
            "level": 100,
            "stars": 5,
            "awakening": 0,
            "damage_multiplier": 3.0,
            "near_milestone": {
                "milestone": "S Survivor Awakening 1",
                "missing": {
                    "awakening_core": 1,
                    "s_survivor_shards": 1,
                },
            },
        },
        "tech": {
            "drone": {
                "rarity": "legendary",
                "resonance": 6000,
                "damage_multiplier": 4.0,
            },
            "twinborn": {
                "unlocked": True,
                "active_pair": "Drone/Forcefield",
                "damage_multiplier": 2.0,
            },
        },
        "pet": {
            "main": {
                "name": "Crab",
                "level": 100,
                "stars": 5,
                "awakening": 1,
                "damage_multiplier": 2.5,
            },
            "assist": [
                {"name": "Eagle", "damage_multiplier": 1.35},
                {"name": "Croaky", "damage_multiplier": 1.25},
            ],
        },
        "collectibles": {
            "damage_multiplier": 3.0,
            "important_breakpoints_active": True,
        },
        "inventory": {
            # Real blockers.
            "relic_cores": 0,
            "needed_relic_cores_for_next_ss_af": 1,
            "awakening_cores": 0,
            "needed_awakening_cores_for_next_survivor_awakening": 1,
            "s_survivor_shards": 49,
            "needed_s_survivor_shards_for_next_survivor_awakening": 50,

            # Known bait blockers.
            "normal_salvage_cubes": 0,
            "basic_gear_fodder": 0,
            "purple_merge_items": 0,
            "yellow_merge_items": 0,
            "common_materials": 0,
        },
        "stats": {
            "hp": 999999999,
            "defense": 999999999,
        },
    }


def _damage_fields(result: dict[str, Any]) -> tuple[float, float, Any]:
    total_damage = _find_key(
        result,
        {"total_damage", "damage_total", "final_damage", "expected_damage", "total_dps", "dps"},
    )
    final_multiplier = _find_key(
        result,
        {"final_damage_multiplier", "final_multiplier", "total_multiplier", "damage_multiplier", "multiplier"},
    )
    breakdown = _find_key(
        result,
        {"multiplier_breakdown", "damage_breakdown", "multipliers", "breakdown", "system_multipliers"},
    )
    return _num(total_damage), _num(final_multiplier), breakdown


def test_red_team_output_has_real_damage_math_not_fake_score():
    result = _run(_base_profile())
    text = _text(result)
    total_damage, final_multiplier, breakdown = _damage_fields(result)

    assert total_damage > 0, "VULNERABILITY: optimizer did not return real total damage."
    assert final_multiplier > 0, "VULNERABILITY: optimizer did not return final damage multiplier."
    assert breakdown is not None, "VULNERABILITY: optimizer did not return multiplier breakdown."

    forbidden = [
        "score_out_of_1000",
        "damage rating",
        "fake score",
        "742/1000",
        "/1000",
    ]
    for term in forbidden:
        assert term not in text, f"VULNERABILITY: fake score/rating language leaked into output: {term}"


def test_red_team_hidden_multipliers_can_beat_flashy_gear():
    hidden_strong = _base_profile()
    hidden_strong["profile_name"] = "Hidden_Multipliers_Strong"
    hidden_strong["gear"]["weapon"]["damage_multiplier"] = 1.2
    hidden_strong["gear"]["belt"]["damage_multiplier"] = 1.1
    hidden_strong["survivor"]["damage_multiplier"] = 12.0
    hidden_strong["tech"]["drone"]["damage_multiplier"] = 12.0
    hidden_strong["tech"]["twinborn"]["damage_multiplier"] = 5.0
    hidden_strong["pet"]["main"]["damage_multiplier"] = 8.0
    hidden_strong["collectibles"]["damage_multiplier"] = 15.0

    flashy_gear_weak_hidden = _base_profile()
    flashy_gear_weak_hidden["profile_name"] = "Flashy_Gear_Weak_Hidden"
    flashy_gear_weak_hidden["gear"]["weapon"]["damage_multiplier"] = 10.0
    flashy_gear_weak_hidden["gear"]["belt"]["damage_multiplier"] = 5.0
    flashy_gear_weak_hidden["survivor"]["damage_multiplier"] = 1.0
    flashy_gear_weak_hidden["tech"]["drone"]["damage_multiplier"] = 1.0
    flashy_gear_weak_hidden["tech"]["twinborn"]["damage_multiplier"] = 1.0
    flashy_gear_weak_hidden["pet"]["main"]["damage_multiplier"] = 1.0
    flashy_gear_weak_hidden["collectibles"]["damage_multiplier"] = 1.0

    hidden_result = _run(hidden_strong)
    flashy_result = _run(flashy_gear_weak_hidden)

    hidden_damage, hidden_mult, _ = _damage_fields(hidden_result)
    flashy_damage, flashy_mult, _ = _damage_fields(flashy_result)

    assert hidden_damage > flashy_damage, (
        "VULNERABILITY: optimizer undervalued hidden multipliers. "
        "Strong survivor/tech/pet/collectibles should beat flashy gear-only damage."
    )
    assert hidden_mult > flashy_mult, (
        "VULNERABILITY: final multiplier does not reflect hidden systems strongly enough."
    )


def test_red_team_hp_and_defense_bait_do_not_increase_damage():
    high_hp_bad_damage = _base_profile()
    high_hp_bad_damage["profile_name"] = "HP_Bait_Bad_Damage"
    high_hp_bad_damage["base_damage"] = 1000
    high_hp_bad_damage["stats"] = {
        "hp": 999999999999,
        "defense": 999999999999,
        "damage_reduction": 999999,
    }
    high_hp_bad_damage["gear"]["weapon"]["damage_multiplier"] = 1.0
    high_hp_bad_damage["survivor"]["damage_multiplier"] = 1.0
    high_hp_bad_damage["tech"]["drone"]["damage_multiplier"] = 1.0
    high_hp_bad_damage["pet"]["main"]["damage_multiplier"] = 1.0
    high_hp_bad_damage["collectibles"]["damage_multiplier"] = 1.0

    low_hp_good_damage = _base_profile()
    low_hp_good_damage["profile_name"] = "Low_HP_Good_Damage"
    low_hp_good_damage["stats"] = {
        "hp": 1,
        "defense": 1,
    }
    low_hp_good_damage["gear"]["weapon"]["damage_multiplier"] = 7.0
    low_hp_good_damage["survivor"]["damage_multiplier"] = 6.0
    low_hp_good_damage["tech"]["drone"]["damage_multiplier"] = 6.0
    low_hp_good_damage["pet"]["main"]["damage_multiplier"] = 4.0
    low_hp_good_damage["collectibles"]["damage_multiplier"] = 5.0

    hp_result = _run(high_hp_bad_damage)
    dmg_result = _run(low_hp_good_damage)

    hp_damage, _, _ = _damage_fields(hp_result)
    real_damage, _, _ = _damage_fields(dmg_result)

    assert real_damage > hp_damage, (
        "VULNERABILITY: HP/defense bait increased damage value. "
        "Damage optimizer should not reward survival-only stats."
    )


def test_red_team_known_cheap_bait_stays_below_real_blockers():
    result = _run(_base_profile())
    text = _text(result)

    assert "relic" in text and "core" in text, (
        "VULNERABILITY: relic core is missing from blocker/milestone output."
    )
    assert "awakening" in text and "core" in text, (
        "VULNERABILITY: awakening core is missing from blocker/milestone output."
    )
    assert "shard" in text, (
        "VULNERABILITY: S survivor shard shortage is missing from milestone output."
    )

    bad_phrases = [
        "main blocker is normal salvage",
        "main blocker: normal salvage",
        "primary blocker is normal salvage",
        "primary blocker: normal salvage",
        "top blocker is normal salvage",
        "top blocker: normal salvage",
        "main blocker is basic gear fodder",
        "primary blocker is basic gear fodder",
    ]

    for phrase in bad_phrases:
        assert phrase not in text, (
            "VULNERABILITY: cheap low-tier bait became the main blocker. "
            f"Bad phrase: {phrase}"
        )


def test_red_team_alias_cheap_materials_do_not_trick_blocker_logic():
    profile = _base_profile()
    profile["profile_name"] = "Alias_Cheap_Material_Bait"

    # Same idea as normal salvage/fodder, but under tricky names.
    # A smarter optimizer should classify these as cheap/common bait too.
    profile["inventory"].update(
        {
            "salvage_cube_normal": 0,
            "normal_cube": 0,
            "regular_salvage": 0,
            "fodder_basic": 0,
            "gear_food": 0,
            "purple_trash_merge": 0,
            "yellow_trash_merge": 0,
        }
    )

    result = _run(profile)
    text = _text(result)

    assert "relic" in text and "core" in text, (
        "VULNERABILITY: alias bait confused the optimizer and hid relic core priority."
    )
    assert "awakening" in text and "core" in text, (
        "VULNERABILITY: alias bait confused the optimizer and hid awakening core priority."
    )

    bad_alias_phrases = [
        "main blocker is salvage cube normal",
        "primary blocker is salvage cube normal",
        "top blocker is salvage cube normal",
        "main blocker is normal cube",
        "primary blocker is normal cube",
        "main blocker is gear food",
        "primary blocker is gear food",
        "main blocker is fodder basic",
        "primary blocker is fodder basic",
    ]

    for phrase in bad_alias_phrases:
        assert phrase not in text, (
            "VULNERABILITY: cheap alias material became main blocker. "
            f"Bad phrase: {phrase}"
        )


def test_red_team_near_milestone_is_not_ignored_when_one_item_away():
    profile = _base_profile()
    profile["profile_name"] = "One_Item_From_Awakening"
    profile["inventory"]["s_survivor_shards"] = 49
    profile["inventory"]["needed_s_survivor_shards_for_next_survivor_awakening"] = 50
    profile["inventory"]["awakening_cores"] = 0
    profile["inventory"]["needed_awakening_cores_for_next_survivor_awakening"] = 1

    result = _run(profile)
    text = _text(result)

    assert "awakening" in text, (
        "VULNERABILITY: optimizer ignored a near survivor awakening milestone."
    )
    assert "core" in text, (
        "VULNERABILITY: optimizer did not identify awakening core as mandatory."
    )
    assert "shard" in text, (
        "VULNERABILITY: optimizer did not identify final shard gap for near milestone."
    )


def test_red_team_zero_negative_and_missing_multipliers_do_not_crash_or_inflate():
    profile = _base_profile()
    profile["profile_name"] = "Broken_Multiplier_Profile"

    profile["gear"]["weapon"]["damage_multiplier"] = 0
    profile["gear"]["belt"]["damage_multiplier"] = -99
    profile["survivor"]["damage_multiplier"] = None
    profile["tech"]["drone"]["damage_multiplier"] = "not-a-number"
    profile["pet"]["main"]["damage_multiplier"] = 3.0
    profile["collectibles"]["damage_multiplier"] = 2.0

    result = _run(profile)
    total_damage, final_multiplier, breakdown = _damage_fields(result)

    assert total_damage > 0, (
        "VULNERABILITY: broken multipliers made total damage zero or invalid."
    )
    assert final_multiplier > 0, (
        "VULNERABILITY: broken multipliers made final multiplier zero or invalid."
    )
    assert breakdown is not None, (
        "VULNERABILITY: broken multipliers removed multiplier breakdown."
    )


def test_red_team_deterministic_same_profile_same_damage():
    profile = _base_profile()
    profile["profile_name"] = "Determinism_Check"

    result_1 = _run(deepcopy(profile))
    result_2 = _run(deepcopy(profile))

    damage_1, mult_1, _ = _damage_fields(result_1)
    damage_2, mult_2, _ = _damage_fields(result_2)

    assert damage_1 == damage_2, (
        "VULNERABILITY: same profile returned different total damage."
    )
    assert mult_1 == mult_2, (
        "VULNERABILITY: same profile returned different final multiplier."
    )


def test_red_team_public_result_contains_capability_evidence():
    result = _run(_base_profile())
    text = _text(result)

    required_evidence = [
        "total_damage",
        "final_damage_multiplier",
        "multiplier_breakdown",
        "blocker",
    ]

    for term in required_evidence:
        assert term in text, (
            f"VULNERABILITY: public optimizer result does not expose capability evidence: {term}"
        )
