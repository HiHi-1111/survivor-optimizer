"""Fast structural and semantic validation for generated source-pack data."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "knowledge" / "source_pack"
GPU_TABLES = ROOT / "knowledge" / "gpu_tables" / "source_pack"
REPORTS = ROOT / "reports"
REQUIRED = {
    "row_id", "data_type", "system", "name", "source_kind", "confidence",
    "extraction_method", "damage_relevance", "long_term_value", "recommended_disposition",
    "ignore_for_dps", "needs_review", "source", "notes",
}
SOURCE_REQUIRED = {"source_file", "page_or_image", "section"}


def validate() -> dict[str, object]:
    rows = json.loads((PACK / "source_database.json").read_text(encoding="utf-8"))
    actions = json.loads((PACK / "action_templates.json").read_text(encoding="utf-8"))
    errors: list[str] = []
    ids: set[str] = set()
    row_by_id = {}
    for index, row in enumerate(rows):
        missing = REQUIRED - row.keys()
        if missing:
            errors.append(f"row[{index}] missing {sorted(missing)}")
        if row.get("row_id") in ids:
            errors.append(f"duplicate row_id {row.get('row_id')}")
        ids.add(row.get("row_id"))
        row_by_id[row.get("row_id")] = row
        if SOURCE_REQUIRED - row.get("source", {}).keys():
            errors.append(f"{row.get('row_id')} missing source trace")
        if row.get("source_kind") in {"opinion", "placeholder"} and not row.get("needs_review"):
            errors.append(f"{row.get('row_id')} unsafe non-exact row")
        if row.get("source_kind") == "opinion" and row.get("confidence") == "exact":
            errors.append(f"{row.get('row_id')} opinion marked exact")
        if row.get("cost") is not None and not isinstance(row["cost"], (int, float)):
            errors.append(f"{row.get('row_id')} non-numeric cost")
        if row.get("data_type") == "shop_item" and not row.get("currency"):
            errors.append(f"{row.get('row_id')} shop item missing currency")
        if row.get("data_type") == "unlock" and not row.get("unlock_condition"):
            errors.append(f"{row.get('row_id')} unlock missing condition")
        if ("pending" in row.get("name", "").lower() or "icon" in row.get("name", "").lower()) and not row.get("needs_review"):
            errors.append(f"{row.get('row_id')} unclear identity not marked review")
        if (row.get("damage_relevance") == "direct" and row.get("effect_value") is not None
                and not row.get("ignore_for_dps") and not row.get("damage_bucket")):
            errors.append(f"{row.get('row_id')} direct DPS value missing bucket")

    action_ids: set[str] = set()
    for action in actions:
        required_action = {"expected_dps_gain", "breakpoint_distance", "unlock_target", "confidence", "explanation_key", "aliases"}
        if required_action - action.keys():
            errors.append(f"{action.get('action_id')} incomplete numeric template")
        if action["action_id"] in action_ids:
            errors.append(f"duplicate action_id {action['action_id']}")
        action_ids.add(action["action_id"])
        for row_id in action["source_row_ids"]:
            source = row_by_id.get(row_id)
            if not source:
                errors.append(f"{action['action_id']} references missing {row_id}")
            elif source["source_kind"] != "exact" or source["needs_review"]:
                errors.append(f"{action['action_id']} references unsafe {row_id}")

    matrix = json.loads((GPU_TABLES / "numeric_tables.json").read_text(encoding="utf-8"))
    if len(matrix["action_template_ids"]) != len(matrix["action_template_matrix"]):
        errors.append("action numeric table row/id mismatch")
    for map_name, id_map in matrix["id_maps"].items():
        if list(id_map.values()) != list(range(len(id_map))):
            errors.append(f"{map_name} IDs are not compact/stable")
    import hashlib
    expected_hash = hashlib.sha256(json.dumps(matrix["id_maps"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if matrix.get("id_map_hash") != expected_hash:
        errors.append("GPU ID map hash mismatch")
    required_matrices = {
        "profile_feature_matrix", "inventory_feature_matrix", "resource_feature_matrix",
        "action_template_matrix", "shop_item_matrix", "unlock_requirement_matrix",
        "breakpoint_matrix", "chest_expected_value_matrix", "damage_effect_matrix",
    }
    if required_matrices - matrix.keys():
        errors.append(f"missing GPU matrices {sorted(required_matrices - matrix.keys())}")
    report = {
        "valid": not errors,
        "errors": errors,
        "rows": len(rows),
        "actions": len(actions),
        "systems": dict(sorted(Counter(row["system"] for row in rows).items())),
        "exact_rows": sum(row["source_kind"] == "exact" for row in rows),
        "review_rows": sum(bool(row["needs_review"]) for row in rows),
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "source_pack_semantic_validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    result = validate()
    print(json.dumps(result))
    raise SystemExit(0 if result["valid"] else 1)
