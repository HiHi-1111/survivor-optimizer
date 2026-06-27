import json
from pathlib import Path

from PIL import Image

from tools import ingest_guide_images


def _jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_image_pdf_ingest_preserves_evidence_and_review_queue(tmp_path, monkeypatch):
    source = tmp_path / "guides"
    output = tmp_path / "knowledge" / "extracted"
    source.mkdir()
    image = Image.new("RGB", (100, 60), "white")
    image.save(source / "Drone_Resonance_Cost.png")
    image.save(source / "Pet_Awakening_Rex.tiff")
    image.save(source / "Chest_Odds.pdf", "PDF")

    def fake_ocr(path, timeout=180):
        if "Rex" in path.name:
            return "", [], 0.0, "tesseract_tsv", ["unreadable test image"]
        text = "Drone resonance level 10 costs 25 chips and gives 15% damage"
        return text, [{"text": "Drone", "confidence": 0.96, "bbox": [1, 2, 20, 8]}], 0.96, "tesseract_tsv", []

    monkeypatch.setattr(ingest_guide_images, "_run_ocr", fake_ocr)
    summary = ingest_guide_images.ingest_guides(source, output, manual_review=True)

    manifest = json.loads((output / "source_manifest.json").read_text(encoding="utf-8"))
    raw = _jsonl(output / "raw_ocr.jsonl")
    facts = _jsonl(output / "structured_facts.jsonl")
    reviews = _jsonl(output / "manual_review_queue.jsonl")
    assert summary["source_files_discovered"] == 3
    assert len(manifest) == 3
    assert all(Path(row["preview_path"]).suffix == ".png" for row in manifest)
    assert all("source_image_hash" in row and "modified_date" in row for row in manifest)
    assert any(row["word_boxes"] and row["ocr_confidence"] == 0.96 for row in raw)
    assert any(row["confidence"] == "missing" for row in raw)
    assert reviews
    assert facts and all("normalized_value" in row and "source_refs" in row for row in facts)
    accepted = json.loads((output / "accepted_knowledge.json").read_text(encoding="utf-8"))
    assert accepted["schema_version"] == 1
    assert (output / "uncertain_entries.jsonl").exists()
    assert (output / "rejected_entries.jsonl").exists()
    assert json.loads((output / "missing_data_warnings.json").read_text(encoding="utf-8"))["count"] >= 1
    assert not any(path.name == "items.json" for path in output.iterdir())


def test_ingest_checkpoint_skips_unchanged_files(tmp_path, monkeypatch):
    source = tmp_path / "guides"
    output = tmp_path / "knowledge" / "extracted"
    source.mkdir()
    Image.new("RGB", (40, 40), "white").save(source / "Crit_Rate.png")
    monkeypatch.setattr(ingest_guide_images, "_run_ocr", lambda path, timeout=180: ("Crit rate 10%", [], 0.9, "tesseract_tsv", []))
    first = ingest_guide_images.ingest_guides(source, output, manual_review=True)
    second = ingest_guide_images.ingest_guides(source, output, manual_review=True)
    assert first["processed_this_run"] == 1
    assert second["processed_this_run"] == 0
    assert second["skipped_unchanged"] == 1
    assert len(_jsonl(output / "raw_ocr.jsonl")) == 1
