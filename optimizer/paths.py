"""Canonical project paths with read-only legacy fallbacks.

New writes always use the canonical layout. ``existing_path`` may be used by
readers during migration to locate an older checkout without recreating old
top-level clutter.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

APP_DIR = ROOT / "app"
OPTIMIZER_DIR = ROOT / "optimizer"
KNOWLEDGE_DIR = ROOT / "knowledge"
SCHEMAS_DIR = KNOWLEDGE_DIR / "schemas"
REVIEW_QUEUE_DIR = KNOWLEDGE_DIR / "review_queue"
GPU_TABLES_DIR = KNOWLEDGE_DIR / "gpu_tables"

DATA_SOURCES_DIR = ROOT / "data_sources"
SOURCE_PACK_DIR = DATA_SOURCES_DIR / "source_pack"
SOURCE_PACK_RAW_DIR = SOURCE_PACK_DIR / "raw"
EXTRACTED_DIR = DATA_SOURCES_DIR / "extracted"
EXTRACTED_TEXT_DIR = EXTRACTED_DIR / "text"
OCR_DIR = EXTRACTED_DIR / "ocr"
AI_OUTPUTS_DIR = EXTRACTED_DIR / "ai_outputs"
PROCESSED_IMAGES_DIR = EXTRACTED_DIR / "processed_images"

TRAINING_OUTPUTS_DIR = ROOT / "training_outputs"
TRAINING_RAW_DIR = TRAINING_OUTPUTS_DIR / "raw"
TRAINING_STATE_DIR = TRAINING_OUTPUTS_DIR / "state"
TRAINING_BUILD_DIR = TRAINING_OUTPUTS_DIR / "knowledge_build"

REPORTS_DIR = ROOT / "reports"
LOGS_DIR = ROOT / "logs"
ARCHIVE_DIR = ROOT / "archive"
TMP_DIR = ROOT / "tmp"


def existing_path(canonical: Path, *legacy: Path) -> Path:
    """Return the canonical path, or the first existing legacy read path."""
    if canonical.exists():
        return canonical
    for candidate in legacy:
        if candidate.exists():
            return candidate
    return canonical
