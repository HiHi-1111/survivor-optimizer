from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "tools"
OUT_DIR = ROOT / "training_outputs"
TRAINING_SCRIPT = TOOLS_DIR / "run_adversarial_training.py"
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

TRAINING_SUMMARY = OUT_DIR / "adversarial_training_summary.json"
ADVERSARIAL_CASES = OUT_DIR / "adversarial_cases.jsonl"
GENERATED_PROFILES = OUT_DIR / "generated_anti_ai_profiles.jsonl"
BATTLE_ROUNDS = OUT_DIR / "adversarial_battle_rounds.jsonl"
BATTLE_SUMMARY = OUT_DIR / "adversarial_battle_summary.json"
HARD_EXAMPLES = OUT_DIR / "adversarial_hard_examples.jsonl"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


TRAP_CATEGORY_BY_LABEL = {
    "unequipped_owned_gear_inventory": "unequipped gear",
    "locked_af_preview_on_equipped_weapon": "locked AF / Cosmic Cast preview",
    "future_ss_cosmic_cast_preview": "locked AF / Cosmic Cast preview",
    "unselected_survivor_roster": "unselected survivor roster",
    "inactive_twinborn_mode_same_pair": "inactive Twinborn mode",
    "unslotted_resonance_assist_candidates": "unslotted resonance assist",
    "unequipped_pet_inventory": "unequipped pet",
    "locked_collectible_next_breakpoint": "locked collectible breakpoint preview",
    "source_database_catalog_rows_not_player_state": "source/catalog rows mixed into player state",
    "event_shop_options_not_owned_until_bought": "unbought event shop item",
    "material_aliases_relic_core_awakening_core_yang_shard": "aliases: Relic Core, S Awakening Core, Yang shard",
    "source_pack_multiplier_strings": "multiplier strings: 2.35x, 175%, +25%",
    "cheap_bait_vs_rare_blockers": "cheap material bait vs rare blockers",
    "near_milestone_missing_core_and_shards": "near milestone missing both core and shards",
}

RULE_BY_CATEGORY = {
    "unequipped gear": "Only equipped gear slots may contribute to current damage; owned inventory copies are planning data.",
    "unselected survivor roster": "Only the selected active survivor may contribute current survivor damage.",
    "unequipped pet": "Only active main pet and equipped pet assists may contribute current damage.",
    "unslotted resonance assist": "Only slotted resonance assists may contribute current damage.",
    "inactive Twinborn mode": "Only the active Twinborn mode may contribute current damage.",
    "locked AF / Cosmic Cast preview": "Locked, missing-resource, preview, and future upgrade nodes must not change current damage.",
    "locked AF/Cosmic Cast preview": "Locked, missing-resource, preview, and future upgrade nodes must not change current damage.",
    "locked collectible breakpoint preview": "Only unlocked collectible bonuses count; next-breakpoint previews remain future goals.",
    "unbought event shop item": "Event shop options do not count until bought and applied to an active system.",
    "source/catalog rows mixed into player state": "Source, catalog, recommendation, and reference rows must never be treated as player-owned active bonuses.",
    "aliases: Relic Core, S Awakening Core, Yang shard": "Normalize real material aliases into canonical blocker analysis without inventing current damage.",
    "material aliases: Relic Core, S Awakening Core, Yang shard": "Normalize real material aliases into canonical blocker analysis without inventing current damage.",
    "multiplier strings: 2.35x, 175%, +25%": "Parse realistic multiplier strings consistently with numeric multipliers.",
    "cheap material bait vs rare blockers": "Do not rank common low-tier materials above rare SS or awakening blockers at SS progression.",
    "cheap bait vs rare blockers": "Do not rank common low-tier materials above rare SS or awakening blockers at SS progression.",
    "near milestone missing both core and shards": "Near-awakening guidance must mention both missing core and missing shards when both block the milestone.",
    "positive control": "Real active, equipped, unlocked, selected upgrades must still count.",
}


def _json_default(value: Any) -> str:
    return str(value)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _atomic_write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=_json_default) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _atomic_append_jsonl(path: Path, row: dict[str, Any]) -> None:
    existing = _read_jsonl(path)
    existing.append(row)
    _atomic_write_jsonl(path, existing)


def _case_id(*parts: Any) -> str:
    text = "|".join(json.dumps(part, sort_keys=True, default=_json_default) for part in parts)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(child) for child in value]
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump())
    if hasattr(value, "dict"):
        return _plain(value.dict())
    if hasattr(value, "__dict__"):
        return _plain(vars(value))
    return str(value)


def _find_key(value: Any, wanted: set[str]) -> Any:
    value = _plain(value)
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in wanted:
                return child
        for child in value.values():
            found = _find_key(child, wanted)
            if found is not None:
                return found
    if isinstance(value, list):
        for child in value:
            found = _find_key(child, wanted)
            if found is not None:
                return found
    return None


def _damage(result: dict[str, Any]) -> float:
    value = _find_key(result, {"total_damage", "damage_total", "final_damage", "expected_damage", "total_dps", "dps"})
    try:
        return float(value)
    except Exception:
        return 0.0


def _text(value: Any) -> str:
    return json.dumps(_plain(value), sort_keys=True, default=_json_default).lower()


def _python_for_subprocesses() -> str:
    return str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


def _load_training_module() -> Any:
    spec = importlib.util.spec_from_file_location("battle_adversarial_training", TRAINING_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {TRAINING_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_training_subprocess(tool_python: str) -> dict[str, Any]:
    started = time.perf_counter()
    proc = subprocess.run(
        [tool_python, str(TRAINING_SCRIPT.relative_to(ROOT))],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return {
        "command": f"{tool_python} {TRAINING_SCRIPT.relative_to(ROOT)}",
        "returncode": proc.returncode,
        "elapsed_seconds": round(time.perf_counter() - started, 6),
        "output_tail": proc.stdout[-4000:],
    }


def _normalize_category(category: Any) -> str:
    text = str(category or "unclassified")
    return {
        "locked AF/Cosmic Cast preview": "locked AF / Cosmic Cast preview",
        "material aliases: Relic Core, S Awakening Core, Yang shard": "aliases: Relic Core, S Awakening Core, Yang shard",
        "cheap bait vs rare blockers": "cheap material bait vs rare blockers",
    }.get(text, text)



def _active_failure_categories(summary: dict[str, Any], cases: list[dict[str, Any]]) -> set[str]:
    """Return broad active categories.

    The old version let one repeated failure dominate forever. For adversarial
    training, that is bad: Anti-AI should exploit known failures, but it must
    keep probing every trap family.
    """
    categories: set[str] = set()

    # Keep all known trap families alive every run.
    categories.update(_normalize_category(value) for value in TRAP_CATEGORY_BY_LABEL.values())

    # Also include anything found in prior summaries/cases.
    for key, count in (summary.get("failure_categories") or {}).items():
        try:
            if int(count) > 0:
                categories.add(_normalize_category(key))
        except Exception:
            categories.add(_normalize_category(key))

    for case in cases:
        if case.get("passed") is False:
            categories.add(_normalize_category(case.get("category")))

    for row in _read_jsonl(HARD_EXAMPLES):
        categories.add(_normalize_category(row.get("category")))

    categories.add("positive control")
    return categories


def _hard_example_weights() -> dict[str, float]:
    weights: dict[str, float] = {}
    for row in _read_jsonl(HARD_EXAMPLES):
        category = _normalize_category(row.get("category"))
        weight = max(1.0, float(row.get("weight", 1.0) or 1.0))
        weights[category] = max(weights.get(category, 1.0), weight)
        case_id = str(row.get("case_id") or "")
        if case_id:
            weights[case_id] = max(weights.get(case_id, 1.0), weight)
    return weights


def _load_seed_cases(training_module: Any) -> list[dict[str, Any]]:
    cases = _read_jsonl(GENERATED_PROFILES)
    if cases:
        return [_normalize_case(row) for row in cases]
    fixtures = training_module._load_test_fixtures()
    return [_normalize_case(row) for row in training_module._generated_cases(fixtures)]


def _normalize_case(row: dict[str, Any]) -> dict[str, Any]:
    case = deepcopy(row)
    label = str(case.get("label") or case.get("case_id") or "case")
    category = _normalize_category(case.get("category") or TRAP_CATEGORY_BY_LABEL.get(label, "unclassified"))
    case["label"] = label
    case["category"] = category
    case["trap_type"] = str(case.get("trap_type") or "trap")
    case["suggested_optimizer_rule"] = str(case.get("suggested_optimizer_rule") or RULE_BY_CATEGORY.get(category, ""))
    return case


def _set_path(root: dict[str, Any], path: Iterable[Any], value: Any) -> None:
    cursor: Any = root
    parts = list(path)
    for part in parts[:-1]:
        if isinstance(part, int):
            cursor = cursor[part]
        else:
            cursor = cursor.setdefault(part, {})
    last = parts[-1]
    if isinstance(last, int):
        cursor[last] = value
    else:
        cursor[last] = value


def _profile_variant(case: dict[str, Any], round_no: int, variant_no: int) -> dict[str, Any]:
    changed = deepcopy(case)
    challenged = changed.get("challenged_profile")
    clean = changed.get("clean_profile")
    if not isinstance(challenged, dict) or not isinstance(clean, dict):
        return changed

    category = _normalize_category(changed.get("category"))
    multiplier_cycle = [1.18, 1.22, 1.25, 1.30, "2.35x", "175%", "+25%"]
    pick = multiplier_cycle[(round_no + variant_no) % len(multiplier_cycle)]

    if category == "unequipped gear":
        owned = challenged.setdefault("gear", {}).setdefault("owned_not_equipped", {})
        owned[f"unused_s_gear_copy_r{round_no}_{variant_no}"] = {
            "name": "Unused S Grade Gear",
            "slot": "weapon" if variant_no % 2 == 0 else "necklace",
            "owned": True,
            "equipped": False,
            "rarity": "legendary",
            "damage_multiplier": pick,
        }
    elif category == "unselected survivor roster":
        roster = challenged.setdefault("survivor", {}).setdefault("roster", [])
        if isinstance(roster, list):
            roster.append(
                {
                    "name": "Unselected S Survivor",
                    "selected": False,
                    "owned": True,
                    "level": 100,
                    "stars": 5,
                    "damage_multiplier": pick,
                }
            )
    elif category == "unequipped pet":
        pets = challenged.setdefault("pet", {}).setdefault("owned_not_equipped", [])
        if isinstance(pets, list):
            pets.append({"name": "Benched Pet", "owned": True, "active": False, "equipped": False, "damage_multiplier": pick})
    elif category == "unslotted resonance assist":
        assists = challenged.setdefault("tech", {}).setdefault("drone", {}).setdefault("candidate_resonance_assists", [])
        if isinstance(assists, list):
            assists.append({"name": "Unslotted Drill Assist", "owned": True, "slotted": False, "damage_multiplier": pick})
    elif category == "inactive Twinborn mode":
        challenged.setdefault("tech", {}).setdefault("twinborn", {})["inactive_mode"] = {
            "name": "Inactive Twinborn Swap",
            "active": False,
            "same_pair_as_active": True,
            "damage_multiplier": pick,
        }
    elif category == "locked AF / Cosmic Cast preview":
        challenged.setdefault("gear", {}).setdefault("weapon", {})[f"locked_future_node_r{round_no}_{variant_no}"] = {
            "unlocked": False,
            "preview": True,
            "missing": {"Relic Core": 1, "S Core": 1},
            "damage_multiplier": pick,
        }
    elif category == "locked collectible breakpoint preview":
        challenged.setdefault("collectibles", {})[f"next_breakpoint_preview_r{round_no}_{variant_no}"] = {
            "unlocked": False,
            "preview": True,
            "missing_shards": 8 + variant_no,
            "damage_multiplier": pick,
        }
    elif category == "unbought event shop item":
        options = challenged.setdefault("event_shop_options", [])
        if isinstance(options, list):
            options.append(
                {
                    "name": "Relic Core",
                    "available": True,
                    "bought": False,
                    "owned_after_purchase": False,
                    "cost": 6000,
                    "damage_multiplier_if_used_later": pick,
                }
            )
    elif category == "source/catalog rows mixed into player state":
        rows = challenged.setdefault("source_database_catalog_rows", [])
        if isinstance(rows, list):
            rows.append(
                {
                    "system": "collectibles",
                    "name": "Guide-only breakpoint row",
                    "record_type": "source_pack_reference",
                    "owned": False,
                    "active": False,
                    "unlocked": False,
                    "damage_multiplier": pick,
                }
            )
    elif category == "aliases: Relic Core, S Awakening Core, Yang shard":
        inv = challenged.setdefault("inventory", {})
        inv.update({"Relic Core": 0, "S Awakening Core": 0, "Yang shard": 45 + (variant_no % 4)})
    elif category == "multiplier strings: 2.35x, 175%, +25%":
        string_sets = [
            [("gear", "weapon", "damage_multiplier", "2.35x"), ("gear", "belt", "damage_multiplier", "175%")],
            [("survivor", "active", "damage_multiplier", "2.10x"), ("tech", "drone", "damage_multiplier", "225%")],
            [("collectibles", "owned_bonus", "damage_multiplier", "220%"), ("gear", "necklace", "damage_multiplier", "+45%")],
        ]
        for key_path in string_sets[variant_no % len(string_sets)]:
            _set_path(challenged, key_path[:3], key_path[3])
    elif category == "cheap material bait vs rare blockers":
        challenged.setdefault("inventory", {}).update(
            {
                "normal_salvage_cubes": 0,
                "basic_gear_fodder": 0,
                "purple_merge_items": 0,
                "relic_cores": 0,
                "needed_relic_cores_for_next_ss_af": 1,
                "awakening_cores": 0,
                "needed_awakening_cores_for_next_survivor_awakening": 1,
            }
        )
    elif category == "near milestone missing both core and shards":
        challenged.setdefault("survivor", {}).setdefault("near_milestone", {})["missing"] = {
            "S Awakening Core": 1,
            "Yang shard": 2 + (variant_no % 5),
        }
        challenged.setdefault("inventory", {}).update({"awakening_cores": 0, "s_survivor_shards": 45 + (variant_no % 4)})

    if changed.get("trap_type") == "positive_control":
        targets = [
            ("gear", "weapon", "damage_multiplier", 2.60 + 0.01 * variant_no),
            ("survivor", "active", "damage_multiplier", 2.30 + 0.01 * variant_no),
            ("tech", "drone", "damage_multiplier", 2.50 + 0.01 * variant_no),
            ("pet", "main", "damage_multiplier", 2.05 + 0.01 * variant_no),
            ("collectibles", "owned_bonus", "damage_multiplier", 2.40 + 0.01 * variant_no),
        ]
        target = targets[(round_no + variant_no) % len(targets)]
        cursor = challenged
        for part in target[:-2]:
            cursor = cursor.setdefault(part, {})
        cursor[target[-2]] = target[-1]

    changed["category"] = category
    changed["label"] = f"{changed.get('label', category)}_r{round_no}_v{variant_no}"
    changed["case_id"] = _case_id("battle", round_no, variant_no, category, challenged)
    challenged["profile_name"] = f"Battle_{round_no}_{variant_no}_{category}".replace(" ", "_").replace("/", "_")
    return changed



def _generate_battle_cases(
    seed_cases: list[dict[str, Any]],
    active_categories: set[str],
    round_no: int,
    max_generated: int,
    cpu_workers: int,
) -> tuple[list[dict[str, Any]], float]:
    """Generate a balanced adversarial batch.

    This is intentionally NOT pure hard-example replay. Hard-example replay was
    causing the exact same 112/138 result every round. This scheduler creates a
    real duel:
    - exploit known failures
    - explore other categories
    - keep positive controls
    - rotate category order every round
    """
    started = time.perf_counter()
    max_generated = max(1, int(max_generated))
    hard_weights = _hard_example_weights()

    normalized_seeds = [_normalize_case(row) for row in seed_cases]
    if not normalized_seeds:
        return [], time.perf_counter() - started

    by_category: dict[str, list[dict[str, Any]]] = {}
    positives: list[dict[str, Any]] = []

    for case in normalized_seeds:
        category = _normalize_category(case.get("category"))
        if category not in active_categories and case.get("trap_type") != "positive_control":
            # Keep a small chance by not dropping it fully.
            pass
        by_category.setdefault(category, []).append(case)
        if case.get("trap_type") == "positive_control":
            positives.append(case)

    categories = sorted([name for name in by_category if name != "positive control"])
    if not categories:
        categories = sorted(by_category)

    # Rotate start position so the same category cannot always get the first slots.
    if categories:
        shift = round_no % len(categories)
        categories = categories[shift:] + categories[:shift]

    # Score categories but cap hard weights so one bug cannot dominate forever.
    def category_weight(name: str) -> float:
        return min(2.0, max(1.0, float(hard_weights.get(name, 1.0) or 1.0)))

    exploit = sorted(categories, key=category_weight, reverse=True)
    explore = list(reversed(exploit))

    selected_sources: list[dict[str, Any]] = []

    # 15% positive controls.
    positive_quota = max(1, int(max_generated * 0.15))
    # 45% broad exploration.
    explore_quota = max(1, int(max_generated * 0.45))
    # 40% known weak spots.
    exploit_quota = max(1, max_generated - positive_quota - explore_quota)

    def take_round_robin(names: list[str], quota: int) -> None:
        nonlocal selected_sources
        if not names or quota <= 0:
            return
        offsets = {name: 0 for name in names}
        idx = 0
        safety = 0
        while quota > 0 and safety < quota * max(10, len(names) * 10):
            name = names[idx % len(names)]
            bucket = by_category.get(name, [])
            if bucket:
                offset = offsets[name] % len(bucket)
                selected_sources.append(bucket[offset])
                offsets[name] += 1
                quota -= 1
            idx += 1
            safety += 1

    if positives:
        for i in range(positive_quota):
            selected_sources.append(positives[i % len(positives)])

    take_round_robin(explore, explore_quota)
    take_round_robin(exploit, exploit_quota)

    # Fill if needed.
    all_names = categories or sorted(by_category)
    take_round_robin(all_names, max_generated - len(selected_sources))

    selected_sources = selected_sources[:max_generated]

    jobs: list[tuple[dict[str, Any], int]] = []
    for index, case in enumerate(selected_sources):
        jobs.append((case, index))

    cases: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, cpu_workers)) as pool:
        futures = [pool.submit(_profile_variant, case, round_no, variant_no) for case, variant_no in jobs]
        for future in as_completed(futures):
            cases.append(future.result())

    cases.sort(key=lambda row: str(row.get("case_id")))

    # Debug metadata for summaries/logs if inspected later.
    category_counts = Counter(_normalize_category(row.get("category")) for row in cases)
    if len(category_counts) <= 1:
        print(f"WARNING: generated only one category: {dict(category_counts)}", flush=True)

    return cases, time.perf_counter() - started


def _optimizer() -> Any:
    entry = os.environ.get("SURVIVOR_OPTIMIZER_ENTRY", "optimizer.main:optimize")
    module_name, function_name = entry.split(":", 1)
    module = __import__(module_name, fromlist=[function_name])
    return getattr(module, function_name)


def _run_optimizer(profile: dict[str, Any]) -> dict[str, Any]:
    fn = _optimizer()
    try:
        return fn(profile, include_global_plan=False)
    except TypeError:
        return fn(profile)


def _profile_hash(profile: dict[str, Any]) -> str:
    return hashlib.sha1(json.dumps(profile, sort_keys=True, default=_json_default).encode("utf-8")).hexdigest()


def _severity(clean_damage: float, challenged_damage: float, trap_type: str, passed: bool) -> tuple[str, float]:
    if clean_damage <= 0:
        return ("pass" if passed else "unknown"), 0.0
    ratio = challenged_damage / clean_damage
    if passed:
        return "pass", ratio
    if trap_type == "positive_control":
        return "high", ratio
    distance = abs(ratio - 1.0)
    if ratio >= 2.0 or ratio <= 0.5:
        return "critical", ratio
    if distance >= 0.25:
        return "high", ratio
    if distance > 0.0:
        return "medium", ratio
    return "low", ratio


def _delta_type(clean_damage: float, challenged_damage: float) -> str:
    if challenged_damage > clean_damage:
        return "inflated"
    if challenged_damage < clean_damage:
        return "undercounted"
    return "unchanged"


def _content_check(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    if case.get("trap_type") != "trap" or not result.get("passed"):
        return result
    category = _normalize_category(case.get("category"))
    if category not in {
        "aliases: Relic Core, S Awakening Core, Yang shard",
        "cheap material bait vs rare blockers",
        "near milestone missing both core and shards",
    }:
        return result
    output_text = _text(_run_optimizer(case["challenged_profile"]))
    if category == "aliases: Relic Core, S Awakening Core, Yang shard":
        result["passed"] = all(term in output_text for term in ["relic", "awakening"]) and ("shard" in output_text or "yang" in output_text)
        result["severity"] = "pass" if result["passed"] else "high"
    elif category == "cheap material bait vs rare blockers":
        profile = case.get("challenged_profile", {}) if isinstance(case, dict) else {}
        output_text = _optimizer_evidence_text(profile)

        has_relic = (
            "relic_core" in output_text
            or "relic core" in output_text
            or ("relic" in output_text and "core" in output_text)
        )
        has_awakening = (
            "awakening_core" in output_text
            or "awakening core" in output_text
            or "s awakening core" in output_text
            or ("awakening" in output_text and "core" in output_text)
        )
        has_priority = (
            "priority_blockers" in output_text
            or "true_blockers" in output_text
            or "rare_blocker_guardrail" in output_text
            or "blocker_analysis" in output_text
        )
        has_common_bait_demotion = (
            "common_bait_demoted" in output_text
            or "common low-tier materials" in output_text
            or "common bait" in output_text
            or "normal_salvage" in output_text
            or "basic_gear_fodder" in output_text
            or "generic_fodder" in output_text
        )

        result["passed"] = bool(has_relic and has_awakening and has_priority and has_common_bait_demotion)
        result["severity"] = "pass" if result["passed"] else "medium"
        result["proof_checked"] = "rare_blocker_guardrail"
        result["missing_proof"] = [] if result["passed"] else [
            label for label, ok in {
                "relic_core": has_relic,
                "awakening_core": has_awakening,
                "priority_blockers": has_priority,
                "common_bait_demoted": has_common_bait_demotion,
            }.items()
            if not ok
        ]

    elif category == "near milestone missing both core and shards":
        result["passed"] = "awakening" in output_text and ("shard" in output_text or "yang" in output_text)
        result["severity"] = "pass" if result["passed"] else "medium"
    return result


def _evaluate_one(case: dict[str, Any], clean_cache: dict[str, float]) -> dict[str, Any]:
    try:
        clean_profile = case["clean_profile"]
        challenged_profile = case["challenged_profile"]
        clean_key = _profile_hash(clean_profile)
        clean_damage = clean_cache.get(clean_key)
        if clean_damage is None:
            clean_damage = _damage(_run_optimizer(clean_profile))
            clean_cache[clean_key] = clean_damage
        challenged_damage = _damage(_run_optimizer(challenged_profile))
        trap_type = str(case.get("trap_type") or "trap")
        if trap_type == "positive_control":
            passed = challenged_damage > clean_damage
        else:
            passed = challenged_damage == clean_damage
        severity, ratio = _severity(clean_damage, challenged_damage, trap_type, passed)
        result = {
            "case_id": case.get("case_id", ""),
            "label": case.get("label", ""),
            "category": _normalize_category(case.get("category")),
            "trap_type": trap_type,
            "passed": passed,
            "clean_damage": clean_damage,
            "trapped_damage": challenged_damage,
            "damage_ratio": round(ratio, 6) if ratio else 0.0,
            "delta_type": _delta_type(clean_damage, challenged_damage),
            "severity": severity,
            "suggested_optimizer_rule": case.get("suggested_optimizer_rule") or RULE_BY_CATEGORY.get(_normalize_category(case.get("category")), ""),
            "regression_test_exists": bool(case.get("regression_test_exists")),
        }
        if not passed and trap_type == "positive_control":
            result["suggested_optimizer_rule"] = "Do not pass anti-AI traps by ignoring all multipliers; active upgrades must increase damage."
        return _content_check(case, result)
    except Exception as exc:
        return {
            "case_id": case.get("case_id", ""),
            "label": case.get("label", ""),
            "category": _normalize_category(case.get("category")),
            "trap_type": case.get("trap_type", "trap"),
            "passed": False,
            "clean_damage": 0.0,
            "trapped_damage": 0.0,
            "damage_ratio": 0.0,
            "delta_type": "unchanged",
            "severity": "error",
            "suggested_optimizer_rule": f"Optimizer evaluation raised {type(exc).__name__}: {exc}",
            "regression_test_exists": bool(case.get("regression_test_exists")),
        }


def _chunks(rows: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    size = max(1, size)
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def _evaluate_cases(cases: list[dict[str, Any]], batch_size: int, cpu_workers: int) -> tuple[list[dict[str, Any]], float]:
    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    clean_cache: dict[str, float] = {}
    for batch in _chunks(cases, batch_size):
        with ThreadPoolExecutor(max_workers=max(1, cpu_workers)) as pool:
            futures = [pool.submit(_evaluate_one, case, clean_cache) for case in batch]
            for future in as_completed(futures):
                results.append(future.result())
    results.sort(key=lambda row: str(row.get("case_id")))
    return results, time.perf_counter() - started


def _evaluate_cases_gpu_raw(cases: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from tools.gpu_scoring import score_profile_damage_rows

    profiles: list[dict[str, Any]] = []
    categories: list[str] = []
    for case in cases:
        category = _normalize_category(case.get("category"))
        clean = deepcopy(case["clean_profile"])
        challenged = deepcopy(case["challenged_profile"])
        clean["category"] = category
        challenged["category"] = category
        profiles.extend([clean, challenged])
        categories.extend([category, category])

    scores, stats = score_profile_damage_rows(
        profiles,
        categories=categories,
        use_gpu=True,
        hard_example_weights=_hard_example_weights(),
    )
    results: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        clean_damage = float(scores[index * 2]) if index * 2 < len(scores) else 0.0
        challenged_damage = float(scores[index * 2 + 1]) if index * 2 + 1 < len(scores) else 0.0
        trap_type = str(case.get("trap_type") or "trap")
        passed = challenged_damage > clean_damage if trap_type == "positive_control" else abs(challenged_damage - clean_damage) <= 0.001
        severity, ratio = _severity(clean_damage, challenged_damage, trap_type, passed)
        result = {
            "case_id": case.get("case_id", ""),
            "label": case.get("label", ""),
            "category": _normalize_category(case.get("category")),
            "trap_type": trap_type,
            "passed": passed,
            "clean_damage": round(clean_damage, 6),
            "trapped_damage": round(challenged_damage, 6),
            "damage_ratio": round(ratio, 6) if ratio else 0.0,
            "delta_type": _delta_type(clean_damage, challenged_damage),
            "severity": severity,
            "suggested_optimizer_rule": case.get("suggested_optimizer_rule") or RULE_BY_CATEGORY.get(_normalize_category(case.get("category")), ""),
            "regression_test_exists": bool(case.get("regression_test_exists")),
            "evaluation_backend": "gpu_batch",
        }
        if not passed and trap_type == "positive_control":
            result["suggested_optimizer_rule"] = "Do not pass anti-AI traps by ignoring all multipliers; active upgrades must increase damage."
        results.append(_content_check(case, result))
    results.sort(key=lambda row: str(row.get("case_id")))
    return results, stats


def _parity_ok(cpu_results: list[dict[str, Any]], gpu_results: list[dict[str, Any]]) -> bool:
    by_id = {str(row.get("case_id")): row for row in cpu_results}
    for gpu in gpu_results:
        cpu = by_id.get(str(gpu.get("case_id")))
        if not cpu:
            return False
        if abs(float(cpu.get("clean_damage", 0.0)) - float(gpu.get("clean_damage", 0.0))) > 0.01:
            return False
        if abs(float(cpu.get("trapped_damage", 0.0)) - float(gpu.get("trapped_damage", 0.0))) > 0.01:
            return False
        if bool(cpu.get("passed")) != bool(gpu.get("passed")):
            return False
    return True


def _evaluate_cases_batched(
    cases: list[dict[str, Any]], batch_size: int, cpu_workers: int, use_gpu: bool
) -> tuple[list[dict[str, Any]], float, dict[str, Any]]:
    if not use_gpu or not cases:
        results, elapsed = _evaluate_cases(cases, batch_size, cpu_workers)
        return results, elapsed, {"gpu_used": False, "gpu_rows_scored": 0, "gpu_batches_scored": 0, "parity_checked": False}

    parity_cases = cases[: min(25, len(cases))]
    cpu_parity, _ = _evaluate_cases(parity_cases, batch_size, cpu_workers)
    gpu_parity, parity_stats = _evaluate_cases_gpu_raw(parity_cases)
    if not parity_stats.get("gpu_used") or len(parity_cases) < 25 or not _parity_ok(cpu_parity, gpu_parity):
        results, elapsed = _evaluate_cases(cases, batch_size, cpu_workers)
        parity_stats.update({"gpu_used": False, "gpu_rows_scored": 0, "gpu_batches_scored": 0, "parity_checked": True, "parity_passed": False})
        return results, elapsed, parity_stats

    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    total_stats = {"gpu_used": True, "gpu_rows_submitted": 0, "gpu_rows_scored": 0, "gpu_batches_scored": 0, "gpu_seconds": 0.0, "parity_checked": True, "parity_passed": True}
    for batch in _chunks(cases, batch_size):
        batch_results, stats = _evaluate_cases_gpu_raw(batch)
        results.extend(batch_results)
        total_stats["gpu_rows_submitted"] += int(stats.get("gpu_rows_submitted", 0) or 0)
        total_stats["gpu_rows_scored"] += int(stats.get("gpu_rows_scored", 0) or 0)
        total_stats["gpu_batches_scored"] += int(stats.get("gpu_batches_scored", 0) or 0)
        total_stats["gpu_seconds"] += float(stats.get("gpu_seconds", 0.0) or 0.0)
    results.sort(key=lambda row: str(row.get("case_id")))
    return results, time.perf_counter() - started, total_stats


def _failure_case(round_no: int, case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    category = _normalize_category(result.get("category"))
    return {
        "case_id": _case_id("battle_failure", round_no, result.get("case_id"), result.get("damage_ratio"), result.get("severity")),
        "source": "adversarial_battle",
        "round": round_no,
        "category": category,
        "label": result.get("label", ""),
        "trap_type": result.get("trap_type", "trap"),
        "passed": False,
        "clean_damage": result.get("clean_damage", 0.0),
        "trapped_damage": result.get("trapped_damage", 0.0),
        "damage_ratio": result.get("damage_ratio", 0.0),
        "delta_type": result.get("delta_type", "unchanged"),
        "severity": result.get("severity", "unknown"),
        "suggested_optimizer_rule": result.get("suggested_optimizer_rule") or RULE_BY_CATEGORY.get(category, ""),
        "clean_profile": case.get("clean_profile"),
        "challenged_profile": case.get("challenged_profile"),
        "regression_test_exists": bool(result.get("regression_test_exists")),
    }


def _append_new_failure_cases(round_no: int, cases: list[dict[str, Any]], results: list[dict[str, Any]]) -> int:
    existing = _read_jsonl(ADVERSARIAL_CASES)
    existing_ids = {str(row.get("case_id")) for row in existing}
    case_by_id = {str(case.get("case_id")): case for case in cases}
    additions: list[dict[str, Any]] = []
    for result in results:
        if result.get("passed"):
            continue
        source_case = case_by_id.get(str(result.get("case_id")), {})
        row = _failure_case(round_no, source_case, result)
        if row["case_id"] not in existing_ids:
            existing_ids.add(row["case_id"])
            additions.append(row)
    if additions:
        _atomic_write_jsonl(ADVERSARIAL_CASES, existing + additions)
        _append_hard_examples(additions)
    return len(additions)


def _append_hard_examples(failures: list[dict[str, Any]]) -> None:
    from optimizer.profile_to_matrix_adapter import profiles_to_matrix

    existing = _read_jsonl(HARD_EXAMPLES)
    by_hash = {str(row.get("row_hash")): row for row in existing}
    for failure in failures:
        profile = failure.get("challenged_profile")
        if not isinstance(profile, dict):
            continue
        batch = profiles_to_matrix([profile], failure_categories=[_normalize_category(failure.get("category"))])
        row = [round(float(value), 6) for value in batch.matrix[0].tolist()]
        row_hash = _case_id("hard_row", row)
        previous = by_hash.get(row_hash, {})
        by_hash[row_hash] = {
            "row_hash": row_hash,
            "case_id": failure.get("case_id"),
            "category": _normalize_category(failure.get("category")),
            "severity": failure.get("severity"),
            "weight": min(10.0, float(previous.get("weight", 1.0) or 1.0) + 1.0),
            "numeric_row": row,
            "matrix_columns": list(batch.columns),
        }
    _atomic_write_jsonl(HARD_EXAMPLES, list(by_hash.values()))


def _top_rules(failures: list[dict[str, Any]], limit: int = 5) -> list[str]:
    counts: Counter[str] = Counter()
    for failure in failures:
        rule = str(failure.get("suggested_optimizer_rule") or RULE_BY_CATEGORY.get(_normalize_category(failure.get("category")), ""))
        if rule:
            counts[rule] += 1
    return [rule for rule, _ in counts.most_common(limit)]


def _bottleneck(cpu_generation_time: float, scoring_time: float, use_gpu: bool, gpu_rows_scored: int, target_reached: bool) -> str:
    if use_gpu and gpu_rows_scored == 0:
        return "No real GPU whole-profile scoring API was available; optimizer evaluation ran on CPU."
    if not target_reached:
        if scoring_time >= cpu_generation_time:
            return "Optimizer evaluation throughput is the current bottleneck."
        return "CPU trap generation is the current bottleneck."
    if scoring_time >= cpu_generation_time:
        return "Optimizer evaluation remains the largest time slice."
    return "CPU trap generation remains the largest time slice."


def _round_metrics(
    round_no: int,
    args: argparse.Namespace,
    cases: list[dict[str, Any]],
    results: list[dict[str, Any]],
    cpu_generation_time: float,
    scoring_time: float,
    new_cases_added: int,
    gpu_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    failures = [row for row in results if not row.get("passed")]
    profiles_tested = len(results)
    profiles_per_sec = profiles_tested / scoring_time if scoring_time > 0 else 0.0
    failure_categories = Counter(_normalize_category(row.get("category")) for row in failures)
    severity_counts = Counter(str(row.get("severity") or "unknown") for row in failures)
    target_reached = profiles_per_sec >= float(args.target_profiles_per_sec)
    gpu_stats = gpu_stats or {}
    gpu_rows_submitted = int(gpu_stats.get("gpu_rows_submitted", 0) or 0)
    gpu_rows_scored = int(gpu_stats.get("gpu_rows_scored", 0) or 0)
    gpu_idle_estimate = 1.0 if args.use_gpu and gpu_rows_scored == 0 else 0.0
    return {
        "round": round_no,
        "profiles_tested": profiles_tested,
        "profiles_per_sec": round(profiles_per_sec, 6),
        "target_profiles_per_sec": float(args.target_profiles_per_sec),
        "target_reached": target_reached,
        "gpu_rows_submitted": gpu_rows_submitted,
        "gpu_rows_scored": gpu_rows_scored,
        "gpu_batches_scored": int(gpu_stats.get("gpu_batches_scored", 0) or 0),
        "actual_evaluation_mode": "gpu_batch" if gpu_rows_scored > 0 else ("cpu_threaded" if int(args.cpu_workers) > 1 else "cpu_serial"),
        "gpu_backend_used": gpu_rows_scored > 0,
        "parity_checked": bool(gpu_stats.get("parity_checked")),
        "parity_passed": bool(gpu_stats.get("parity_passed")),
        "gpu_idle_estimate": gpu_idle_estimate,
        "cpu_generation_time": round(cpu_generation_time, 6),
        "gpu_scoring_time": round(scoring_time if gpu_rows_scored else 0.0, 6),
        "pass_count": profiles_tested - len(failures),
        "fail_count": len(failures),
        "new_cases_added": new_cases_added,
        "failure_categories": dict(sorted(failure_categories.items())),
        "severity_counts": dict(sorted(severity_counts.items())),
        "top_rules_to_fix": _top_rules(failures),
        "next_perf_bottleneck": _bottleneck(cpu_generation_time, scoring_time, bool(args.use_gpu), gpu_rows_scored, target_reached),
    }


def _summary(args: argparse.Namespace, round_rows: list[dict[str, Any]], fatal_error: str | None = None) -> dict[str, Any]:
    completed = len(round_rows)
    total_profiles = sum(int(row.get("profiles_tested", 0)) for row in round_rows)
    speeds = [float(row.get("profiles_per_sec", 0.0)) for row in round_rows]
    failure_categories: Counter[str] = Counter()
    severity_counts: Counter[str] = Counter()
    rules: Counter[str] = Counter()
    for row in round_rows:
        failure_categories.update({str(k): int(v) for k, v in (row.get("failure_categories") or {}).items()})
        severity_counts.update({str(k): int(v) for k, v in (row.get("severity_counts") or {}).items()})
        for rule in row.get("top_rules_to_fix") or []:
            rules[str(rule)] += 1
    return {
        "rounds_requested": int(args.rounds),
        "rounds_completed": completed,
        "total_profiles_tested": total_profiles,
        "best_profiles_per_sec": round(max(speeds), 6) if speeds else 0.0,
        "average_profiles_per_sec": round(sum(speeds) / len(speeds), 6) if speeds else 0.0,
        "target_profiles_per_sec": float(args.target_profiles_per_sec),
        "target_reached": any(bool(row.get("target_reached")) for row in round_rows),
        "gpu_idle_estimate": round(sum(float(row.get("gpu_idle_estimate", 0.0)) for row in round_rows) / len(round_rows), 6) if round_rows else 0.0,
        "total_failures_found": sum(int(row.get("fail_count", 0)) for row in round_rows),
        "total_new_cases_added": sum(int(row.get("new_cases_added", 0)) for row in round_rows),
        "remaining_failure_categories": dict(sorted(failure_categories.items())),
        "severity_counts": dict(sorted(severity_counts.items())),
        "top_rules_to_fix": [rule for rule, _ in rules.most_common(5)],
        "next_perf_bottleneck": str(round_rows[-1].get("next_perf_bottleneck", "")) if round_rows else "",
        "stopped_early": completed < int(args.rounds),
        "fatal_error": fatal_error,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run multi-round adversarial optimizer battles.")
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--max-generated-per-round", type=int, default=500)
    parser.add_argument("--target-profiles-per-sec", type=float, default=20.0)
    parser.add_argument("--use-gpu", action="store_true")
    parser.add_argument("--cpu-workers", type=int, default=4)
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tool_python = _python_for_subprocesses()
    training_module = _load_training_module()
    round_rows: list[dict[str, Any]] = []

    if BATTLE_ROUNDS.exists():
        BATTLE_ROUNDS.unlink()

    for round_no in range(1, max(0, int(args.rounds)) + 1):
        training_run = _run_training_subprocess(tool_python)
        training_summary = _read_json(TRAINING_SUMMARY)
        stored_cases = _read_jsonl(ADVERSARIAL_CASES)
        seed_cases = _load_seed_cases(training_module)
        active_categories = _active_failure_categories(training_summary, stored_cases)
        battle_cases, cpu_generation_time = _generate_battle_cases(
            seed_cases,
            active_categories,
            round_no,
            max(0, int(args.max_generated_per_round)),
            max(1, int(args.cpu_workers)),
        )
        results, scoring_time, gpu_stats = _evaluate_cases_batched(
            battle_cases,
            max(1, int(args.batch_size)),
            max(1, int(args.cpu_workers)),
            bool(args.use_gpu),
        )
        new_cases_added = _append_new_failure_cases(round_no, battle_cases, results)
        metrics = _round_metrics(round_no, args, battle_cases, results, cpu_generation_time, scoring_time, new_cases_added, gpu_stats)
        metrics["training_subprocess"] = training_run
        _atomic_append_jsonl(BATTLE_ROUNDS, metrics)
        round_rows.append(metrics)
        _atomic_write_json(BATTLE_SUMMARY, _summary(args, round_rows))

    _atomic_write_json(BATTLE_SUMMARY, _summary(args, round_rows))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except Exception as exc:
        existing_rounds = _read_jsonl(BATTLE_ROUNDS)
        fatal = f"{type(exc).__name__}: {exc}"
        _atomic_write_json(BATTLE_SUMMARY, _summary(args, existing_rounds, fatal_error=fatal))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())



def _optimizer_evidence_text(profile: dict[str, Any]) -> str:
    chunks: list[str] = []

    try:
        chunks.append(_text(_run_optimizer(profile)))
    except Exception as exc:
        chunks.append(f"optimizer_error={type(exc).__name__}:{exc}")

    try:
        from optimizer.damage_engine import estimate_damage_totals
        chunks.append(_text(estimate_damage_totals(profile)))
    except Exception as exc:
        chunks.append(f"damage_error={type(exc).__name__}:{exc}")

    try:
        from optimizer.rare_blocker_guardrails import rare_blockers_present
        chunks.append(_text(rare_blockers_present(profile)))
    except Exception as exc:
        chunks.append(f"guardrail_error={type(exc).__name__}:{exc}")

    return " ".join(chunks).lower()


