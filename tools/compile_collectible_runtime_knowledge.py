import json
import re
from pathlib import Path

def slug(text):
    text = str(text or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "unknown"

def clean_confidence(value):
    value = str(value or "").lower()
    if value == "exact":
        return "confirmed"
    if value in {"confirmed", "high", "medium", "low", "missing"}:
        return value
    if value == "inferred":
        return "high"
    return "medium"

def clean_source(value):
    if isinstance(value, dict):
        parts = []
        if value.get("source_file"):
            parts.append(str(value["source_file"]))
        if value.get("page_or_image") is not None:
            parts.append(f"page_or_image={value['page_or_image']}")
        if value.get("section"):
            parts.append(str(value["section"]))
        return " | ".join(parts) if parts else "source_pack"
    return str(value or "source_pack")

def clean_row(row, prefix):
    out = dict(row)

    item_id = row.get("item_id") or row.get("id") or row.get("row_id") or row.get("name")
    out["id"] = slug(item_id)
    out["source"] = clean_source(row.get("source"))
    out["confidence"] = clean_confidence(row.get("confidence"))

    # Keep names readable but stable
    if not out.get("name"):
        out["name"] = out["id"]

    return out

def compile_file(src, dst, prefix, filter_fn=None):
    src_p = Path(src)
    dst_p = Path(dst)

    if not src_p.exists():
        raise FileNotFoundError(src)

    rows = json.loads(src_p.read_text(encoding="utf-8"))
    if filter_fn:
        rows = [r for r in rows if filter_fn(r)]

    cleaned = [clean_row(r, prefix) for r in rows]
    dst_p.write_text(json.dumps(cleaned, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"wrote {dst}: {len(cleaned)} rows")

compile_file(
    "knowledge/source_pack/tables/collectibles.json",
    "knowledge/collectibles.json",
    "collectible",
)

compile_file(
    "knowledge/source_pack/tables/collectible_sets.json",
    "knowledge/collectible_sets.json",
    "collectible_set",
)

compile_file(
    "knowledge/source_pack/tables/chest_odds.json",
    "knowledge/collectible_chest_odds.json",
    "collectible_chest_odds",
    lambda r: (
        "collectible" in str(r.get("subsystem", "")).lower()
        or "collectible" in str(r.get("applies_to", "")).lower()
        or "collectible" in str(r.get("name", "")).lower()
    )
)
