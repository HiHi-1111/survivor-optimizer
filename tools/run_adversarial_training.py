from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import types
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "training_outputs"
AUDIT_REPORT = OUT_DIR / "latest_anti_ai_audit_report.json"
ADVERSARIAL_CASES = OUT_DIR / "adversarial_cases.jsonl"
GENERATED_PROFILES = OUT_DIR / "generated_anti_ai_profiles.jsonl"
SUMMARY_PATH = OUT_DIR / "adversarial_training_summary.json"
TEST_MODULE_PATH = ROOT / "tests" / "test_optimizer_anti_ai_real_gameplay.py"
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
EVALUATION_RESULTS = OUT_DIR / ".adversarial_evaluation_results.json"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


TRAP_CATEGORY_BY_LABEL = {
    "unequipped_owned_gear_inventory": "unequipped gear",
    "locked_af_preview_on_equipped_weapon": "locked AF/Cosmic Cast preview",
    "future_ss_cosmic_cast_preview": "locked AF/Cosmic Cast preview",
    "unselected_survivor_roster": "unselected survivor roster",
    "inactive_twinborn_mode_same_pair": "inactive Twinborn mode",
    "unslotted_resonance_assist_candidates": "unslotted resonance assist",
    "unequipped_pet_inventory": "unequipped pet",
    "locked_collectible_next_breakpoint": "locked collectible breakpoint preview",
    "source_database_catalog_rows_not_player_state": "source/catalog rows mixed into player state",
    "event_shop_options_not_owned_until_bought": "unbought event shop item",
    "material_aliases_relic_core_awakening_core_yang_shard": "material aliases: Relic Core, S Awakening Core, Yang shard",
    "source_pack_multiplier_strings": "multiplier strings: 2.35x, 175%, +25%",
    "cheap_bait_vs_rare_blockers": "cheap bait vs rare blockers",
    "near_milestone_missing_core_and_shards": "near milestone missing both core and shards",
}


RULE_BY_CATEGORY = {
    "unequipped gear": "Only equipped gear slots may contribute to current damage; owned inventory copies are planning data.",
    "unselected survivor roster": "Only the selected active survivor may contribute current survivor damage.",
    "unequipped pet": "Only active main pet and equipped pet assists may contribute current damage.",
    "unslotted resonance assist": "Only slotted resonance assists may contribute current damage.",
    "inactive Twinborn mode": "Only the active Twinborn mode may contribute current damage.",
    "locked AF/Cosmic Cast preview": "Locked, missing-resource, preview, and future upgrade nodes must not change current damage.",
    "locked collectible breakpoint preview": "Only unlocked collectible bonuses count; next-breakpoint previews remain future goals.",
    "unbought event shop item": "Event shop options do not count until bought and applied to an active system.",
    "source/catalog rows mixed into player state": "Source, catalog, recommendation, and reference rows must never be treated as player-owned active bonuses.",
    "material aliases: Relic Core, S Awakening Core, Yang shard": "Normalize real material aliases into canonical blocker analysis without inventing current damage.",
    "multiplier strings: 2.35x, 175%, +25%": "Parse realistic multiplier strings consistently with numeric multipliers.",
    "cheap bait vs rare blockers": "Do not rank common low-tier materials above rare SS or awakening blockers at SS progression.",
    "near milestone missing both core and shards": "Near-awakening guidance must mention both missing core and missing shards when both block the milestone.",
}


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _case_id(*parts: Any) -> str:
    text = "|".join(str(part) for part in parts)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _run_command(cmd: list[str]) -> dict[str, Any]:
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return {
        "command": " ".join(cmd),
        "returncode": proc.returncode,
        "output": proc.stdout,
    }


def _python_for_subprocesses() -> str:
    return str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


def _load_test_fixtures() -> Any:
    if "pytest" not in sys.modules:
        fake_pytest = types.SimpleNamespace(
            mark=types.SimpleNamespace(
                parametrize=lambda *args, **kwargs: (lambda fn: fn),
            )
        )
        sys.modules["pytest"] = fake_pytest
    spec = importlib.util.spec_from_file_location("anti_ai_real_gameplay_fixtures", TEST_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load anti-AI fixtures from {TEST_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _optimizer() -> Callable[..., dict[str, Any]]:
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


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
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
    return json.dumps(_plain(value), default=str, sort_keys=True).lower()


def _severity(clean_damage: float, challenged_damage: float) -> tuple[str, float]:
    if clean_damage <= 0:
        return "unknown", 0.0
    ratio = challenged_damage / clean_damage
    if ratio >= 2.0:
        return "critical", ratio
    if ratio >= 1.25:
        return "high", ratio
    if ratio > 1.0:
        return "medium", ratio
    return "low", ratio


def _label_from_test_name(test_name: str) -> str:
    match = re.search(r"\[([^-]+(?:-[^-]+)*)-<lambda>\]", test_name)
    if match:
        return match.group(1)
    if "material_aliases" in test_name:
        return "material_aliases_relic_core_awakening_core_yang_shard"
    if "multiplier_strings" in test_name:
        return "source_pack_multiplier_strings"
    return test_name.split("::")[-1]


def _audit_failures_to_cases(audit: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for failure in audit.get("failures", []) or []:
        test_name = str(failure.get("test", ""))
        label = _label_from_test_name(test_name)
        category = TRAP_CATEGORY_BY_LABEL.get(label, str(failure.get("category") or "unclassified"))
        rows.append(
            {
                "case_id": _case_id("audit", test_name, category),
                "source": "latest_anti_ai_audit_report",
                "test": test_name,
                "category": category,
                "trap_type": "positive_control" if "FALSE-POSITIVE" in str(failure) else "trap",
                "passed": False,
                "clean_damage": failure.get("clean_damage"),
                "trapped_damage": failure.get("trapped_damage"),
                "inflation_ratio": failure.get("inflation_ratio"),
                "severity": failure.get("severity") or "unknown",
                "suggested_optimizer_rule": failure.get("training_lesson") or RULE_BY_CATEGORY.get(category, ""),
                "regression_test_exists": True,
            }
        )
    return rows


def _apply(profile: dict[str, Any], mutator: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    changed = deepcopy(profile)
    mutator(changed)
    return changed


def _extra_traps() -> list[tuple[str, Callable[[dict[str, Any]], None]]]:
    return [
        (
            "material_aliases_relic_core_awakening_core_yang_shard",
            lambda p: p["inventory"].update(
                {
                    "Relic Core": 0,
                    "Relic Core needed for SS AF": 1,
                    "S Awakening Core": 0,
                    "S Awakening Core needed": 1,
                    "Yang shard": 46,
                    "Yang shard needed": 50,
                }
            ),
        ),
        (
            "source_pack_multiplier_strings",
            lambda p: (
                p["gear"]["weapon"].update({"damage_multiplier": "2.35x"}),
                p["gear"]["belt"].update({"damage_multiplier": "175%"}),
                p["gear"]["necklace"].update({"damage_multiplier": "+45%"}),
                p["survivor"]["active"].update({"damage_multiplier": "2.10x"}),
                p["tech"]["drone"].update({"damage_multiplier": "2.25x"}),
                p["collectibles"]["owned_bonus"].update({"damage_multiplier": "220%"}),
            ),
        ),
        (
            "cheap_bait_vs_rare_blockers",
            lambda p: p["inventory"].update(
                {
                    "normal_salvage_cubes": 0,
                    "basic_gear_fodder": 0,
                    "purple_merge_items": 0,
                    "relic_cores": 0,
                    "needed_relic_cores_for_next_ss_af": 1,
                    "awakening_cores": 0,
                    "needed_awakening_cores_for_next_survivor_awakening": 1,
                }
            ),
        ),
        (
            "near_milestone_missing_core_and_shards",
            lambda p: (
                p["survivor"]["near_milestone"].update(
                    {
                        "milestone": "Yang Awakening 1",
                        "missing": {"S Awakening Core": 1, "Yang shard": 4},
                    }
                ),
                p["inventory"].update({"awakening_cores": 0, "s_survivor_shards": 46}),
            ),
        ),
    ]


def _generated_cases(fixtures: Any) -> list[dict[str, Any]]:
    base = fixtures._base_profile()
    cases: list[dict[str, Any]] = []

    for label, trap in list(fixtures.ANTI_AI_TRAPS) + _extra_traps():
        category = TRAP_CATEGORY_BY_LABEL.get(label, "unclassified")
        clean = deepcopy(base)
        challenged = _apply(base, trap)
        challenged["profile_name"] = f"Generated_Anti_AI_{label}"
        cases.append(
            {
                "case_id": _case_id("generated", "trap", label),
                "category": category,
                "label": label,
                "trap_type": "trap",
                "expected": "challenged profile must not increase current damage",
                "suggested_optimizer_rule": RULE_BY_CATEGORY.get(category, ""),
                "clean_profile": clean,
                "challenged_profile": challenged,
                "regression_test_exists": label in {item[0] for item in fixtures.ANTI_AI_TRAPS},
            }
        )

    for label, upgrade in fixtures.ACTIVE_UPGRADES:
        clean = deepcopy(base)
        challenged = _apply(base, upgrade)
        challenged["profile_name"] = f"Generated_Anti_AI_Positive_{label}"
        cases.append(
            {
                "case_id": _case_id("generated", "positive", label),
                "category": "positive control",
                "label": label,
                "trap_type": "positive_control",
                "expected": "challenged profile must increase current damage",
                "suggested_optimizer_rule": "Real active, equipped, unlocked, selected upgrades must still count.",
                "clean_profile": clean,
                "challenged_profile": challenged,
                "regression_test_exists": True,
            }
        )

    return cases


def _evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    clean_damage = _damage(_run_optimizer(case["clean_profile"]))
    challenged_damage = _damage(_run_optimizer(case["challenged_profile"]))

    trap_type = case["trap_type"]
    if trap_type == "positive_control":
        passed = challenged_damage > clean_damage
    else:
        passed = challenged_damage == clean_damage

    severity, ratio = _severity(clean_damage, challenged_damage)
    if trap_type == "positive_control" and not passed:
        severity = "high"
    if passed:
        severity = "pass"

    result = {
        "category": case["category"],
        "case_id": case["case_id"],
        "trap_type": trap_type,
        "passed": passed,
        "clean_damage": clean_damage,
        "trapped_damage": challenged_damage,
        "inflation_ratio": round(ratio, 6) if ratio else 0.0,
        "severity": severity,
        "suggested_optimizer_rule": case["suggested_optimizer_rule"],
        "regression_test_exists": bool(case.get("regression_test_exists")),
    }

    if not passed and trap_type == "positive_control":
        result["suggested_optimizer_rule"] = "Do not pass anti-AI traps by ignoring all multipliers; active upgrades must increase damage."
    return result


def _content_checks(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    if case["trap_type"] != "trap" or not result["passed"]:
        return result

    category = str(case.get("category", ""))
    output_text = _text(_run_optimizer(case["challenged_profile"]))

    if category.startswith("material aliases"):
        result["passed"] = all(term in output_text for term in ["relic", "awakening"]) and ("shard" in output_text or "yang" in output_text)
        if not result["passed"]:
            result["severity"] = "high"
    elif category == "cheap bait vs rare blockers":
        result["passed"] = "relic core" in output_text and "awakening core" in output_text
        if not result["passed"]:
            result["severity"] = "medium"
    elif category == "near milestone missing both core and shards":
        result["passed"] = "awakening" in output_text and ("shard" in output_text or "yang" in output_text)
        if not result["passed"]:
            result["severity"] = "medium"
    return result


def _evaluate_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in cases:
        try:
            result = _evaluate_case(case)
            result = _content_checks(case, result)
        except Exception as exc:
            result = {
                "category": case.get("category", "unclassified"),
                "case_id": case.get("case_id", ""),
                "trap_type": case.get("trap_type", "trap"),
                "passed": False,
                "clean_damage": 0.0,
                "trapped_damage": 0.0,
                "inflation_ratio": 0.0,
                "severity": "error",
                "suggested_optimizer_rule": f"Optimizer evaluation raised {type(exc).__name__}: {exc}",
                "regression_test_exists": bool(case.get("regression_test_exists")),
            }
        results.append(result)
    return results


def _evaluate_cases_with_tool_python(cases: list[dict[str, Any]], tool_python: str) -> list[dict[str, Any]]:
    if Path(tool_python).resolve() == Path(sys.executable).resolve() or not Path(tool_python).exists():
        return _evaluate_cases(cases)

    EVALUATION_RESULTS.unlink(missing_ok=True)
    proc = subprocess.run(
        [tool_python, str(Path(__file__).resolve()), "--evaluate-generated-only", str(EVALUATION_RESULTS)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if proc.returncode == 0 and EVALUATION_RESULTS.exists():
        payload = _read_json(EVALUATION_RESULTS)
        EVALUATION_RESULTS.unlink(missing_ok=True)
        results = payload.get("results")
        if isinstance(results, list):
            return [row for row in results if isinstance(row, dict)]

    fallback = _evaluate_cases(cases)
    for result in fallback:
        if result.get("severity") == "error":
            result["suggested_optimizer_rule"] = (
                f"{result.get('suggested_optimizer_rule', '')}; delegated venv evaluation failed "
                f"with return code {proc.returncode}: {proc.stdout[-500:]}"
            )
    return fallback


def _next_trap_to_add(results: list[dict[str, Any]]) -> str:
    missing = [
        result
        for result in results
        if not result["passed"] and not result.get("regression_test_exists")
    ]
    if not missing:
        return ""
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "unknown": 4, "error": 5}
    missing.sort(key=lambda row: (severity_order.get(str(row.get("severity")), 9), str(row.get("category"))))
    return str(missing[0].get("category") or "")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if len(sys.argv) >= 2 and sys.argv[1] == "--evaluate-generated-only":
        output_path = Path(sys.argv[2]) if len(sys.argv) >= 3 else EVALUATION_RESULTS
        cases = _read_jsonl(GENERATED_PROFILES)
        results = _evaluate_cases(cases)
        output_path.write_text(json.dumps({"results": results}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return 0

    tool_python = _python_for_subprocesses()
    pytest_run = _run_command([tool_python, "-m", "pytest", "-q", str(TEST_MODULE_PATH.relative_to(ROOT))])
    audit_run = _run_command([tool_python, str((ROOT / "tools" / "run_anti_ai_audit.py").relative_to(ROOT))])
    audit = _read_json(AUDIT_REPORT)

    audit_cases = _audit_failures_to_cases(audit)
    _write_jsonl(ADVERSARIAL_CASES, audit_cases)

    fixtures = _load_test_fixtures()
    generated_cases = _generated_cases(fixtures)
    _write_jsonl(GENERATED_PROFILES, generated_cases)

    evaluations = _evaluate_cases_with_tool_python(generated_cases, tool_python)
    failures = [result for result in evaluations if not result["passed"]]

    failure_categories: dict[str, int] = {}
    severity_counts: dict[str, int] = {}
    for result in failures:
        failure_categories[result["category"]] = failure_categories.get(result["category"], 0) + 1
        severity_counts[result["severity"]] = severity_counts.get(result["severity"], 0) + 1

    summary = {
        "total_tested": len(evaluations),
        "passed": len(evaluations) - len(failures),
        "failed": len(failures),
        "generated_cases_count": len(generated_cases),
        "failure_categories": failure_categories,
        "severity_counts": severity_counts,
        "failures": failures,
        "next_trap_to_add": _next_trap_to_add(evaluations),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "pytest": {
            "command": pytest_run["command"],
            "returncode": pytest_run["returncode"],
        },
        "audit": {
            "command": audit_run["command"],
            "returncode": audit_run["returncode"],
            "summary": audit.get("summary", {}),
        },
    }

    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    print(f"pytest return code: {pytest_run['returncode']}")
    print(f"audit return code: {audit_run['returncode']}")
    print(f"wrote {ADVERSARIAL_CASES.relative_to(ROOT)} ({len(audit_cases)} audit failure case(s))")
    print(f"wrote {GENERATED_PROFILES.relative_to(ROOT)} ({len(generated_cases)} generated case(s))")
    print(f"wrote {SUMMARY_PATH.relative_to(ROOT)} ({summary['failed']} generated failure(s))")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
