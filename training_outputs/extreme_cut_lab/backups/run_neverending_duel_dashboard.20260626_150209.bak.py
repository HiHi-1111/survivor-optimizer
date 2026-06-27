from __future__ import annotations

# ONLY_VENV_PYTHON_GUARD
import sys as _survivor_guard_sys
from pathlib import Path as _survivor_guard_Path

_exe = str(_survivor_guard_Path(_survivor_guard_sys.executable)).lower()
if "\\.venv\\scripts\\python.exe" not in _exe:
    print(f"REFUSING TO RUN: wrong Python executable: {_survivor_guard_sys.executable}", flush=True)
    print("Use .venv\\Scripts\\python.exe only.", flush=True)
    raise SystemExit(88)

import argparse
import copy
import html
import json
import os
import random
import sys
import time
from collections import Counter, deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from optimizer.knowledge_compactor import compact_case, profile_size_score
from tools import run_adversarial_battle as battle


OUT_DIR = ROOT / "training_outputs"
SUMMARY_FILE = OUT_DIR / "neverending_duel_summary.json"
ROUNDS_FILE = OUT_DIR / "neverending_duel_rounds.json"
LESSONS_FILE = OUT_DIR / "neverending_duel_lessons.json"
DASHBOARD_FILE = OUT_DIR / "neverending_duel_dashboard.html"

SCENARIO_AXES = [
    {"name": "normal_180s", "goal_scenario": "normal", "battle_duration_seconds": 180, "progression_stage": "midgame", "steamroll_unlocked": False},
    {"name": "steamroll_180s", "goal_scenario": "steamroll", "battle_duration_seconds": 180, "progression_stage": "ss_endgame", "steamroll_unlocked": True},
    {"name": "boss_180s", "goal_scenario": "boss", "battle_duration_seconds": 180, "progression_stage": "ss_endgame", "steamroll_unlocked": True},
    {"name": "elders_echo_180s", "goal_scenario": "elders_echo", "battle_duration_seconds": 180, "progression_stage": "ss_endgame", "steamroll_unlocked": True},
    {"name": "short_60s", "goal_scenario": "burst", "battle_duration_seconds": 60, "progression_stage": "ss_endgame", "steamroll_unlocked": True},
    {"name": "long_300s", "goal_scenario": "long_fight", "battle_duration_seconds": 300, "progression_stage": "ss_endgame", "steamroll_unlocked": True},
    {"name": "cyclic_uptime_180s", "goal_scenario": "timed_cycle", "battle_duration_seconds": 180, "progression_stage": "ss_endgame", "steamroll_unlocked": True, "inject_timed_effect": True},
    {"name": "dirty_profile_180s", "goal_scenario": "dirty_profile", "battle_duration_seconds": 180, "progression_stage": "ss_endgame", "steamroll_unlocked": True, "inject_junk": True},
    {"name": "alias_trap_180s", "goal_scenario": "alias_trap", "battle_duration_seconds": 180, "progression_stage": "ss_endgame", "steamroll_unlocked": True, "inject_aliases": True},
]


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _load_seed_cases() -> list[dict[str, Any]]:
    training_module = battle._load_training_module()
    seeds = battle._load_seed_cases(training_module)
    return [battle._normalize_case(row) for row in seeds]


def _apply_axis(case: dict[str, Any], axis: dict[str, Any], rng: random.Random, difficulty: int) -> dict[str, Any]:
    case = copy.deepcopy(case)
    case["scenario_axis"] = axis["name"]
    case["enemy_difficulty"] = difficulty

    for key in ("clean_profile", "challenged_profile"):
        profile = case.get(key)
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
                tech[f"enemy_timed_cycle_probe_d{difficulty}"] = {
                    "name": "Enemy Timed Cycle Probe",
                    "equipped": True,
                    "active": True,
                    "damage_multiplier": 1.4 + (0.08 * difficulty),
                    "active_seconds": max(3, 12 - difficulty),
                    "charge_seconds": 5 + difficulty,
                    "off_seconds": 12 + difficulty,
                    "cycle_seconds": 30 + difficulty,
                    "note": "Anti-AI checks that cycle uptime is not treated as permanent uptime.",
                }

        if axis.get("inject_junk"):
            profile[f"enemy_fake_catalog_d{difficulty}"] = [
                {
                    "name": f"Locked Fake Damage Relic {i}",
                    "damage_multiplier": 999,
                    "equipped": False,
                    "active": False,
                    "unlocked": False,
                    "owned": False,
                    "source": "enemy_junk_catalog",
                }
                for i in range(1, 1 + min(30, 5 + difficulty * 2))
            ]

        if axis.get("inject_aliases"):
            profile[f"enemy_alias_blockers_d{difficulty}"] = {
                "Relic-Core": rng.choice(["Relic Core", "relic_core", "RELIC CORE", "relic-core"]),
                "S-Awakening-Core": rng.choice(["S Awakening Core", "awakening_core", "AWAKENING CORE", "awakening-core"]),
                "YangShardAlias": rng.choice(["Yang shard", "survivor_shard", "S shard"]),
                "ResonanceChipAlias": rng.choice(["resonance chip", "resonance_chip"]),
                "bait": rng.choice(["normal_salvage", "basic_gear_fodder", "generic_fodder", "purple_merge"]),
                "lesson": "Rare blockers must stay above common bait even under alias/noisy profile mutation.",
            }

    return case


def _aggressive_compact_value(value: Any) -> Any:
    keep_terms = (
        "damage", "multiplier", "atk", "attack", "dps",
        "equipped", "selected", "active", "slotted", "unlocked", "owned",
        "relic", "core", "awakening", "shard", "resonance", "blocker",
        "rarity", "tier", "level", "star", "progression", "steamroll",
        "duration", "seconds", "uptime", "cycle", "cooldown", "charge",
        "goal_scenario", "battle_duration_seconds", "player_stage",
        "name", "id", "item_id", "system", "category", "type",
    )
    drop_terms = (
        "source", "catalog", "preview", "future", "raw", "wiki", "notes_dump",
        "image", "screenshot", "description_long", "unused",
    )

    if isinstance(value, list):
        out = []
        for item in value:
            c = _aggressive_compact_value(item)
            if c not in ({}, [], None):
                out.append(c)
        return out

    if isinstance(value, dict):
        if any(value.get(k) is False for k in ("equipped", "selected", "active", "slotted", "unlocked", "owned")):
            return {}

        out = {}
        for k, v in value.items():
            key = str(k).lower()
            if any(term in key for term in drop_terms):
                continue

            c = _aggressive_compact_value(v)
            if c in ({}, [], None):
                continue

            if any(term in key for term in keep_terms):
                out[k] = c
            elif isinstance(c, (dict, list)) and c:
                out[k] = c

        return out

    return value


def _aggressive_compact_case(case: dict[str, Any]) -> dict[str, Any]:
    c = copy.deepcopy(case)
    for key in ("clean_profile", "challenged_profile"):
        if isinstance(c.get(key), dict):
            c[key] = _aggressive_compact_value(c[key])
    c["compression_applied"] = "aggressive"
    return c


def _balanced_cases(seed_cases: list[dict[str, Any]], round_no: int, max_cases: int, difficulty: int) -> list[dict[str, Any]]:
    by_category: dict[str, list[dict[str, Any]]] = {}
    for case in seed_cases:
        category = battle._normalize_category(case.get("category"))
        by_category.setdefault(category, []).append(case)

    categories = sorted(by_category)
    if categories:
        shift = round_no % len(categories)
        categories = categories[shift:] + categories[:shift]

    cases = []
    rng = random.Random(9000 + round_no)
    idx = 0

    while len(cases) < max_cases and categories:
        category = categories[idx % len(categories)]
        bucket = by_category[category]
        source = bucket[(round_no + idx) % len(bucket)]
        variant = battle._profile_variant(source, round_no, idx)

        axis = SCENARIO_AXES[(round_no + idx + difficulty) % len(SCENARIO_AXES)]
        variant = _apply_axis(variant, axis, rng, difficulty)
        variant["case_id"] = f"{variant.get('case_id', 'case')}::never::{round_no}::{idx}"
        cases.append(variant)
        idx += 1

    return cases


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


def _regressions(full: list[dict[str, Any]], compact: list[dict[str, Any]], label: str) -> list[dict[str, Any]]:
    out = []
    for i, (a, b) in enumerate(zip(full, compact)):
        if a.get("passed") and not b.get("passed"):
            row = dict(b)
            row["category"] = f"{label} compression removed required fact"
            row["regression_index"] = i
            out.append(row)
    return out


def _repo_weight_report() -> dict[str, Any]:
    exclude = ("\\.venv\\", "\\.git\\", "\\training_outputs\\", "\\backup_", "\\__pycache__\\")
    files = []

    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        text = str(path)
        if any(x in text for x in exclude):
            continue
        try:
            size = path.stat().st_size
        except Exception:
            continue
        files.append((path, size))

    top = sorted(files, key=lambda x: x[1], reverse=True)[:20]
    total = sum(size for _, size in files)

    return {
        "active_repo_mb_excluding_venv_git_outputs": round(total / 1024 / 1024, 3),
        "top_files": [
            {"path": str(path.relative_to(ROOT)), "mb": round(size / 1024 / 1024, 3)}
            for path, size in top
        ],
        "note": "This is a safe audit only. It does not delete or rewrite code.",
    }


def _gpu_warmup() -> dict[str, Any]:
    try:
        import torch
        if not torch.cuda.is_available():
            return {"torch_installed": True, "cuda_available": False}

        device = torch.device("cuda")
        a = torch.randn((2048, 2048), device=device)
        b = torch.randn((2048, 2048), device=device)
        start = time.perf_counter()
        c = a @ b
        torch.cuda.synchronize()
        seconds = time.perf_counter() - start

        return {
            "torch_installed": True,
            "cuda_available": True,
            "gpu_name": torch.cuda.get_device_name(0),
            "warmup_seconds": round(seconds, 6),
            "warmup_note": "GPU is available. Real duel speed still depends on whether the evaluator uses batched numeric scoring or Python rule checks.",
        }
    except Exception as exc:
        return {"torch_error": f"{type(exc).__name__}: {exc}"}


def _write_dashboard(summary: dict[str, Any], rounds: list[dict[str, Any]]) -> None:
    rows = []
    for r in rounds[-50:][::-1]:
        rows.append(
            "<tr>"
            f"<td>{r.get('round')}</td>"
            f"<td>{r.get('difficulty')}</td>"
            f"<td>{r.get('full_accuracy')}</td>"
            f"<td>{r.get('safe_accuracy')}</td>"
            f"<td>{r.get('aggressive_accuracy')}</td>"
            f"<td>{r.get('fail_count')}</td>"
            f"<td>{r.get('safe_regressions')}</td>"
            f"<td>{r.get('aggressive_regressions')}</td>"
            f"<td>{r.get('best_size_cut_percent')}</td>"
            f"<td>{r.get('profiles_per_sec')}</td>"
            f"<td>{r.get('gpu_rows')}</td>"
            f"<td>{html.escape(str(r.get('top_fail')))}</td>"
            "</tr>"
        )

    top_files = summary.get("repo_weight", {}).get("top_files", [])
    file_rows = []
    for f in top_files[:10]:
        file_rows.append(f"<tr><td>{html.escape(str(f.get('path')))}</td><td>{f.get('mb')}</td></tr>")

    content = f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<title>Survivor Optimizer Never-Ending Duel</title>
<style>
body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; background: #111; color: #eee; }}
.card {{ background: #1b1b1b; border: 1px solid #333; border-radius: 10px; padding: 16px; margin-bottom: 16px; }}
.good {{ color: #7CFC98; }}
.bad {{ color: #FF7373; }}
.warn {{ color: #FFD166; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
td, th {{ border: 1px solid #333; padding: 6px; }}
th {{ background: #222; }}
</style>
</head>
<body>
<h1>Survivor Optimizer: Never-Ending Enemy Duel</h1>

<div class="card">
<h2>Status</h2>
<p>Rounds: <b>{summary.get('rounds_completed')}</b></p>
<p>Optimizer wins: <b class="good">{summary.get('optimizer_wins')}</b> | Enemy wins: <b class="bad">{summary.get('enemy_wins')}</b></p>
<p>Latest full accuracy: <b>{summary.get('latest_full_accuracy')}</b></p>
<p>Latest safe compact accuracy: <b>{summary.get('latest_safe_accuracy')}</b></p>
<p>Latest aggressive compact accuracy: <b>{summary.get('latest_aggressive_accuracy')}</b></p>
<p>Best size cut: <b class="good">{summary.get('best_size_cut_percent')}%</b></p>
<p>Plateau: <b class="warn">{summary.get('plateau')}</b></p>
<p>Latest top fail: <b class="bad">{html.escape(str(summary.get('latest_top_fail')))}</b></p>
</div>

<div class="card">
<h2>Latest Round History</h2>
<table>
<tr>
<th>Round</th><th>Difficulty</th><th>Full</th><th>Safe</th><th>Aggressive</th>
<th>Fail</th><th>Safe Regr</th><th>Agg Regr</th><th>Best Cut %</th>
<th>Profiles/sec</th><th>GPU Rows</th><th>Top Fail</th>
</tr>
{''.join(rows)}
</table>
</div>

<div class="card">
<h2>Repo Weight Audit</h2>
<p>Active repo MB excluding venv/git/outputs: <b>{summary.get('repo_weight', {}).get('active_repo_mb_excluding_venv_git_outputs')}</b></p>
<table><tr><th>File</th><th>MB</th></tr>{''.join(file_rows)}</table>
</div>
</body>
</html>
"""
    DASHBOARD_FILE.write_text(content, encoding="utf-8")


def _plateau(rounds: list[dict[str, Any]], window: int) -> bool:
    if len(rounds) < window:
        return False
    recent = rounds[-window:]
    no_fail = all(r.get("fail_count", 0) == 0 and r.get("safe_regressions", 0) == 0 and r.get("aggressive_regressions", 0) == 0 for r in recent)
    cuts = [float(r.get("best_size_cut_percent", 0)) for r in recent]
    stable_cut = max(cuts) - min(cuts) < 0.25
    return bool(no_fail and stable_cut)


def run(args: argparse.Namespace) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    seed_cases = _load_seed_cases()
    lessons = _load_json(LESSONS_FILE, {"optimizer_wins": 0, "enemy_wins": 0, "failures": {}, "plateaus": 0})
    old_rounds = _load_json(ROUNDS_FILE, [])
    if not isinstance(old_rounds, list):
        old_rounds = []

    rounds: list[dict[str, Any]] = old_rounds[-500:]

    gpu_info = _gpu_warmup()
    print(f"Loaded seed cases: {len(seed_cases)}", flush=True)
    print(f"GPU info: {gpu_info}", flush=True)
    print(f"Dashboard: {DASHBOARD_FILE}", flush=True)

    if args.open_dashboard:
        try:
            os.startfile(str(DASHBOARD_FILE))
        except Exception:
            pass

    deadline = time.time() + float(args.minutes) * 60.0
    round_no = int(rounds[-1]["round"]) + 1 if rounds else 1

    while time.time() < deadline and round_no <= int(args.max_rounds):
        started = time.perf_counter()

        difficulty = min(20, 1 + int(lessons.get("optimizer_wins", 0) or 0) // 3)
        cases = _balanced_cases(seed_cases, round_no, int(args.max_cases_per_round), difficulty)

        safe_cases = [compact_case(c) for c in cases]
        aggressive_cases = [_aggressive_compact_case(c) for c in cases]

        full_size = sum(profile_size_score(c.get("challenged_profile", {})) for c in cases)
        safe_size = sum(profile_size_score(c.get("challenged_profile", {})) for c in safe_cases)
        aggressive_size = sum(profile_size_score(c.get("challenged_profile", {})) for c in aggressive_cases)

        safe_cut = 1.0 - (safe_size / max(1, full_size))
        aggressive_cut = 1.0 - (aggressive_size / max(1, full_size))

        full_results, full_seconds, full_gpu = _evaluate(cases, args)
        safe_results, safe_seconds, safe_gpu = _evaluate(safe_cases, args)
        aggressive_results, aggressive_seconds, aggressive_gpu = _evaluate(aggressive_cases, args)

        full_failures = [r for r in full_results if not r.get("passed")]
        safe_regr = _regressions(full_results, safe_results, "safe")
        aggressive_regr = _regressions(full_results, aggressive_results, "aggressive")

        fail_cats = Counter(battle._normalize_category(r.get("category")) for r in full_failures)
        safe_regr_cats = Counter(battle._normalize_category(r.get("category")) for r in safe_regr)
        aggressive_regr_cats = Counter(battle._normalize_category(r.get("category")) for r in aggressive_regr)

        full_pass = len(full_results) - len(full_failures)
        safe_pass = len(safe_results) - len([r for r in safe_results if not r.get("passed")])
        aggressive_pass = len(aggressive_results) - len([r for r in aggressive_results if not r.get("passed")])

        full_acc = full_pass / max(1, len(full_results))
        safe_acc = safe_pass / max(1, len(safe_results))
        aggressive_acc = aggressive_pass / max(1, len(aggressive_results))

        best_cut = safe_cut
        if not aggressive_regr and aggressive_acc >= full_acc:
            best_cut = max(best_cut, aggressive_cut)

        enemy_won = bool(full_failures or safe_regr or aggressive_regr)
        if enemy_won:
            lessons["enemy_wins"] = int(lessons.get("enemy_wins", 0) or 0) + 1
            for k, v in fail_cats.items():
                lessons.setdefault("failures", {}).setdefault(k, 0)
                lessons["failures"][k] += int(v)
        else:
            lessons["optimizer_wins"] = int(lessons.get("optimizer_wins", 0) or 0) + 1

        gpu_rows = (
            int(full_gpu.get("gpu_rows_scored", 0) or 0)
            + int(safe_gpu.get("gpu_rows_scored", 0) or 0)
            + int(aggressive_gpu.get("gpu_rows_scored", 0) or 0)
        )

        wall = time.perf_counter() - started
        total_profiles = len(full_results) + len(safe_results) + len(aggressive_results)

        row = {
            "round": round_no,
            "difficulty": difficulty,
            "profiles_tested_total": total_profiles,
            "full_accuracy": round(full_acc, 6),
            "safe_accuracy": round(safe_acc, 6),
            "aggressive_accuracy": round(aggressive_acc, 6),
            "fail_count": len(full_failures),
            "safe_regressions": len(safe_regr),
            "aggressive_regressions": len(aggressive_regr),
            "failure_categories": dict(sorted(fail_cats.items())),
            "safe_regression_categories": dict(sorted(safe_regr_cats.items())),
            "aggressive_regression_categories": dict(sorted(aggressive_regr_cats.items())),
            "safe_size_cut_percent": round(safe_cut * 100, 3),
            "aggressive_size_cut_percent": round(aggressive_cut * 100, 3),
            "best_size_cut_percent": round(best_cut * 100, 3),
            "gpu_rows": gpu_rows,
            "wall_seconds": round(wall, 4),
            "profiles_per_sec": round(total_profiles / wall, 3) if wall else 0.0,
            "top_fail": fail_cats.most_common(1)[0][0] if fail_cats else "none",
        }

        rounds.append(row)
        rounds = rounds[-500:]

        plateau = _plateau(rounds, int(args.plateau_window))
        if plateau:
            lessons["plateaus"] = int(lessons.get("plateaus", 0) or 0) + 1

        repo_weight = _repo_weight_report() if round_no == 1 or round_no % int(args.repo_audit_every) == 0 else _load_json(SUMMARY_FILE, {}).get("repo_weight", {})

        summary = {
            "rounds_completed": round_no,
            "optimizer_wins": lessons.get("optimizer_wins", 0),
            "enemy_wins": lessons.get("enemy_wins", 0),
            "latest_full_accuracy": row["full_accuracy"],
            "latest_safe_accuracy": row["safe_accuracy"],
            "latest_aggressive_accuracy": row["aggressive_accuracy"],
            "latest_top_fail": row["top_fail"],
            "latest_profiles_per_sec": row["profiles_per_sec"],
            "best_size_cut_percent": row["best_size_cut_percent"],
            "plateau": plateau,
            "gpu_info": gpu_info,
            "repo_weight": repo_weight,
            "lessons": lessons,
            "dashboard": str(DASHBOARD_FILE),
            "rounds_file": str(ROUNDS_FILE),
        }

        _atomic_write_json(LESSONS_FILE, lessons)
        _atomic_write_json(ROUNDS_FILE, rounds)
        _atomic_write_json(SUMMARY_FILE, summary)
        _write_dashboard(summary, rounds)

        print(
            f"Round {round_no} | diff={difficulty} | "
            f"full={full_pass}/{len(full_results)} | safe={safe_pass}/{len(safe_results)} | "
            f"aggr={aggressive_pass}/{len(aggressive_results)} | "
            f"fail={len(full_failures)} | regr={len(safe_regr)}/{len(aggressive_regr)} | "
            f"cut={row['best_size_cut_percent']}% | p/s={row['profiles_per_sec']} | "
            f"gpu_rows={gpu_rows} | top={row['top_fail']} | plateau={plateau}",
            flush=True,
        )

        round_no += 1

    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Never-ending adversarial optimizer duel with dashboard.")
    p.add_argument("--minutes", type=float, default=480)
    p.add_argument("--max-rounds", type=int, default=100000)
    p.add_argument("--max-cases-per-round", type=int, default=2500)
    p.add_argument("--batch-size", type=int, default=1024)
    p.add_argument("--cpu-workers", type=int, default=6)
    p.add_argument("--use-gpu", action="store_true")
    p.add_argument("--plateau-window", type=int, default=25)
    p.add_argument("--repo-audit-every", type=int, default=10)
    p.add_argument("--open-dashboard", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))

