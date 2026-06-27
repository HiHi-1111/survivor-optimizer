from __future__ import annotations

import argparse
import html
import json
import os
import random
import sys
import time
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

try:
    from tools import run_extreme_cut_critic_lab as critic
except Exception:
    critic = None

OUT = ROOT / "training_outputs" / "gpu_policy_sprint"
SUMMARY_FILE = OUT / "gpu_policy_sprint_summary.json"
ROUNDS_FILE = OUT / "gpu_policy_sprint_rounds.json"
POLICY_FILE = OUT / "gpu_cut_policy.json"
DASHBOARD_FILE = OUT / "gpu_policy_sprint_dashboard.html"
ERROR_FILE = OUT / "gpu_policy_sprint_errors.jsonl"
CHECKPOINT_DIR = OUT / "checkpoints"

FEATURE_NAMES = [
    "text_len", "numeric_count", "numeric_sum", "numeric_max",
    "damage", "multiplier", "atk", "dps", "crit",
    "equipped", "selected", "active", "slotted", "unlocked", "owned",
    "relic", "awakening", "core", "shard", "resonance", "blocker",
    "cooldown", "uptime", "cycle", "duration", "seconds",
    "locked", "preview", "future", "catalog", "source",
    "ss", "xeno", "astral", "steamroll",
]

IMPORTANT_INDEXES = {
    "damage": [4, 5, 6, 7, 8],
    "active_flags": [9, 10, 11, 12, 13, 14],
    "rare_blockers": [15, 16, 17, 18, 19, 20],
    "timing": [21, 22, 23, 24, 25],
    "junk": [26, 27, 28, 29, 30],
    "endgame": [31, 32, 33, 34],
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


def load_seeds() -> list[dict[str, Any]]:
    training_module = battle._load_training_module()
    seeds = battle._load_seed_cases(training_module)
    return [battle._normalize_case(x) for x in seeds]


def make_cases(seeds: list[dict[str, Any]], count: int, round_no: int, difficulty: int) -> list[dict[str, Any]]:
    out = []
    for idx in range(count):
        seed = seeds[idx % len(seeds)]
        case = lab.mutate_case(seed, round_no, idx, difficulty)
        if critic is not None and hasattr(critic, "harden_case"):
            try:
                case = critic.harden_case(case, round_no, idx, difficulty)
            except Exception:
                pass
        out.append(case)
    return out


def features_from_cases(cases: list[dict[str, Any]]) -> list[list[float]]:
    rows = []
    for case in cases:
        profile = case.get("challenged_profile", {})
        if isinstance(profile, dict):
            rows.append(lab.numeric_features(profile))
    return rows


def feature_name(i: int) -> str:
    if i < len(FEATURE_NAMES):
        return FEATURE_NAMES[i]
    return f"feature_{i}"


def verify_exact_sample(cases: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    sample = cases[: max(10, min(args.verify_cases, len(cases)))]

    class A:
        use_gpu = True
        batch_size = 256
        cpu_workers = 4

    try:
        starve = [lab.cut_case(c, "starvation", 999, i) for i, c in enumerate(sample)]

        full_results, _, _ = lab.evaluate(sample, A)
        cut_results, _, _ = lab.evaluate(starve, A)

        full_fail = sum(1 for r in full_results if not r.get("passed"))
        cut_fail = sum(1 for r in cut_results if not r.get("passed"))

        regressions = 0
        for a, b in zip(full_results, cut_results):
            if a.get("passed") and not b.get("passed"):
                regressions += 1

        return {
            "verified_cases": len(sample),
            "full_failures": full_fail,
            "starvation_failures": cut_fail,
            "regressions": regressions,
            "ok": full_fail == 0 and cut_fail == 0 and regressions == 0,
        }
    except Exception as exc:
        return {
            "verified_cases": len(sample),
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def gpu_train_policy(feature_rows: list[list[float]], args: argparse.Namespace, round_no: int) -> dict[str, Any]:
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available")

    device = torch.device("cuda")

    base = torch.tensor(feature_rows, dtype=torch.float32, device=device)
    n, f = base.shape

    scale = torch.clamp(base.abs().amax(dim=0, keepdim=True), min=1.0)
    base = base / scale

    logits = torch.nn.Parameter(torch.zeros((f,), dtype=torch.float32, device=device))
    optimizer = torch.optim.Adam([logits], lr=args.lr)

    damage_idx = torch.tensor(IMPORTANT_INDEXES["damage"], device=device)
    active_idx = torch.tensor(IMPORTANT_INDEXES["active_flags"], device=device)
    rare_idx = torch.tensor(IMPORTANT_INDEXES["rare_blockers"], device=device)
    timing_idx = torch.tensor(IMPORTANT_INDEXES["timing"], device=device)
    junk_idx = torch.tensor(IMPORTANT_INDEXES["junk"], device=device)

    # Teacher weights: preserve real useful signals, cut junk/catalog/dead weight.
    weights = torch.ones((f,), device=device) * 0.25
    weights[damage_idx] = 4.0
    weights[active_idx] = 3.0
    weights[rare_idx] = 6.0
    weights[timing_idx] = 4.5
    weights[junk_idx] = -2.5

    gen = torch.Generator(device=device)
    gen.manual_seed(900000 + round_no)

    rows_processed = 0
    start = time.perf_counter()
    last_loss = 0.0
    last_preserve = 0.0
    last_keep = 0.0

    for step in range(1, args.steps + 1):
        idx = torch.randint(0, n, (args.gpu_batch_rows,), device=device, generator=gen)
        x = base[idx]

        noise = torch.randn(x.shape, device=device, generator=gen) * args.noise
        enemy_dropout = (torch.rand(x.shape, device=device, generator=gen) > args.enemy_dropout).float()
        x_enemy = (x + noise) * enemy_dropout

        keep_soft = torch.sigmoid(logits / args.temperature)

        # Force it to prefer keeping protected features, but still pay a size cost.
        cut_x = x_enemy * keep_soft

        full_score = torch.relu(x_enemy @ weights)
        cut_score = torch.relu(cut_x @ weights)

        preserve_loss = torch.mean((cut_score - full_score) ** 2)

        # Group loss: do not delete all rare/timing/damage signal.
        group_losses = []
        for idxs in (damage_idx, active_idx, rare_idx, timing_idx):
            full_group = x_enemy[:, idxs].abs().sum(dim=1)
            cut_group = cut_x[:, idxs].abs().sum(dim=1)
            group_losses.append(torch.mean(torch.relu(full_group * args.group_min_ratio - cut_group)))

        group_loss = sum(group_losses)

        # Junk should be dropped hard.
        junk_keep = keep_soft[junk_idx].mean()

        # Main goal: tiny profile, preserved useful score, junk gone.
        keep_cost = keep_soft.mean()
        loss = preserve_loss + group_loss * args.group_weight + keep_cost * args.keep_weight + junk_keep * args.junk_weight

        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()

        with torch.no_grad():
            logits.clamp_(-12.0, 12.0)

        rows_processed += args.gpu_batch_rows

        if step % args.log_every == 0 or step == args.steps:
            torch.cuda.synchronize()
            last_loss = float(loss.detach().cpu().item())
            last_preserve = float(preserve_loss.detach().cpu().item())
            last_keep = float(keep_soft.mean().detach().cpu().item())

    torch.cuda.synchronize()
    seconds = time.perf_counter() - start

    keep = torch.sigmoid(logits.detach()).cpu().tolist()
    ranked = sorted(
        [
            {
                "index": i,
                "name": feature_name(i),
                "keep_score": round(float(v), 6),
                "drop_score": round(float(1.0 - v), 6),
            }
            for i, v in enumerate(keep)
        ],
        key=lambda x: x["keep_score"],
        reverse=True,
    )

    kept_hard = [x for x in ranked if x["keep_score"] >= args.keep_threshold]
    cut_percent = 100.0 * (1.0 - (len(kept_hard) / max(1, len(ranked))))

    return {
        "gpu_rows_processed": rows_processed,
        "gpu_seconds": round(seconds, 6),
        "gpu_rows_per_sec": round(rows_processed / seconds, 3) if seconds else 0.0,
        "feature_count": f,
        "kept_features": len(kept_hard),
        "cut_percent_by_feature_policy": round(cut_percent, 3),
        "last_loss": round(last_loss, 8),
        "last_preserve_loss": round(last_preserve, 8),
        "last_keep_mean": round(last_keep, 6),
        "top_kept": ranked[:18],
        "top_dropped": list(reversed(ranked))[:18],
        "raw_keep_scores": keep,
    }


def write_dashboard(summary: dict[str, Any], rounds: list[dict[str, Any]]) -> None:
    rows = []
    for r in rounds[-80:][::-1]:
        rows.append(
            "<tr>"
            f"<td>{r.get('round')}</td>"
            f"<td>{r.get('difficulty')}</td>"
            f"<td>{r.get('gpu_rows_processed')}</td>"
            f"<td>{r.get('gpu_rows_per_sec')}</td>"
            f"<td>{r.get('cut_percent_by_feature_policy')}</td>"
            f"<td>{r.get('kept_features')}</td>"
            f"<td>{r.get('verify_ok')}</td>"
            f"<td>{r.get('verify_regressions')}</td>"
            f"<td>{r.get('last_loss')}</td>"
            "</tr>"
        )

    kept = summary.get("latest_top_kept", [])
    dropped = summary.get("latest_top_dropped", [])

    kept_rows = "".join(
        f"<tr><td>{x.get('index')}</td><td>{html.escape(str(x.get('name')))}</td><td>{x.get('keep_score')}</td></tr>"
        for x in kept
    )
    drop_rows = "".join(
        f"<tr><td>{x.get('index')}</td><td>{html.escape(str(x.get('name')))}</td><td>{x.get('drop_score')}</td></tr>"
        for x in dropped
    )

    page = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="5">
<title>GPU-First Policy Sprint</title>
<style>
body {{ background:#101010; color:#eee; font-family:Segoe UI, Arial; margin:24px; }}
.card {{ background:#1b1b1b; border:1px solid #333; border-radius:12px; padding:16px; margin-bottom:16px; }}
.good {{ color:#7CFC98; }}
.bad {{ color:#ff7373; }}
.warn {{ color:#ffd166; }}
table {{ border-collapse:collapse; width:100%; font-size:13px; }}
td, th {{ border:1px solid #333; padding:6px; }}
th {{ background:#222; }}
</style>
</head>
<body>
<h1>Survivor Optimizer: GPU-First Policy Sprint</h1>

<div class="card">
<p>Rounds: <b>{summary.get('rounds_completed')}</b></p>
<p>GPU rows processed latest: <b class="good">{summary.get('latest_gpu_rows_processed')}</b></p>
<p>GPU rows/sec latest: <b>{summary.get('latest_gpu_rows_per_sec')}</b></p>
<p>Policy cut percent: <b class="warn">{summary.get('latest_cut_percent_by_feature_policy')}%</b></p>
<p>Kept features: <b>{summary.get('latest_kept_features')}</b></p>
<p>CPU exact verify OK: <b>{summary.get('latest_verify_ok')}</b></p>
<p>Verify regressions: <b class="bad">{summary.get('latest_verify_regressions')}</b></p>
</div>

<div class="card">
<h2>Round History</h2>
<table>
<tr><th>Round</th><th>Diff</th><th>GPU Rows</th><th>GPU Rows/sec</th><th>Policy Cut %</th><th>Kept</th><th>Verify OK</th><th>Regressions</th><th>Loss</th></tr>
{''.join(rows)}
</table>
</div>

<div class="card">
<h2>Top Kept Features</h2>
<table><tr><th>Index</th><th>Name</th><th>Keep Score</th></tr>{kept_rows}</table>
</div>

<div class="card">
<h2>Top Dropped Features</h2>
<table><tr><th>Index</th><th>Name</th><th>Drop Score</th></tr>{drop_rows}</table>
</div>
</body>
</html>
"""
    DASHBOARD_FILE.write_text(page, encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

    seeds = load_seeds()
    rounds = load_json(ROUNDS_FILE, [])

    print(f"Loaded seeds: {len(seeds)}", flush=True)
    print(f"Dashboard: {DASHBOARD_FILE}", flush=True)
    print(f"GPU batch rows: {args.gpu_batch_rows}", flush=True)
    print(f"GPU steps/cycle: {args.steps}", flush=True)

    if args.open_dashboard:
        try:
            os.startfile(str(DASHBOARD_FILE))
        except Exception:
            pass

    deadline = time.time() + args.minutes * 60.0
    round_no = int(rounds[-1]["round"]) + 1 if rounds else 1

    while time.time() < deadline:
        try:
            difficulty = min(100, 1 + round_no // 2)
            cases = make_cases(seeds, args.case_count, round_no, difficulty)
            rows = features_from_cases(cases)

            gpu_result = gpu_train_policy(rows, args, round_no)
            verify = verify_exact_sample(cases, args)

            row = {
                "round": round_no,
                "difficulty": difficulty,
                "case_count": len(cases),
                "gpu_rows_processed": gpu_result["gpu_rows_processed"],
                "gpu_rows_per_sec": gpu_result["gpu_rows_per_sec"],
                "cut_percent_by_feature_policy": gpu_result["cut_percent_by_feature_policy"],
                "kept_features": gpu_result["kept_features"],
                "last_loss": gpu_result["last_loss"],
                "last_preserve_loss": gpu_result["last_preserve_loss"],
                "last_keep_mean": gpu_result["last_keep_mean"],
                "verify_ok": verify.get("ok"),
                "verify_cases": verify.get("verified_cases"),
                "verify_regressions": verify.get("regressions", 0),
                "verify": verify,
            }

            rounds.append(row)
            rounds = rounds[-500:]

            policy = {
                "round": round_no,
                "difficulty": difficulty,
                "feature_policy": gpu_result,
                "verify": verify,
                "note": "GPU-trained feature retention policy. Exact CPU verifier is still final judge.",
            }

            summary = {
                "rounds_completed": round_no,
                "latest_gpu_rows_processed": row["gpu_rows_processed"],
                "latest_gpu_rows_per_sec": row["gpu_rows_per_sec"],
                "latest_cut_percent_by_feature_policy": row["cut_percent_by_feature_policy"],
                "latest_kept_features": row["kept_features"],
                "latest_verify_ok": row["verify_ok"],
                "latest_verify_regressions": row["verify_regressions"],
                "latest_top_kept": gpu_result["top_kept"],
                "latest_top_dropped": gpu_result["top_dropped"],
                "dashboard": str(DASHBOARD_FILE),
                "policy_file": str(POLICY_FILE),
                "rounds_file": str(ROUNDS_FILE),
            }

            atomic_json(POLICY_FILE, policy)
            atomic_json(ROUNDS_FILE, rounds)
            atomic_json(SUMMARY_FILE, summary)
            atomic_json(CHECKPOINT_DIR / "latest_checkpoint.json", {"summary": summary, "policy": policy})
            write_dashboard(summary, rounds)

            print(
                f"Round {round_no} | diff={difficulty} | gpu_rows={row['gpu_rows_processed']} | "
                f"gpu_rps={row['gpu_rows_per_sec']} | policy_cut={row['cut_percent_by_feature_policy']}% | "
                f"kept={row['kept_features']} | verify_ok={row['verify_ok']} | "
                f"verify_regr={row['verify_regressions']} | loss={row['last_loss']}",
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
    p.add_argument("--case-count", type=int, default=2000)
    p.add_argument("--gpu-batch-rows", type=int, default=262144)
    p.add_argument("--steps", type=int, default=120)
    p.add_argument("--log-every", type=int, default=10)
    p.add_argument("--verify-cases", type=int, default=160)
    p.add_argument("--lr", type=float, default=0.08)
    p.add_argument("--noise", type=float, default=0.04)
    p.add_argument("--enemy-dropout", type=float, default=0.10)
    p.add_argument("--temperature", type=float, default=0.75)
    p.add_argument("--group-min-ratio", type=float, default=0.12)
    p.add_argument("--group-weight", type=float, default=3.0)
    p.add_argument("--keep-weight", type=float, default=1.8)
    p.add_argument("--junk-weight", type=float, default=3.0)
    p.add_argument("--keep-threshold", type=float, default=0.50)
    p.add_argument("--open-dashboard", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
