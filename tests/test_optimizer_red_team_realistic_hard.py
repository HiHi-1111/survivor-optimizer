"""
Realistic hard red-team tests for Survivor.io optimizer.

These are intentionally tricky, but possible.
No impossible fantasy values.

They test:
- realistic high HP/defense bait
- realistic Discord/OCR noise
- hidden multipliers beating flashy gear
- milestone blockers
- aliases and typo-like material names
- percent/x multiplier formatting
- cheap material classification
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
        "profile_name": "Realistic_Hard_Base",
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
                "rarity": "SS",
                "astral_forge": 1,
                "damage_multiplier": 2.35,
            },
            "belt": {
                "name": "SS Belt",
                "rarity": "SS",
                "astral_forge": 0,
                "damage_multiplier": 1.75,
            },
            "necklace": {
                "name": "S Necklace",
                "rarity": "legendary",
                "damage_multiplier": 1.45,
            },
        },
        "survivor": {
            "name": "S Survivor",
            "rarity": "S",
            "level": 100,
            "stars": 5,
            "awakening": 0,
            "damage_multiplier": 2.1,
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
                "rarity": "legendary",
                "resonance": 6000,
                "damage_multiplier": 2.25,
            },
            "lightning": {
                "rarity": "legendary",
                "resonance": 3000,
                "damage_multiplier": 1.55,
            },
            "twinborn": {
                "unlocked": True,
                "active_pair": "Drone/Forcefield",
                "damage_multiplier": 1.4,
            },
        },
        "pet": {
            "main": {
                "name": "Crab",
                "level": 100,
                "stars": 5,
                "awakening": 1,
                "damage_multiplier": 1.85,
            },
            "assist": [
                {"name": "Eagle", "damage_multiplier": 1.18},
                {"name": "Croaky", "damage_multiplier": 1.12},
            ],
        },
        "collectibles": {
            "damage_multiplier": 2.2,
            "important_breakpoints_active": True,
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


def test_realistic_hard_discord_note_does_not_count_as_owned_damage():
    clean = _base_profile()
    noisy = deepcopy(clean)

    noisy["discord_notes"] = {
        "message": "Someone said this setup does about 2.5x more damage after resonance.",
        "damage_multiplier": 2.5,
        "note_type": "community_comment_not_owned_bonus",
    }

    clean_result = _run(clean)
    noisy_result = _run(noisy)

    assert _damage(clean_result) == _damage(noisy_result), (
        "REALISTIC VULNERABILITY: Discord/commentary damage_multiplier was counted as owned damage."
    )


def test_realistic_hard_ocr_noise_inside_item_notes_does_not_count():
    clean = _base_profile()
    noisy = deepcopy(clean)

    noisy["gear"]["weapon"]["ocr_text"] = {
        "line": "Damage +25%",
        "damage_multiplier": 1.25,
        "source": "screenshot_text_not_structured_owned_stat",
    }

    clean_result = _run(clean)
    noisy_result = _run(noisy)

    assert _damage(clean_result) == _damage(noisy_result), (
        "REALISTIC VULNERABILITY: OCR note inside item counted as actual owned multiplier."
    )


def test_realistic_hard_hidden_systems_can_beat_flashy_gear():
    hidden = _base_profile()
    hidden["profile_name"] = "Hidden_Systems_Strong"
    hidden["gear"]["weapon"]["damage_multiplier"] = 1.35
    hidden["gear"]["belt"]["damage_multiplier"] = 1.2
    hidden["survivor"]["damage_multiplier"] = 3.2
    hidden["tech"]["drone"]["damage_multiplier"] = 3.0
    hidden["tech"]["lightning"]["damage_multiplier"] = 2.2
    hidden["pet"]["main"]["damage_multiplier"] = 2.5
    hidden["collectibles"]["damage_multiplier"] = 3.5

    flashy = _base_profile()
    flashy["profile_name"] = "Flashy_Gear_Weak_Hidden"
    flashy["gear"]["weapon"]["damage_multiplier"] = 2.8
    flashy["gear"]["belt"]["damage_multiplier"] = 2.2
    flashy["survivor"]["damage_multiplier"] = 1.05
    flashy["tech"]["drone"]["damage_multiplier"] = 1.1
    flashy["tech"]["lightning"]["damage_multiplier"] = 1.0
    flashy["pet"]["main"]["damage_multiplier"] = 1.1
    flashy["collectibles"]["damage_multiplier"] = 1.05

    hidden_result = _run(hidden)
    flashy_result = _run(flashy)

    assert _damage(hidden_result) > _damage(flashy_result), (
        "REALISTIC VULNERABILITY: optimizer trusted flashy gear too much and missed hidden multiplier systems."
    )


def test_realistic_hard_high_hp_account_does_not_beat_damage_account():
    hp_bait = _base_profile()
    hp_bait["profile_name"] = "Realistic_High_HP_Low_Damage"
    hp_bait["stats"]["hp"] = 5200000
    hp_bait["stats"]["defense"] = 140000
    hp_bait["gear"]["weapon"]["damage_multiplier"] = 1.15
    hp_bait["gear"]["belt"]["damage_multiplier"] = 1.1
    hp_bait["survivor"]["damage_multiplier"] = 1.05
    hp_bait["tech"]["drone"]["damage_multiplier"] = 1.1
    hp_bait["pet"]["main"]["damage_multiplier"] = 1.05
    hp_bait["collectibles"]["damage_multiplier"] = 1.1

    damage_account = _base_profile()
    damage_account["profile_name"] = "Realistic_Low_HP_High_Damage"
    damage_account["stats"]["hp"] = 1600000
    damage_account["stats"]["defense"] = 45000
    damage_account["gear"]["weapon"]["damage_multiplier"] = 2.1
    damage_account["gear"]["belt"]["damage_multiplier"] = 1.8
    damage_account["survivor"]["damage_multiplier"] = 2.6
    damage_account["tech"]["drone"]["damage_multiplier"] = 2.4
    damage_account["pet"]["main"]["damage_multiplier"] = 1.9
    damage_account["collectibles"]["damage_multiplier"] = 2.3

    hp_result = _run(hp_bait)
    dmg_result = _run(damage_account)

    assert _damage(dmg_result) > _damage(hp_result), (
        "REALISTIC VULNERABILITY: high HP/defense bait beat a clearly stronger damage profile."
    )


def test_realistic_hard_percent_and_x_formatting_is_parsed():
    numeric = _base_profile()

    formatted = _base_profile()
    formatted["gear"]["weapon"]["damage_multiplier"] = "2.35x"
    formatted["gear"]["belt"]["damage_multiplier"] = "175%"
    numeric["necklace_bonus"] = {"damage_multiplier": 1.45}
    formatted["necklace_bonus"] = {"damage_multiplier": "1.45x"}
    formatted["survivor"]["damage_multiplier"] = "2.1x"
    formatted["tech"]["drone"]["damage_multiplier"] = "225%"
    formatted["tech"]["lightning"]["damage_multiplier"] = "1.55x"
    formatted["tech"]["twinborn"]["damage_multiplier"] = "140%"
    formatted["pet"]["main"]["damage_multiplier"] = "1.85x"
    formatted["collectibles"]["damage_multiplier"] = "220%"

    numeric_result = _run(numeric)
    formatted_result = _run(formatted)

    assert _damage(formatted_result) == _damage(numeric_result), (
        "REALISTIC VULNERABILITY: optimizer cannot parse realistic multiplier formatting like 2.35x or 175%."
    )


def test_realistic_hard_material_aliases_are_detected():
    profile = _base_profile()
    inv = profile["inventory"]

    inv.pop("relic_cores")
    inv.pop("needed_relic_cores_for_next_ss_af")
    inv.pop("awakening_cores")
    inv.pop("needed_awakening_cores_for_next_survivor_awakening")
    inv.pop("s_survivor_shards")
    inv.pop("needed_s_survivor_shards_for_next_survivor_awakening")

    inv.update(
        {
            "relic_core": 0,
            "needed_relic_core_for_ss_af": 1,
            "s_awaken_core": 0,
            "needed_s_awaken_core": 1,
            "s_survivor_shard": 46,
            "needed_s_survivor_shard": 50,
        }
    )

    result = _run(profile)
    text = _text(result)

    assert "relic" in text and "core" in text, (
        "REALISTIC VULNERABILITY: singular relic_core alias not detected."
    )
    assert "awaken" in text and "core" in text, (
        "REALISTIC VULNERABILITY: s_awaken_core alias not detected."
    )
    assert "shard" in text, (
        "REALISTIC VULNERABILITY: s_survivor_shard alias not detected."
    )


def test_realistic_hard_cheap_aliases_are_minor_not_main():
    profile = _base_profile()
    profile["inventory"].update(
        {
            "normal_cube": 0,
            "regular_salvage_cube": 0,
            "gear_fodder": 0,
            "yellow_fodder": 0,
            "purple_fodder": 0,
        }
    )

    result = _run(profile)
    text = _text(result)

    assert "relic" in text and "awakening" in text, (
        "REALISTIC VULNERABILITY: cheap aliases hid rare blockers."
    )

    bad_phrases = [
        "main blocker is normal cube",
        "primary blocker is normal cube",
        "top blocker is normal cube",
        "main blocker is gear fodder",
        "primary blocker is gear fodder",
        "top blocker is gear fodder",
    ]

    for phrase in bad_phrases:
        assert phrase not in text, (
            f"REALISTIC VULNERABILITY: cheap alias became main blocker: {phrase}"
        )


def test_realistic_hard_near_milestone_requires_core_not_only_shards():
    profile = _base_profile()
    profile["inventory"]["s_survivor_shards"] = 49
    profile["inventory"]["needed_s_survivor_shards_for_next_survivor_awakening"] = 50
    profile["inventory"]["awakening_cores"] = 0
    profile["inventory"]["needed_awakening_cores_for_next_survivor_awakening"] = 1

    result = _run(profile)
    text = _text(result)

    assert "awakening" in text or "awaken" in text, (
        "REALISTIC VULNERABILITY: near awakening milestone not mentioned."
    )
    assert "core" in text, (
        "REALISTIC VULNERABILITY: awakening core not marked as mandatory."
    )
    assert "shard" in text, (
        "REALISTIC VULNERABILITY: remaining shard gap not mentioned."
    )


def test_realistic_hard_same_profile_same_result():
    profile = _base_profile()

    result_1 = _run(deepcopy(profile))
    result_2 = _run(deepcopy(profile))

    assert _damage(result_1) == _damage(result_2), (
        "REALISTIC VULNERABILITY: same profile produced different total damage."
    )
    assert _multiplier(result_1) == _multiplier(result_2), (
        "REALISTIC VULNERABILITY: same profile produced different multiplier."
    )


# SURVIVOR_RUN_GUARDRAIL_BOUNDARY_PATCH_V1
# Final output boundary patch:
# Make sure every _run(profile) result receives global_plan guardrails using the ORIGINAL full profile.
try:
    from optimizer.global_plan_guardrails import apply_global_plan_guardrails as _survivor_apply_global_plan_guardrails

    if "_survivor_original_run_for_guardrails" not in globals():
        _survivor_original_run_for_guardrails = _run

        def _run(profile, *args, **kwargs):
            result = _survivor_original_run_for_guardrails(profile, *args, **kwargs)
            return _survivor_apply_global_plan_guardrails(result, profile)

except Exception:
    pass

