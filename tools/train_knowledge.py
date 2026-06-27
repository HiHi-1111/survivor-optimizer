"""Resumable local knowledge builder for the Survivor optimizer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.build_vector_index import build_vector_index
from tools.check_ai_output import check_ai_outputs
from tools.compile_knowledge import compile_knowledge
from tools.device_utils import resolve_device
from tools.extract_dataset import ExtractedFile, dump_extraction_log, extract_file, file_fingerprint, scan_files


TRAINING_STATE = ROOT / "training_outputs" / "state" / "knowledge_builder"
TRAINING_OUTPUTS = ROOT / "training_outputs" / "knowledge_build"
PROCESSED_IMAGES = ROOT / "data_sources" / "extracted" / "processed_images"
EXTRACTED_TEXT = ROOT / "data_sources" / "extracted" / "text"
AI_OUTPUTS = ROOT / "data_sources" / "extracted" / "ai_outputs"
DEFAULT_AI_OUTPUT = AI_OUTPUTS / "training_extracted.json"

SECTIONS = [
    "items",
    "item_effects",
    "gear",
    "gear_sets",
    "weapons",
    "skills",
    "survivors",
    "survivor_awakenings",
    "survivor_energy_essence_costs",
    "pets",
    "pet_merging",
    "pet_awakenings",
    "xeno_pets",
    "tech_parts",
    "tech_resonance",
    "tech_resonance_costs",
    "collectibles",
    "collectible_sets",
    "collectible_chest_odds",
    "resources",
    "chests",
    "chest_odds",
    "events",
    "event_shops",
    "conversions",
    "crit_stats",
    "source_confidence",
    "breakpoints",
    "rules",
    "hidden_interactions",
    "warnings",
]

RULE_KEYWORDS = [
    "best",
    "priority",
    "worth it",
    "trap",
    "avoid",
    "only good if",
    "not worth",
    "breakpoint",
    "scales later",
    "not worth without",
]
HIDDEN_KEYWORDS = [
    "works differently than written",
    "hidden",
    "tested",
    "overperforms",
    "underperforms",
    "veteran knowledge",
]
BREAKPOINT_KEYWORDS = ["threshold", "cost", "stars", "awakening", "cores", "af", "ee", "ce", "pot", "mode"]
SURVIVAL_TERMS = ["hp", "health", "healing", "damage reduction", "armor", "revival", "revive", "shield durability"]
DAMAGE_TERMS = [
    "atk",
    "attack",
    "crit",
    "damage",
    "boss damage",
    "final damage",
    "skill damage",
    "vulnerability",
    "pet damage",
]
KNOWN_RESOURCES = {
    "astral_core": ("Astral Core", ["astral core", "astral cores"], ["core", "astral_forge", "ss_gear"]),
    "xeno_core": ("Xeno Core", ["xeno core", "xeno cores"], ["core", "xeno_pet", "awakening"]),
    "resonance_chip": ("Resonance Chip", ["resonance chip", "resonance chips"], ["tech", "resonance"]),
    "relic_core": ("Relic Core", ["relic core", "relic cores"], ["core", "relic"]),
    "energy_essence": ("Energy Essence", ["energy essence"], ["survivor", "upgrade"]),
    "gem": ("Gem", ["gem", "gems"], ["currency"]),
}


def empty_output() -> dict[str, list[dict[str, Any]]]:
    return {section: [] for section in SECTIONS}


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def snake_case(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower())
    return re.sub(r"_+", "_", value).strip("_") or "unknown"


def source_key(source: str) -> str:
    stem = snake_case(Path(source).stem)
    return stem[:64]


def infer_date(value: str) -> str:
    match = re.search(r"(20\d{2})[-_](\d{2})[-_](\d{2})", value)
    return "-".join(match.groups()) if match else ""


def normalize_device(device: str) -> str:
    resolved, _ = resolve_device(device)
    return resolved


def chunk_text(text: str, source: str, max_chars: int = 1800) -> list[dict[str, Any]]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if not paragraphs and text.strip():
        paragraphs = [text.strip()]

    chunks: list[dict[str, Any]] = []
    current = ""
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) + 2 > max_chars:
            chunks.append({"id": f"{source_key(source)}_{len(chunks) + 1}", "source": source, "text": current.strip()})
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}".strip()
    if current:
        chunks.append({"id": f"{source_key(source)}_{len(chunks) + 1}", "source": source, "text": current.strip()})
    return chunks


def classify_chunk(text: str, source: str) -> list[str]:
    haystack = f"{source} {text}".lower()
    systems: list[str] = []
    checks = {
        "gear": ["gear", "astral forge", "ss gear", "eternal", "void", "chaos"],
        "resources": ["core", "chip", "gem", "essence", "oil"],
        "pets": ["pet", "xeno", "murica", "croaky", "rex"],
        "tech_parts": ["tech", "resonance", "twinborn"],
        "collectibles": ["collectible", "collection"],
        "survivors": ["survivor", "awakening", "combat harmony"],
        "events": ["event", "shop", "clan", "exchange"],
        "damage": DAMAGE_TERMS,
        "survival": SURVIVAL_TERMS,
    }
    for system, terms in checks.items():
        if any(term in haystack for term in terms):
            systems.append(system)
    return systems or ["unknown"]


def sentence_for_keyword(text: str, keywords: list[str]) -> tuple[str, str] | None:
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]
    for sentence in sentences:
        lower = sentence.lower()
        for keyword in keywords:
            if keyword in lower:
                return keyword, sentence[:800]
    return None


def relevance_for(text: str, category: str) -> list[str]:
    lower = text.lower()
    has_survival = any(term in lower for term in SURVIVAL_TERMS)
    has_damage = any(term in lower for term in DAMAGE_TERMS)
    if has_survival and not has_damage:
        return ["survival", "ignored_by_default"]
    if category in {"resource", "chest"}:
        return ["resource"]
    if has_damage:
        return ["damage"]
    return ["utility"]


def base_record(
    record_id: str,
    name: str,
    category: str,
    description: str,
    source: str,
    source_type: str,
    confidence: str = "low",
    notes: str = "",
    tags: list[str] | None = None,
    scoring_relevance: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": record_id,
        "name": name,
        "category": category,
        "description": description,
        "effects": [],
        "tags": tags or [],
        "source": source,
        "source_type": source_type,
        "date": infer_date(source),
        "confidence": confidence,
        "notes": notes,
        "scoring_relevance": scoring_relevance or relevance_for(description, category),
    }


def add_record(output: dict[str, list[dict[str, Any]]], section: str, record: dict[str, Any]) -> None:
    existing_ids = {item.get("id") for item in output[section]}
    if record["id"] not in existing_ids:
        output[section].append(record)


def extract_optimizer_knowledge(chunks: list[dict[str, Any]], source_type: str) -> dict[str, list[dict[str, Any]]]:
    output = empty_output()
    for chunk in chunks:
        text = chunk["text"]
        source = chunk["source"]
        key = source_key(f"{source}_{chunk['id']}")
        lower = text.lower()

        for resource_id, (name, terms, tags) in KNOWN_RESOURCES.items():
            if any(term in lower for term in terms):
                add_record(
                    output,
                    "resources",
                    base_record(
                        resource_id,
                        name,
                        "resource",
                        f"{name} is mentioned as an optimizer resource in the source dataset.",
                        source,
                        source_type,
                        "low",
                        "Heuristic extraction from local training text; verify exact values before trusting recommendations.",
                        tags,
                        ["resource"],
                    ),
                )

        if "selector chest" in lower or "core selector" in lower:
            add_record(
                output,
                "chests",
                {
                    **base_record(
                        f"selector_chest_{key}",
                        "Selector Chest",
                        "chest",
                        "A selector chest is mentioned in the source dataset.",
                        source,
                        source_type,
                        "low",
                        "Heuristic extraction. Exact choices should be verified.",
                        ["selector"],
                        ["resource"],
                    ),
                    "choices": [resource_id for resource_id, (_, terms, _) in KNOWN_RESOURCES.items() if any(term in lower for term in terms)],
                },
            )

        rule_sentence = sentence_for_keyword(text, RULE_KEYWORDS)
        if rule_sentence:
            keyword, sentence = rule_sentence
            add_record(
                output,
                "rules",
                {
                    **base_record(
                        f"rule_{snake_case(keyword)}_{key}",
                        f"Rule: {keyword.title()}",
                        "rule",
                        sentence,
                        source,
                        source_type,
                        "low",
                        "Heuristic rule extracted from trigger wording. Review before treating as final balance guidance.",
                        [snake_case(keyword)],
                        relevance_for(sentence, "rule"),
                    ),
                    "applies_to": classify_chunk(text, source),
                },
            )

        hidden_sentence = sentence_for_keyword(text, HIDDEN_KEYWORDS)
        if hidden_sentence:
            keyword, sentence = hidden_sentence
            add_record(
                output,
                "hidden_interactions",
                base_record(
                    f"hidden_{snake_case(keyword)}_{key}",
                    f"Hidden Interaction: {keyword.title()}",
                    "hidden_interaction",
                    sentence,
                    source,
                    source_type,
                    "low",
                    "Heuristic hidden-interaction extraction from community-style wording.",
                    [snake_case(keyword), "needs_review"],
                    relevance_for(sentence, "hidden_interaction"),
                ),
            )

        breakpoint_sentence = sentence_for_keyword(text, BREAKPOINT_KEYWORDS)
        if breakpoint_sentence:
            keyword, sentence = breakpoint_sentence
            add_record(
                output,
                "breakpoints",
                {
                    **base_record(
                        f"breakpoint_{snake_case(keyword)}_{key}",
                        f"Breakpoint: {keyword.upper()}",
                        "breakpoint",
                        sentence,
                        source,
                        source_type,
                        "low",
                        "Heuristic breakpoint extraction. Exact threshold/cost must be verified.",
                        [snake_case(keyword), "needs_review"],
                        relevance_for(sentence, "breakpoint"),
                    ),
                    "requirements": {},
                },
            )

        if any(term in lower for term in SURVIVAL_TERMS):
            add_record(
                output,
                "item_effects",
                base_record(
                    f"survival_effect_{key}",
                    "Survival-Only Effect",
                    "item_effect",
                    "Survival-only stat mentioned in source text.",
                    source,
                    source_type,
                    "low",
                    "Stored for completeness and ignored by default in damage-first scoring.",
                    ["survival", "ignored_by_default"],
                    ["survival", "ignored_by_default"],
                ),
            )
    return output


def merge_outputs(base: dict[str, list[dict[str, Any]]], incoming: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    merged = empty_output()
    for section in SECTIONS:
        by_id: dict[str, dict[str, Any]] = {}
        no_id: list[dict[str, Any]] = []
        for record in base.get(section, []) + incoming.get(section, []):
            record_id = record.get("id")
            if record_id:
                by_id[str(record_id)] = record
            else:
                no_id.append(record)
        merged[section] = no_id + list(by_id.values())
    return merged


def ensure_output_metadata(output: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    normalized = empty_output()
    for section, records in output.items():
        if section not in normalized:
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            fixed = dict(record)
            fixed.setdefault("source", "unknown")
            fixed.setdefault("source_type", "unknown")
            fixed.setdefault("date", infer_date(str(fixed.get("source", ""))))
            fixed.setdefault("confidence", "low")
            fixed.setdefault("notes", "")
            fixed.setdefault("scoring_relevance", relevance_for(str(fixed.get("description", "")), str(fixed.get("category", section))))
            normalized[section].append(fixed)
    return normalized


def build_search_index(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    index: list[dict[str, Any]] = []
    for chunk in chunks:
        text = chunk["text"]
        keywords = sorted(
            {
                snake_case(term)
                for term in RULE_KEYWORDS + HIDDEN_KEYWORDS + BREAKPOINT_KEYWORDS + DAMAGE_TERMS + SURVIVAL_TERMS
                if term in text.lower()
            }
        )
        index.append(
            {
                "id": chunk["id"],
                "source": chunk["source"],
                "systems": classify_chunk(text, chunk["source"]),
                "keywords": keywords,
                "excerpt": text[:500],
            }
        )
    return index


def warning_record(record_id: str, source: str, source_type: str, description: str, notes: str) -> dict[str, Any]:
    return base_record(
        record_id,
        "Training Warning",
        "warning",
        description,
        source,
        source_type,
        "low",
        notes,
        ["training", "needs_review"],
        ["utility"],
    )


def train_knowledge(
    data_folder: Path,
    minutes: float,
    device: str,
    force: bool = False,
    heavy_gpu: bool = False,
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
    state_dir: Path = TRAINING_STATE,
    training_outputs_dir: Path = TRAINING_OUTPUTS,
    ai_outputs_file: Path = DEFAULT_AI_OUTPUT,
    extracted_text_dir: Path = EXTRACTED_TEXT,
    processed_images_dir: Path = PROCESSED_IMAGES,
    compile_after: bool = True,
) -> dict[str, Any]:
    data_folder = data_folder.resolve()
    if not data_folder.exists():
        raise FileNotFoundError(f"Data folder does not exist: {data_folder}")

    requested_device = device
    device, device_warnings = resolve_device(device)
    state_path = state_dir / "processed_files.json"
    state = read_json(state_path, {"files": {}})
    processed_files: dict[str, Any] = state.setdefault("files", {})
    deadline = time.monotonic() + max(minutes, 0.01) * 60

    training_outputs_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "checkpoints").mkdir(parents=True, exist_ok=True)
    (state_dir / "logs").mkdir(parents=True, exist_ok=True)
    processed_images_dir.mkdir(parents=True, exist_ok=True)
    extracted_text_dir.mkdir(parents=True, exist_ok=True)
    ai_outputs_file.parent.mkdir(parents=True, exist_ok=True)

    existing_output = ensure_output_metadata(read_json(training_outputs_dir / "draft_knowledge.json", empty_output()))
    new_output = empty_output()
    existing_chunks = read_json(training_outputs_dir / "chunks.json", [])
    all_chunks: list[dict[str, Any]] = existing_chunks if isinstance(existing_chunks, list) else []
    new_chunks: list[dict[str, Any]] = []
    extraction_log: list[ExtractedFile] = []
    scanned = 0
    processed = 0
    skipped = 0
    failed = 0

    for path in scan_files(data_folder):
        if time.monotonic() >= deadline:
            break
        relative = str(path.relative_to(data_folder))
        scanned += 1
        fingerprint = file_fingerprint(path)
        previous = processed_files.get(relative)
        if not force and previous and previous.get("fingerprint") == fingerprint:
            skipped += 1
            continue

        extracted = extract_file(path, data_folder, extracted_text_dir, processed_images_dir, device=device)
        extraction_log.append(extracted)
        source_type = extracted.source_type
        if extracted.text:
            chunks = chunk_text(extracted.text, extracted.source)
        else:
            chunks = chunk_text(Path(extracted.source).stem.replace("_", " "), extracted.source)
            add_record(
                new_output,
                "warnings",
                warning_record(
                    f"training_no_text_{source_key(extracted.source)}",
                    extracted.source,
                    source_type,
                    "No readable text was extracted from a dataset file.",
                    "; ".join(extracted.warnings) or "The file may require OCR tooling or manual transcription.",
                ),
            )
        all_chunks.extend(chunks)
        new_chunks.extend(chunks)
        new_output = merge_outputs(new_output, extract_optimizer_knowledge(chunks, source_type))
        for index, warning in enumerate(extracted.warnings):
            add_record(
                new_output,
                "warnings",
                warning_record(
                    f"training_extract_warning_{source_key(extracted.source)}_{index + 1}",
                    extracted.source,
                    source_type,
                    "Dataset extraction warning.",
                    warning,
                ),
            )

        processed_files[relative] = {
            "fingerprint": fingerprint,
            "processed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "text_path": extracted.text_path,
            "processed_path": extracted.processed_path,
            "warnings": extracted.warnings,
        }
        processed += 1
        if extracted.warnings:
            failed += 1
        write_json(state_path, state)

    if processed:
        merged_output = ensure_output_metadata(merge_outputs(existing_output, new_output))
        write_json(training_outputs_dir / "draft_knowledge.json", merged_output)
        write_json(ai_outputs_file, merged_output)
        write_json(training_outputs_dir / "chunks.json", all_chunks)
        write_json(training_outputs_dir / "search_index.json", build_search_index(all_chunks))
        dump_extraction_log(state_dir / "logs" / "last_extraction.json", extraction_log)
    elif not (training_outputs_dir / "draft_knowledge.json").exists():
        write_json(training_outputs_dir / "draft_knowledge.json", existing_output)
        write_json(ai_outputs_file, existing_output)
    else:
        write_json(training_outputs_dir / "draft_knowledge.json", existing_output)
        write_json(ai_outputs_file, existing_output)

    write_json(state_path, state)
    write_json(
        state_dir / "checkpoints" / "latest.json",
        {
            "data_folder": str(data_folder),
            "requested_device": requested_device,
            "device": device,
            "device_warnings": device_warnings,
            "heavy_gpu": heavy_gpu,
            "force": force,
            "scanned": scanned,
            "processed": processed,
            "skipped": skipped,
            "failed_or_warned": failed,
            "stopped_for_time": scanned < len(scan_files(data_folder)),
        },
    )

    vector_summary = None
    if heavy_gpu or device == "cuda":
        vector_summary = build_vector_index(
            all_chunks,
            training_outputs_dir / "vector_index.json",
            device=device,
            model_name=embedding_model,
        )

    check_code = check_ai_outputs(ai_outputs_file.parent)
    compile_summary = compile_knowledge() if compile_after and check_code == 0 else None
    summary = {
        "scanned": scanned,
        "processed": processed,
        "skipped": skipped,
        "failed_or_warned": failed,
        "device": device,
        "device_warnings": device_warnings,
        "heavy_gpu": heavy_gpu,
        "vector_index": vector_summary,
        "check_ai_output": check_code,
        "compile_summary": compile_summary,
    }
    print("training summary")
    for key, value in summary.items():
        if key != "compile_summary":
            print(f"{key}: {value}")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build local optimizer knowledge from a dataset folder.")
    parser.add_argument("--data-folder", default="data_sources/source_pack/raw", help="Dataset folder to scan.")
    parser.add_argument("--minutes", type=float, default=30, help="Maximum run time in minutes.")
    parser.add_argument("--device", choices=["cpu", "gpu", "auto"], default="auto", help="Execution device hint. CPU is the default implementation.")
    parser.add_argument("--force", action="store_true", help="Reprocess unchanged files.")
    parser.add_argument("--heavy-gpu", action="store_true", help="Build optional GPU embedding cache during training when CUDA packages are installed.")
    parser.add_argument("--embedding-model", default="sentence-transformers/all-MiniLM-L6-v2", help="SentenceTransformer model for optional vector index building.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = train_knowledge(
            data_folder=(ROOT / args.data_folder) if not Path(args.data_folder).is_absolute() else Path(args.data_folder),
            minutes=args.minutes,
            device=args.device,
            force=args.force,
            heavy_gpu=args.heavy_gpu,
            embedding_model=args.embedding_model,
        )
    except Exception as exc:
        print(f"training failed: {exc}")
        return 1
    return 0 if summary["check_ai_output"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
