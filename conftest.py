"""Project-wide pytest reliability hooks for Windows temp paths."""

from __future__ import annotations

from pathlib import Path


def pytest_configure(config) -> None:
    basetemp = getattr(config.option, "basetemp", None)
    if basetemp:
        Path(basetemp).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
