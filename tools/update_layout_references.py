"""One-time, idempotent path-reference migration for the professional layout."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def update_layout_references() -> dict[str, int]:
    root_json = str(ROOT).replace("\\", "\\\\")
    replacements = {
        "Untitled document (4).pdf": "data_sources/source_pack/optimizer_source_reference.pdf",
        "Source files received _ data types found.pdf": "data_sources/source_pack/source_database_map.pdf",
        "raw_data/manual_notes/": "data_sources/legacy/manual_notes/",
        str(ROOT / "folder"): str(ROOT / "data_sources" / "source_pack" / "raw"),
        str(ROOT / "processed_images"): str(ROOT / "data_sources" / "extracted" / "processed_images"),
        str(ROOT / "extracted_text"): str(ROOT / "data_sources" / "extracted" / "text"),
        str(ROOT / "training_state"): str(ROOT / "training_outputs" / "state"),
        str(ROOT / "training_outputs" / "simulation_results.jsonl"): str(ROOT / "training_outputs" / "raw" / "simulation_results.jsonl"),
        str(ROOT / "training_outputs" / "synthetic_profiles.jsonl"): str(ROOT / "training_outputs" / "raw" / "synthetic_profiles.jsonl"),
        str(ROOT / "training_outputs" / "latest_debug.log"): str(ROOT / "logs" / "training" / "latest_debug.log"),
        root_json + "\\\\folder": root_json + "\\\\data_sources\\\\source_pack\\\\raw",
        root_json + "\\\\processed_images": root_json + "\\\\data_sources\\\\extracted\\\\processed_images",
        root_json + "\\\\extracted_text": root_json + "\\\\data_sources\\\\extracted\\\\text",
        root_json + "\\\\training_state": root_json + "\\\\training_outputs\\\\state",
    }
    targets: list[Path] = []
    for base in [
        ROOT / "knowledge",
        ROOT / "data_sources" / "extracted" / "ai_outputs",
        ROOT / "data_sources" / "extracted" / "ocr",
        ROOT / "reports",
        ROOT / "training_outputs",
        ROOT / "logs" / "training" / "knowledge_builder",
    ]:
        if base.exists():
            targets.extend(path for path in base.rglob("*") if path.is_file() and path.suffix.lower() in {".json", ".jsonl", ".md", ".txt"})

    changed_files = 0
    replacement_count = 0
    for path in sorted(set(targets)):
        text = path.read_text(encoding="utf-8", errors="ignore")
        updated = text
        for old, new in replacements.items():
            count = updated.count(old)
            if count:
                updated = updated.replace(old, new)
                replacement_count += count
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            changed_files += 1
    return {"changed_files": changed_files, "replacements": replacement_count}


if __name__ == "__main__":
    print(update_layout_references())
