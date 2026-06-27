"""Check AI output JSON before compiling it into knowledge files."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from optimizer.knowledge_loader import KNOWLEDGE_MODELS


AI_OUTPUTS = ROOT / "data_sources" / "extracted" / "ai_outputs"
ALLOWED_SECTIONS = set(KNOWLEDGE_MODELS) - {"scenarios", "stat_buckets"}
REQUIRED_FIELDS = {"id", "name", "confidence", "source", "source_type", "date", "notes", "scoring_relevance"}


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{_display_path(path)} has invalid JSON: {exc}") from exc


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def check_ai_outputs(ai_outputs_dir: Path | str = AI_OUTPUTS) -> int:
    base = Path(ai_outputs_dir)
    files = sorted(path for path in base.rglob("*.json") if path.is_file())
    if not files:
        print("no AI output JSON files found")
        return 0

    had_errors = False
    total_counts = {section: 0 for section in sorted(ALLOWED_SECTIONS)}

    for path in files:
        data = _load_json(path)
        print(f"file: {_display_path(path)}")
        if not isinstance(data, dict):
            print("  error: top-level JSON must be an object")
            had_errors = True
            continue

        unknown = sorted(set(data) - ALLOWED_SECTIONS)
        if unknown:
            print(f"  unknown top-level keys: {', '.join(unknown)}")

        for section, records in data.items():
            if section not in ALLOWED_SECTIONS:
                continue
            if isinstance(records, dict):
                records = [records]
            if not isinstance(records, list):
                print(f"  warning: {section} must be a list or object")
                continue

            print(f"  {section}: {len(records)}")
            total_counts[section] += len(records)
            model = KNOWLEDGE_MODELS.get(section)
            for index, record in enumerate(records):
                if not isinstance(record, dict):
                    print(f"  warning: {section}[{index}] is not an object")
                    continue
                missing = sorted(REQUIRED_FIELDS - set(record))
                if missing:
                    print(f"  warning: {section}[{index}] missing fields: {', '.join(missing)}")
                if model:
                    try:
                        model(**record)
                    except Exception as exc:
                        print(f"  warning: {section}[{index}] failed model validation: {exc}")

    print("summary:")
    for section, count in total_counts.items():
        if count:
            print(f"  {section}: {count}")
    return 1 if had_errors else 0


def main() -> int:
    try:
        return check_ai_outputs()
    except ValueError as exc:
        print(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
