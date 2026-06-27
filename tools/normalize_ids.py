"""Helpers for converting display names into stable snake_case ids."""

from __future__ import annotations

import re


def normalize_id(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or "unknown"


if __name__ == "__main__":
    import sys

    for item in sys.argv[1:]:
        print(normalize_id(item))
