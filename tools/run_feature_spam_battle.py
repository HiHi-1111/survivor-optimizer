from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import run_adversarial_battle as battle  # noqa: E402


OUT_DIR = ROOT / "training_outputs"
ROUNDS_FILE = OUT_DIR / "feature_spam_battle_rounds.jsonl"
SUMMARY_FILE = OUT_DIR / "feature_spam_battle_summary.json"


SCENARIO_AXES = [
    {
        "name": "normal_180s",
        "goal_scenario": "normal",
        "battle_duration_seconds": 180,
        "progression_stage": "midgame",
        "steamroll_unlocked": False,
    },
    {
        "name": "steamroll_180s",
        "goal_scenario": "steamroll",
        "battle_duration_seconds": 180,
        "progression_stage": "ss_endgame",
        "steamroll_unlocked": True,
    },
    {
        "name": "boss_180s",
        "goal_scenario": "boss",
        "battle_duration_seconds": 180,
        "progression_stage": "ss_endgame",
        "steamroll_unlocked": True,
    },
    {
        "name": "elders_echo_180s",
        "goal_scenario": "elders_echo",
        "battle_duration_seconds": 180,
        "progression_stage": "ss_endgame",
        "steamroll_unlocked": True,
    },
    {
        "name": "short_60s",
        "goal_scenario": "burst",
        "battle_duration_seconds": 60,
        "progression_stage": "ss_endgame",
        "steamroll_unlocked": True,
    },
    {
        "name": "long_300s",
        "goal_scenario": "long_fight",
        "battle_duration_seconds": 300,
        "progression_stage": "ss_endgame",
        "steamroll_unlocked": True,
    },
    {
        "name": "cyclic_uptime_180s",
        "goal_scenario": "timed_cycle",
        "battle_duration_seconds": 180,
        "progression_stage": "ss_endgame",
        "steamroll_unlocked": True,
        "inject_timed_effect": True,
    },
]


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")


def _set_nested_stage(profile: dict[str, Any], axis: dict[str, Any]) -> None:
    profile["goal_scenario"] = axis["goal_scenario"]
    profile["battle_duration_seconds"] = float(axis["battle_duration_seconds"])

    player_stage = profile.setdefault("player_stage", {})
    if isinstance(player_stage, dict):
        player_stage["progression_stage"] = axis["progression_stage"]
        player_stage["steamroll_unlocked"] = bool(axis["steamroll_unlocked"])


def _inject_timed_effect(profile: dict[str, Any]) -> None:
    tech = profile.setdefault("tech", {})
    if not isinstance(tech, dict):
        return

    tech["anti_ai_timed_cycle_probe"] = {
        "name": "Anti-AI Timed Cycle Probe",
        "equipped": True,
        "active": True,
        "damage_multiplier": 2.0,
        "active_seconds": 10,
        "charge_seconds": 5,
        "off_seconds": 15,
        "cycle_seconds": 30,
        "note": "Should count as 10/30 uptime over battle duration, not permanent 2.0x.",
    }


def _apply_axis(case: dict[str, Any], axis: dict[str, Any]) -> dict[str, Any]:
    case = dict(case)
    case["scenario_axis"] = axis["name"]

    for key in ("clean_profile", "challenged_profile"):
        profile = case.get(key)
        if isinstance(profile, dict):
            _set_nested_stage(profile, axis)
            if axis.get("inject_timed_effect"):
                _inject_timed_effect(profile)

    return case


def _load_seed_cases() -> list[dict[str, Any]]:
    training_module = battle._load_training_module()
    seeds = battle._load_seed_cases(training_module)
    return [battle._normalize_case(row) for row in seeds]


def _balanced_sources(seed_cases: list[dict[str, Any]], round_no: int, max_count: int) -> list[dict[str, Any]]:
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    positives: list[dict[str, Any]] = []

    for case in seed_cases:
        category = battle._normalize_category(case.get("category"))
        by_category[category].append(case)
        if str(case.get("trap_type")) == "positive_control":
            positives.append(case)

    categories = sorted([name for name in by_category if name != "positive control"])
    if categories:
        shift = round_no % len(categories)
        categories = categories[shift:] + categories[:shift]

    selected: list[dict[str, Any]] = []

    positive_quota = max(1, int(max_count * 0.15))
    if positives:
        for i in range(positive_quota):
            selected.append(positives[i % len(positives)])

    # Round-robin every category so Anti-AI looks at all feature families.
    idx = 0
    offsets = {name: 0 for name in categories}
    while len(selected) < max_count and categories:
        name = categories[idx % len(categories)]
        bucket = by_category[name]
        offset = offsets[name] % len(bucket)
        selected.append(bucket[offset])
        offsets[name] += 1
        idx += 1

    return selected[:max_count]


def _make_cases(seed_cases: list[dict[str, Any]], round_no: int, max_count: int) -> list[dict[str, Any]]:
    sources = _balanced_sources(seed_cases, round_no, max_count)
    cases: list[dict[str, Any]] = []

    for i, source in enumerate(sources):
        variant = battle._profile_variant(source, round_no, i)
        axis = SCENARIO_AXES[(round_no + i) % len(SCENARIO_AXES)]
        variant = _apply_axis(variant, axis)
        cases.append(variant)

    cases.sort(key=lambda row: str(row.get("case_id", "")))
    return cases


def _summarize(rounds: list[dict[str, Any]], fatal_error: str | None = None) -> dict[str, Any]:
    failures = Counter()
    axes = Counter()
    categories_seen = Counter()

    for row in rounds:
        failures.update(row.get("failure_categories") or {})
        axes.update(row.get("scenario_axes") or {})
        categories_seen.update(row.get("categories_tested") or {})

    return {
        "fatal_error": fatal_error,
        "rounds_completed": len(rounds),
        "total_profiles_tested": sum(int(row.get("profiles_tested", 0)) for row in rounds),
        "total_failures_found": sum(int(row.get("fail_count", 0)) for row in rounds),
        "total_passed": sum(int(row.get("pass_count", 0)) for row in rounds),
        "remaining_failure_categories": dict(sorted(failures.items())),
        "scenario_axes_tested": dict(sorted(axes.items())),
        "categories_tested": dict(sorted(categories_seen.items())),
        "latest_accuracy": rounds[-1].get("accuracy") if rounds else 0.0,
        "latest_top_fail": rounds[-1].get("top_fail") if rounds else "none",
        "gpu_rows_scored": sum(int(row.get("gpu_rows_scored", 0)) for row in rounds),
    }


def run(args: argparse.Namespace) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ROUNDS_FILE.unlink(missing_ok=True)

    seed_cases = _load_seed_cases()
    if not seed_cases:
        raise RuntimeError("No seed cases loaded.")

    print(f"Loaded seed cases: {len(seed_cases)}", flush=True)
    print(f"Scenario axes: {', '.join(axis['name'] for axis in SCENARIO_AXES)}", flush=True)

    deadline = time.time() + float(args.minutes) * 60.0
    rounds: list[dict[str, Any]] = []
    round_no = 0

    while time.time() < deadline and round_no < int(args.max_rounds):
        round_no += 1
        started = time.perf_counter()

        cases = _make_cases(seed_cases, round_no, int(args.max_cases_per_round))
        scenario_axes = Counter(str(case.get("scenario_axis", "unknown")) for case in cases)
        categories_tested = Counter(battle._normalize_category(case.get("category")) for case in cases)

        if args.use_gpu:
            results, scoring_time, gpu_stats = battle._evaluate_cases_batched(
                cases,
                max(1, int(args.batch_size)),
                max(1, int(args.cpu_workers)),
                True,
            )
        else:
            results, scoring_time = battle._evaluate_cases(
                cases,
                max(1, int(args.batch_size)),
                max(1, int(args.cpu_workers)),
            )
            gpu_stats = {"gpu_rows_scored": 0, "gpu_rows_submitted": 0, "gpu_batches_scored": 0}

        failures_list = [row for row in results if not row.get("passed")]
        failure_categories = Counter(battle._normalize_category(row.get("category")) for row in failures_list)
        new_cases = battle._append_new_failure_cases(round_no, cases, results)

        wall = time.perf_counter() - started
        tested = len(results)
        passed = tested - len(failures_list)
        failed = len(failures_list)
        accuracy = passed / max(1, tested)
        top_fail = failure_categories.most_common(1)[0][0] if failure_categories else "none"

        payload = {
            "round": round_no,
            "profiles_tested": tested,
            "pass_count": passed,
            "fail_count": failed,
            "accuracy": round(accuracy, 6),
            "failure_categories": dict(sorted(failure_categories.items())),
            "categories_tested": dict(sorted(categories_tested.items())),
            "scenario_axes": dict(sorted(scenario_axes.items())),
            "gpu_rows_scored": int(gpu_stats.get("gpu_rows_scored", 0) or 0),
            "gpu_rows_submitted": int(gpu_stats.get("gpu_rows_submitted", 0) or 0),
            "gpu_batches_scored": int(gpu_stats.get("gpu_batches_scored", 0) or 0),
            "new_cases_added": new_cases,
            "top_fail": top_fail,
            "wall_seconds": round(wall, 6),
            "profiles_per_sec_wall": round(tested / wall, 6) if wall else 0.0,
            "profiles_per_sec_scoring_only": round(tested / scoring_time, 6) if scoring_time else 0.0,
        }

        rounds.append(payload)
        _append_jsonl(ROUNDS_FILE, payload)
        _atomic_write_json(SUMMARY_FILE, _summarize(rounds))

        print(
            f"Round {round_no} | tested={tested} | pass={passed} | fail={failed} | "
            f"acc={accuracy*100:.1f}% | top_fail={top_fail} | "
            f"categories={len(categories_tested)} | axes={len(scenario_axes)} | "
            f"gpu_rows={payload['gpu_rows_scored']} | new_cases={new_cases}",
            flush=True,
        )

        # If fixer wins, stop.
        if failed == 0:
            print("FIXER WON: zero failures.", flush=True)
            break

    _atomic_write_json(SUMMARY_FILE, _summarize(rounds))
    print(f"Saved summary: {SUMMARY_FILE}", flush=True)
    print(f"Saved rounds: {ROUNDS_FILE}", flush=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Feature-spam adversarial scenario battle.")
    parser.add_argument("--minutes", type=float, default=480.0)
    parser.add_argument("--max-rounds", type=int, default=10000)
    parser.add_argument("--max-cases-per-round", type=int, default=700)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--cpu-workers", type=int, default=4)
    parser.add_argument("--use-gpu", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
