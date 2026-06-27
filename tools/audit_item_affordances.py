"""Audit and compile source-backed affordances for known inventory IDs."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from optimizer.knowledge_loader import load_knowledge


OUT = ROOT / "reports" / "coverage" / "item_affordances.json"
KNOWLEDGE_OUT = ROOT / "knowledge" / "item_affordances.json"
SECTIONS = ["items", "resources", "chests", "gear", "pets", "xeno_pets", "tech_parts", "collectibles", "survivors", "survivor_awakenings", "event_shops", "clan_shop", "universal_exchange"]


def _affordances(section: str, text: str, choices: list[str]) -> list[str]:
    found = {"save_hold"}
    terms = {
        "open": ["chest"], "select": ["selector", "choice"], "spend": ["core", "currency", "essence", "chip"],
        "upgrade": ["upgrade", "forge", "awakening", "resonance", "shard"], "merge": ["gear", "pet", "tech", "merge"],
        "exchange": ["exchange", "shop", "currency"], "salvage_check": ["gear", "pet", "tech", "collectible"],
    }
    for affordance, needles in terms.items():
        if any(needle in text for needle in needles):
            found.add(affordance)
    if choices:
        found.add("select")
    if section in {"event_shops", "clan_shop"}:
        found.add("buy")
    return sorted(found)


def audit_affordances() -> dict:
    knowledge = load_knowledge()
    records = []
    for section in SECTIONS:
        for record in knowledge.get(section, []):
            record_id = str(getattr(record, "id", ""))
            description = str(getattr(record, "description", ""))
            tags = [str(tag) for tag in getattr(record, "tags", [])]
            choices = list(getattr(record, "choices", []) or [])
            text = f"{record_id} {description} {' '.join(tags)}".lower()
            affordances = _affordances(section, text, choices)
            records.append({
                "id": record_id, "section": section, "affordances": affordances,
                "source": str(getattr(record, "source", "")), "confidence": str(getattr(record, "confidence", "low")),
                "has_description": bool(description), "has_effects": bool(getattr(record, "effects", [])), "has_choices": bool(choices),
                "missing_data": [name for name, missing in {"description": not description, "effects": not bool(getattr(record, "effects", [])), "choices_for_selector": "selector" in text and not choices}.items() if missing],
            })
    report = {
        "total_known_items": len(records), "supported_items": sum(bool(row["affordances"]) for row in records),
        "unsupported_items": [row["id"] for row in records if not row["affordances"]],
        "coverage_percent": round((sum(bool(row["affordances"]) for row in records) / len(records) * 100) if records else 100.0, 3),
        "records_audited": len(records), "records_with_affordances": sum(bool(row["affordances"]) for row in records),
        "records_missing_data": sum(bool(row["missing_data"]) for row in records), "records": records,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    OUT.write_text(payload, encoding="utf-8")
    KNOWLEDGE_OUT.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main() -> int:
    report = audit_affordances()
    print(f"wrote {OUT.relative_to(ROOT)} and {KNOWLEDGE_OUT.relative_to(ROOT)}")
    print(f"total known items: {report['total_known_items']}")
    print(f"supported items: {report['supported_items']}")
    print(f"coverage: {report['coverage_percent']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
