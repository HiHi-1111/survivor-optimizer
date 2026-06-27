"""Small persistent JSONL caches for optimizer training."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from pathlib import Path
from typing import Any


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class JsonlCache:
    def __init__(
        self, path: Path, flush_every: int = 1, load_existing: bool = True, *,
        max_file_bytes: int | None = None, retain_entries: int = 50000,
    ) -> None:
        self.path = path
        self.rows: dict[str, dict[str, Any]] = {}
        self.hits = 0
        self.misses = 0
        self.flush_every = max(1, int(flush_every))
        self._pending: list[dict[str, Any]] = []
        self.max_file_bytes = int(max_file_bytes) if max_file_bytes else None
        self.retain_entries = max(1, int(retain_entries))
        self.compactions = 0
        if load_existing:
            self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                key = str(row.get("key", ""))
                if key:
                    self.rows[key] = row

    def get(self, key: str) -> dict[str, Any] | None:
        row = self.rows.get(key)
        if row is None:
            self.misses += 1
        else:
            self.hits += 1
        return row

    def set(self, key: str, value: dict[str, Any]) -> None:
        if key in self.rows:
            return
        row = {"key": key, **value}
        self.rows[key] = row
        self._pending.append(row)
        if len(self._pending) >= self.flush_every:
            self.flush()

    def flush(self) -> None:
        if not self._pending:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            for row in self._pending:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        self._pending.clear()

    def close(self) -> None:
        self.flush()
        self._compact_if_needed()

    def _compact_if_needed(self) -> None:
        if not self.max_file_bytes or not self.path.exists() or self.path.stat().st_size <= self.max_file_bytes:
            return
        kept: deque[str] = deque(maxlen=self.retain_entries)
        with self.path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.strip():
                    kept.append(line if line.endswith("\n") else line + "\n")
        temporary = self.path.with_suffix(self.path.suffix + ".compact.tmp")
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            handle.writelines(kept)
        temporary.replace(self.path)
        self.compactions += 1

    def summary(self) -> dict[str, Any]:
        return {
            "path": str(self.path), "entries": len(self.rows), "hits": self.hits,
            "misses": self.misses, "pending_writes": len(self._pending),
            "file_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "compactions": self.compactions,
        }
