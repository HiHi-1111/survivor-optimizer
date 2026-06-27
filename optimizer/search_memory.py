"""Small local searchable guide memory."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX_PATH = ROOT / "training_outputs" / "knowledge_build" / "search_index.json"
EXTRACTED_OCR_PATH = ROOT / "knowledge" / "extracted" / "raw_ocr.jsonl"


def _tokens(text: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9_]+", text.lower()) if len(token) >= 3}


def load_search_index(path: Path | str = DEFAULT_INDEX_PATH) -> list[dict[str, Any]]:
    index_path = Path(path)
    if not index_path.exists():
        return []
    data = json.loads(index_path.read_text(encoding="utf-8"))
    records = data if isinstance(data, list) else []
    if index_path.resolve() == DEFAULT_INDEX_PATH.resolve() and EXTRACTED_OCR_PATH.exists():
        for line in EXTRACTED_OCR_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            records.append({
                "id": raw.get("source_id"), "source": raw.get("source_file", ""),
                "systems": raw.get("systems", []), "keywords": [],
                "excerpt": str(raw.get("raw_text", ""))[:1000], "confidence": raw.get("confidence", "low"),
                "needs_review": raw.get("needs_review", True),
                "source_ref": f"{raw.get('source_file', '')}#page={raw.get('source_page', 1)}",
            })
    return records


def search_guide_memory(query: str, limit: int = 3, path: Path | str = DEFAULT_INDEX_PATH) -> list[dict[str, Any]]:
    query_terms = _tokens(query)
    if not query_terms:
        return []
    matches: list[tuple[int, dict[str, Any]]] = []
    for record in load_search_index(path):
        searchable = " ".join(
            [
                str(record.get("source", "")),
                str(record.get("excerpt", "")),
                " ".join(str(item) for item in record.get("systems", [])),
                " ".join(str(item) for item in record.get("keywords", [])),
            ]
        )
        score = len(query_terms & _tokens(searchable))
        if score:
            matches.append((score, record))
    confidence_rank = {"unknown": 0, "missing": 0, "low": 1, "medium": 2, "high": 3, "confirmed": 4}
    matches.sort(key=lambda item: (item[0], confidence_rank.get(str(item[1].get("confidence", "unknown")), 0)), reverse=True)
    return [
        {
            "source": record.get("source", ""),
            "systems": record.get("systems", []),
            "keywords": record.get("keywords", []),
            "excerpt": record.get("excerpt", ""),
            "match_score": score,
            "confidence": record.get("confidence", "unknown"),
            "needs_review": record.get("needs_review", record.get("confidence", "unknown") not in {"high", "confirmed"}),
            "source_ref": record.get("source_ref", record.get("source", "")),
        }
        for score, record in matches[:limit]
    ]
