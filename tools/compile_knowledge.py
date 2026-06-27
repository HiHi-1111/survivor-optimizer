"""Compile AI JSON output sections into validated knowledge files."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AI_OUTPUTS = ROOT / "data_sources" / "extracted" / "ai_outputs"
KNOWLEDGE_DIR = ROOT / "knowledge"
KNOWN_SECTIONS = {
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
}
CONFIDENCE_RANK = {"missing": -1, "low": 0, "medium": 1, "high": 2, "confirmed": 3}
METADATA_NOTE = "Missing source metadata from AI output."
SURVIVAL_TERMS = {
    "hp",
    "health",
    "heal",
    "healing",
    "armor",
    "revive",
    "revival",
    "damage reduction",
    "shield durability",
}
DAMAGE_TERMS = {
    "atk",
    "attack",
    "crit",
    "damage",
    "boss",
    "vulnerability",
    "skill damage",
    "final damage",
    "pet damage",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _normalize_record(record: dict[str, Any], source_file: Path) -> dict[str, Any]:
    normalized = dict(record)
    missing_metadata = False
    for field, default in {
        "source": "unknown",
        "source_type": "unknown",
        "date": _infer_date(normalized, source_file),
        "confidence": "low",
        "notes": METADATA_NOTE,
    }.items():
        if not normalized.get(field):
            normalized[field] = default
            missing_metadata = True
    if missing_metadata and normalized.get("notes") != METADATA_NOTE:
        normalized["notes"] = f"{normalized['notes']} {METADATA_NOTE}".strip()
    normalized.setdefault("effects", [])
    normalized.setdefault("tags", [])
    normalized.setdefault("description", "")
    normalized.setdefault("category", "unknown")
    normalized["scoring_relevance"] = _infer_scoring_relevance(normalized)
    normalized["_compiled_from"] = _display_path(source_file)
    return normalized


def _infer_date(record: dict[str, Any], source_file: Path) -> str:
    for value in (record.get("date"), record.get("source"), str(source_file)):
        if not value:
            continue
        match = re.search(r"(20\d{2})[-_](\d{2})[-_](\d{2})", str(value))
        if match:
            return "-".join(match.groups())
    return ""


def _infer_scoring_relevance(record: dict[str, Any]) -> list[str]:
    if record.get("scoring_relevance"):
        value = record["scoring_relevance"]
        return value if isinstance(value, list) else [str(value)]

    text = " ".join(
        str(part).lower()
        for part in [
            record.get("id", ""),
            record.get("name", ""),
            record.get("category", ""),
            record.get("description", ""),
            " ".join(str(tag) for tag in record.get("tags", []) if tag is not None),
        ]
    )
    if any(term in text for term in SURVIVAL_TERMS) and not any(term in text for term in DAMAGE_TERMS):
        return ["survival", "ignored_by_default"]
    if record.get("category") in {"resource", "chest"}:
        return ["resource"]
    if record.get("category") in {"rule", "breakpoint", "hidden_interaction"}:
        return ["utility"]
    if any(term in text for term in DAMAGE_TERMS):
        return ["damage"]
    return ["utility"]


def _records_equivalent(left: dict[str, Any], right: dict[str, Any]) -> bool:
    ignored = {"_compiled_from"}
    return {k: v for k, v in left.items() if k not in ignored} == {
        k: v for k, v in right.items() if k not in ignored
    }


def _prefer_record(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_rank = CONFIDENCE_RANK.get(str(left.get("confidence", "low")), 0)
    right_rank = CONFIDENCE_RANK.get(str(right.get("confidence", "low")), 0)
    if right_rank > left_rank:
        return right
    if left_rank > right_rank:
        return left
    return right if len(json.dumps(right, sort_keys=True, default=str)) > len(json.dumps(left, sort_keys=True, default=str)) else left


def _warning(record_id: str, name: str, description: str, source: str, notes: str) -> dict[str, Any]:
    return {
        "id": record_id,
        "name": name,
        "category": "warning",
        "description": description,
        "effects": [],
        "tags": ["ai_output", "compile"],
        "source_type": "unknown",
        "source": source,
        "date": "",
        "confidence": "low",
        "notes": notes[:1500],
        "scoring_relevance": ["utility"],
    }


def _merge_records(
    section: str,
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    stats = {"added": 0, "duplicates": 0, "conflicts": 0}
    warnings: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    no_id: list[dict[str, Any]] = []

    for record in existing:
        if isinstance(record, dict) and record.get("id"):
            by_id[str(record["id"])] = record
        elif isinstance(record, dict):
            no_id.append(record)

    for record in incoming:
        record_id = record.get("id")
        if not record_id:
            no_id.append(record)
            stats["added"] += 1
            continue

        record_id = str(record_id)
        if record_id not in by_id:
            by_id[record_id] = record
            stats["added"] += 1
            continue

        stats["duplicates"] += 1
        current = by_id[record_id]
        if _records_equivalent(current, record):
            continue

        stats["conflicts"] += 1
        preferred = _prefer_record(current, record)
        by_id[record_id] = preferred
        warnings.append(
            _warning(
                record_id=f"conflict_{section}_{record_id}",
                name=f"Conflicting AI output for {record_id}",
                description=f"Duplicate id {record_id} in {section} had conflicting records. Preferred higher confidence or more complete record.",
                source=str(record.get("_compiled_from", record.get("source", "unknown"))),
                notes=json.dumps({"kept": preferred, "discarded": current if preferred is record else record}, ensure_ascii=False),
            )
        )

    return no_id + list(by_id.values()), stats, warnings


def compile_knowledge() -> dict[str, Any]:
    AI_OUTPUTS.mkdir(parents=True, exist_ok=True)
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(path for path in AI_OUTPUTS.rglob("*.json") if path.is_file())
    if not files:
        print("ai_outputs is empty; keeping existing knowledge files")
        return {
            "files_processed": 0,
            "records_added": {},
            "duplicates_found": 0,
            "conflicts_found": 0,
            "warnings_added": 0,
        }

    incoming: dict[str, list[dict[str, Any]]] = {section: [] for section in KNOWN_SECTIONS}
    for path in files:
        data = _read_json(path)
        if not isinstance(data, dict):
            incoming["warnings"].append(
                _warning(f"invalid_top_level_{path.stem}", "Invalid AI output", "AI output was not a JSON object.", str(path.relative_to(ROOT)), "")
            )
            continue
        for section, value in data.items():
            if section in KNOWN_SECTIONS and isinstance(value, list):
                incoming[section].extend(_normalize_record(record, path) for record in value if isinstance(record, dict))
            elif section in KNOWN_SECTIONS and isinstance(value, dict):
                incoming[section].append(_normalize_record(value, path))
            else:
                incoming["warnings"].append(
                    _warning(
                        f"unknown_section_{path.stem}_{section}".lower(),
                        f"Unknown AI output section: {section}",
                        "AI output contained an unknown top-level section.",
                        str(path.relative_to(ROOT)),
                        json.dumps(value, ensure_ascii=False),
                    )
                )

    summary = {
        "files_processed": len(files),
        "records_added": {},
        "duplicates_found": 0,
        "conflicts_found": 0,
        "warnings_added": 0,
    }
    compile_warnings: list[dict[str, Any]] = []

    for section, records in incoming.items():
        if not records:
            continue
        output_path = KNOWLEDGE_DIR / f"{section}.json"
        existing = _read_json(output_path) if output_path.exists() else []
        if not isinstance(existing, list):
            existing = []
        merged, stats, warnings = _merge_records(section, existing, records)
        compile_warnings.extend(warnings)
        _write_json(output_path, merged)
        summary["records_added"][section] = stats["added"]
        summary["duplicates_found"] += stats["duplicates"]
        summary["conflicts_found"] += stats["conflicts"]

    if compile_warnings:
        warnings_path = KNOWLEDGE_DIR / "warnings.json"
        existing_warnings = _read_json(warnings_path) if warnings_path.exists() else []
        merged_warnings, warning_stats, _ = _merge_records("warnings", existing_warnings, compile_warnings)
        _write_json(warnings_path, merged_warnings)
        summary["warnings_added"] += warning_stats["added"]

    print("compile summary")
    print(f"files processed: {summary['files_processed']}")
    for section, count in sorted(summary["records_added"].items()):
        print(f"records added to {section}: {count}")
    print(f"duplicates found: {summary['duplicates_found']}")
    print(f"conflicts found: {summary['conflicts_found']}")
    print(f"warnings added: {summary['warnings_added']}")
    return summary


if __name__ == "__main__":
    compile_knowledge()
