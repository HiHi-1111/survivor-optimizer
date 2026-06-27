"""Validate structured knowledge files."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from optimizer.knowledge_loader import knowledge_counts, load_knowledge


def main() -> int:
    try:
        knowledge = load_knowledge()
    except Exception as exc:
        print(f"knowledge validation failed: {exc}")
        return 1

    print("knowledge validation passed")
    for section, count in sorted(knowledge_counts(knowledge).items()):
        print(f"{section}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
