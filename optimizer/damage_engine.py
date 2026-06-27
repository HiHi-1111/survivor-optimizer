"""Small damage-first helpers.

This is not final Survivor.io math. It deliberately ignores HP and other
survival-only fields so early tests can enforce the optimizer's default
damage-first behavior.
"""

from __future__ import annotations

import re
from typing import Any


def estimate_damage_score(build_stats: dict[str, Any]) -> float:
    atk = float(build_stats.get("atk", 0))
    crit_rate = max(0.0, float(build_stats.get("crit_rate", 0)))
    crit_damage = max(1.0, float(build_stats.get("crit_damage", 1)))
    additive_damage = sum(
        float(build_stats.get(key, 0))
        for key in [
            "skill_damage",
            "vulnerability",
            "shield_damage",
            "damage_to_chilled",
            "damage_to_poisoned",
            "boss_damage",
            "all_damage",
            "final_damage",
        ]
    )
    crit_multiplier = 1 + crit_rate * max(0.0, crit_damage - 1)
    return round(atk * crit_multiplier * (1 + additive_damage), 3)



def _plain(value: Any) -> Any:
    """Convert nested objects/dataclasses/pydantic models into plain structures."""
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


_MULTIPLIER_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*(x|%)?\s*$", re.IGNORECASE)


def _safe_float(value: Any, default: float = 1.0) -> float:
    if isinstance(value, str):
        match = _MULTIPLIER_RE.match(value)
        if not match:
            return default
        number = float(match.group(1))
        suffix = (match.group(2) or "").lower()
        if suffix == "x":
            pass
        elif suffix == "%":
            number = 1.0 + number / 100.0 if value.strip().startswith("+") else number / 100.0
        value = number
    try:
        number = float(value)
    except Exception:
        return default
    if number <= 0:
        return default
    return number


def _flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "y", "1", "active", "equipped", "selected", "slotted", "owned", "unlocked", "bought", "purchased"}:
            return True
        if text in {"false", "no", "n", "0", "inactive", "unequipped", "unselected", "unslotted", "locked", "preview"}:
            return False
    return None


def _is_source_or_future_row(value: dict[str, Any]) -> str | None:
    text_fields = " ".join(
        str(value.get(key, ""))
        for key in ["record_type", "source", "note_type", "type", "status", "state", "name", "label"]
    ).lower()
    if any(term in text_fields for term in ["catalog", "source_pack", "reference", "recommendation_candidate", "community_comment", "screenshot_text"]):
        return "source/catalog/reference row"
    if any(term in text_fields for term in ["preview", "future", "candidate", "locked"]):
        return "future/preview/candidate row"
    return None


def _inactive_reason(value: dict[str, Any], path: tuple[str, ...]) -> str | None:
    lowered_path = tuple(part.lower() for part in path)
    key_text = ".".join(lowered_path)

    source_reason = _is_source_or_future_row(value)
    if source_reason:
        return source_reason

    negative_flags = {
        "locked": "locked",
        "preview": "preview",
        "future": "future",
        "candidate": "candidate",
        "missing_resource": "missing-resource",
        "missing_resources": "missing-resource",
    }
    for key, label in negative_flags.items():
        if _flag(value.get(key)) is True:
            return label
    if value.get("missing") or value.get("missing_shards"):
        return "missing-resource"

    for key, label in [
        ("equipped", "unequipped"),
        ("selected", "unselected"),
        ("active", "inactive"),
        ("slotted", "unslotted"),
        ("unlocked", "locked"),
        ("bought", "unbought"),
        ("purchased", "unbought"),
        ("owned_after_purchase", "unbought"),
    ]:
        flag = _flag(value.get(key))
        if flag is False:
            return label

    if "owned_not_equipped" in lowered_path:
        return "owned but unequipped"
    if "roster" in lowered_path and "active" not in lowered_path:
        return "unselected survivor roster"
    if "inactive_mode" in lowered_path:
        return "inactive Twinborn mode"
    if "candidate_resonance_assists" in key_text:
        return "unslotted resonance candidate"
    if "next_breakpoint" in key_text:
        return "locked collectible breakpoint preview"
    if "event_shop_options" in lowered_path:
        return "unbought event shop item"
    if any(part in lowered_path for part in ["source_database_catalog_rows", "discord_notes", "ocr_text"]):
        return "catalog/source/note row"

    return None


def _product_damage_multipliers(value: Any, path: tuple[str, ...] = (), ignored: list[str] | None = None) -> float:
    value = _plain(value)

    if isinstance(value, dict):
        reason = _inactive_reason(value, path)
        if reason:
            if ignored is not None and any(str(key).lower().endswith("damage_multiplier") for key in value):
                ignored.append(f"{'.'.join(path) or '<root>'}: {reason}")
            return 1.0
        product = 1.0
        for key, child in value.items():
            key_text = str(key)
            if key_text.lower() == "damage_multiplier":
                product *= _safe_float(child, 1.0)
            else:
                product *= _product_damage_multipliers(child, path + (key_text,), ignored)
        return product

    if isinstance(value, list):
        product = 1.0
        for index, child in enumerate(value):
            product *= _product_damage_multipliers(child, path + (str(index),), ignored)
        return product

    return 1.0


def _get_base_damage(profile: dict[str, Any]) -> float:
    for key in ["base_damage", "base_atk", "atk", "attack"]:
        if key in profile:
            return _safe_float(profile.get(key), 1.0)

    stats = profile.get("stats") if isinstance(profile.get("stats"), dict) else {}
    for key in ["base_damage", "base_atk", "atk", "attack"]:
        if key in stats:
            return _safe_float(stats.get(key), 1.0)

    # If the profile has no attack stat, use 1 so the multiplier math still works.
    return 1.0


def _inventory(profile: dict[str, Any]) -> dict[str, Any]:
    inv = profile.get("inventory")
    return inv if isinstance(inv, dict) else {}


def _near_milestones(profile: dict[str, Any]) -> list[dict[str, Any]]:
    milestones: list[dict[str, Any]] = []

    survivor = profile.get("survivor")
    if isinstance(survivor, dict) and isinstance(survivor.get("near_milestone"), dict):
        milestones.append(survivor["near_milestone"])

    for key, value in profile.items():
        if isinstance(value, dict) and isinstance(value.get("near_milestone"), dict):
            milestones.append(value["near_milestone"])

    return milestones


def _blocker_report(profile: dict[str, Any]) -> dict[str, Any]:
    inv = _inventory(profile)

    real_blockers: list[str] = []
    minor_blockers: list[str] = []

    def inv_num(*names: str) -> float:
        wanted = {name.lower() for name in names}
        for key, value in inv.items():
            key_text = str(key).lower()
            if key_text in wanted or any(name in key_text for name in wanted):
                try:
                    return float(value or 0)
                except Exception:
                    return 0.0
        return 0.0

    if inv_num("relic_cores", "relic core") < inv_num("needed_relic_cores_for_next_ss_af", "relic core needed", "needed relic core"):
        real_blockers.append("relic core")

    if inv_num("awakening_cores", "awakening core", "s awakening core") < inv_num(
        "needed_awakening_cores_for_next_survivor_awakening", "awakening core needed", "needed awakening core", "s awakening core needed"
    ):
        real_blockers.append("awakening core")

    if inv_num("s_survivor_shards", "yang shard", "survivor shard") < inv_num(
        "needed_s_survivor_shards_for_next_survivor_awakening", "yang shard needed", "needed survivor shard", "survivor shard needed"
    ):
        real_blockers.append("S survivor shards")

    cheap_keys = [
        "normal_salvage_cubes",
        "basic_gear_fodder",
        "purple_merge_items",
        "yellow_merge_items",
        "common_materials",
    ]

    stage_text = str(profile.get("player_stage", {}).get("progression_stage", "")).lower()
    is_ss_stage = "ss" in stage_text or bool(profile.get("player_stage", {}).get("steamroll_unlocked"))

    for key in cheap_keys:
        if key in inv and float(inv.get(key, 0) or 0) <= 0:
            label = key.replace("_", " ")
            if is_ss_stage:
                minor_blockers.append(f"{label}: minor bait blocker at SS progression")
            else:
                minor_blockers.append(f"{label}: possible early-stage blocker")

    return {
        "real_blockers": real_blockers,
        "minor_blockers": minor_blockers,
        "near_milestones": _near_milestones(profile),
    }


def estimate_damage_totals(profile_input: Any) -> dict[str, Any]:
    """
    Real damage report for optimizer output.

    This does not create a fake score. It reports:
    - base damage
    - system multiplier breakdown
    - final damage multiplier
    - total damage
    - blocker/milestone context
    """
    profile = _plain(profile_input)
    if not isinstance(profile, dict):
        profile = {}

    base_damage = _get_base_damage(profile)

    systems = [
        "gear",
        "survivor",
        "tech",
        "pet",
        "collectibles",
    ]

    breakdown: dict[str, float] = {}
    ignored_rows: list[str] = []
    for system in systems:
        breakdown[system] = round(_product_damage_multipliers(profile.get(system, {}), (system,), ignored_rows), 6)

    known_systems = set(systems)
    other_payload = {
        key: value
        for key, value in profile.items()
        if key not in known_systems and key not in {"inventory", "player_stage", "profile_name", "expected_logic"}
    }
    breakdown["other"] = round(_product_damage_multipliers(other_payload, ("other",), ignored_rows), 6)

    final_multiplier = 1.0
    for value in breakdown.values():
        final_multiplier *= _safe_float(value, 1.0)

    final_multiplier = round(final_multiplier, 6)
    total_damage = round(base_damage * final_multiplier, 6)

    blocker_report = _blocker_report(profile)

    return {
        "base_damage": base_damage,
        "total_damage": total_damage,
        "final_damage_multiplier": final_multiplier,
        "multiplier_breakdown": breakdown,
        "damage_math_type": "real_total_damage_and_multipliers",
        "blocker_analysis": blocker_report,
        "ignored_inactive_or_future_damage_rows": sorted(set(ignored_rows)),
        "true_blockers": blocker_report["real_blockers"],
        "minor_blockers": blocker_report["minor_blockers"],
        "next_milestones": blocker_report["near_milestones"],
    }


# --- Runtime timing wrapper added by adaptive learning patch ---
# It adjusts cyclic/charged/timed effects over a normal 180 second battle.
try:
    from optimizer.effect_timing import apply_timed_effect_adjustment as _apply_timed_effect_adjustment

    _estimate_damage_totals_without_timing = estimate_damage_totals

    def estimate_damage_totals(profile: dict[str, Any]) -> dict[str, Any]:
        report = _estimate_damage_totals_without_timing(profile)
        return _apply_timed_effect_adjustment(profile, report)

except Exception:
    pass


# RARE_BLOCKER_GUARDRAIL_DAMAGE_WRAPPER
try:
    from optimizer.rare_blocker_guardrails import enrich_optimizer_result as _enrich_rare_blocker_damage_result

    _estimate_damage_totals_without_rare_blocker_guardrail = estimate_damage_totals

    def estimate_damage_totals(profile: dict[str, Any]) -> dict[str, Any]:
        result = _estimate_damage_totals_without_rare_blocker_guardrail(profile)
        return _enrich_rare_blocker_damage_result(profile, result)

except Exception:
    pass


# GLOBAL_PLAN_GUARDRAILS_AUTO_PATCH_V1
try:
    import sys as _survivor_sys
    from optimizer.global_plan_guardrails import patch_module_functions as _survivor_patch_module_functions
    _survivor_patch_module_functions(_survivor_sys.modules[__name__])
except Exception:
    pass

