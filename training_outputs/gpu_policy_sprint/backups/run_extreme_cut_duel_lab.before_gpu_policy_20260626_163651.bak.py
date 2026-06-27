from __future__ import annotations

# EXTREME_CUT_HARD_VENV_GUARD
import sys
from pathlib import Path as _ExtremeGuardPath
_expected_python = (_ExtremeGuardPath(__file__).resolve().parents[1] / ".venv" / "Scripts" / "python.exe").resolve()
_actual_python = _ExtremeGuardPath(sys.executable).resolve()
if str(_actual_python).lower() != str(_expected_python).lower():
    print(f"REFUSING TO RUN: wrong Python executable: {_actual_python}", flush=True)
    print(f"Expected: {_expected_python}", flush=True)
    raise SystemExit(88)
# END_EXTREME_CUT_HARD_VENV_GUARD

# EXTREME_CUT_LAB_VENV_ONLY_GUARD
import sys as _guard_sys
from pathlib import Path as _guard_Path

_exe = str(_guard_Path(_guard_sys.executable)).lower()


# EXTREME_CUT_STRICT_EXPECTED_VENV
_expected_python = (_guard_Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "python.exe").resolve()
_actual_python = _guard_Path(_guard_sys.executable).resolve()
if str(_actual_python).lower() != str(_expected_python).lower():
    print(f"REFUSING TO RUN: wrong Python executable: {_actual_python}", flush=True)
    print(f"Expected: {_expected_python}", flush=True)
    raise SystemExit(88)

import argparse
import sys
import sys
import copy
import hashlib
import html
import json
import os
import random
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from optimizer.knowledge_compactor import compact_case, profile_size_score
from tools import run_adversarial_battle as battle

OUT = ROOT / "training_outputs" / "extreme_cut_lab"
PATCH_DIR = OUT / "patch_candidates"
CHECKPOINT_DIR = OUT / "checkpoints"

SUMMARY_FILE = OUT / "extreme_cut_summary.json"
ROUNDS_FILE = OUT / "extreme_cut_rounds.json"
LESSONS_FILE = OUT / "extreme_cut_lessons.json"
DASHBOARD_FILE = OUT / "extreme_cut_dashboard.html"
ERROR_FILE = OUT / "extreme_cut_errors.jsonl"

KEEP_TERMS = (
    "damage", "multiplier", "atk", "attack", "dps", "crit",
    "equipped", "selected", "active", "slotted", "unlocked", "owned",
    "relic", "core", "awakening", "shard", "resonance", "harmony", "blocker",
    "rarity", "tier", "level", "star", "astral", "forge", "xeno", "ss",
    "duration", "seconds", "cooldown", "uptime", "cycle", "charge",
    "goal_scenario", "battle_duration_seconds", "progression", "steamroll",
    "name", "id", "item_id", "system", "category", "type",
)

DROP_TERMS = (
    "raw", "wiki", "screenshot", "image", "source_url", "description_long",
    "comment", "note", "unused", "preview", "future", "catalog", "fake",
)

RARE_BLOCKER_TERMS = (
    "relic core", "relic_core", "relic-core",
    "awakening core", "awakening_core", "s awakening core",
    "yang shard", "survivor shard", "resonance chip", "harmony chip",
    "ss", "xeno", "astral forge",
)

AXES = [
    {"name": "normal_180s", "goal_scenario": "normal", "seconds": 180, "stage": "midgame", "steamroll": False},
    {"name": "steamroll_180s", "goal_scenario": "steamroll", "seconds": 180, "stage": "ss_endgame", "steamroll": True},
    {"name": "boss_60s_burst", "goal_scenario": "boss_burst", "seconds": 60, "stage": "ss_endgame", "steamroll": True},
    {"name": "elders_echo_180s", "goal_scenario": "elders_echo", "seconds": 180, "stage": "ss_endgame", "steamroll": True},
    {"name": "long_300s", "goal_scenario": "long_fight", "seconds": 300, "stage": "ss_endgame", "steamroll": True},
    {"name": "timing_cycle_trap", "goal_scenario": "timed_cycle", "seconds": 180, "stage": "ss_endgame", "steamroll": True},
    {"name": "dirty_catalog_trap", "goal_scenario": "dirty_catalog", "seconds": 180, "stage": "ss_endgame", "steamroll": True},
    {"name": "rare_alias_trap", "goal_scenario": "rare_alias", "seconds": 180, "stage": "ss_endgame", "steamroll": True},
    {"name": "low_end_offline_trap", "goal_scenario": "low_end", "seconds": 180, "stage": "midgame", "steamroll": False},
    {"name": "starvation_cut_trap", "goal_scenario": "starvation", "seconds": 180, "stage": "ss_endgame", "steamroll": True},
]


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
    out = []
    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for k, child in v.items():
                out.append(str(k).lower())
                walk(child)
        elif isinstance(v, list):
            for child in v:
                walk(child)
        else:
            out.append(str(v).lower())
    walk(value)
    return " ".join(out)


def gpu_warmup() -> dict[str, Any]:
    try:
        import torch
        if not torch.cuda.is_available():
            return {"torch": True, "cuda": False}
        device = torch.device("cuda")
        a = torch.randn((2048, 2048), device=device)
        b = torch.randn((2048, 2048), device=device)
        start = time.perf_counter()
        _ = a @ b
        torch.cuda.synchronize()
        return {
            "torch": True,
            "cuda": True,
            "gpu_name": torch.cuda.get_device_name(0),
            "warmup_seconds": round(time.perf_counter() - start, 6),
        }
    except Exception as exc:
        return {"torch_or_cuda_error": f"{type(exc).__name__}: {exc}"}


def numeric_features(profile: dict[str, Any]) -> list[float]:
    text = flatten_text(profile)
    nums = []

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for child in v.values():
                walk(child)
        elif isinstance(v, list):
            for child in v:
                walk(child)
        else:
            if isinstance(v, bool):
                nums.append(1.0 if v else 0.0)
            elif isinstance(v, (int, float)):
                nums.append(float(v))
            elif isinstance(v, str):
                try:
                    nums.append(float(v.strip().replace("%", "")))
                except Exception:
                    pass

    walk(profile)

    def c(term: str) -> float:
        return float(text.count(term))

    features = [
        float(len(text)),
        float(len(nums)),
        float(sum(nums)) if nums else 0.0,
        float(max(nums)) if nums else 0.0,
        c("damage"), c("multiplier"), c("atk"), c("dps"), c("crit"),
        c("equipped"), c("selected"), c("active"), c("slotted"), c("unlocked"), c("owned"),
        c("relic"), c("awakening"), c("core"), c("shard"), c("resonance"), c("blocker"),
        c("cooldown"), c("uptime"), c("cycle"), c("duration"), c("seconds"),
        c("locked"), c("preview"), c("future"), c("catalog"), c("source"),
        c("ss"), c("xeno"), c("astral"), c("steamroll"),
    ]

    while len(features) < 96:
        features.append(0.0)
    return features[:96]


def gpu_matrix_score(cases: list[dict[str, Any]], label: str) -> dict[str, Any]:
    try:
        import torch
        if not torch.cuda.is_available():
            return {"label": label, "gpu_matrix_rows": 0, "available": False, "reason": "cuda_not_available"}

        rows = []
        for case in cases:
            p = case.get("challenged_profile", {})
            if isinstance(p, dict):
                rows.append(numeric_features(p))

        if not rows:
            return {"label": label, "gpu_matrix_rows": 0, "available": True, "reason": "no_rows"}

        device = torch.device("cuda")
        start = time.perf_counter()
        x = torch.tensor(rows, dtype=torch.float32, device=device)
        w = torch.linspace(0.1, 2.5, steps=x.shape[1], device=device)
        y = torch.log1p(torch.relu(x @ w))
        total = float(y.sum().detach().cpu().item())
        torch.cuda.synchronize()
        seconds = time.perf_counter() - start

        return {
            "label": label,
            "available": True,
            "gpu_matrix_rows": len(rows),
            "gpu_matrix_features": int(x.shape[1]),
            "gpu_matrix_seconds": round(seconds, 6),
            "gpu_matrix_rps": round(len(rows) / seconds, 3) if seconds else 0.0,
            "gpu_proxy_total": round(total, 3),
        }
    except Exception as exc:
        return {"label": label, "gpu_matrix_rows": 0, "available": False, "reason": f"{type(exc).__name__}: {exc}"}


def mutate_case(case: dict[str, Any], round_no: int, idx: int, difficulty: int) -> dict[str, Any]:
    rng = random.Random(777000 + round_no * 1000 + idx)
    c = battle._profile_variant(case, round_no, idx)
    axis = AXES[(round_no + idx + difficulty) % len(AXES)]

    c["scenario_axis"] = axis["name"]
    c["enemy_difficulty"] = difficulty
    c["case_id"] = f"{c.get('case_id', 'case')}::extreme::{round_no}::{idx}"

    for key in ("clean_profile", "challenged_profile"):
        p = c.get(key)
        if not isinstance(p, dict):
            continue

        p["goal_scenario"] = axis["goal_scenario"]
        p["battle_duration_seconds"] = axis["seconds"]

        stage = p.setdefault("player_stage", {})
        if isinstance(stage, dict):
            stage["progression_stage"] = axis["stage"]
            stage["steamroll_unlocked"] = axis["steamroll"]

        p[f"enemy_rare_blocker_aliases_d{difficulty}"] = {
            "relic": rng.choice(["Relic Core", "relic_core", "relic-core", "RELIC CORE"]),
            "awakening": rng.choice(["S Awakening Core", "awakening_core", "awakening-core"]),
            "survivor": rng.choice(["Yang shard", "Survivor Shard", "survivor_shard"]),
            "resonance": rng.choice(["resonance chip", "Harmony chip", "resonance_chip"]),
            "cheap_bait": rng.choice(["generic fodder", "purple merge", "yellow merge", "normal salvage"]),
            "trap": "Rare blocker must beat cheap material bait.",
        }

        p[f"enemy_timing_probe_d{difficulty}"] = {
            "equipped": True,
            "active": True,
            "damage_multiplier": round(1.2 + difficulty * 0.08, 3),
            "active_seconds": max(2, 14 - difficulty),
            "off_seconds": 8 + difficulty,
            "cycle_seconds": 22 + difficulty,
            "trap": "Do not count cyclic uptime as permanent damage.",
        }

        p[f"enemy_fake_locked_catalog_d{difficulty}"] = [
            {
                "name": f"Fake Huge DPS Preview {i}",
                "damage_multiplier": 5000 + i,
                "equipped": False,
                "selected": False,
                "active": False,
                "unlocked": False,
                "owned": False,
                "source": "enemy_dirty_catalog",
                "trap": "Do not count locked/source/future rows.",
            }
            for i in range(1, min(120, 15 + difficulty * 5))
        ]

        p["low_end_device_constraints"] = {
            "offline_mode": True,
            "unstable_internet": True,
            "target_memory_mb": max(128, 1024 - difficulty * 40),
            "no_large_logs": True,
            "trap": "Runtime should survive weak devices and poor internet.",
        }

    return c


def is_false_inactive_dict(d: dict[str, Any]) -> bool:
    for k in ("equipped", "selected", "active", "slotted", "unlocked", "owned"):
        if d.get(k) is False:
            return True
    return False


def risky_cut_value(value: Any, mode: str, rng: random.Random, path: str = "") -> Any:
    level = {
        "safe": 1,
        "aggressive": 3,
        "extreme": 5,
        "risky": 7,
        "starvation": 9,
    }[mode]

    if isinstance(value, list):
        out = []
        for i, item in enumerate(value):
            c = risky_cut_value(item, mode, rng, f"{path}[{i}]")
            if c not in ({}, [], None, ""):
                out.append(c)

        cap = {
            "safe": 120,
            "aggressive": 60,
            "extreme": 30,
            "risky": 15,
            "starvation": 8,
        }[mode]
        return out[:cap]

    if isinstance(value, dict):
        text = flatten_text(value)

        if is_false_inactive_dict(value):
            return {}

        # Risky/starvation modes may cut more, but must try to preserve rare blocker/damage active facts.
        protected = any(t in text for t in RARE_BLOCKER_TERMS) or any(t in text for t in ("damage", "multiplier", "equipped", "active", "selected"))

        out = {}
        for k, v in value.items():
            key = str(k).lower()
            child_text = key + " " + flatten_text(v)

            if any(term in key for term in DROP_TERMS):
                continue

            keep = any(term in key for term in KEEP_TERMS) or any(term in child_text for term in KEEP_TERMS)

            if mode in ("risky", "starvation") and not keep and not protected:
                if rng.random() < (0.50 if mode == "risky" else 0.75):
                    continue

            if mode == "starvation" and keep and not protected:
                if rng.random() < 0.25:
                    continue

            c = risky_cut_value(v, mode, rng, f"{path}.{k}")
            if c not in ({}, [], None, ""):
                out[k] = c

        return out

    if isinstance(value, str):
        if mode in ("extreme", "risky", "starvation") and len(value) > 120:
            return value[:120]
        if mode == "starvation" and len(value) > 60:
            return value[:60]
        return value

    return value


def cut_case(case: dict[str, Any], mode: str, round_no: int, idx: int) -> dict[str, Any]:
    if mode == "safe":
        c = compact_case(case)
        c["cut_mode"] = "safe_existing_compactor"
        return c

    rng = random.Random(991000 + round_no * 1000 + idx + len(mode))
    c = copy.deepcopy(case)
    for key in ("clean_profile", "challenged_profile"):
        if isinstance(c.get(key), dict):
            c[key] = risky_cut_value(c[key], mode, rng)
    c["cut_mode"] = mode
    return c


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
    for i, (a, b) in enumerate(zip(full_results, cut_results)):
        if a.get("passed") and not b.get("passed"):
            r = dict(b)
            r["category"] = f"{mode} cut regression"
            r["regression_index"] = i
            rows.append(r)
    return rows


def repo_weight_report() -> dict[str, Any]:
    excludes = ("\\.venv\\", "\\.git\\", "\\training_outputs\\", "\\__pycache__\\")
    files = []
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        s = str(p)
        if any(x in s for x in excludes):
            continue
        try:
            files.append((p, p.stat().st_size))
        except Exception:
            pass

    top = sorted(files, key=lambda x: x[1], reverse=True)[:30]
    candidates = []
    for p, size in top:
        rel = str(p.relative_to(ROOT))
        low = rel.lower()
        if "archive\\training_runs" in low or "legacy" in low or "cache" in low or low.endswith(".jsonl"):
            candidates.append({"path": rel, "mb": round(size / 1024 / 1024, 3), "reason": "audit-only old/archive/cache candidate"})

    return {
        "active_repo_mb_excluding_venv_git_outputs": round(sum(s for _, s in files) / 1024 / 1024, 3),
        "top_files": [{"path": str(p.relative_to(ROOT)), "mb": round(s / 1024 / 1024, 3)} for p, s in top],
        "risky_cleanup_candidates_audit_only": candidates,
        "note": "Audit only. No source deletion.",
    }


def patch_candidate(round_no: int, failures: list[dict[str, Any]], regressions: list[dict[str, Any]], reward: float) -> dict[str, Any] | None:
    if not failures and not regressions:
        return None

    cats = Counter(battle._normalize_category(x.get("category")) for x in failures + regressions)
    payload = {
        "round": round_no,
        "status": "proposal_only",
        "reward_pressure": reward,
        "categories": dict(cats),
        "safe_policy": "Do not auto-edit production code. Apply only after compile + unit tests + duel smoke pass.",
        "candidate_actions": [],
    }

    for cat, count in cats.items():
        if "regression" in cat or "cut" in cat:
            payload["candidate_actions"].append({
                "target": "optimizer/knowledge_compactor.py",
                "action": "protect required field class removed by risky/starvation cut",
                "category": cat,
                "count": count,
            })
        elif "rare" in cat or "blocker" in cat or "cheap" in cat:
            payload["candidate_actions"].append({
                "target": "optimizer/rare_blocker_guardrails.py",
                "action": "raise rare blocker priority over cheap material bait",
                "category": cat,
                "count": count,
            })
        elif "cycle" in cat or "timing" in cat or "uptime" in cat:
            payload["candidate_actions"].append({
                "target": "optimizer/damage_engine.py",
                "action": "move uptime/cycle math into numeric scoring feature",
                "category": cat,
                "count": count,
            })
        else:
            payload["candidate_actions"].append({
                "target": "human_review",
                "action": f"investigate new enemy failure: {cat}",
                "category": cat,
                "count": count,
            })

    digest = hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:12]
    path = PATCH_DIR / f"patch_candidate_round_{round_no}_{digest}.json"
    atomic_json(path, payload)
    payload["path"] = str(path)
    return payload


def reward_score(full_acc: float, mode_accs: dict[str, float], cuts: dict[str, float], regressions: dict[str, int]) -> float:
    reward = 0.0
    for mode, cut in cuts.items():
        acc = mode_accs.get(mode, 0.0)
        reg = regressions.get(mode, 0)
        risk_multiplier = {"safe": 1.0, "aggressive": 1.4, "extreme": 2.0, "risky": 3.0, "starvation": 4.0}.get(mode, 1.0)

        if acc >= full_acc and reg == 0:
            reward += cut * 100.0 * risk_multiplier
        else:
            reward -= (100.0 - acc * 100.0) * risk_multiplier
            reward -= reg * 5.0 * risk_multiplier
    return round(reward, 4)


def plateau(rounds: list[dict[str, Any]], window: int) -> bool:
    if len(rounds) < window:
        return False
    recent = rounds[-window:]
    no_fails = all(r.get("full_fail_count", 0) == 0 and r.get("total_regressions", 0) == 0 for r in recent)
    rewards = [float(r.get("reward_score", 0.0)) for r in recent]
    cuts = [float(r.get("best_cut_percent", 0.0)) for r in recent]
    return no_fails and (max(cuts) - min(cuts) < 0.2) and (max(rewards) - min(rewards) < 2.0)


def write_dashboard(summary: dict[str, Any], rounds: list[dict[str, Any]]) -> None:
    rows = []
    for r in rounds[-80:][::-1]:
        rows.append(
            "<tr>"
            f"<td>{r.get('round')}</td>"
            f"<td>{r.get('difficulty')}</td>"
            f"<td>{r.get('full_accuracy')}</td>"
            f"<td>{r.get('safe_accuracy')}</td>"
            f"<td>{r.get('aggressive_accuracy')}</td>"
            f"<td>{r.get('extreme_accuracy')}</td>"
            f"<td>{r.get('risky_accuracy')}</td>"
            f"<td>{r.get('starvation_accuracy')}</td>"
            f"<td>{r.get('full_fail_count')}</td>"
            f"<td>{r.get('total_regressions')}</td>"
            f"<td>{r.get('best_mode')}</td>"
            f"<td>{r.get('best_cut_percent')}</td>"
            f"<td>{r.get('reward_score')}</td>"
            f"<td>{r.get('profiles_per_sec')}</td>"
            f"<td>{r.get('gpu_eval_rows')}</td>"
            f"<td>{r.get('gpu_matrix_rows')}</td>"
            f"<td>{html.escape(str(r.get('top_fail')))}</td>"
            "</tr>"
        )

    top_files = summary.get("repo_weight", {}).get("top_files", [])[:10]
    file_rows = "".join(f"<tr><td>{html.escape(str(x.get('path')))}</td><td>{x.get('mb')}</td></tr>" for x in top_files)

    content = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<title>Extreme Cut Duel Lab</title>
<style>
body {{ background:#0f0f0f; color:#eee; font-family:Segoe UI, Arial; margin:24px; }}
.card {{ background:#1b1b1b; border:1px solid #333; border-radius:12px; padding:16px; margin-bottom:16px; }}
.good {{ color:#69f08f; }}
.bad {{ color:#ff7070; }}
.warn {{ color:#ffd166; }}
table {{ border-collapse:collapse; width:100%; font-size:12px; }}
td, th {{ border:1px solid #333; padding:5px; }}
th {{ background:#222; }}
</style>
</head>
<body>
<h1>Survivor Optimizer: Extreme Cut Duel Lab</h1>

<div class="card">
<h2>Status</h2>
<p>Rounds: <b>{summary.get('rounds_completed')}</b></p>
<p>Optimizer wins: <b class="good">{summary.get('optimizer_wins')}</b> | Enemy wins: <b class="bad">{summary.get('enemy_wins')}</b></p>
<p>Best mode: <b class="warn">{summary.get('latest_best_mode')}</b></p>
<p>Best cut: <b class="good">{summary.get('best_cut_percent')}%</b></p>
<p>Reward score: <b>{summary.get('latest_reward_score')}</b></p>
<p>Profiles/sec: <b>{summary.get('latest_profiles_per_sec')}</b></p>
<p>GPU eval rows: <b>{summary.get('latest_gpu_eval_rows')}</b> | GPU matrix rows: <b>{summary.get('latest_gpu_matrix_rows')}</b></p>
<p>Plateau: <b class="warn">{summary.get('plateau')}</b></p>
<p>Top fail: <b class="bad">{html.escape(str(summary.get('latest_top_fail')))}</b></p>
</div>

<div class="card">
<h2>Round History</h2>
<table>
<tr>
<th>Round</th><th>Diff</th><th>Full</th><th>Safe</th><th>Agg</th><th>Extreme</th><th>Risky</th><th>Starve</th>
<th>Fail</th><th>Regr</th><th>Best Mode</th><th>Cut %</th><th>Reward</th><th>P/S</th><th>GPU Eval</th><th>GPU Matrix</th><th>Top Fail</th>
</tr>
{''.join(rows)}
</table>
</div>

<div class="card">
<h2>Repo Weight Audit</h2>
<p>Active repo MB excluding venv/git/outputs: <b>{summary.get('repo_weight', {}).get('active_repo_mb_excluding_venv_git_outputs')}</b></p>
<table><tr><th>File</th><th>MB</th></tr>{file_rows}</table>
</div>
</body>
</html>"""
    DASHBOARD_FILE.write_text(content, encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    PATCH_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

    training_module = battle._load_training_module()
    seeds = [battle._normalize_case(x) for x in battle._load_seed_cases(training_module)]

    rounds = load_json(ROUNDS_FILE, [])
    lessons = load_json(LESSONS_FILE, {"optimizer_wins": 0, "enemy_wins": 0, "failures": {}, "patch_candidates": []})

    gpu_info = gpu_warmup()

    print(f"Loaded seeds: {len(seeds)}", flush=True)
    print(f"GPU info: {gpu_info}", flush=True)
    print(f"Dashboard: {DASHBOARD_FILE}", flush=True)

    if args.open_dashboard:
        try:
            os.startfile(str(DASHBOARD_FILE))
        except Exception:
            pass

    deadline = time.time() + args.minutes * 60.0
    round_no = int(rounds[-1]["round"]) + 1 if rounds else 1

    while time.time() < deadline:
        try:
            start = time.perf_counter()
            difficulty = min(50, 1 + int(lessons.get("optimizer_wins", 0)) // 2)

            cases = []
            idx = 0
            while len(cases) < args.max_cases_per_round:
                seed = seeds[idx % len(seeds)]
                cases.append(mutate_case(seed, round_no, idx, difficulty))
                idx += 1

            modes = ["safe", "aggressive", "extreme", "risky", "starvation"]
            cut_cases = {
                mode: [cut_case(c, mode, round_no, i) for i, c in enumerate(cases)]
                for mode in modes
            }

            full_size = sum(profile_size_score(c.get("challenged_profile", {})) for c in cases)
            cuts = {}
            for mode in modes:
                size = sum(profile_size_score(c.get("challenged_profile", {})) for c in cut_cases[mode])
                cuts[mode] = max(0.0, 1.0 - size / max(1, full_size))

            gpu_matrix = [gpu_matrix_score(cases, "full")]
            for mode in modes:
                gpu_matrix.append(gpu_matrix_score(cut_cases[mode], mode))

            full_results, _, full_gpu = evaluate(cases, args)
            cut_results = {}
            cut_gpu = {}
            for mode in modes:
                r, _, g = evaluate(cut_cases[mode], args)
                cut_results[mode] = r
                cut_gpu[mode] = g

            full_failures = [r for r in full_results if not r.get("passed")]
            full_acc = (len(full_results) - len(full_failures)) / max(1, len(full_results))

            accs = {}
            regrs = {}
            all_regressions = []
            for mode in modes:
                fails = [r for r in cut_results[mode] if not r.get("passed")]
                accs[mode] = (len(cut_results[mode]) - len(fails)) / max(1, len(cut_results[mode]))
                regr = regression_rows(full_results, cut_results[mode], mode)
                regrs[mode] = len(regr)
                all_regressions.extend(regr)

            valid_modes = [
                mode for mode in modes
                if accs[mode] >= full_acc and regrs[mode] == 0
            ]
            if valid_modes:
                best_mode = max(valid_modes, key=lambda m: cuts[m])
            else:
                best_mode = "none"

            best_cut = cuts.get(best_mode, 0.0)
            reward = reward_score(full_acc, accs, cuts, regrs)

            fail_cats = Counter(battle._normalize_category(x.get("category")) for x in full_failures)
            total_regressions = sum(regrs.values())
            enemy_won = bool(full_failures or total_regressions)

            pc = patch_candidate(round_no, full_failures, all_regressions, reward)
            if pc:
                lessons.setdefault("patch_candidates", []).append(pc.get("path"))

            if enemy_won:
                lessons["enemy_wins"] = int(lessons.get("enemy_wins", 0)) + 1
                for k, v in fail_cats.items():
                    lessons.setdefault("failures", {}).setdefault(k, 0)
                    lessons["failures"][k] += int(v)
            else:
                lessons["optimizer_wins"] = int(lessons.get("optimizer_wins", 0)) + 1

            gpu_eval_rows = int(full_gpu.get("gpu_rows_scored", 0) or 0)
            for mode in modes:
                gpu_eval_rows += int(cut_gpu[mode].get("gpu_rows_scored", 0) or 0)

            gpu_matrix_rows = sum(int(x.get("gpu_matrix_rows", 0) or 0) for x in gpu_matrix)

            wall = time.perf_counter() - start
            total_profiles = len(full_results) * (1 + len(modes))

            row = {
                "round": round_no,
                "difficulty": difficulty,
                "full_accuracy": round(full_acc, 6),
                "safe_accuracy": round(accs["safe"], 6),
                "aggressive_accuracy": round(accs["aggressive"], 6),
                "extreme_accuracy": round(accs["extreme"], 6),
                "risky_accuracy": round(accs["risky"], 6),
                "starvation_accuracy": round(accs["starvation"], 6),
                "full_fail_count": len(full_failures),
                "total_regressions": total_regressions,
                "regressions_by_mode": regrs,
                "cuts_by_mode_percent": {k: round(v * 100, 3) for k, v in cuts.items()},
                "best_mode": best_mode,
                "best_cut_percent": round(best_cut * 100, 3),
                "reward_score": reward,
                "profiles_per_sec": round(total_profiles / wall, 3) if wall else 0.0,
                "wall_seconds": round(wall, 4),
                "gpu_eval_rows": gpu_eval_rows,
                "gpu_matrix_rows": gpu_matrix_rows,
                "top_fail": fail_cats.most_common(1)[0][0] if fail_cats else "none",
            }

            rounds.append(row)
            rounds = rounds[-800:]

            is_plateau = plateau(rounds, args.plateau_window)
            repo_weight = repo_weight_report() if round_no == 1 or round_no % args.repo_audit_every == 0 else load_json(SUMMARY_FILE, {}).get("repo_weight", {})

            summary = {
                "rounds_completed": round_no,
                "optimizer_wins": lessons.get("optimizer_wins", 0),
                "enemy_wins": lessons.get("enemy_wins", 0),
                "latest_best_mode": best_mode,
                "best_cut_percent": row["best_cut_percent"],
                "latest_reward_score": reward,
                "latest_profiles_per_sec": row["profiles_per_sec"],
                "latest_gpu_eval_rows": gpu_eval_rows,
                "latest_gpu_matrix_rows": gpu_matrix_rows,
                "latest_top_fail": row["top_fail"],
                "plateau": is_plateau,
                "gpu_info": gpu_info,
                "repo_weight": repo_weight,
                "lessons": lessons,
                "dashboard": str(DASHBOARD_FILE),
                "rounds_file": str(ROUNDS_FILE),
                "patch_dir": str(PATCH_DIR),
            }

            atomic_json(ROUNDS_FILE, rounds)
            atomic_json(LESSONS_FILE, lessons)
            atomic_json(SUMMARY_FILE, summary)
            atomic_json(CHECKPOINT_DIR / "latest_checkpoint.json", {"summary": summary, "round": row})
            write_dashboard(summary, rounds)

            print(
                f"Round {round_no} | diff={difficulty} | full={len(full_results)-len(full_failures)}/{len(full_results)} | "
                f"safe={accs['safe']:.3f} aggr={accs['aggressive']:.3f} extreme={accs['extreme']:.3f} risky={accs['risky']:.3f} starve={accs['starvation']:.3f} | "
                f"regr={total_regressions} | best={best_mode}:{row['best_cut_percent']}% | reward={reward} | "
                f"p/s={row['profiles_per_sec']} | gpu_eval={gpu_eval_rows} gpu_matrix={gpu_matrix_rows} | top={row['top_fail']} | plateau={is_plateau}",
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
    p.add_argument("--minutes", type=float, default=480)
    p.add_argument("--max-cases-per-round", type=int, default=2000)
    p.add_argument("--batch-size", type=int, default=1024)
    p.add_argument("--cpu-workers", type=int, default=8)
    p.add_argument("--use-gpu", action="store_true")
    p.add_argument("--plateau-window", type=int, default=20)
    p.add_argument("--repo-audit-every", type=int, default=10)
    p.add_argument("--open-dashboard", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))



