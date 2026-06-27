"""Audit knowledge inventory IDs against action-generator support."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from optimizer.coverage import coverage_audit_state, coverage_report
from optimizer.knowledge_loader import load_knowledge
from optimizer.player_state import PlayerState


OUT_JSON = ROOT / "reports" / "coverage" / "inventory_action_coverage.json"
OUT_MD = ROOT / "reports" / "coverage" / "inventory_action_coverage.md"


def audit() -> dict:
    knowledge = load_knowledge()
    state = coverage_audit_state(knowledge)
    report = coverage_report(knowledge, state)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    lines = [
        "# Inventory Action Coverage",
        "",
        f"- Total known inventory item ids: {report['total_known_inventory_item_ids']}",
        f"- Supported by action generator: {report['total_supported_by_action_generator']}",
        f"- Unsupported ids: {len(report['unsupported_ids'])}",
        f"- Inventory action coverage: {report['inventory_action_coverage_percent']}%",
        f"- Item affordance coverage: {report['item_affordance_coverage_percent']}%",
        f"- Actions generated for audit state: {report['actions_generated_for_audit_state']}",
        "",
        "## Systems With Generators",
        *[f"- {system}" for system in report["systems_with_generators"]],
        "",
        "## Actions By System",
        *[f"- {system}: {count}" for system, count in report["actions_by_system"].items()],
        "",
        "## Unsupported Categories",
    ]
    for section, ids in report["unsupported_categories"].items():
        lines.append(f"- {section}: {len(ids)}")
    lines.extend(["", "## Unsupported IDs"])
    lines.extend(f"- {item_id}" for item_id in report["unsupported_ids"][:500])
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main() -> int:
    report = audit()
    print(f"wrote {OUT_JSON.relative_to(ROOT)}")
    print(f"wrote {OUT_MD.relative_to(ROOT)}")
    print(f"total known inventory item ids: {report['total_known_inventory_item_ids']}")
    print(f"supported by action generator: {report['total_supported_by_action_generator']}")
    print(f"unsupported ids: {len(report['unsupported_ids'])}")
    print(f"supported systems: {', '.join(report['supported_systems']) or 'none'}")
    print(f"unsupported systems: {', '.join(report['unsupported_systems']) or 'none'}")
    print(f"inventory action coverage: {report['inventory_action_coverage_percent']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
