"""
Adversarial optimizer test:
This checks whether the Survivor.io optimizer can handle confusing high-tier profiles.

It should catch bad logic like:
- ranking cheap/common low-tier materials as the main blocker
- ignoring rare milestone blockers
- using fake damage scores instead of real total damage / multipliers
- missing hidden damage from survivor, pet, tech, resonance, collectibles, etc.
"""

import importlib
import inspect
import json
import os
import pkgutil
from typing import Any


def _to_plain(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_plain(v) for v in value]
    if hasattr(value, "__dict__"):
        return {str(k): _to_plain(v) for k, v in vars(value).items()}
    return str(value)


def _flatten_text(value: Any) -> str:
    return json.dumps(_to_plain(value), default=str, sort_keys=True).lower()


def _find_any_key(value: Any, wanted_keys: set[str]) -> Any:
    value = _to_plain(value)

    if isinstance(value, dict):
        for k, v in value.items():
            if str(k).lower() in wanted_keys:
                return v
        for v in value.values():
            found = _find_any_key(v, wanted_keys)
            if found is not None:
                return found

    if isinstance(value, list):
        for item in value:
            found = _find_any_key(item, wanted_keys)
            if found is not None:
                return found

    return None


def _first_recommendations(result: Any, limit: int = 3) -> str:
    plain = _to_plain(result)
    recs = _find_any_key(
        plain,
        {
            "recommendations",
            "actions",
            "best_actions",
            "next_actions",
            "priority_actions",
            "upgrade_plan",
            "plan",
            "ranked_actions",
        },
    )

    if isinstance(recs, list):
        return json.dumps(recs[:limit], default=str).lower()

    return ""


def _make_profile() -> dict:
    return {
        "profile_name": "SS_Endgame_LowTier_Bait",
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
                "damage_multiplier": 8.50,
            },
            "belt": {
                "name": "SS belt",
                "rarity": "SS",
                "astral_forge": 0,
                "damage_multiplier": 3.20,
            },
            "necklace": {
                "name": "S necklace",
                "rarity": "legendary",
                "damage_multiplier": 2.40,
            },
            "gloves": {
                "name": "S gloves",
                "rarity": "legendary",
                "damage_multiplier": 2.10,
            },
            "suit": {
                "name": "S suit",
                "rarity": "legendary",
                "damage_multiplier": 1.40,
            },
            "boots": {
                "name": "S boots",
                "rarity": "legendary",
                "damage_multiplier": 1.50,
            },
        },
        "survivor": {
            "name": "S Survivor",
            "rarity": "S",
            "level": 100,
            "stars": 5,
            "awakening": 0,
            "damage_multiplier": 4.75,
            "near_milestone": {
                "milestone": "S Survivor Awakening 1",
                "missing": {
                    "awakening_core": 1,
                    "s_survivor_shards": 6,
                },
            },
        },
        "tech": {
            "offensive": {
                "drone": {
                    "rarity": "legendary",
                    "resonance": 6000,
                    "damage_multiplier": 5.00,
                },
                "lightning": {
                    "rarity": "legendary",
                    "resonance": 3000,
                    "damage_multiplier": 2.40,
                },
                "soccer": {
                    "rarity": "legendary",
                    "damage_multiplier": 1.90,
                },
            },
            "twinborn": {
                "unlocked": True,
                "active_pair": "Drone/Forcefield",
                "damage_multiplier": 2.30,
            },
        },
        "pet": {
            "main": {
                "name": "Crab",
                "level": 100,
                "stars": 5,
                "awakening": 1,
                "damage_multiplier": 2.75,
            },
            "assist": [
                {"name": "Eagle", "damage_multiplier": 1.35},
                {"name": "Croaky", "damage_multiplier": 1.25},
            ],
        },
        "collectibles": {
            "damage_multiplier": 3.90,
            "important_breakpoints_active": True,
        },
        "inventory": {
            # Bait shortages. These should NOT be major blockers at SS progression.
            "normal_salvage_cubes": 0,
            "basic_gear_fodder": 0,
            "purple_merge_items": 0,
            "yellow_merge_items": 0,
            "common_materials": 0,

            # Real blockers.
            "relic_cores": 0,
            "needed_relic_cores_for_next_ss_af": 1,
            "awakening_cores": 0,
            "needed_awakening_cores_for_next_survivor_awakening": 1,
            "s_survivor_shards": 44,
            "needed_s_survivor_shards_for_next_survivor_awakening": 50,
        },
    }


def _try_call(fn, profile: dict):
    call_styles = [
        lambda: fn(profile),
        lambda: fn(player_profile=profile),
        lambda: fn(profile=profile),
        lambda: fn(state=profile),
        lambda: fn(player_state=profile),
    ]

    last_error = None
    for call in call_styles:
        try:
            return call()
        except TypeError as exc:
            last_error = exc

    raise TypeError(last_error)


def _looks_like_optimizer_callable(name: str) -> bool:
    lower = name.lower()

    good = [
        "recommend",
        "optimize",
        "score",
        "rank",
        "plan",
        "select",
        "build",
        "evaluate",
    ]

    bad = [
        "train",
        "training",
        "benchmark",
        "debug",
        "main",
        "cli",
        "parse",
        "load",
        "save",
        "read",
        "write",
        "test",
    ]

    return any(x in lower for x in good) and not any(x in lower for x in bad)


def _load_optimizer_function(profile: dict):
    """
    Best way:
    Set your exact function manually before running pytest, for example:

    $env:SURVIVOR_OPTIMIZER_ENTRY = "optimizer.recommender:YOUR_FUNCTION_NAME"
    pytest -q -k adversarial_high_tier_low_tier_bait
    """

    override = os.environ.get("SURVIVOR_OPTIMIZER_ENTRY")
    if override:
        module_name, function_name = override.split(":", 1)
        module = importlib.import_module(module_name)
        fn = getattr(module, function_name)
        _try_call(fn, profile)
        return fn

    direct_candidates = [
        ("optimizer.main", "optimize"),
        ("optimizer.recommender", "recommend"),
        ("optimizer.recommender", "recommend_build"),
        ("optimizer.recommender", "recommend_actions"),
        ("optimizer.recommender", "recommend_upgrades"),
        ("optimizer.recommender", "build_recommendations"),
        ("optimizer.recommender", "get_recommendations"),
        ("optimizer.recommender", "optimize"),
        ("optimizer.recommender", "optimize_profile"),
        ("optimizer.recommender", "optimize_build"),
        ("optimizer.recommender", "rank_actions"),
        ("optimizer.recommender", "score_profile"),
        ("optimizer.recommender", "evaluate_profile"),
        ("optimizer.core_selector", "recommend"),
        ("optimizer.core_selector", "optimize"),
        ("optimizer.core_selector", "select_best"),
        ("optimizer.player_state", "recommend"),
        ("optimizer.player_state", "optimize"),
    ]

    tried = []

    for module_name, function_name in direct_candidates:
        try:
            module = importlib.import_module(module_name)
            fn = getattr(module, function_name)
            _try_call(fn, profile)
            return fn
        except Exception as exc:
            tried.append(f"{module_name}:{function_name} -> {type(exc).__name__}: {exc}")

    # Auto-search optimizer package.
    try:
        package = importlib.import_module("optimizer")
    except Exception as exc:
        raise AssertionError(f"Could not import optimizer package: {exc}")

    discovered = []

    for modinfo in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        module_name = modinfo.name

        if any(skip in module_name.lower() for skip in ["test", "training", "benchmark", "debug", "cli"]):
            continue

        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue

        for name, obj in inspect.getmembers(module):
            if inspect.isfunction(obj) and _looks_like_optimizer_callable(name):
                discovered.append(f"{module_name}:{name}")
                try:
                    _try_call(obj, profile)
                    return obj
                except Exception as exc:
                    tried.append(f"{module_name}:{name} -> {type(exc).__name__}: {exc}")

    raise AssertionError(
        "Could not find a callable optimizer function for this profile.\n\n"
        "Likely fix:\n"
        "1. Look at the discovered functions below.\n"
        "2. Pick the real recommender function.\n"
        "3. Run with SURVIVOR_OPTIMIZER_ENTRY.\n\n"
        "Example:\n"
        '$env:SURVIVOR_OPTIMIZER_ENTRY = "optimizer.recommender:YOUR_FUNCTION_NAME"\n'
        'pytest -q -k "adversarial_high_tier_low_tier_bait"\n\n'
        "Discovered possible functions:\n"
        + "\n".join(discovered[:200])
        + "\n\nTried:\n"
        + "\n".join(tried[:200])
    )


def _run_optimizer(profile: dict) -> Any:
    fn = _load_optimizer_function(profile)
    return _try_call(fn, profile)


def test_adversarial_high_tier_low_tier_bait_profile():
    profile = _make_profile()
    result = _run_optimizer(profile)
    text = _flatten_text(result)

    total_damage = _find_any_key(
        result,
        {
            "total_damage",
            "damage_total",
            "final_damage",
            "expected_damage",
            "total_dps",
            "dps",
        },
    )

    final_multiplier = _find_any_key(
        result,
        {
            "final_damage_multiplier",
            "final_multiplier",
            "total_multiplier",
            "damage_multiplier",
            "multiplier",
        },
    )

    multiplier_breakdown = _find_any_key(
        result,
        {
            "multiplier_breakdown",
            "damage_breakdown",
            "multipliers",
            "breakdown",
            "system_multipliers",
        },
    )

    assert total_damage is not None, (
        "Optimizer must return real total damage/final damage/DPS. "
        "Do not replace this with a fake score."
    )

    assert final_multiplier is not None, (
        "Optimizer must return final damage multiplier or total multiplier."
    )

    assert multiplier_breakdown is not None, (
        "Optimizer must return multiplier breakdown by system."
    )

    forbidden_fake_score_terms = [
        "score_out_of_1000",
        "742/1000",
        "/1000",
    ]

    for term in forbidden_fake_score_terms:
        assert term not in text, (
            f"Optimizer used fake score term '{term}'. "
            "Use total damage and real multipliers instead."
        )

    assert "relic" in text and "core" in text, (
        "Optimizer must identify relic core as a real blocker for SS/AF progression."
    )

    assert "awakening" in text and "core" in text, (
        "Optimizer must identify awakening core as a real blocker for survivor awakening."
    )

    assert "shard" in text, (
        "Optimizer must identify missing S survivor shards for the near-awakening milestone."
    )

    assert "milestone" in text or "close" in text or "near" in text or "awakening" in text, (
        "Optimizer must warn when the player is close to a major milestone."
    )

    first_recs = _first_recommendations(result, limit=3)

    cheap_bait_terms = [
        "normal salvage cube",
        "salvage cubes",
        "basic gear fodder",
        "purple merge",
        "yellow merge",
        "common materials",
        "low-tier",
        "low tier",
    ]

    if first_recs:
        for term in cheap_bait_terms:
            assert term not in first_recs, (
                f"Cheap/common bait material '{term}' was placed in top recommendations. "
                "At SS progression, rare blockers should outrank it."
            )

    bad_main_blocker_phrases = [
        "main blocker is normal salvage",
        "main blocker: normal salvage",
        "primary blocker is normal salvage",
        "primary blocker: normal salvage",
        "top blocker is normal salvage",
        "top blocker: normal salvage",
        "main blocker is basic gear fodder",
        "primary blocker is basic gear fodder",
    ]

    for phrase in bad_main_blocker_phrases:
        assert phrase not in text, (
            "Optimizer treated cheap/common low-tier materials as the main blocker. "
            "That is wrong for this high-tier SS profile."
        )
