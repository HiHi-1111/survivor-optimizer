from __future__ import annotations

import argparse
import copy
import html
import json
import os
import random
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = (ROOT / ".venv" / "Scripts" / "python.exe").resolve()
ACTUAL = Path(sys.executable).resolve()

if str(ACTUAL).lower() != str(EXPECTED).lower():
    print(f"REFUSING TO RUN: wrong Python executable: {ACTUAL}", flush=True)
    print(f"Expected: {EXPECTED}", flush=True)
    raise SystemExit(88)

sys.path.insert(0, str(ROOT))

from tools import run_adversarial_battle as battle
from tools import run_extreme_cut_duel_lab as lab
from optimizer.knowledge_compactor import profile_size_score

OUT = ROOT / "training_outputs" / "critic_sprint_lab"
SUMMARY_FILE = OUT / "critic_sprint_summary.json"
ROUNDS_FILE = OUT / "critic_sprint_rounds.json"
DASHBOARD_FILE = OUT / "critic_sprint_dashboard.html"
ERROR_FILE = OUT / "critic_sprint_errors.jsonl"
PATCH_DIR = OUT / "patch_candidates"
CHECKPOINT_DIR = OUT / "checkpoints"

MODES = ["safe", "aggressive", "extreme", "risky", "starvation"]

PROTECTED_GROUPS = {
    "damage": ["damage", "multiplier", "atk", "attack", "dps", "crit"],
    "active_flags": ["equipped", "selected", "active", "slotted", "unlocked", "owned"],
    "rare_blockers": [
        "relic core", "relic_core", "relic-core",
        "awakening core", "awakening_core", "s awakening core",
        "yang shard", "survivor shard",
        "resonance chip", "harmony chip",
        "ss", "xeno", "astral forge",
    ],
    "timing": ["cooldown", "uptime", "cycle", "duration", "seconds", "charge", "active_seconds", "off_seconds"],
    "identity": ["name", "id", "item_id", "system", "category", "rarity", "tier", "level", "star"],
}

THRESHOLDS = {
    "safe": 0.65,
    "aggressive": 0.45,
    "extreme": 0.30,
    "risky": 0.18,
    "starvation": 0.10,
}


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, sort_keys=True, default=str) + "\n")


def flatten_text(value: Any) -> str:
    parts = []

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for k, child in v.items():
                parts.append(str(k).lower())
                walk(child)
        elif isinstance(v, list):
            for child in v:
                walk(child)
        else:
            parts.append(str(v).lower())

    walk(value)
    return " ".join(parts)


def group_counts(value: Any) -> dict[str, int]:
    text = flatten_text(value)
    out = {}
    for group, terms in PROTECTED_GROUPS.items():
        out[group] = sum(text.count(term) for term in terms)
    return out


def protected_score(value: Any) -> int:
    counts = group_counts(value)
    return (
        counts["damage"] * 4
        + counts["active_flags"] * 3
        + counts["rare_blockers"] * 6
        + counts["timing"] * 4
        + counts["identity"] * 2
    )


def harden_case(case: dict[str, Any], round_no: int, idx: int, difficulty: int) -> dict[str, Any]:
    c = copy.deepcopy(case)

    for key in ("clean_profile", "challenged_profile"):
        p = c.get(key)
        if not isinstance(p, dict):
            continue

        p[f"critic_must_survive_active_damage_d{difficulty}"] = {
            "name": f"Critic Required Active Damage Sentinel {round_no}-{idx}",
            "equipped": True,
            "selected": True,
            "active": True,
            "unlocked": True,
            "owned": True,
            "damage_multiplier": round(2.35 + difficulty * 0.03, 3),
            "battle_duration_seconds": p.get("battle_duration_seconds", 180),
            "must_survive_compaction": True,
            "trap": "If starvation deletes this, the critic counts it as a real regression.",
        }

        p[f"critic_must_survive_rare_blocker_d{difficulty}"] = {
            "name": f"Critic Required Rare Blocker {round_no}-{idx}",
            "relic_core_required": True,
            "awakening_core_required": True,
            "survivor_shard_required": True,
            "resonance_chip_required": True,
            "cheap_material_bait": "generic fodder",
            "must_survive_compaction": True,
            "trap": "Rare blocker facts must survive even under starvation cutting.",
        }

        p[f"critic_must_survive_timing_d{difficulty}"] = {
            "name": f"Critic Required Timing Cycle {round_no}-{idx}",
            "equipped": True,
            "active": True,
            "damage_multiplier": round(1.25 + difficulty * 0.05, 3),
            "active_seconds": max(1, 10 - difficulty // 3),
            "off_seconds": 8 + difficulty,
            "cycle_seconds": 18 + difficulty,
            "cooldown_seconds": 6 + difficulty,
            "must_survive_compaction": True,
            "trap": "Timing facts must survive, or uptime math can be wrong.",
        }

        p[f"critic_fake_bloat_d{difficulty}"] = [
            {
                "name": f"Fake Dead Weight {i}",
                "damage_multiplier": 999999,
                "equipped": False,
                "selected": False,
                "active": False,
                "unlocked": False,
                "owned": False,
                "source": "critic_fake_catalog",
                "trap": "This should be cut.",
            }
            for i in range(1, min(220, 30 + difficulty * 6))
        ]

    return c


def invariant_violations(full_case: dict[str, Any], cut_case: dict[str, Any], mode: str, index: int) -> list[dict[str, Any]]:
    full_profile = full_case.get("challenged_profile", {})
    cut_profile = cut_case.get("challenged_profile", {})

    full_counts = group_counts(full_profile)
    cut_counts = group_counts(cut_profile)

    violations = []

    # Hard must-survive categories.
    for group in ("damage", "active_flags", "rare_blockers", "timing"):
        if full_counts[group] > 0 and cut_counts[group] == 0:
            violations.append({
                "passed": False,
                "category": f"{mode} invariant lost all {group}",
                "mode": mode,
                "regression_index": index,
                "full_count": full_counts[group],
                "cut_count": cut_counts[group],
            })

    full_score = protected_score(full_profile)
    cut_score = protected_score(cut_profile)
    ratio = cut_score / max(1, full_score)
    threshold = THRESHOLDS.get(mode, 0.2)

    if full_score > 0 and ratio < threshold:
        violations.append({
            "passed": False,
            "category": f"{mode} protected fact score too low",
            "mode": mode,
            "regression_index": index,
            "ratio": round(ratio, 6),
            "threshold": threshold,
            "full_score": full_score,
            "cut_score": cut_score,
        })

    return violations


def gpu_critic_sprint(all_case_sets: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    try:
        import torch
        if not torch.cuda.is_available():
            return {"available": False, "gpu_rows": 0, "reason": "cuda_not_available"}

        rows = []
        labels = []

        for label, cases in all_case_sets.items():
            for c in cases:
                p = c.get("challenged_profile", {})
                if isinstance(p, dict):
                    rows.append(lab.numeric_features(p))
                    labels.append(label)

        if not rows:
            return {"available": True, "gpu_rows": 0, "reason": "no_rows"}

        multiplier = int(os.environ.get("SURVIVOR_CRITIC_GPU_MULTIPLIER", "96"))
        multiplier = max(1, min(multiplier, 256))

        device = torch.device("cuda")
        start = time.perf_counter()

        base = torch.tensor(rows, dtype=torch.float32, device=device)
        scale = torch.clamp(base.abs().amax(dim=0, keepdim=True), min=1.0)
        base = base / scale

        n, f = base.shape
        total_rows = n * multiplier
        chunk = 65536

        weights_1 = torch.linspace(0.1, 3.0, steps=f, device=device)
        weights_2 = torch.sin(torch.linspace(0, 6.28318, steps=f, device=device)) + 1.5
        weights_3 = torch.cos(torch.linspace(0, 3.14159, steps=f, device=device)) + 1.5

        gen = torch.Generator(device=device)
        gen.manual_seed(55001 + n + f)

        total_pressure = 0.0
        done = 0

        while done < total_rows:
            take = min(chunk, total_rows - done)

            idx = torch.randint(0, n, (take,), device=device, generator=gen)
            x = base[idx].clone()

            noise = torch.randn(x.shape, device=device, generator=gen) * 0.035
            dropout = (torch.rand(x.shape, device=device, generator=gen) > 0.08).float()
            x = (x + noise) * dropout

            # Heavy enough to actually exercise GPU while searching pressure space.
            y = x
            score = torch.zeros((take,), device=device)
            for step in range(8):
                y = torch.relu(y + torch.sin(y * (step + 1)) * 0.25)
                score = score + (y @ weights_1)
                score = score + torch.log1p(torch.relu(y @ weights_2))
                score = score + torch.sqrt(torch.relu(y @ weights_3) + 1e-6)

            density = (x.abs() > 1e-6).float().mean(dim=1)
            protected = x[:, 15:30].abs().sum(dim=1)
            pressure = score + protected * 3.0 - density * 2.5

            total_pressure += float(pressure.sum().detach().cpu().item())
            done += take

        torch.cuda.synchronize()
        seconds = time.perf_counter() - start

        return {
            "available": True,
            "gpu_rows": int(total_rows),
            "base_rows": int(n),
            "multiplier": int(multiplier),
            "features": int(f),
            "seconds": round(seconds, 6),
            "rows_per_sec": round(total_rows / seconds, 3) if seconds else 0.0,
            "pressure_total": round(total_pressure, 3),
        }

    except Exception as exc:
        return {"available": False, "gpu_rows": 0, "reason": f"{type(exc).__name__}: {exc}"}


def evaluate(cases: list[dict[str, Any]], args: argparse.Namespace):
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
    return results, seconds, {"gpu_rows_scored": 0}


def regression_rows(full_results, cut_results, mode: str):
    rows = []
    for i, (full, cut) in enumerate(zip(full_results, cut_results)):
        if full.get("passed") and not cut.get("passed"):
            r = dict(cut)
            r["category"] = f"{mode} evaluator regression"
            r["mode"] = mode
            r["regression_index"] = i
            rows.append(r)
    return rows


def write_dashboard(summary: dict[str, Any], rounds: list[dict[str, Any]]) -> None:
    lines = []
    for r in rounds[-80:][::-1]:
        lines.append(
            "<tr>"
            f"<td>{r.get('round')}</td>"
            f"<td>{r.get('difficulty')}</td>"
            f"<td>{r.get('full_accuracy')}</td>"
            f"<td>{r.get('starvation_accuracy')}</td>"
            f"<td>{r.get('best_mode')}</td>"
            f"<td>{r.get('best_cut_percent')}</td>"
            f"<td>{r.get('evaluator_regressions')}</td>"
            f"<td>{r.get('critic_regressions')}</td>"
            f"<td>{r.get('gpu_critic_rows')}</td>"
            f"<td>{r.get('profiles_per_sec')}</td>"
            f"<td>{html.escape(str(r.get('top_fail')))}</td>"
            "</tr>"
        )

    page = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<title>Critic Sprint Lab</title>
<style>
body {{ background:#101010; color:#eee; font-family:Segoe UI, Arial; margin:24px; }}
.card {{ background:#1c1c1c; border:1px solid #333; border-radius:12px; padding:16px; margin-bottom:16px; }}
.good {{ color:#75f59a; }}
.bad {{ color:#ff7373; }}
.warn {{ color:#ffd166; }}
table {{ border-collapse:collapse; width:100%; font-size:13px; }}
td, th {{ border:1px solid #333; padding:6px; }}
th {{ background:#222; }}
</style>
</head>
<body>
<h1>Survivor Optimizer: Critic Sprint Lab</h1>
<div class="card">
<p>Rounds: <b>{summary.get('rounds_completed')}</b></p>
<p>Optimizer wins: <b class="good">{summary.get('optimizer_wins')}</b> | Enemy/Critic wins: <b class="bad">{summary.get('enemy_wins')}</b></p>
<p>Best mode: <b class="warn">{summary.get('latest_best_mode')}</b></p>
<p>Best cut: <b class="good">{summary.get('best_cut_percent')}%</b></p>
<p>Latest evaluator regressions: <b>{summary.get('latest_evaluator_regressions')}</b></p>
<p>Latest critic regressions: <b>{summary.get('latest_critic_regressions')}</b></p>
<p>GPU critic rows: <b>{summary.get('latest_gpu_critic_rows')}</b></p>
<p>Top fail: <b class="bad">{html.escape(str(summary.get('latest_top_fail')))}</b></p>
</div>
<div class="card">
<table>
<tr><th>Round</th><th>Diff</th><th>Full</th><th>Starve</th><th>Best</th><th>Cut %</th><th>Eval Regr</th><th>Critic Regr</th><th>GPU Rows</th><th>P/S</th><th>Top Fail</th></tr>
{''.join(lines)}
</table>
</div>
</body>
</html>
"""
    DASHBOARD_FILE.write_text(page, encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    PATCH_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

    training_module = battle._load_training_module()
    seeds = [battle._normalize_case(x) for x in battle._load_seed_cases(training_module)]

    rounds = load_json(ROUNDS_FILE, [])
    lessons = load_json(OUT / "critic_sprint_lessons.json", {"optimizer_wins": 0, "enemy_wins": 0, "failures": {}})

    print(f"Loaded seeds: {len(seeds)}", flush=True)
    print(f"Dashboard: {DASHBOARD_FILE}", flush=True)

    if args.open_dashboard:
        try:
            os.startfile(str(DASHBOARD_FILE))
        except Exception:
            pass

    deadline = time.time() + args.minutes * 60
    round_no = int(rounds[-1]["round"]) + 1 if rounds else 1

    while time.time() < deadline:
        try:
            start = time.perf_counter()

            difficulty = min(80, 1 + int(lessons.get("optimizer_wins", 0)) // 2)

            cases = []
            for idx in range(args.max_cases_per_round):
                seed = seeds[idx % len(seeds)]
                base = lab.mutate_case(seed, round_no, idx, difficulty)
                cases.append(harden_case(base, round_no, idx, difficulty))

            cut_cases = {
                mode: [lab.cut_case(c, mode, round_no, i) for i, c in enumerate(cases)]
                for mode in MODES
            }

            full_size = sum(profile_size_score(c.get("challenged_profile", {})) for c in cases)
            cuts = {}
            for mode in MODES:
                size = sum(profile_size_score(c.get("challenged_profile", {})) for c in cut_cases[mode])
                cuts[mode] = max(0.0, 1.0 - size / max(1, full_size))

            gpu_critic = gpu_critic_sprint({"full": cases, **cut_cases})

            full_results, _, full_gpu = evaluate(cases, args)
            full_failures = [r for r in full_results if not r.get("passed")]
            full_acc = (len(full_results) - len(full_failures)) / max(1, len(full_results))

            cut_results = {}
            accs = {}
            evaluator_regressions = []
            critic_regressions = []

            for mode in MODES:
                results, _, _gpu = evaluate(cut_cases[mode], args)
                cut_results[mode] = results
                fails = [r for r in results if not r.get("passed")]
                accs[mode] = (len(results) - len(fails)) / max(1, len(results))
                evaluator_regressions.extend(regression_rows(full_results, results, mode))

                for i, (full_case, cut_case) in enumerate(zip(cases, cut_cases[mode])):
                    critic_regressions.extend(invariant_violations(full_case, cut_case, mode, i))

            valid_modes = [
                mode for mode in MODES
                if accs[mode] >= full_acc
                and not any(r.get("mode") == mode for r in evaluator_regressions)
                and not any(r.get("mode") == mode for r in critic_regressions)
            ]

            best_mode = max(valid_modes, key=lambda m: cuts[m]) if valid_modes else "none"
            best_cut = cuts.get(best_mode, 0.0)

            all_fail_rows = full_failures + evaluator_regressions + critic_regressions
            fail_cats = Counter(battle._normalize_category(r.get("category")) for r in all_fail_rows)

            enemy_won = bool(all_fail_rows)

            if enemy_won:
                lessons["enemy_wins"] = int(lessons.get("enemy_wins", 0)) + 1
                for k, v in fail_cats.items():
                    lessons.setdefault("failures", {}).setdefault(k, 0)
                    lessons["failures"][k] += int(v)
            else:
                lessons["optimizer_wins"] = int(lessons.get("optimizer_wins", 0)) + 1

            wall = time.perf_counter() - start
            total_profiles = len(cases) * (1 + len(MODES))

            row = {
                "round": round_no,
                "difficulty": difficulty,
                "full_accuracy": round(full_acc, 6),
                "safe_accuracy": round(accs["safe"], 6),
                "aggressive_accuracy": round(accs["aggressive"], 6),
                "extreme_accuracy": round(accs["extreme"], 6),
                "risky_accuracy": round(accs["risky"], 6),
                "starvation_accuracy": round(accs["starvation"], 6),
                "cuts_by_mode_percent": {k: round(v * 100, 3) for k, v in cuts.items()},
                "best_mode": best_mode,
                "best_cut_percent": round(best_cut * 100, 3),
                "full_failures": len(full_failures),
                "evaluator_regressions": len(evaluator_regressions),
                "critic_regressions": len(critic_regressions),
                "gpu_critic_rows": gpu_critic.get("gpu_rows", 0),
                "gpu_critic_rps": gpu_critic.get("rows_per_sec", 0),
                "profiles_per_sec": round(total_profiles / wall, 3) if wall else 0,
                "wall_seconds": round(wall, 4),
                "top_fail": fail_cats.most_common(1)[0][0] if fail_cats else "none",
            }

            rounds.append(row)
            rounds = rounds[-800:]

            summary = {
                "rounds_completed": round_no,
                "optimizer_wins": lessons.get("optimizer_wins", 0),
                "enemy_wins": lessons.get("enemy_wins", 0),
                "latest_best_mode": best_mode,
                "best_cut_percent": row["best_cut_percent"],
                "latest_evaluator_regressions": row["evaluator_regressions"],
                "latest_critic_regressions": row["critic_regressions"],
                "latest_gpu_critic_rows": row["gpu_critic_rows"],
                "latest_top_fail": row["top_fail"],
                "latest_profiles_per_sec": row["profiles_per_sec"],
                "lessons": lessons,
                "dashboard": str(DASHBOARD_FILE),
                "rounds_file": str(ROUNDS_FILE),
            }

            atomic_json(ROUNDS_FILE, rounds)
            atomic_json(OUT / "critic_sprint_lessons.json", lessons)
            atomic_json(SUMMARY_FILE, summary)
            atomic_json(CHECKPOINT_DIR / "latest_checkpoint.json", {"summary": summary, "latest_round": row})
            write_dashboard(summary, rounds)

            print(
                f"Round {round_no} | diff={difficulty} | full={full_acc:.3f} | "
                f"starve={accs['starvation']:.3f} | best={best_mode}:{row['best_cut_percent']}% | "
                f"eval_regr={len(evaluator_regressions)} critic_regr={len(critic_regressions)} | "
                f"gpu_critic={row['gpu_critic_rows']} | p/s={row['profiles_per_sec']} | top={row['top_fail']}",
                flush=True,
            )

            round_no += 1

        except Exception as exc:
            append_jsonl(ERROR_FILE, {"round": round_no, "error": f"{type(exc).__name__}: {exc}", "time": time.time()})
            print(f"ROUND ERROR CONTINUING: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(3)
            round_no += 1

    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--minutes", type=float, default=45)
    p.add_argument("--max-cases-per-round", type=int, default=700)
    p.add_argument("--batch-size", type=int, default=512)
    p.add_argument("--cpu-workers", type=int, default=8)
    p.add_argument("--use-gpu", action="store_true")
    p.add_argument("--open-dashboard", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
