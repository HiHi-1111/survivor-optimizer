"""Resumable, evidence-first ingestion for Survivor.io guide images and PDFs."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | {".pdf"}
CONFIDENCE_RANK = {"missing": 0, "low": 1, "medium": 2, "high": 3, "confirmed": 4}

SYSTEM_TERMS: dict[str, tuple[str, ...]] = {
    "tech_resonance": ("tech resonance", "resonance", "drone", "hi maintainer", "rpg"),
    "tech_resonance_costs": ("resonance cost", "resonance costs"),
    "gear": ("weapon", "weapons", "boots", "gloves", "necklace", "belt", "armor", "astral forge", "ss gear", "equip", "equipment"),
    "skills": ("skill", "skills", "evolution", "evo"),
    "clan_shop": ("clan shop",),
    "survivors": ("survivor", "survivors", "survivor sp", "combat harmony"),
    "survivor_energy_essence_costs": ("energy essence", "essence cost", "full energy essence"),
    "pet_merging": ("merging pets", "pet merging", "merge pets"),
    "pet_awakenings": ("pet awakening", "rex awakening", "awakening rex"),
    "pets": ("pets", "pet guide", "murica", "croaky", "rex"),
    "crit_stats": ("crit rate", "crit damage", "critical rate", "critical damage"),
    "collectible_chest_odds": ("collectible chest odds", "collectible odds"),
    "collectibles": ("collectible", "collectibles", "collection set"),
    "chest_odds": ("s grade supply", "wishlist", "chest odds", "crate probability", "crate odds"),
    "chests": ("chest", "crate", "supply"),
    "tech_parts": ("tech part", "tech parts", "twinborn"),
    "resources": ("core guide", "relic core", "oil", "resource"),
    "merge": ("merging equip", "merging tech", "merge equip", "merge tech"),
    "conversions": ("conversion", "timed muster", "medal conversion"),
    "events": ("event", "exchange", "clan expedition", "enders echo", "ee rewards"),
    "breakpoints": ("breakpoint", "threshold"),
}

DAMAGE_TERMS = (
    "damage", "dps", "attack", "atk", "crit", "boss", "vulnerability", "skill damage",
    "pet damage", "final damage", "multiplier",
)
SURVIVAL_TERMS = ("hp", "health", "heal", "armor", "revive", "damage reduction", "shield")
NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?%?")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stable_id(*parts: Any) -> str:
    return hashlib.sha256("\0".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:24]


def _snake_case(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", value.lower())).strip("_") or "unknown"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _write_json(path: Path, value: Any) -> None:
    _atomic_text(path, json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    _atomic_text(path, "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows))


def scan_sources(input_dir: Path) -> list[Path]:
    return sorted(path for path in input_dir.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS)


def classify_systems(filename: str, text: str) -> list[str]:
    haystack = re.sub(r"[_-]+", " ", f"{filename} {text}").lower()
    scored = [(system, sum(term in haystack for term in terms)) for system, terms in SYSTEM_TERMS.items()]
    systems = [system for system, score in sorted(scored, key=lambda item: (-item[1], item[0])) if score]
    return systems[:5] or ["unknown"]


def damage_relevance(text: str) -> str:
    lower = text.lower()
    damage = any(term in lower for term in DAMAGE_TERMS)
    survival = any(term in lower for term in SURVIVAL_TERMS)
    if survival and not damage:
        return "survival_ignored_by_default"
    if damage:
        return "damage"
    return "resource_or_utility"


def _preview_pages(source: Path, input_dir: Path, preview_dir: Path) -> list[dict[str, Any]]:
    relative = source.relative_to(input_dir)
    output_parent = preview_dir / relative.parent
    output_parent.mkdir(parents=True, exist_ok=True)
    pages: list[dict[str, Any]] = []
    if source.suffix.lower() == ".pdf":
        try:
            import fitz
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("PyMuPDF is required for PDF preview conversion") from exc
        with fitz.open(source) as document:
            for index, page in enumerate(document, start=1):
                output = output_parent / f"{source.name}.page-{index:03d}.png"
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                pixmap.save(output)
                pages.append({"page": index, "preview": output, "width": pixmap.width, "height": pixmap.height})
        return pages

    try:
        from PIL import Image, ImageOps, ImageSequence
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Pillow is required for image preview conversion") from exc
    with Image.open(source) as image:
        for index, frame in enumerate(ImageSequence.Iterator(image), start=1):
            output = output_parent / f"{source.name}.page-{index:03d}.png"
            converted = ImageOps.exif_transpose(frame.copy()).convert("RGB")
            converted.thumbnail((6000, 6000), Image.Resampling.LANCZOS)
            converted.save(output, "PNG", optimize=True)
            pages.append({"page": index, "preview": output, "width": converted.width, "height": converted.height})
    return pages


def _tesseract_executable() -> str | None:
    found = shutil.which("tesseract")
    if found:
        return found
    windows = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    return str(windows) if windows.exists() else None


def _parse_tsv(tsv: str) -> tuple[str, list[dict[str, Any]], float]:
    words: list[dict[str, Any]] = []
    lines: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t")
    for row in reader:
        text = str(row.get("text", "")).strip()
        try:
            confidence = float(row.get("conf", -1))
        except (TypeError, ValueError):
            confidence = -1
        if not text or confidence < 0:
            continue
        word = {
            "text": text,
            "confidence": round(confidence / 100.0, 4),
            "bbox": [int(row.get("left", 0)), int(row.get("top", 0)), int(row.get("width", 0)), int(row.get("height", 0))],
        }
        words.append(word)
        key = (str(row.get("page_num", "1")), str(row.get("block_num", "0")), str(row.get("par_num", "0")), str(row.get("line_num", "0")))
        lines[key].append(word)
    text_lines = [" ".join(word["text"] for word in line) for line in lines.values()]
    weighted = sum(word["confidence"] * max(1, len(word["text"])) for word in words)
    characters = sum(max(1, len(word["text"])) for word in words)
    return "\n".join(text_lines).strip(), words, round(weighted / characters, 4) if characters else 0.0


def _run_ocr(preview: Path, timeout: int = 180) -> tuple[str, list[dict[str, Any]], float, str, list[str]]:
    executable = _tesseract_executable()
    if not executable:
        return "", [], 0.0, "unavailable", ["Tesseract executable was not found."]
    command = [executable, str(preview), "stdout", "-l", "eng", "--psm", "6", "tsv"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False, encoding="utf-8", errors="ignore")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", [], 0.0, "tesseract_tsv", [f"Tesseract failed: {exc}"]
    text, words, confidence = _parse_tsv(result.stdout)
    warnings = [] if result.returncode == 0 else [f"Tesseract exited with code {result.returncode}: {result.stderr[:500]}"]
    return text, words, confidence, "tesseract_tsv", warnings


def _cached_ocr(source: Path, input_dir: Path) -> str:
    relative = source.relative_to(input_dir)
    cached = ROOT / "data_sources" / "extracted" / "text" / input_dir.name / relative.with_suffix(relative.suffix + ".txt")
    if not cached.exists():
        return ""
    text = cached.read_text(encoding="utf-8", errors="ignore")
    text = re.sub(r"---\s*PAGE\s+\d+\s*---", "", text, flags=re.IGNORECASE).strip()
    return text if len(re.sub(r"\W", "", text)) >= 20 else ""


def _confidence(text: str, score: float, method: str) -> str:
    if not text.strip():
        return "missing"
    if method == "cached_legacy_ocr":
        return "low"
    words = len(text.split())
    if score >= 0.85 and words >= 20:
        return "high"
    if score >= 0.60 and words >= 8:
        return "medium"
    return "low"


def _normalized_numbers(text: str) -> dict[str, Any]:
    tokens = NUMBER_RE.findall(text)
    percentages: list[float] = []
    numbers: list[float] = []
    for token in tokens:
        cleaned = token.replace(",", "")
        try:
            value = float(cleaned.rstrip("%"))
        except ValueError:
            continue
        (percentages if cleaned.endswith("%") else numbers).append(value)
    return {"numbers": numbers, "percentages": percentages}


def _fact_candidates(raw: dict[str, Any]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    systems = raw["systems"]
    confidence = raw["confidence"]
    for line_number, line in enumerate(raw["raw_text"].splitlines(), start=1):
        values = _normalized_numbers(line)
        if not values["numbers"] and not values["percentages"]:
            continue
        if len(line.strip()) < 4:
            continue
        subject = _snake_case(NUMBER_RE.sub(" ", line)[:100])
        ambiguous = subject == "unknown" or len(values["numbers"]) + len(values["percentages"]) > 8
        fact_confidence = confidence if confidence in {"high", "medium"} else "low"
        needs_review = fact_confidence not in {"confirmed", "high"} or ambiguous
        facts.append({
            "id": f"fact_{_stable_id(raw['source_id'], line_number, line)}",
            "canonical_subject": subject,
            "system": systems[0],
            "source_file": raw["source_file"],
            "source_page": raw["source_page"],
            "source_image_hash": raw["source_image_hash"],
            "raw_text": line[:2000],
            "normalized_value": values,
            "confidence": fact_confidence,
            "extraction_method": raw["extraction_method"],
            "needs_review": needs_review,
            "notes": "Numeric evidence extracted without assigning an unverified game meaning.",
            "damage_relevance": damage_relevance(line),
            "source_refs": [f"{raw['source_file']}#page={raw['source_page']}"],
        })
    return facts


def _table_candidates(raw: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw["raw_text"].splitlines(), start=1):
        values = NUMBER_RE.findall(line)
        if len(values) < 2:
            continue
        rows.append({
            "id": f"table_{_stable_id(raw['source_id'], line_number, line)}",
            "source_id": raw["source_id"], "source_file": raw["source_file"], "source_page": raw["source_page"],
            "systems": raw["systems"], "line_number": line_number, "raw_text": line[:2000],
            "detected_values": values, "confidence": raw["confidence"], "needs_review": True,
            "notes": "Candidate table row; column headers and icon meanings require visual verification.",
        })
    return rows


def _conflicts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        grouped[(str(fact["system"]), str(fact["canonical_subject"]))].append(fact)
    conflicts: list[dict[str, Any]] = []
    for (system, subject), candidates in grouped.items():
        values = {json.dumps(candidate["normalized_value"], sort_keys=True) for candidate in candidates}
        sources = {candidate["source_file"] for candidate in candidates}
        if subject == "unknown" or len(values) <= 1 or len(sources) <= 1:
            continue
        conflicts.append({
            "id": f"conflict_{_stable_id(system, subject)}", "system": system, "canonical_subject": subject,
            "candidate_fact_ids": [candidate["id"] for candidate in candidates],
            "source_files": sorted(sources), "status": "manual_review_required",
            "notes": "Different numeric values were extracted for the same normalized subject; no value was overwritten.",
        })
    return conflicts


def _source_confidence_records(manifest: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page in manifest:
        grouped[page["source_file"]].append(page)
    records: list[dict[str, Any]] = []
    for source_file, pages in sorted(grouped.items()):
        confidence = min((page["confidence"] for page in pages), key=lambda value: CONFIDENCE_RANK.get(value, 0))
        records.append({
            "id": f"source_{_stable_id(source_file)}", "name": Path(source_file).name,
            "category": "source_confidence", "description": "OCR/source confidence metadata for a local guide file.",
            "effects": [], "tags": sorted({system for page in pages for system in page["systems"]}),
            "source_type": "discord", "source": source_file, "date": "", "confidence": confidence,
            "notes": "Gameplay values remain in the extraction review workflow until verified.",
            "scoring_relevance": ["utility"], "needs_review": any(page["needs_review"] for page in pages),
            "page_count": len(pages), "source_refs": [source_file],
        })
    return records


def ingest_guides(
    input_dir: Path,
    output_dir: Path,
    *,
    manual_review: bool = False,
    force: bool = False,
    run_ocr: bool = True,
    max_files: int | None = None,
) -> dict[str, Any]:
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    if not input_dir.exists():
        raise FileNotFoundError(f"Guide input folder does not exist: {input_dir}")
    preview_dir = output_dir / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = output_dir / "source_manifest.json"
    raw_path = output_dir / "raw_ocr.jsonl"
    facts_path = output_dir / "structured_facts.jsonl"
    table_path = output_dir / "table_candidates.jsonl"
    review_path = output_dir / "manual_review_queue.jsonl"
    old_manifest = _read_json(manifest_path, [])
    manifest = [row for row in old_manifest if isinstance(row, dict)]
    raw_rows = _read_jsonl(raw_path)
    facts = _read_jsonl(facts_path)
    tables = _read_jsonl(table_path)
    reviews = _read_jsonl(review_path)

    sources = scan_sources(input_dir)
    if max_files is not None:
        sources = sources[: max(0, max_files)]
    current_files = {str(path.relative_to(input_dir)) for path in sources}
    manifest = [row for row in manifest if row.get("source_file") in current_files]
    raw_rows = [row for row in raw_rows if row.get("source_file") in current_files]
    facts = [row for row in facts if row.get("source_file") in current_files]
    tables = [row for row in tables if row.get("source_file") in current_files]
    reviews = [row for row in reviews if row.get("source_file") in current_files]
    processed = skipped = failed = 0

    for source in sources:
        relative = str(source.relative_to(input_dir))
        source_sha = _sha256(source)
        existing_pages = [row for row in manifest if row.get("source_file") == relative]
        if not force and existing_pages and all(row.get("source_sha256") == source_sha and Path(row.get("preview_path", "")).exists() for row in existing_pages):
            skipped += 1
            continue
        old_ids = {row.get("source_id") for row in existing_pages}
        manifest = [row for row in manifest if row.get("source_file") != relative]
        raw_rows = [row for row in raw_rows if row.get("source_id") not in old_ids]
        facts = [row for row in facts if row.get("source_file") != relative]
        tables = [row for row in tables if row.get("source_file") != relative]
        reviews = [row for row in reviews if row.get("source_file") != relative]
        try:
            pages = _preview_pages(source, input_dir, preview_dir)
        except Exception as exc:
            failed += 1
            reviews.append({"id": f"review_{_stable_id(relative, 'preview')}", "source_file": relative, "reason": "preview_conversion_failed", "details": str(exc), "status": "open"})
            continue
        cached_text = _cached_ocr(source, input_dir)
        for page in pages:
            page_number = int(page["page"])
            source_id = f"source_{_stable_id(relative, page_number)}"
            if cached_text and len(pages) == 1:
                text, boxes, score, method, warnings = cached_text, [], 0.0, "cached_legacy_ocr", ["Legacy OCR has no word boxes/confidence; review before promotion."]
            elif run_ocr:
                text, boxes, score, method, warnings = _run_ocr(page["preview"])
            else:
                text, boxes, score, method, warnings = "", [], 0.0, "ocr_disabled", ["OCR was disabled for this run."]
            confidence = _confidence(text, score, method)
            systems = classify_systems(relative, text[:6000])
            needs_review = confidence in {"missing", "low", "medium"}
            preview_hash = _sha256(page["preview"])
            manifest_row = {
                "source_id": source_id, "source_file": relative, "source_page": page_number,
                "source_path": str(source), "source_sha256": source_sha, "source_image_hash": preview_hash,
                "modified_date": datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).isoformat(),
                "extraction_timestamp": _now(), "preview_path": str(page["preview"]),
                "width": page["width"], "height": page["height"], "systems": systems,
                "confidence": confidence, "needs_review": needs_review, "extraction_method": method,
            }
            manifest.append(manifest_row)
            raw = {
                "source_id": source_id, "source_file": relative, "source_page": page_number,
                "source_image_hash": preview_hash, "raw_text": text, "word_boxes": boxes,
                "ocr_confidence": score, "confidence": confidence, "extraction_method": method,
                "systems": systems, "needs_review": needs_review, "notes": "; ".join(warnings),
            }
            raw_rows.append(raw)
            page_facts = _fact_candidates(raw)
            page_tables = _table_candidates(raw)
            facts.extend(page_facts)
            tables.extend(page_tables)
            if needs_review:
                reviews.append({
                    "id": f"review_{source_id}", "source_id": source_id, "source_file": relative,
                    "source_page": page_number, "preview_path": str(page["preview"]), "systems": systems,
                    "reason": "missing_or_uncertain_ocr", "confidence": confidence, "ocr_confidence": score,
                    "raw_text_excerpt": text[:1000], "status": "open", "notes": "; ".join(warnings),
                })
            if manual_review:
                reviews.extend({
                    "id": f"review_{fact['id']}", "source_id": source_id, "source_file": relative,
                    "source_page": page_number, "reason": "numeric_fact_verification", "fact_id": fact["id"],
                    "raw_text": fact["raw_text"], "normalized_value": fact["normalized_value"],
                    "confidence": fact["confidence"], "status": "open",
                } for fact in page_facts if fact["needs_review"])
        processed += 1

    manifest.sort(key=lambda row: (row["source_file"], int(row["source_page"])))
    raw_rows.sort(key=lambda row: (row["source_file"], int(row["source_page"])))
    facts.sort(key=lambda row: row["id"])
    tables.sort(key=lambda row: row["id"])
    dedup_reviews = {str(row.get("id")): row for row in reviews if row.get("id")}
    reviews = sorted(dedup_reviews.values(), key=lambda row: row["id"])
    conflicts = _conflicts(facts)
    for conflict in conflicts:
        reviews.append({"id": f"review_{conflict['id']}", "reason": "conflicting_extractions", "conflict_id": conflict["id"], "status": "open"})

    confidence_counts = Counter(row["confidence"] for row in manifest)
    system_counts = Counter(system for row in manifest for system in row["systems"])
    summary = {
        "input_dir": str(input_dir), "output_dir": str(output_dir), "source_files_discovered": len(sources),
        "source_pages_discovered": len(manifest), "processed_this_run": processed, "skipped_unchanged": skipped,
        "failed_this_run": failed, "raw_ocr_records": len(raw_rows), "structured_fact_candidates": len(facts),
        "table_candidates": len(tables), "manual_review_queue_count": len(reviews), "conflicts": len(conflicts),
        "confidence": dict(sorted(confidence_counts.items())), "systems": dict(sorted(system_counts.items())),
        "generated_at": _now(),
    }
    _write_json(manifest_path, manifest)
    _write_jsonl(raw_path, raw_rows)
    _write_jsonl(facts_path, facts)
    _write_jsonl(table_path, tables)
    _write_jsonl(review_path, reviews)
    _write_json(output_dir / "extraction_conflicts.json", conflicts)
    rejected_fact_ids = {
        str(row.get("fact_id")) for row in reviews
        if str(row.get("status", "")).lower() == "rejected" and row.get("fact_id")
    }
    conflicting_fact_ids = {str(fact_id) for conflict in conflicts for fact_id in conflict.get("candidate_fact_ids", [])}
    accepted_facts = [
        fact for fact in facts
        if fact["id"] not in rejected_fact_ids
        and fact["id"] not in conflicting_fact_ids
        and not fact.get("needs_review", True)
        and fact.get("confidence") in {"confirmed", "high"}
    ]
    uncertain_facts = [fact for fact in facts if fact not in accepted_facts and fact["id"] not in rejected_fact_ids]
    rejected_facts = [fact for fact in facts if fact["id"] in rejected_fact_ids]
    _write_json(output_dir / "accepted_knowledge.json", {
        "schema_version": 1, "game_data_version": "needs_review", "generated_at": _now(),
        "policy": "Only confirmed/high-confidence, non-conflicting facts are accepted automatically.",
        "facts": accepted_facts,
    })
    _write_jsonl(output_dir / "uncertain_entries.jsonl", uncertain_facts)
    _write_jsonl(output_dir / "rejected_entries.jsonl", rejected_facts)
    missing_warnings = [
        {
            "source_file": row["source_file"], "source_page": row["source_page"],
            "systems": row["systems"], "warning": "OCR text is missing; visual manual review is required.",
            "source_ref": f"{row['source_file']}#page={row['source_page']}",
        }
        for row in manifest if row.get("confidence") == "missing"
    ]
    _write_json(output_dir / "missing_data_warnings.json", {"warnings": missing_warnings, "count": len(missing_warnings)})
    _write_json(output_dir / "extraction_confidence_report.json", {
        "confidence_levels": dict(sorted(confidence_counts.items())),
        "usable_without_warning": confidence_counts.get("confirmed", 0) + confidence_counts.get("high", 0),
        "requires_review": sum(confidence_counts.get(level, 0) for level in ["missing", "low", "medium"]),
        "policy": "Only confirmed/high evidence may be promoted; low/missing evidence is never used for final scoring.",
    })
    lines = [
        "# Guide Extraction Summary", "", f"- Source files discovered: {len(sources)}",
        f"- Source pages discovered: {len(manifest)}", f"- Processed this run: {processed}",
        f"- Skipped unchanged: {skipped}", f"- Failed: {failed}", f"- Raw OCR records: {len(raw_rows)}",
        f"- Structured fact candidates: {len(facts)}", f"- Table candidates: {len(tables)}",
        f"- Manual review queue: {len(reviews)}", f"- Conflicts: {len(conflicts)}", "", "## Systems",
        *[f"- {system}: {count}" for system, count in sorted(system_counts.items())], "", "## Confidence",
        *[f"- {level}: {count}" for level, count in sorted(confidence_counts.items())], "",
        "Unreviewed numeric candidates were not written into optimizer scoring knowledge.",
    ]
    _atomic_text(output_dir / "extraction_summary.md", "\n".join(lines) + "\n")
    _write_json(output_dir / "ingest_state.json", {"source_hashes": {row["source_file"]: row["source_sha256"] for row in manifest}, "updated_at": _now()})

    source_confidence_path = (output_dir.parent / "source_confidence.json") if output_dir.name == "extracted" else (output_dir / "source_confidence.json")
    existing_confidence = {row.get("id"): row for row in _read_json(source_confidence_path, []) if isinstance(row, dict) and row.get("id")}
    for record in _source_confidence_records(manifest):
        current = existing_confidence.get(record["id"])
        if current and current.get("confidence") == "confirmed":
            continue
        existing_confidence[record["id"]] = record
    _write_json(source_confidence_path, sorted(existing_confidence.values(), key=lambda row: row["id"]))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest Survivor.io guide images/PDFs into reviewable OCR evidence.")
    parser.add_argument("--input", default="data_sources/source_pack/raw")
    parser.add_argument("--output", default="data_sources/extracted/ocr")
    parser.add_argument("--manual-review", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-ocr", action="store_true")
    parser.add_argument("--max-files", type=int, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input)
    if not input_dir.is_absolute():
        input_dir = ROOT / input_dir
    if not input_dir.exists() and (ROOT / "folder").exists():
        print("canonical source directory is absent; using legacy folder/ for compatibility")
        input_dir = ROOT / "folder"
    output_dir = Path(args.output)
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    try:
        summary = ingest_guides(input_dir, output_dir, manual_review=args.manual_review, force=args.force, run_ocr=not args.no_ocr, max_files=args.max_files)
    except Exception as exc:
        print(f"guide ingestion failed: {exc}")
        return 1
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["failed_this_run"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
