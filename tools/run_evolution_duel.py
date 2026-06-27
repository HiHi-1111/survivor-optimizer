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

from optimizer.knowledge_compactor import compact_case, profile_size_score  # noqa: E402
from tools import run_adversarial_battle as battle  # noqa: E402


OUT_DIR = ROOT / "training_outputs"
ROUNDS_FILE = OUT_DIR / "evolution_duel_rounds.jsonl"
SUMMARY_FILE = OUT_DIR / "evolution_duel_summary.json"
LESSONS_FILE = OUT_DIR / "evolution_duel_lessons.json"


SCENARIO_AXES = [
    {"name": "normal_180s", "goal_scenario": "normal", "battle_duration_seconds": 180, "progression_stage": "midgame", "steamroll_unlocked": False},
    {"name": "steamroll_180s", "goal_scenario": "steamroll", "battle_duration_seconds": 180, "progression_stage": "ss_endgame", "steamroll_unlocked": True},
    {"name": "boss_180s", "goal_scenario": "boss", "battle_duration_seconds": 180, "progression_stage": "ss_endgame", "steamroll_unlocked": True},
    {"name": "elders_echo_180s", "goal_scenario": "elders_echo", "battle_duration_seconds": 180, "progression_stage": "ss_endgame", "steamroll_unlocked": True},
    {"name": "short_60s", "goal_scenario": "burst", "battle_duration_seconds": 60, "progression_stage": "ss_endgame", "steamroll_unlocked": True},
    {"name": "long_300s", "goal_scenario": "long_fight", "battle_duration_seconds": 300, "progression_stage": "ss_endgame", "steamroll_unlocked": True},
    {"name": "cyclic_uptime_180s", "goal_scenario": "timed_cycle", "battle_duration_seconds": 180, "progression_stage": "ss_endgame", "steamroll_unlocked": True, "inject_timed_effect": True},
]


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=str) + chr(10))


def _load_lessons() -> dict[str, Any]:
    if not LESSONS_FILE.exists():
        return {
            "version": 1,
            "fixer_lessons": {},
            "enemy_lessons": {},
            "compression_lessons": {},
            "optimizer_wins": 0,
            "enemy_wins": 0,
        }
    try:
        return json.loads(LESSONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {
            "version": 1,
            "fixer_lessons": {},
            "enemy_lessons": {},
            "compression_lessons": {},
            "optimizer_wins": 0,
            "enemy_wins": 0,
        }


def _save_lessons(lessons: dict[str, Any]) -> None:
    lessons["updated_at"] = time.time()
    _atomic_write_json(LESSONS_FILE, lessons)


def _load_seed_cases() -> list[dict[str, Any]]:
    training_module = battle._load_training_module()
    seeds = battle._load_seed_cases(training_module)
    return [battle._normalize_case(row) for row in seeds]


def _apply_axis(case: dict[str, Any], axis: dict[str, Any]) -> dict[str, Any]:
    copied = dict(case)
    copied["scenario_axis"] = axis["name"]

    for key in ("clean_profile", "challenged_profile"):
        profile = copied.get(key)
        if not isinstance(profile, dict):
            continue

        profile["goal_scenario"] = axis["goal_scenario"]
        profile["battle_duration_seconds"] = float(axis["battle_duration_seconds"])

        stage = profile.setdefault("player_stage", {})
        if isinstance(stage, dict):
            stage["progression_stage"] = axis["progression_stage"]
            stage["steamroll_unlocked"] = bool(axis["steamroll_unlocked"])

        if axis.get("inject_timed_effect"):
            tech = profile.setdefault("tech", {})
            if isinstance(tech, dict):
                tech["anti_ai_timed_cycle_probe"] = {
                    "name": "Anti-AI Timed Cycle Probe",
                    "equipped": True,
                    "active": True,
                    "damage_multiplier": 2.0,
                    "active_seconds": 10,
                    "charge_seconds": 5,
                    "off_seconds": 15,
                    "cycle_seconds": 30,
                }

    return copied


def _balanced_sources(seed_cases: list[dict[str, Any]], lessons: dict[str, Any], round_no: int, max_count: int) -> list[dict[str, Any]]:
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    positives: list[dict[str, Any]] = []

    for case in seed_cases:
        category = battle._normalize_category(case.get("category"))
        by_category[category].append(case)
        if str(case.get("trap_type")) == "positive_control":
            positives.append(case)

    categories = sorted([name for name in by_category if name != "positive control"])
    enemy_lessons = lessons.get("enemy_lessons", {}) or {}

    def enemy_interest(name: str) -> tuple[float, str]:
        data = enemy_lessons.get(name, {}) or {}
        novelty = 1.0 / (1.0 + float(data.get("times_tested", 0) or 0) * 0.03)
        wins = float(data.get("enemy_wins", 0) or 0)
        recent_fixed = float(data.get("recent_fixed_by_optimizer", 0) or 0)
        return (novelty + wins * 0.02 + recent_fixed * 0.1, name)

    categories = sorted(categories, key=enemy_interest, reverse=True)
    if categories:
        shift = round_no % len(categories)
        categories = categories[shift:] + categories[:shift]

    selected: list[dict[str, Any]] = []

    positive_quota = max(1, int(max_count * 0.12))
    if positives:
        for i in range(positive_quota):
            selected.append(positives[(round_no + i) % len(positives)])

    offsets = {name: round_no % max(1, len(by_category[name])) for name in categories}
    idx = 0
    while len(selected) < max_count and categories:
        name = categories[idx % len(categories)]
        bucket = by_category[name]
        selected.append(bucket[offsets[name] % len(bucket)])
        offsets[name] += 1
        idx += 1

    return selected[:max_count]


def _make_cases(seed_cases: list[dict[str, Any]], lessons: dict[str, Any], round_no: int, max_count: int) -> list[dict[str, Any]]:
    sources = _balanced_sources(seed_cases, lessons, round_no, max_count)
    out: list[dict[str, Any]] = []

    for i, source in enumerate(sources):
        variant = battle._profile_variant(source, round_no, i)
        axis = SCENARIO_AXES[(round_no + i) % len(SCENARIO_AXES)]
        variant = _apply_axis(variant, axis)
        out.append(variant)

    out.sort(key=lambda row: str(row.get("case_id", "")))
    return out


def _evaluate(cases: list[dict[str, Any]], args: argparse.Namespace) -> tuple[list[dict[str, Any]], float, dict[str, Any]]:
    if args.use_gpu:
        return battle._evaluate_cases_batched(
            cases,
            max(1, int(args.batch_size)),
            max(1, int(args.cpu_workers)),
            True,
        )
    results, seconds = battle._evaluate_cases(
        cases,
        max(1, int(args.batch_size)),
        max(1, int(args.cpu_workers)),
    )
    return results, seconds, {"gpu_rows_scored": 0, "gpu_rows_submitted": 0, "gpu_batches_scored": 0}


def _compression_regressions(full_results: list[dict[str, Any]], compact_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    regressions: list[dict[str, Any]] = []
    for index, (full, compact) in enumerate(zip(full_results, compact_results)):
        if full.get("passed") and not compact.get("passed"):
            row = dict(compact)
            row["category"] = "compression removed required damage/blocker fact"
            row["severity"] = "high"
            row["regression_index"] = index
            row["full_passed"] = True
            row["compact_passed"] = False
            regressions.append(row)
    return regressions


def _learn_from_round(lessons: dict[str, Any], full_failures: list[dict[str, Any]], compression_failures: list[dict[str, Any]], categories_tested: Counter[str]) -> dict[str, Any]:
    fixer = lessons.setdefault("fixer_lessons", {})
    enemy = lessons.setdefault("enemy_lessons", {})
    compression = lessons.setdefault("compression_lessons", {})

    failure_counts = Counter(battle._normalize_category(row.get("category")) for row in full_failures)
    compression_counts = Counter(battle._normalize_category(row.get("category")) for row in compression_failures)

    for category, count in categories_tested.items():
        data = enemy.setdefault(category, {})
        data["times_tested"] = int(data.get("times_tested", 0) or 0) + int(count)

    for category, count in failure_counts.items():
        data = fixer.setdefault(category, {})
        data["times_failed"] = int(data.get("times_failed", 0) or 0) + int(count)
        data["last_lesson"] = _lesson_for_category(category)
        data["status"] = "needs_code_or_rule_fix"

        enemy_data = enemy.setdefault(category, {})
        enemy_data["enemy_wins"] = int(enemy_data.get("enemy_wins", 0) or 0) + int(count)

    for category, count in compression_counts.items():
        data = compression.setdefault(category, {})
        data["times_failed"] = int(data.get("times_failed", 0) or 0) + int(count)
        data["last_lesson"] = "Compactor removed something required. Keep current active damage, blocker, timing, shard/core, rarity, and scenario fields."

    if not full_failures and not compression_failures:
        lessons["optimizer_wins"] = int(lessons.get("optimizer_wins", 0) or 0) + 1
    else:
        lessons["enemy_wins"] = int(lessons.get("enemy_wins", 0) or 0) + 1

    return lessons


def _lesson_for_category(category: str) -> str:
    if category == "cheap material bait vs rare blockers":
        return "At SS/endgame, relic cores, awakening cores, survivor shards, resonance chips, and true blockers must outrank common fodder/materials."
    if "timed" in category or "cycle" in category:
        return "Use 180s battle uptime math, not permanent uptime."
    if "locked" in category or "preview" in category:
        return "Ignore locked/preview/future rows."
    if "unequipped" in category or "unselected" in category or "inactive" in category:
        return "Ignore inactive or unequipped rows."
    return "Preserve only current valid damage math and prove the recommendation with total_damage/final_multiplier/blockers."


def _summarize(rounds: list[dict[str, Any]], lessons: dict[str, Any], fatal_error: str | None = None) -> dict[str, Any]:
    failures = Counter()
    compression_failures = Counter()
    categories = Counter()
    axes = Counter()

    for row in rounds:
        failures.update(row.get("failure_categories") or {})
        compression_failures.update(row.get("compression_failure_categories") or {})
        categories.update(row.get("categories_tested") or {})
        axes.update(row.get("scenario_axes") or {})

    return {
        "fatal_error": fatal_error,
        "rounds_completed": len(rounds),
        "total_profiles_tested": sum(int(row.get("profiles_tested", 0)) for row in rounds),
        "total_failures_found": sum(int(row.get("fail_count", 0)) for row in rounds),
        "total_compression_regressions": sum(int(row.get("compression_fail_count", 0)) for row in rounds),
        "remaining_failure_categories": dict(sorted(failures.items())),
        "compression_failure_categories": dict(sorted(compression_failures.items())),
        "categories_tested": dict(sorted(categories.items())),
        "scenario_axes_tested": dict(sorted(axes.items())),
        "latest_accuracy": rounds[-1].get("accuracy") if rounds else 0.0,
        "latest_compact_accuracy": rounds[-1].get("compact_accuracy") if rounds else 0.0,
        "latest_top_fail": rounds[-1].get("top_fail") if rounds else "none",
        "average_size_reduction": round(sum(float(row.get("size_reduction", 0.0)) for row in rounds) / len(rounds), 6) if rounds else 0.0,
        "optimizer_wins": lessons.get("optimizer_wins", 0),
        "enemy_wins": lessons.get("enemy_wins", 0),
        "lessons_file": str(LESSONS_FILE),
    }


def run(args: argparse.Namespace) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ROUNDS_FILE.unlink(missing_ok=True)

    seed_cases = _load_seed_cases()
    lessons = _load_lessons()

    print(f"Loaded seed cases: {len(seed_cases)}", flush=True)
    print(f"Scenario axes: {', '.join(axis['name'] for axis in SCENARIO_AXES)}", flush=True)
    print("Evolution loop: enemy attacks full data, optimizer compacts, enemy attacks compacted data.", flush=True)

    deadline = time.time() + float(args.minutes) * 60.0
    rounds: list[dict[str, Any]] = []
    round_no = 0

    while time.time() < deadline and round_no < int(args.max_rounds):
        round_no += 1
        started = time.perf_counter()

        full_cases = _make_cases(seed_cases, lessons, round_no, int(args.max_cases_per_round))
        compact_cases = [compact_case(case) for case in full_cases]

        full_size = sum(profile_size_score(case.get("challenged_profile", {})) for case in full_cases)
        compact_size = sum(profile_size_score(case.get("challenged_profile", {})) for case in compact_cases)
        size_reduction = 1.0 - (compact_size / max(1, full_size))

        categories_tested = Counter(battle._normalize_category(case.get("category")) for case in full_cases)
        scenario_axes = Counter(str(case.get("scenario_axis", "unknown")) for case in full_cases)

        full_results, full_scoring, full_gpu = _evaluate(full_cases, args)
        compact_results, compact_scoring, compact_gpu = _evaluate(compact_cases, args)

        full_failures = [row for row in full_results if not row.get("passed")]
        compact_failures = [row for row in compact_results if not row.get("passed")]
        compression_failures = _compression_regressions(full_results, compact_results)

        failure_categories = Counter(battle._normalize_category(row.get("category")) for row in full_failures)
        compression_categories = Counter(battle._normalize_category(row.get("category")) for row in compression_failures)

        battle._append_new_failure_cases(round_no, full_cases, full_results)
        battle._append_new_failure_cases(round_no, compact_cases, compact_results)

        lessons = _learn_from_round(lessons, full_failures, compression_failures, categories_tested)
        _save_lessons(lessons)

        tested = len(full_results)
        passed = tested - len(full_failures)
        failed = len(full_failures)
        compact_tested = len(compact_results)
        compact_passed = compact_tested - len(compact_failures)

        accuracy = passed / max(1, tested)
        compact_accuracy = compact_passed / max(1, compact_tested)
        top_fail = failure_categories.most_common(1)[0][0] if failure_categories else "none"

        wall = time.perf_counter() - started
        payload = {
            "round": round_no,
            "profiles_tested": tested,
            "pass_count": passed,
            "fail_count": failed,
            "accuracy": round(accuracy, 6),
            "compact_accuracy": round(compact_accuracy, 6),
            "compression_fail_count": len(compression_failures),
            "failure_categories": dict(sorted(failure_categories.items())),
            "compression_failure_categories": dict(sorted(compression_categories.items())),
            "categories_tested": dict(sorted(categories_tested.items())),
            "scenario_axes": dict(sorted(scenario_axes.items())),
            "top_fail": top_fail,
            "size_reduction": round(size_reduction, 6),
            "full_chars": full_size,
            "compact_chars": compact_size,
            "gpu_rows_scored": int(full_gpu.get("gpu_rows_scored", 0) or 0) + int(compact_gpu.get("gpu_rows_scored", 0) or 0),
            "wall_seconds": round(wall, 6),
            "profiles_per_sec_wall": round((tested + compact_tested) / wall, 6) if wall else 0.0,
            "full_scoring_seconds": round(full_scoring, 6),
            "compact_scoring_seconds": round(compact_scoring, 6),
        }

        rounds.append(payload)
        _append_jsonl(ROUNDS_FILE, payload)
        _atomic_write_json(SUMMARY_FILE, _summarize(rounds, lessons))

        print(
            f"Round {round_no} | full={passed}/{tested} acc={accuracy*100:.1f}% | "
            f"compact={compact_passed}/{compact_tested} acc={compact_accuracy*100:.1f}% | "
            f"compression_regressions={len(compression_failures)} | "
            f"size_cut={size_reduction*100:.1f}% | top_fail={top_fail} | "
            f"categories={len(categories_tested)} | axes={len(scenario_axes)} | "
            f"gpu_rows={payload['gpu_rows_scored']}",
            flush=True,
        )

        if failed == 0 and len(compression_failures) == 0:
            print("OPTIMIZER WINS: zero full-data failures and zero compression regressions.", flush=True)
            break

    _atomic_write_json(SUMMARY_FILE, _summarize(rounds, lessons))
    print(f"Saved summary: {SUMMARY_FILE}", flush=True)
    print(f"Saved rounds: {ROUNDS_FILE}", flush=True)
    print(f"Saved lessons: {LESSONS_FILE}", flush=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evolution duel: fixer learns, optimizer compacts, enemy attacks compacted data.")
    parser.add_argument("--minutes", type=float, default=480.0)
    parser.add_argument("--max-rounds", type=int, default=10000)
    parser.add_argument("--max-cases-per-round", type=int, default=700)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--cpu-workers", type=int, default=4)
    parser.add_argument("--use-gpu", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
