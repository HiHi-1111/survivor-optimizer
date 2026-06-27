from pathlib import Path
import json
import subprocess
import sys

from optimizer.damage_engine import estimate_damage_score
from optimizer.models import Scenario
from optimizer.player_state import PlayerState
from optimizer.scorer import should_score_stat
from optimizer.scoring_weights import load_scoring_weights, weight_for
from optimizer.simulator import simulate_upgrade_chain
from tools import compile_knowledge as compile_module
from tools.train_knowledge import chunk_text, extract_optimizer_knowledge, train_knowledge


ROOT = Path(__file__).resolve().parents[1]


def test_training_scans_fake_folder_and_checkpoints(tmp_path):
    data_folder = tmp_path / "folder"
    data_folder.mkdir()
    (data_folder / "guide.txt").write_text(
        "Astral Core is best near an AF breakpoint.\n"
        "Xeno Core scales later for awakening.\n"
        "HP and healing are survival-only comfort stats.",
        encoding="utf-8",
    )
    (data_folder / "screenshot.png").write_bytes(b"not a real png, but it should still be copied")

    state_dir = tmp_path / "training_state"
    output_dir = tmp_path / "training_outputs"
    ai_file = tmp_path / "ai_outputs" / "training_extracted.json"
    extracted_dir = tmp_path / "extracted_text"
    processed_images = tmp_path / "processed_images"

    first = train_knowledge(
        data_folder=data_folder,
        minutes=1,
        device="cpu",
        state_dir=state_dir,
        training_outputs_dir=output_dir,
        ai_outputs_file=ai_file,
        extracted_text_dir=extracted_dir,
        processed_images_dir=processed_images,
        compile_after=False,
    )

    assert first["scanned"] == 2
    assert first["processed"] == 2
    assert ai_file.exists()
    assert (state_dir / "processed_files.json").exists()
    assert (processed_images / "folder" / "screenshot.png").exists()

    data = ai_file.read_text(encoding="utf-8")
    assert "astral_core" in data
    assert '"category": "rule"' in data

    second = train_knowledge(
        data_folder=data_folder,
        minutes=1,
        device="cpu",
        state_dir=state_dir,
        training_outputs_dir=output_dir,
        ai_outputs_file=ai_file,
        extracted_text_dir=extracted_dir,
        processed_images_dir=processed_images,
        compile_after=False,
    )

    assert second["processed"] == 0
    assert second["skipped"] == 2


def test_survival_only_training_records_do_not_score_by_default():
    chunks = chunk_text("HP and healing are priority for survival-only comfort.", "fake_survival_guide.txt")
    output = extract_optimizer_knowledge(chunks, "discord")
    survival_record = output["item_effects"][0]

    scenario = Scenario(id="scenario_1", name="Damage", weights={"damage_score": 1.0})
    assert survival_record["scoring_relevance"] == ["survival", "ignored_by_default"]
    assert should_score_stat(survival_record["scoring_relevance"], scenario) is False


def test_hp_does_not_increase_damage_score():
    base = {"atk": 1000, "crit_rate": 0.5, "crit_damage": 2.0}
    with_hp = {**base, "hp": 999999, "healing": 999}

    assert estimate_damage_score(base) == estimate_damage_score(with_hp)


def test_scoring_weights_load_correctly():
    weights = load_scoring_weights(ROOT / "knowledge")

    assert weights["version"] == 1
    assert weight_for(weights, "scenario_1", "damage_score") > 0
    assert weight_for(weights, "missing_scenario", "breakpoint_score") == weights["default"]["breakpoint_value"]


def test_chain_reaction_simulation_applies_multi_step_upgrade():
    state = PlayerState(resources={"astral_core": 1}, build_stats={"atk": 1000})
    result = simulate_upgrade_chain(
        state,
        [
            {"action": "add_resource", "resource": "astral_core", "amount": 1},
            {"action": "unlock_breakpoint", "id": "ss_af_test_breakpoint", "requirements": {"astral_core": 2}},
            {
                "action": "apply_damage_effect",
                "requires_unlocked": "ss_af_test_breakpoint",
                "stat": "atk",
                "amount": 250,
            },
        ],
    )

    assert result["state"].resources.astral_core == 2
    assert "ss_af_test_breakpoint" in result["state"].owned_items
    assert result["state"].build_stats.atk == 1250
    assert [step["applied"] for step in result["trace"]] == [True, True, True]


def test_compile_logs_conflicts(tmp_path, monkeypatch):
    ai_outputs = tmp_path / "ai_outputs"
    knowledge = tmp_path / "knowledge"
    ai_outputs.mkdir()
    knowledge.mkdir()
    (knowledge / "resources.json").write_text("[]", encoding="utf-8")
    (knowledge / "warnings.json").write_text("[]", encoding="utf-8")
    conflicting = {
        "resources": [
            {
                "id": "test_core",
                "name": "Test Core",
                "category": "resource",
                "description": "Old lower confidence value.",
                "source": "old.txt",
                "source_type": "unknown",
                "date": "2024-01-01",
                "confidence": "low",
                "notes": "old",
                "scoring_relevance": ["resource"],
            },
            {
                "id": "test_core",
                "name": "Test Core",
                "category": "resource",
                "description": "New higher confidence value.",
                "source": "new.txt",
                "source_type": "community-tested",
                "date": "2025-01-01",
                "confidence": "medium",
                "notes": "new",
                "scoring_relevance": ["resource"],
            },
        ]
    }
    (ai_outputs / "conflict.json").write_text(json.dumps(conflicting), encoding="utf-8")

    monkeypatch.setattr(compile_module, "AI_OUTPUTS", ai_outputs)
    monkeypatch.setattr(compile_module, "KNOWLEDGE_DIR", knowledge)
    summary = compile_module.compile_knowledge()

    warnings = json.loads((knowledge / "warnings.json").read_text(encoding="utf-8"))
    resources = json.loads((knowledge / "resources.json").read_text(encoding="utf-8"))
    assert summary["conflicts_found"] == 1
    assert any("Conflicting AI output" in warning["name"] for warning in warnings)
    assert resources[0]["description"] == "New higher confidence value."


def test_run_demo_script_still_works():
    result = subprocess.run(
        [sys.executable, "run_demo.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "top_options" in result.stdout
