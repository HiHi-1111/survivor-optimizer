from __future__ import annotations

from typing import Any


DEFAULT_BATTLE_SECONDS = 180.0


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, str):
            text = value.strip().lower().replace(",", "")
            if text.endswith("x"):
                return float(text[:-1])
            if text.endswith("%"):
                raw = float(text[:-1])
                if text.startswith("+"):
                    return 1.0 + raw / 100.0
                return raw / 100.0
            return float(text)
        return float(value)
    except Exception:
        return default


def battle_duration_seconds(profile: dict[str, Any]) -> float:
    for key in ("battle_duration_seconds", "fight_duration_seconds", "round_duration_seconds", "duration_seconds"):
        if key in profile:
            return max(1.0, _num(profile.get(key), DEFAULT_BATTLE_SECONDS))

    meta = profile.get("metadata") if isinstance(profile.get("metadata"), dict) else {}
    for key in ("battle_duration_seconds", "fight_duration_seconds", "round_duration_seconds"):
        if key in meta:
            return max(1.0, _num(meta.get(key), DEFAULT_BATTLE_SECONDS))

    return DEFAULT_BATTLE_SECONDS


def has_timing_fields(row: dict[str, Any]) -> bool:
    keys = {
        "active_seconds",
        "duration_seconds",
        "uptime",
        "uptime_percent",
        "on_seconds",
        "off_seconds",
        "cooldown_seconds",
        "charge_seconds",
        "cycle_seconds",
        "trigger_interval_seconds",
        "proc_interval_seconds",
    }
    return any(key in row for key in keys)


def uptime_ratio(row: dict[str, Any], *, battle_seconds: float = DEFAULT_BATTLE_SECONDS) -> float:
    if "uptime" in row:
        value = _num(row.get("uptime"), 1.0)
        return max(0.0, min(1.0, value if value <= 1 else value / 100.0))

    if "uptime_percent" in row:
        return max(0.0, min(1.0, _num(row.get("uptime_percent"), 100.0) / 100.0))

    active = _num(row.get("active_seconds", row.get("duration_seconds", row.get("on_seconds", 0.0))), 0.0)
    off = _num(row.get("off_seconds", 0.0), 0.0)
    cooldown = _num(row.get("cooldown_seconds", 0.0), 0.0)
    charge = _num(row.get("charge_seconds", 0.0), 0.0)

    cycle = _num(
        row.get(
            "cycle_seconds",
            row.get("trigger_interval_seconds", row.get("proc_interval_seconds", active + off + cooldown + charge)),
        ),
        0.0,
    )

    if active <= 0:
        return 1.0

    if cycle <= 0:
        # One-time effect lasting active seconds inside the normal 3-minute fight.
        return max(0.0, min(1.0, active / max(1.0, battle_seconds)))

    # Strict cycle: active for X, off/charge/cooldown for rest.
    return max(0.0, min(1.0, active / max(active, cycle)))


def effective_multiplier(multiplier: Any, row: dict[str, Any], *, battle_seconds: float = DEFAULT_BATTLE_SECONDS) -> tuple[float, dict[str, Any]]:
    full = _num(multiplier, 1.0)
    if full <= 0:
        full = 1.0

    if not has_timing_fields(row):
        return full, {
            "full_multiplier": full,
            "effective_multiplier": full,
            "uptime": 1.0,
            "battle_seconds": battle_seconds,
            "timed": False,
        }

    up = uptime_ratio(row, battle_seconds=battle_seconds)
    effective = 1.0 + (full - 1.0) * up
    return effective, {
        "full_multiplier": round(full, 6),
        "effective_multiplier": round(effective, 6),
        "uptime": round(up, 6),
        "battle_seconds": round(battle_seconds, 3),
        "timed": True,
        "active_seconds": row.get("active_seconds", row.get("duration_seconds", row.get("on_seconds"))),
        "cycle_seconds": row.get("cycle_seconds", row.get("trigger_interval_seconds", row.get("proc_interval_seconds"))),
        "off_seconds": row.get("off_seconds"),
        "cooldown_seconds": row.get("cooldown_seconds"),
        "charge_seconds": row.get("charge_seconds"),
    }


def row_should_count(row: dict[str, Any]) -> bool:
    text_flags = " ".join(str(value).lower() for value in row.values() if isinstance(value, str))
    if row.get("locked") or row.get("preview") or row.get("future") or row.get("missing") or row.get("missing_shards"):
        return False
    if "locked" in text_flags or "preview" in text_flags or "future" in text_flags:
        return False
    for key in ("equipped", "selected", "active", "slotted", "unlocked", "owned"):
        if key in row and row.get(key) is False:
            return False
    return True


def collect_timed_rows(profile: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def walk(value: Any, path: list[str]) -> None:
        if isinstance(value, dict):
            if "damage_multiplier" in value and has_timing_fields(value) and row_should_count(value):
                system = path[0] if path else "other"
                if system not in {"gear", "survivor", "tech", "pet", "collectibles"}:
                    system = "other"
                eff, detail = effective_multiplier(
                    value.get("damage_multiplier"),
                    value,
                    battle_seconds=battle_duration_seconds(profile),
                )
                rows.append(
                    {
                        "path": ".".join(path),
                        "system": system,
                        "full_multiplier": detail["full_multiplier"],
                        "effective_multiplier": detail["effective_multiplier"],
                        "uptime": detail["uptime"],
                        "battle_seconds": detail["battle_seconds"],
                        "detail": detail,
                    }
                )
            for key, child in value.items():
                walk(child, [*path, str(key)])
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, [*path, str(index)])

    walk(profile, [])
    return rows


def apply_timed_effect_adjustment(profile: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(report, dict):
        return report

    timed_rows = collect_timed_rows(profile)
    if not timed_rows:
        report.setdefault("battle_duration_seconds", battle_duration_seconds(profile))
        report.setdefault("timed_effects", [])
        return report

    breakdown = dict(report.get("multiplier_breakdown") or {})
    for row in timed_rows:
        full = float(row.get("full_multiplier", 1.0) or 1.0)
        effective = float(row.get("effective_multiplier", full) or full)
        if full <= 0:
            continue
        system = str(row.get("system") or "other")
        current = float(breakdown.get(system, 1.0) or 1.0)
        breakdown[system] = round(current * (effective / full), 6)

    final_multiplier = 1.0
    for value in breakdown.values():
        final_multiplier *= float(value or 1.0)

    base_damage = float(report.get("base_damage", profile.get("base_damage", 0.0)) or 0.0)
    report["multiplier_breakdown"] = breakdown
    report["final_damage_multiplier"] = round(final_multiplier, 6)
    report["total_damage"] = round(base_damage * final_multiplier, 6)
    report["battle_duration_seconds"] = battle_duration_seconds(profile)
    report["timed_effects"] = timed_rows
    report["timed_effect_math"] = "effective_multiplier = 1 + (full_multiplier - 1) * uptime over battle_duration_seconds"
    return report
