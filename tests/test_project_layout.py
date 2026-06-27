"""Targeted checks for the canonical project layout and compatibility paths."""

from __future__ import annotations

import json
from pathlib import Path

import app.cli
import run_demo
from optimizer import paths


ROOT = Path(__file__).resolve().parents[1]


def test_required_top_level_areas_exist() -> None:
    required = {
        "app",
        "optimizer",
        "knowledge",
        "data_sources",
        "tools",
        "tests",
        "docs",
        "reports",
        "training_outputs",
        "logs",
        "archive",
        "tmp",
    }
    assert required <= {path.name for path in ROOT.iterdir() if path.is_dir()}


def test_legacy_root_clutter_is_not_recreated() -> None:
    legacy = {
        "folder",
        "extracted_text",
        "processed_images",
        "raw_data",
        "ai_outputs",
        "ai_prompts",
        "schemas",
        "training_state",
        "training_logs",
    }
    assert not [name for name in legacy if (ROOT / name).exists()]


def test_canonical_paths_point_to_reorganized_content() -> None:
    assert paths.ROOT == ROOT
    assert paths.SOURCE_PACK_RAW_DIR.is_dir()
    assert (paths.SOURCE_PACK_DIR / "source_database_map.pdf").is_file()
    assert (paths.KNOWLEDGE_DIR / "source_pack" / "source_database.json").is_file()
    assert (paths.GPU_TABLES_DIR / "source_pack" / "numeric_tables.json").is_file()
    assert (paths.REVIEW_QUEUE_DIR / "source_pack_queue.jsonl").is_file()
    assert paths.TRAINING_RAW_DIR.is_dir()
    assert paths.TRAINING_STATE_DIR.is_dir()


def test_source_rename_manifest_is_complete() -> None:
    manifest = json.loads((paths.DATA_SOURCES_DIR / "source_manifest.json").read_text(encoding="utf-8"))
    required_fields = {"old_name", "new_name", "file_type", "purpose", "source", "date_moved"}
    assert manifest["moves"]
    assert all(required_fields <= set(move) for move in manifest["moves"])
    assert any(move["old_name"] == "aipromt.txt" for move in manifest["moves"])
    assert any(move["old_name"] == "Untitled document (4).pdf" for move in manifest["moves"])


def test_root_demo_remains_a_compatibility_wrapper() -> None:
    assert run_demo.main is app.cli.main
    assert run_demo.sample_player is app.cli.sample_player
