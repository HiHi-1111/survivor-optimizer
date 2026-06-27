import json
from pathlib import Path
import subprocess
import sys

from tools.benchmark_optimizer import benchmark
from tools.device_utils import cuda_available
from tools.device_utils import detect_npu
from tools.generate_synthetic_profiles import generate_profiles, load_id_pools, write_profiles
from tools.gpu_scoring import AsyncGpuScorer, score_cpu, score_rows
from tools.train_optimizer import learning_decision_usage, run_training, simulate_profile_actions, stable_metrics_summary
from optimizer.profile_priors import add_observation, build_chart, load_chart, new_chart, recommend_training_plan, record_audit, recover_chart_from_report, save_chart
from optimizer.action_registry import generate_inventory_actions
from optimizer.knowledge_loader import load_knowledge
from optimizer.training_cache import JsonlCache, stable_hash


ROOT = Path(__file__).resolve().parents[1]


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_synthetic_profile_generation_has_no_exact_duplicates_when_avoidable():
    profiles = generate_profiles(count=50, seed=123, stage="mixed")
    states = [json.dumps(profile["player_state"], sort_keys=True) for profile in profiles]

    assert len(profiles) == 50
    assert len(states) == len(set(states))


def test_synthetic_profiles_use_real_knowledge_ids_only():
    profiles = generate_profiles(count=25, seed=456, stage="mixed")
    pools = load_id_pools()
    resource_ids = set(pools["resources"])
    owned_ids = set().union(
            pools["gear"],
            pools["skills"],
        pools["pets"],
        pools["xeno_pets"],
        pools["tech_parts"],
        pools["collectibles"],
        pools["survivors"],
        pools["survivor_awakenings"],
        pools["chests"],
        pools["event_shops"],
        pools["clan_shop"],
        pools["universal_exchange"],
        pools["breakpoints"],
    )

    for profile in profiles:
        state = profile["player_state"]
        assert set(state["resources"]) <= resource_ids
        assert set(state["owned_items"]) <= owned_ids


def test_optimizer_training_resume_and_multiprocessing_tiny_sample(tmp_path):
    profiles_path = tmp_path / "profiles.jsonl"
    results_path = tmp_path / "results.jsonl"
    weights_dir = tmp_path / "knowledge"
    weights_dir.mkdir()
    weights_path = weights_dir / "scoring_weights.json"
    weights_path.write_text((ROOT / "knowledge" / "scoring_weights.json").read_text(encoding="utf-8"), encoding="utf-8")
    write_profiles(generate_profiles(count=6, seed=789, stage="mixed"), profiles_path)

    first = run_training(
        minutes=1,
        workers="2",
        device="cpu",
        resume=False,
        seed=1,
        batch_size=3,
        profiles_path=profiles_path,
        results_path=results_path,
        weights_path=weights_path,
    )
    second = run_training(
        minutes=1,
        workers="2",
        device="cpu",
        resume=True,
        seed=1,
        batch_size=3,
        profiles_path=profiles_path,
        results_path=results_path,
        weights_path=weights_path,
    )

    rows = _jsonl(results_path)
    assert first["profiles_processed"] == 6
    assert second["profiles_processed"] == 0
    assert len(rows) == 6
    assert len({row["profile_id"] for row in rows}) == 6
    assert first["workers"] == 2
    assert first["gpu_acceleration_enabled"] is False
    assert first["full_mode_profiles_per_second"] > 0
    assert first["learned_ranker"] is True
    assert first["learned_ranker_updates"] > 0
    assert first["gpu_scored_chain_coverage_percent"] == 0
    assert weights_path.with_suffix(".tuning.jsonl").exists()
    assert Path(first["assumption_chart_path"]).parent == tmp_path


def test_benchmark_optimizer_runs_on_tiny_profiles(tmp_path):
    profiles_path = tmp_path / "profiles.jsonl"
    write_profiles(generate_profiles(count=5, seed=321, stage="mixed"), profiles_path)

    summary = benchmark(profiles_path=profiles_path, count=5, workers="2")

    assert summary["total_profiles_processed"] == 5
    assert summary["total_actions_tested"] > 0
    assert summary["multiprocessing_working"] is True
    assert summary["gpu_used"] is False


def test_global_planner_reuse_reports_nonzero_deep_chain_metrics():
    profile = generate_profiles(count=1, seed=991, stage="midgame")[0]
    result = simulate_profile_actions(profile, 1, 10, 50, True, True, True, True, True)
    assert result["global_planner_ran"] is True
    assert result["chain_search_reused_global_planner"] is True
    assert result["chain_actions_simulated"] == result["global_plan"]["chains_considered"]
    assert result["chain_actions_simulated"] > 0
    assert result["chains_skipped"] == 0
    assert result["standalone_chain_runs_skipped"] == 1
    assert "global planner reused" in result["chain_simulation_reason"]
    assert result["global_plan"]["gpu_preprune"]["prebuilt_root_actions_reused"] > 0


def test_synthetic_profiles_make_survivor_awakening_observable():
    profiles = generate_profiles(count=50, seed=20260628, stage="mixed")
    awakening_ids = set(load_id_pools()["survivor_awakenings"])
    owned = {
        item_id
        for profile in profiles
        for item_id in profile["player_state"]["owned_items"]
    }
    assert owned & awakening_ids
    knowledge = load_knowledge()
    assert any(
        action["system"] == "survivor_awakening"
        for profile in profiles
        for action in generate_inventory_actions(
            profile["player_state"], knowledge, systems=["survivor_awakening"],
            include_missing_placeholders=False, scoreable_only=True,
        )
    )


def test_resource_catalog_is_not_reported_as_an_unobservable_action_system():
    from optimizer.coverage import coverage_audit_state, coverage_report

    report = coverage_report(load_knowledge(), coverage_audit_state(load_knowledge()))
    assert "resources" in report["catalog_only_systems"]
    assert "resources" not in report["observable_real_data_systems"]
    assert "resources" in report["unobservable_system_reasons"]


def test_stable_metrics_summary_has_unambiguous_gpu_and_learning_fields():
    metrics = {
        "profiles_processed": 12, "profiles_per_second": 3.5,
        "benchmark_valid": True, "startup_failed": False,
        "cuda_preflight": {"passed": True},
        "false_prune_rate": 0.0, "preprune_false_prune_rate": 0.0,
        "learned_pruning_usage_percent": 75.0,
        "learned_reordered_profiles": 9, "learned_pruned_profiles": 0,
        "learned_ranker_samples": 100, "learned_ranker_updates": 90,
        "systems_covered": ["pets"], "observable_real_data_systems": ["pets", "survivor_awakening"],
        "hardware_bottleneck": "CPU candidate generation/search is starving the GPU",
        "gpu_scoring": {
            "gpu_idle_percentage": 68.0, "gpu_idle_reason": "waiting_for_cpu_candidates",
            "gpu_batch_utilization": 61.0, "gpu_wall_rows_per_sec": 3000.0,
            "gpu_waiting_on_cpu": True, "cpu_waiting_on_gpu": False,
        },
    }
    summary = stable_metrics_summary(metrics)
    required = {
        "profiles_tested", "profiles_per_second", "benchmark_valid", "startup_failed",
        "cuda_preflight_passed", "false_prune_rate", "preprune_false_prune_rate",
        "learned_pruning_usage_percent", "learned_reordered_profiles", "learned_pruned_profiles",
        "learned_ranker_samples", "learned_ranker_updates", "systems_covered_count",
        "systems_not_observed", "gpu_idle_percentage", "gpu_idle_reason",
        "gpu_batch_utilization", "gpu_wall_rows_per_sec", "gpu_waiting_on_cpu",
        "cpu_waiting_on_gpu", "main_bottleneck",
    }
    assert required <= set(summary)
    assert summary["systems_not_observed"] == ["survivor_awakening"]
    assert summary["gpu_waiting_on_cpu"] is True


def test_learned_reordering_and_pruning_counters_are_distinct():
    reordered = learning_decision_usage(
        {"reordering_applied": True, "pruning_applied": False, "hard_pruning_blocked_reason": "audit latch"},
        ["pet", "save_hold"],
    )
    pruned = learning_decision_usage(
        {"reordering_applied": True, "pruning_applied": True, "pruned_systems": ["gear"]},
        ["pet", "save_hold"],
    )
    assert reordered == (False, True, "reordered", "audit latch")
    assert pruned[:3] == (True, False, "pruned")


def test_gpu_lifecycle_status_is_pending_not_disabled_before_work():
    scorer = AsyncGpuScorer({}, "cpu", gpu_score=True, batch_size=32)
    scorer.start()
    status = scorer.snapshot()
    assert status["gpu_requested"] is True
    assert status["gpu_pipeline_active"] is False
    assert status["gpu_actually_used"] is False
    assert status["gpu_rows_submitted"] == 0


def test_benchmark_script_runs(tmp_path):
    profiles_path = tmp_path / "profiles.jsonl"
    write_profiles(generate_profiles(count=3, seed=654, stage="mixed"), profiles_path)

    result = subprocess.run(
        [sys.executable, "tools/benchmark_optimizer.py", "--profiles", str(profiles_path), "--count", "3", "--workers", "1"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "profiles_per_second" in result.stdout


def test_gpu_scoring_cpu_path_works():
    rows = [
        {"features": {"immediate_damage": 10, "long_term_damage": 5, "breakpoint_value": 2}},
        {"features": {"immediate_damage": 3, "resource_efficiency": 4, "confidence": 1}},
    ]
    weights = {"default": {"immediate_damage": 1, "long_term_damage": 2, "breakpoint_value": 3, "resource_efficiency": 4, "confidence": 5}}

    scores, summary = score_rows(rows, weights, "cpu", False, 64)

    assert scores == score_cpu(rows, weights)
    assert summary["gpu_used"] is False


def test_cuda_path_skips_cleanly_when_too_small_or_unavailable():
    rows = [{"features": {"immediate_damage": 1}}]
    weights = {"default": {"immediate_damage": 1}}

    scores, summary = score_rows(rows, weights, "cuda", True, 64)

    assert scores == [1.0]
    if cuda_available():
        assert summary["gpu_acceleration_enabled"] is False
        assert "minimum GPU batch threshold" in summary["gpu_acceleration_reason"]
    else:
        assert summary["gpu_used"] is False
        assert "CUDA is unavailable" in summary["gpu_acceleration_reason"]


def test_profile_assumption_chart_learns_similar_bucket_pruning():
    base_profile = generate_profiles(count=1, seed=111, stage="midgame")[0]
    base_profile["player_state"].setdefault("metadata", {}).update({
        "near_breakpoint": False,
        "close_to_xeno_breakpoint": False,
        "close_to_astral_forge_breakpoint": False,
        "close_to_tech_resonance_breakpoint": False,
        "close_to_collectible_set_breakpoint": False,
        "close_to_survivor_breakpoint": False,
    })
    base_profile["player_state"]["close_to_breakpoint"] = False
    for resource_id in ["astral_core", "xeno_core", "resonance_chip"]:
        base_profile["player_state"]["resources"][resource_id] = 0
    profiles = []
    for index in range(25):
        cloned = json.loads(json.dumps(base_profile))
        cloned["id"] = f"similar_{index}"
        profiles.append(cloned)
    results = [
        {
            "profile_id": profile["id"],
            "best_action_id": "chests:open_selector:core",
            "best_score": 50.0,
            "breakpoint_reason": True,
            "self_consistent": True,
            "chain_steps_applied": 2,
            "chain_simulator_ran": True,
            "global_planner_ran": True,
            "global_plan": {"save_hold_recommended": False},
        }
        for profile in profiles
    ]

    chart = build_chart(profiles, results)
    plan = recommend_training_plan(
        profiles[0],
        chart,
        sequence=2,
        base_chain_interval=1,
        base_global_interval=1,
        min_samples=2,
    )

    assert chart["total_samples"] == 25
    assert "chest_opening" in plan["systems"]
    assert plan["samples"] >= 2
    assert "learned priors" in plan["reason"]


def test_low_evidence_does_not_hard_prune():
    base_profile = generate_profiles(count=1, seed=112, stage="midgame")[0]
    profiles = []
    for index in range(2):
        cloned = json.loads(json.dumps(base_profile))
        cloned["id"] = f"low_evidence_{index}"
        profiles.append(cloned)
    results = [
        {
            "profile_id": profile["id"],
            "best_action_id": "chests:open_selector:core",
            "best_score": 50.0,
            "self_consistent": True,
        }
        for profile in profiles
    ]

    chart = build_chart(profiles, results)
    plan = recommend_training_plan(profiles[0], chart, sequence=1, base_chain_interval=1, base_global_interval=1, min_samples=10)

    assert plan["systems"] is None
    assert plan["confidence"] == "low"


def test_saved_profile_chart_accepts_new_explanation_counters(tmp_path):
    profile = generate_profiles(count=1, seed=113, stage="midgame")[0]
    result = {
        "profile_id": profile["id"],
        "best_action_id": "resources:use:astral_core",
        "best_score": 50.0,
        "best_reasons": ["Guide rule: first saved reason"],
        "breakpoint_reason": True,
    }
    chart_path = tmp_path / "profile_assumption_chart.json"
    chart = build_chart([profile], [result])
    save_chart(chart, chart_path)

    resumed = load_chart(chart_path)
    result["best_reasons"] = ["Guide rule: a new reason after resume"]
    add_observation(resumed, profile, result)

    assert resumed["total_samples"] == 2


def test_profile_chart_recovers_only_complete_report_buckets(tmp_path):
    report_path = tmp_path / "profile_prior_report.json"
    report_path.write_text(
        json.dumps(
            {
                "total_samples": 100,
                "top_buckets": [
                    {"bucket": "known", "features": {}, "samples": 25, "systems": {}, "combos": {}}
                ],
            }
        ),
        encoding="utf-8",
    )

    recovered = recover_chart_from_report({"version": 2, "total_samples": 0, "buckets": {}}, report_path)

    assert recovered["total_samples"] == 25
    assert recovered["recovery"]["reported_historical_samples"] == 100


def test_learned_canonical_system_names_reach_registry_generators():
    profile = generate_profiles(count=1, seed=114, stage="midgame")[0]
    knowledge = load_knowledge()

    actions = generate_inventory_actions(profile["player_state"], knowledge, systems=["core_selector", "chest_opening", "pet"])

    assert actions
    assert {action["system"] for action in actions} <= {"resources", "cores", "chests", "pets"}


def test_gpu_scoring_matches_cpu_when_cuda_available():
    if not cuda_available():
        return
    rows = [
        {"features": {"immediate_damage": float(index), "long_term_damage": float(index % 7), "breakpoint_value": 2.0}}
        for index in range(600)
    ]
    weights = {"default": {"immediate_damage": 1.25, "long_term_damage": 0.5, "breakpoint_value": 2.0}}

    cpu_scores = score_cpu(rows, weights)
    gpu_scores, summary = score_rows(rows, weights, "cuda", True, 256)

    assert summary["gpu_used"] is True
    assert len(gpu_scores) == len(cpu_scores)
    assert max(abs(left - right) for left, right in zip(cpu_scores, gpu_scores)) < 1e-3


def test_jsonl_training_cache_round_trips(tmp_path):
    cache = JsonlCache(tmp_path / "cache.jsonl")
    key = stable_hash({"state": 1})
    cache.set(key, {"value": 2})

    reloaded = JsonlCache(tmp_path / "cache.jsonl")

    assert reloaded.get(key)["value"] == 2
    assert reloaded.summary()["hits"] == 1


def test_npu_detection_does_not_crash():
    status = detect_npu()

    assert "available" in status
    assert "reason" in status


def test_full_search_audit_detects_false_prune_and_downgrades():
    profile = generate_profiles(count=1, seed=115, stage="midgame")[0]
    chart = new_chart()
    audit = record_audit(
        chart,
        profile,
        learned_systems=["pet"],
        full_best_system="resources",
        full_score=10.0,
        learned_score=5.0,
        full_best_action_id="resources:spend:astral_core",
        learned_best_action_id="pets:upgrade:murica",
    )
    assert audit["false_prune"] is True
    assert chart["audit"]["false_prunes"] == 1
    assert chart["audit"]["false_prune_examples"][0]["missed_action_id"] == "resources:spend:astral_core"
