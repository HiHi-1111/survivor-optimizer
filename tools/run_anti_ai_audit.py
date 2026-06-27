from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "training_outputs"
OUT_DIR.mkdir(exist_ok=True)

REPORT_TXT = OUT_DIR / "latest_anti_ai_real_gameplay_report.txt"
REPORT_JSON = OUT_DIR / "latest_anti_ai_audit_report.json"


CATEGORY_RULES = [
    ("gear_state", ["unequipped_owned_gear", "gear", "weapon", "necklace"]),
    ("locked_upgrade_preview", ["locked_af_preview", "future_ss_cosmic_cast", "preview", "locked"]),
    ("survivor_state", ["unselected_survivor", "survivor_roster", "selected"]),
    ("twinborn_mode", ["inactive_twinborn", "twinborn", "inactive mode"]),
    ("tech_resonance_state", ["unslotted_resonance", "resonance", "slotted"]),
    ("pet_state", ["unequipped_pet", "pet_inventory"]),
    ("collectible_state", ["locked_collectible", "collectible", "breakpoint"]),
    ("source_database_contamination", ["source_database", "catalog", "source_pack"]),
    ("material_aliasing", ["material_alias", "relic core", "awakening core", "yang shard"]),
    ("multiplier_parsing", ["multiplier_strings", "2.35x", "source-pack multiplier strings"]),
    ("determinism", ["same_profile", "different damages", "different multipliers"]),
]


def classify_failure(text: str) -> str:
    low = text.lower()
    for category, terms in CATEGORY_RULES:
        if any(term.lower() in low for term in terms):
            return category
    return "unclassified"


def severity_from_text(text: str) -> dict:
    # Example from pytest:
    # clean=60841740.32, trapped=175224211.87
    match = re.search(r"clean=([0-9.]+), trapped=([0-9.]+)", text)
    if not match:
        return {
            "severity": "unknown",
            "clean_damage": None,
            "trapped_damage": None,
            "inflation_ratio": None,
            "inflation_percent": None,
        }

    clean = float(match.group(1))
    trapped = float(match.group(2))
    ratio = trapped / clean if clean else None
    inflation_percent = ((trapped - clean) / clean * 100) if clean else None

    if ratio is None:
        severity = "unknown"
    elif ratio >= 2.0:
        severity = "critical"
    elif ratio >= 1.25:
        severity = "high"
    elif ratio > 1.0:
        severity = "medium"
    else:
        severity = "low"

    return {
        "severity": severity,
        "clean_damage": clean,
        "trapped_damage": trapped,
        "inflation_ratio": round(ratio, 4) if ratio is not None else None,
        "inflation_percent": round(inflation_percent, 2) if inflation_percent is not None else None,
    }


def extract_failures(output: str) -> list[dict]:
    failures = []

    # Capture pytest FAILED lines.
    failed_lines = re.findall(r"FAILED\s+([^\n]+)", output)

    for line in failed_lines:
        category = classify_failure(line)
        related_text = line

        # Try to find nearby assertion text by searching whole output around test name.
        test_name = line.split("::")[-1]
        if test_name:
            idx = output.find(test_name)
            if idx >= 0:
                related_text = output[idx : idx + 2500]

        sev = severity_from_text(related_text)

        failures.append(
            {
                "test": line.strip(),
                "category": category,
                "severity": sev["severity"],
                "clean_damage": sev["clean_damage"],
                "trapped_damage": sev["trapped_damage"],
                "inflation_ratio": sev["inflation_ratio"],
                "inflation_percent": sev["inflation_percent"],
                "training_lesson": lesson_for_category(category),
            }
        )

    return failures


def lesson_for_category(category: str) -> str:
    lessons = {
        "gear_state": "Count only equipped/current gear. Owned inventory copies must not affect current damage.",
        "locked_upgrade_preview": "Do not count future, locked, preview, or missing-resource upgrade bonuses as current damage.",
        "survivor_state": "Only the selected active survivor should affect current damage. Roster entries must not stack.",
        "twinborn_mode": "Only one active Twinborn mode/pair can count at a time.",
        "tech_resonance_state": "Only slotted/equipped resonance assists count. Candidate assists are planning data only.",
        "pet_state": "Only active main pet and equipped assists count. Owned pet inventory must not stack.",
        "collectible_state": "Only unlocked collectible bonuses count. Next breakpoint previews are future goals, not current damage.",
        "source_database_contamination": "Catalog/source-pack/reference rows must never be treated as owned player-state bonuses.",
        "material_aliasing": "Normalize real player wording like Relic Core, S Awakening Core, and Yang shard into canonical blockers.",
        "multiplier_parsing": "Parse realistic multiplier strings like 2.35x and 175% the same as numeric values.",
        "determinism": "Same profile must produce the same damage and multiplier every run.",
        "unclassified": "Review failure and add a new category/rule if this is a real gameplay trap.",
    }
    return lessons.get(category, lessons["unclassified"])


def main() -> int:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-k",
        "anti_ai",
    ]

    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    output = proc.stdout
    REPORT_TXT.write_text(output, encoding="utf-8")

    failures = extract_failures(output)

    summary_match = re.search(r"(\d+) failed, (\d+) passed", output)
    if summary_match:
        failed = int(summary_match.group(1))
        passed = int(summary_match.group(2))
    else:
        failed = len(failures)
        passed_match = re.search(r"(\d+) passed", output)
        passed = int(passed_match.group(1)) if passed_match else 0

    by_category = {}
    by_severity = {}
    for failure in failures:
        by_category[failure["category"]] = by_category.get(failure["category"], 0) + 1
        by_severity[failure["severity"]] = by_severity.get(failure["severity"], 0) + 1

    audit = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "command": " ".join(cmd),
        "summary": {
            "failed": failed,
            "passed": passed,
            "total_checked": failed + passed,
            "status": "pass" if failed == 0 else "fail",
        },
        "by_category": by_category,
        "by_severity": by_severity,
        "failures": failures,
        "next_training_instruction": (
            "Use these failures as adversarial training cases. "
            "Fix optimizer damage logic so it separates current active owned bonuses from locked/future/candidate/source-only data. "
            "Keep false-positive guards passing so real active upgrades still increase damage."
        ),
    }

    REPORT_JSON.write_text(json.dumps(audit, indent=2), encoding="utf-8")

    print(output)
    print("")
    print(f"Saved text report: {REPORT_TXT}")
    print(f"Saved JSON audit: {REPORT_JSON}")

    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
