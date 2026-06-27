"""Build a source-traced database from the source-pack map and existing image OCR.

The PDF is an index/explanation layer. Raw files in
``data_sources/source_pack/raw/`` remain the
primary evidence. Curated table readers below only emit exact facts whose
values can be located in the PDF's embedded text; all legacy OCR is retained
as review-only evidence and is never allowed into executable actions.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import fitz


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PDF = ROOT / "data_sources" / "source_pack" / "source_database_map.pdf"
DEFAULT_OUTPUT = ROOT / "knowledge" / "source_pack"
MASTER_NAME = DEFAULT_PDF.name


def slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return value or "unknown"


def stable_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def source(page: int | str, section: str, original: str | None = None) -> dict[str, Any]:
    return {
        "source_file": MASTER_NAME,
        "page_or_image": page,
        "section": section,
        "original_source_file": original,
    }


def row(
    *,
    data_type: str,
    system: str,
    name: str,
    page: int | str,
    section: str,
    source_kind: str = "exact",
    confidence: str = "exact",
    extraction_method: str = "embedded_pdf_table_verified",
    damage_relevance: str | None = None,
    long_term_value: float | None = None,
    recommended_disposition: str | None = None,
    ignore_for_dps: bool = False,
    needs_review: bool = False,
    notes: str = "",
    original_source_file: str | None = None,
    **values: Any,
) -> dict[str, Any]:
    identity = [data_type, system, values.get("subsystem"), values.get("item_id"), name, page]
    for key in ("rarity", "level", "star", "awakening", "effect_type", "currency"):
        identity.append(values.get(key))
    record = {
        "row_id": stable_id("src", *identity),
        "data_type": data_type,
        "system": system,
        "subsystem": values.pop("subsystem", None),
        "item_id": values.pop("item_id", None),
        "name": name,
        "rarity": values.pop("rarity", None),
        "level": values.pop("level", None),
        "star": values.pop("star", None),
        "awakening": values.pop("awakening", None),
        "cost": values.pop("cost", None),
        "currency": values.pop("currency", None),
        "quantity": values.pop("quantity", None),
        "limit": values.pop("limit", None),
        "unlock_condition": values.pop("unlock_condition", None),
        "effect_type": values.pop("effect_type", None),
        "effect_value": values.pop("effect_value", None),
        "effect_unit": values.pop("effect_unit", None),
        "damage_bucket": values.pop("damage_bucket", None),
        "debuff_type": values.pop("debuff_type", None),
        "applies_to": values.pop("applies_to", None),
        "consumed_by": values.pop("consumed_by", None),
        "source_kind": source_kind,
        "confidence": confidence,
        "extraction_method": extraction_method,
        "damage_relevance": damage_relevance or ("survival" if ignore_for_dps else "direct"),
        "long_term_value": long_term_value,
        "recommended_disposition": recommended_disposition or ("needs_review" if needs_review else "profile_dependent"),
        "ignore_for_dps": ignore_for_dps,
        "needs_review": needs_review,
        "source": source(page, section, original_source_file),
        "notes": notes,
        "metadata": values.pop("metadata", {}),
    }
    if values:
        record["metadata"].update(values)
    return record


def raw_row(*, source_file: str, **values: Any) -> dict[str, Any]:
    """Create a record whose primary evidence is a raw source image."""
    values.setdefault("page", 1)
    values.setdefault("original_source_file", source_file)
    record = row(**values)
    record["source"]["source_file"] = source_file
    return record


def extract_pages(pdf_path: Path, output: Path) -> list[str]:
    pages: list[str] = []
    page_dir = ROOT / "data_sources" / "extracted" / "text" / "source_pack" / "pages"
    page_dir.mkdir(parents=True, exist_ok=True)
    with fitz.open(pdf_path) as document:
        for index, pdf_page in enumerate(document):
            text = pdf_page.get_text("text").strip()
            pages.append(text)
            (page_dir / f"page-{index + 1:03d}.txt").write_text(text + "\n", encoding="utf-8")
    jsonl_path = page_dir.parent / "pages.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for index, text in enumerate(pages, 1):
            handle.write(json.dumps({
                "source_file": pdf_path.name,
                "page_or_image": index,
                "section": (text.splitlines() or [f"Page {index}"])[0],
                "confidence": "exact",
                "extraction_method": "pymupdf_embedded_text",
                "needs_review": False,
                "notes": "Verbatim embedded text; interpretation is performed separately.",
                "text": text,
            }, ensure_ascii=False) + "\n")
    output.mkdir(parents=True, exist_ok=True)
    (output / "raw_extraction_pointer.json").write_text(json.dumps({
        "pages_jsonl": str(jsonl_path.relative_to(ROOT)),
        "page_text_directory": str(page_dir.relative_to(ROOT)),
        "page_count": len(pages),
    }, indent=2) + "\n", encoding="utf-8")
    return pages


def require_page_values(pages: list[str], page: int, values: Iterable[object]) -> None:
    normalized = re.sub(r"\s+", " ", pages[page - 1]).lower()
    missing = [str(value) for value in values if str(value).lower() not in normalized]
    if missing:
        raise ValueError(f"Page {page} no longer contains expected evidence: {missing}")


def curated_rows(pages: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    # Pet awakening tables, pp. 58-59.
    pet_levels = ["Y1", "Y2", "Y3", "Y4", "Y5", "R1", "R2", "R3", "R4", "R5"]
    awakening_costs = [5, 5, 10, 20, 30, 40, 60, 60, 60, 60]
    deploy_counts = [3, 3, 5, 5, 7, 7, 9, 9, 9, 15]
    affection_caps = [20, 30, 40, 50, 60, 70, 80, 90, 105, 130]
    affection_atk = [900, 1800, 2700, 3600, 4800, 6000, 7500, 9000, 11300, 15000]
    require_page_values(pages, 58, ["awakening crystals", "350", "Deploy count"])
    require_page_values(pages, 59, ["Affection", "15k max ATK"])
    for index, level in enumerate(pet_levels):
        page = 58 if index < 7 else 59
        rows.append(row(data_type="pet_awakening_cost", system="pets", subsystem="awakening",
                        name=f"Pet awakening {level}", item_id="pet_awakening", awakening=level,
                        cost=awakening_costs[index], currency="awakening_crystal", page=58,
                        section="Pet Awakening Costs", consumed_by="pet_awakening"))
        rows.append(row(data_type="pet_deploy_skill_limit", system="pets", subsystem="awakening",
                        name=f"Pet deploy skill limit {level}", item_id="pet_deploy_skill_limit",
                        awakening=level, quantity=deploy_counts[index], page=58,
                        section="Pet Awakening Deploy Skills", ignore_for_dps=False))
        rows.append(row(data_type="pet_affection_cap", system="pets", subsystem="affection",
                        name=f"Pet affection cap {level}", item_id="pet_affection_cap",
                        awakening=level, quantity=affection_caps[index], page=page,
                        section="Pet Affection Caps", ignore_for_dps=True,
                        notes="Progression capacity; does not directly represent DPS."))
        rows.append(row(data_type="pet_affection_effect", system="pets", subsystem="affection",
                        name=f"Pet affection player ATK {level}", item_id="pet_affection_atk",
                        awakening=level, effect_type="player_attack_flat", effect_value=affection_atk[index],
                        effect_unit="flat", damage_bucket="attack", page=page,
                        section="Pet Affection Player ATK"))
    rows.extend([
        row(data_type="pet_affection_cap", system="pets", subsystem="affection", name="Pet affection cap Base",
            item_id="pet_affection_cap", awakening="Base", quantity=10, page=58, section="Pet Affection Caps", ignore_for_dps=True),
        row(data_type="pet_affection_effect", system="pets", subsystem="affection", name="Pet affection player ATK Base",
            item_id="pet_affection_atk", awakening="Base", effect_type="player_attack_flat", effect_value=300,
            effect_unit="flat", damage_bucket="attack", page=58, section="Pet Affection Player ATK"),
    ])

    # Pet merge recipes from the raw merge chart. Generic fodder remains a
    # typed rarity resource; no pet identity is inferred from icons.
    pet_merge_source = "Merging_-_Pets_2024-11-28_result.png"
    pet_merges = [
        ("normal", "good", [("same_pet_normal", 3)]),
        ("good", "better", [("same_pet_good", 3)]),
        ("better", "excellent", [("same_pet_better", 3)]),
        ("excellent", "excellent+1", [("target_excellent", 1), ("any_excellent_pet", 1)]),
        ("excellent+1", "excellent+2", [("target_excellent+1", 1), ("any_excellent_pet", 2)]),
        ("excellent+2", "epic", [("same_pet_excellent+2", 2)]),
        ("epic", "epic+1", [("target_epic", 1), ("any_epic_pet", 1)]),
        ("epic+1", "epic+2", [("target_epic+1", 1), ("any_epic_pet", 1)]),
        ("epic+2", "epic+3", [("target_epic+2", 1), ("any_epic_pet", 1)]),
        ("epic+3", "legend", [("target_epic+3", 1), ("same_pet_epic", 2)]),
    ]
    for before, after, ingredients in pet_merges:
        for ingredient, amount in ingredients:
            rows.append(raw_row(source_file=pet_merge_source, data_type="pet_merge_rule", system="pet_merging",
                                subsystem="rarity_merge", name=f"Pet merge {before} to {after}: {ingredient}",
                                item_id="pet_rarity_upgrade", rarity=before, cost=amount, currency=ingredient,
                                quantity=1, applies_to=after, effect_type="rarity_upgrade", section="Pet Merge Recipes",
                                extraction_method="visual_table_verified", ignore_for_dps=True,
                                damage_relevance="indirect", recommended_disposition="profile_dependent"))

    # Exact survivor star/awakening material costs from the raw cost chart.
    survivor_cost_source = "Survivor_Upgrade_Costs_2024-12-19_result.png"
    yellow_costs = {
        "normal": [80, 10, 50, 100, 150, 300],
        "s": [120, 40, 80, 120, 200, 400],
        "sp": [10, 10, 20, 30, 60, 100],
    }
    for survivor_type, costs_by_star in yellow_costs.items():
        resource = {"normal": "normal_survivor_shard", "s": "s_survivor_shard", "sp": "sp_pizza"}[survivor_type]
        for star, amount in enumerate(costs_by_star, 1):
            rows.append(raw_row(source_file=survivor_cost_source, data_type="survivor_upgrade_cost", system="survivors",
                                subsystem="yellow_stars", name=f"{survivor_type.upper()} survivor yellow star {star}",
                                item_id=f"{survivor_type}_survivor", star=star, cost=amount, currency=resource,
                                applies_to=f"yellow_star_{star}", section="Survivor Upgrade Costs",
                                extraction_method="visual_table_verified", ignore_for_dps=True,
                                damage_relevance="indirect", recommended_disposition="profile_dependent"))
    red_costs = {
        "normal": ([200, 250, 300, 350, 400, 500], [2400, 3000, 3600, 4200, 4800, 6000], [1, 2, 3, 5, 7, 12]),
        "s": ([200, 250, 300, 350, 400, 500], [3600, 4500, 5400, 6300, 7200, 9000], [1, 2, 4, 8, 15, 30]),
        "sp": ([30, 50, 70, 90, 120, 160], [600, 900, 1200, 1500, 1800, 2100], [1, 1, 1, 1, 1, 1]),
    }
    for survivor_type, (shards, blue_material, cores) in red_costs.items():
        shard_resource = {"normal": "normal_survivor_shard", "s": "s_survivor_shard", "sp": "sp_pizza"}[survivor_type]
        for star in range(1, 7):
            for resource, amount in [(shard_resource, shards[star - 1]), ("blue_awakening_material", blue_material[star - 1]),
                                     ("awakening_core", cores[star - 1])]:
                rows.append(raw_row(source_file=survivor_cost_source, data_type="survivor_awakening_cost", system="survivors",
                                    subsystem="red_stars", name=f"{survivor_type.upper()} survivor R{star}: {resource}",
                                    item_id=f"{survivor_type}_survivor", awakening=f"R{star}", cost=amount,
                                    currency=resource, applies_to=f"red_star_{star}", section="Survivor Upgrade Costs",
                                    extraction_method="visual_table_verified", ignore_for_dps=True,
                                    damage_relevance="indirect", recommended_disposition="profile_dependent"))

    # Dense 1-120 energy-essence chart. Piecewise sequences are transcribed
    # from the raw chart and keep ATK/HP effects separate for DPS filtering.
    essence_source = "Energy_Essence_Cost_Full_2024-11-28_result.png"
    if not (ROOT / "data_sources" / "source_pack" / "raw" / essence_source).exists():
        raise FileNotFoundError(f"Missing required source table: {essence_source}")

    def normal_essence(level: int) -> int:
        if level == 1: return 0
        if level <= 5: return 100
        if level <= 8: return 150
        if level <= 15: return 160 + (level - 9) * 10
        if level <= 54: return 240 + (level - 16) * 20
        if level <= 65: return 1200 + (level - 55) * 200
        if level <= 93: return 3600 + (level - 66) * 400
        if level <= 99: return 15000 + (level - 94) * 600
        if level == 100: return 19000
        return 19500 + (level - 101) * 500

    def s_essence(level: int) -> int:
        if level == 1: return 0
        if level <= 5: return 100
        if level <= 8: return 150
        if level <= 15: return 160 + (level - 9) * 10
        if level <= 39: return 240 + (level - 16) * 20
        if level <= 45: return 750 + (level - 40) * 50
        if level <= 65: return 1200 + (level - 46) * 200
        if level <= 85: return 5500 + (level - 66) * 500
        return 16000 + (level - 86) * 1000

    normal_attack_tiers = [(30, 10), (40, 20), (50, 30), (60, 100), (70, 120),
                           (80, 150), (90, 180), (100, 200), (110, 230), (120, 250)]
    s_attack_tiers = [(20, 10), (30, 15), (40, 20), (50, 50), (60, 105), (70, 130),
                      (80, 160), (90, 200), (100, 250), (110, 300), (120, 400)]
    for survivor_type, essence_fn, attack_tiers in [
        ("normal", normal_essence, normal_attack_tiers), ("s", s_essence, s_attack_tiers)
    ]:
        for level in range(1, 121):
            attack = next(value for maximum, value in attack_tiers if level <= maximum)
            common = {
                "source_file": essence_source,
                "system": "survivor_energy_essence_costs",
                "subsystem": survivor_type,
                "item_id": f"{survivor_type}_survivor_level",
                "level": level,
                "section": "Energy Essence Cost Full 1-120",
                "extraction_method": "visual_table_piecewise_verified",
            }
            rows.append(raw_row(data_type="survivor_energy_essence_cost",
                                name=f"{survivor_type.upper()} survivor level {level} essence cost",
                                cost=essence_fn(level), currency="energy_essence", ignore_for_dps=True,
                                damage_relevance="indirect", recommended_disposition="profile_dependent", **common))
            rows.append(raw_row(data_type="survivor_level_effect",
                                name=f"{survivor_type.upper()} survivor level {level} ATK",
                                effect_type="attack_flat", effect_value=attack, effect_unit="flat",
                                damage_bucket="attack", damage_relevance="direct",
                                recommended_disposition="knowledge_only", **common))
            rows.append(raw_row(data_type="survivor_level_effect",
                                name=f"{survivor_type.upper()} survivor level {level} HP",
                                effect_type="hp_flat", effect_value=attack * 4.8, effect_unit="flat",
                                ignore_for_dps=True, damage_relevance="survival",
                                recommended_disposition="ignore", **common))

    # Resonance energy and multiplier chip tables, pp. 189-190.
    resonance_energy = [("yellow", 50), ("yellow+1", 100), ("yellow+2", 150), ("yellow+3", 200),
                        ("red", 300), ("red+1", 400), ("red+2", 550), ("red+3", 700),
                        ("red+4", 850), ("eternal", 1000)]
    require_page_values(pages, 189, ["Resonance Energy", "1000"])
    for rarity, energy in resonance_energy:
        rows.append(row(data_type="tech_resonance_energy", system="tech_parts", subsystem="resonance",
                        name=f"Resonance energy {rarity}", item_id="resonance_energy", rarity=rarity,
                        effect_type="resonance_energy", effect_value=energy, effect_unit="points",
                        page=189, section="Tech Part Resonance Energy", ignore_for_dps=True))
    chip_steps = [(1.0, 1.2, 1), (1.2, 1.4, 1), (1.4, 1.6, 2), (1.6, 1.8, 3), (1.8, 2.0, 5),
                  (2.0, 2.2, 5), (2.2, 2.4, 7), (2.4, 2.6, 9), (2.6, 2.8, 12), (2.8, 3.0, 15)]
    require_page_values(pages, 190, ["Multiplier", "60", "3.0"])
    for before, after, cost in chip_steps:
        rows.append(row(data_type="tech_resonance_cost", system="tech_parts", subsystem="resonance",
                        name=f"Resonance multiplier {before:.1f} to {after:.1f}", item_id="resonance_multiplier",
                        level=f"{before:.1f}->{after:.1f}", cost=cost, currency="resonance_chip",
                        effect_type="resonance_multiplier_delta", effect_value=round(after - before, 1),
                        effect_unit="multiplier", damage_bucket="skill_specific", page=190,
                        section="Resonance Chip Multiplier Costs",
                        metadata={"from_multiplier": before, "to_multiplier": after}))
    require_page_values(pages, 193, ["3000", "15000", "18"])
    rows.extend([
        row(data_type="breakpoint", system="tech_parts", subsystem="resonance", name="Resonance overload unlock",
            item_id="resonance_overload", unlock_condition="resonance_energy >= 3000", effect_type="unlock",
            effect_value=3000, effect_unit="resonance_energy", page=193, section="Resonance Overload Breakpoints",
            damage_relevance="indirect"),
        row(data_type="breakpoint", system="tech_parts", subsystem="resonance", name="Resonance overload full access",
            item_id="resonance_overload", unlock_condition="resonance_energy >= 15000", effect_type="unlock",
            effect_value=15000, effect_unit="resonance_energy", page=193, section="Resonance Overload Breakpoints",
            damage_relevance="indirect"),
        row(data_type="breakpoint", system="tech_parts", subsystem="resonance", name="Resonance overload maximum level",
            item_id="resonance_overload", unlock_condition="resonance_energy >= 15000", effect_type="max_level",
            effect_value=18, effect_unit="level", page=193, section="Resonance Overload Breakpoints",
            damage_relevance="indirect"),
    ])

    # Chapter unlocks, p. 230. Winning/losing is retained as evidence, not a DPS multiplier.
    unlocks = [
        ("Shop", 1, "losing"), ("Talent Tree", 1, "losing"),
        ("Growth Fund", 2, "losing"), ("Daily Shop", 2, "losing"), ("Friends", 2, "losing"),
        ("Missions", 2, "winning"), ("Main Challenges", 2, "winning"),
        ("Quick Earnings", 2, "winning"), ("Events", 2, "winning"), ("Clans", 3, "winning"),
        ("Durian skill", 4, "winning"), ("Laser Launcher skill", 4, "winning"),
        ("Gold Mine", 5, "winning"), ("Regular Challenge", 6, "losing"),
        ("Tech Parts", 6, "regular_challenge"), ("Ender's Echo", 8, "winning"),
        ("Survivor Pass", 8, "winning"), ("Special Ops", 8, "winning"),
        ("Pets", 9, "winning"), ("Survivor leveling", 9, "winning"),
        ("Mine skill", 10, "losing"), ("Bomb skill", 10, "losing"),
        ("Permanent Privilege Card", 11, "losing"), ("Zone Operations", 30, "winning"),
        ("Extreme Operations", 30, "winning"),
    ]
    require_page_values(pages, 230, ["Shop", "Extreme Ops", "Chapter 30"])
    for name, chapter, outcome in unlocks:
        rows.append(row(data_type="unlock", system="unlocks", subsystem="chapter",
                        name=name, item_id=slug(name), level=chapter,
                        unlock_condition=f"chapter {chapter} {outcome}", effect_type="unlock",
                        effect_value=chapter, effect_unit="chapter", page=230,
                        section="Chapter Unlocks", ignore_for_dps=True,
                        metadata={"completion_condition": outcome}))

    # Mount unlocks, upgrades and sync rates, pp. 234-239.
    mounts = [("Electric Scooter", 30, 0.20), ("Hoverboard", 50, 0.30), ("Doomsteed", 80, 0.40)]
    require_page_values(pages, 234, ["Electric Scooter", "Hoverboard", "Doomsteed"])
    for name, shards, sync_rate in mounts:
        item_id = slug(name)
        rows.append(row(data_type="mount_unlock_cost", system="mounts", subsystem="unlock", name=f"Unlock {name}",
                        item_id=item_id, cost=shards, currency=f"{item_id}_shard", page=234,
                        section="Mount Unlock Costs", consumed_by=f"unlock_{item_id}"))
        rows.append(row(data_type="mount_sync_rate", system="mounts", subsystem="sync", name=f"{name} Base sync rate",
                        item_id=item_id, rarity="Base", effect_type="attack_sync_rate", effect_value=sync_rate,
                        effect_unit="ratio", damage_bucket="attack", page=234, section="Mount Base Sync Rates"))
    levels = ["Y1", "Y2", "Y3", "Y4", "R1", "R2", "R3", "R4"]
    costs = {
        "Electric Scooter": ([20, 25, 40, 50, 75, 100, 120, 160], [0] * 8, 236),
        "Hoverboard": ([10, 15, 20, 35, 45, 60, 75, 100], [0] * 8, 237),
        "Doomsteed": ([20, 30, 45, 65, 100, 140, 180, 220], [1, 1, 2, 2, 4, 4, 4, 6], 238),
    }
    sync = {
        "Electric Scooter": [0.22, 0.24, 0.28, 0.32, 0.38, 0.44, 0.52, 0.60],
        "Hoverboard": [0.33, 0.36, 0.40, 0.45, 0.50, 0.55, 0.60, 0.75],
        "Doomsteed": [0.44, 0.48, 0.55, 0.62, 0.71, 0.80, 0.90, 1.00],
    }
    for mount, (shards, cores, cost_page) in costs.items():
        mount_id = slug(mount)
        for index, rarity in enumerate(levels):
            rows.append(row(data_type="mount_upgrade_cost", system="mounts", subsystem="upgrade",
                            name=f"{mount} upgrade {rarity}", item_id=mount_id, rarity=rarity,
                            cost=shards[index], currency=f"{mount_id}_shard", page=cost_page,
                            section="Mount Upgrade Costs", consumed_by=f"upgrade_{mount_id}_{rarity.lower()}"))
            if cores[index]:
                rows.append(row(data_type="mount_upgrade_cost", system="mounts", subsystem="upgrade",
                                name=f"{mount} upgrade {rarity} mount cores", item_id=mount_id, rarity=rarity,
                                cost=cores[index], currency="mount_core", page=cost_page,
                                section="Mount Upgrade Costs", consumed_by=f"upgrade_{mount_id}_{rarity.lower()}"))
            rows.append(row(data_type="mount_sync_rate", system="mounts", subsystem="sync",
                            name=f"{mount} {rarity} sync rate", item_id=mount_id, rarity=rarity,
                            effect_type="attack_sync_rate", effect_value=sync[mount][index], effect_unit="ratio",
                            damage_bucket="attack", page=239 if mount == "Doomsteed" else 238,
                            section="Mount Sync Rate Table"))

    # Mount component exact stat matrix and merge rules, pp. 248-249.
    component_stats = {
        "shield_damage": [5, 7, 9, 11, 14, 18, 21],
        "boss_damage": [1, 2, 2.5, 3, 3.5, 4, 5],
        "lacerated_damage": [2, 3, 4, 5, 5, 7, 8],
        "skill_damage": [10, 15, 19, 23, 27, 35, 42],
        "crit_rate": [8, 12, 15, 18, 22, 28, 33],
        "weakened_damage": [10, 15, 19, 23, 27, 35, 42],
        "chilled_damage": [10, 15, 19, 23, 27, 35, 42],
        "poisoned_damage": [10, 15, 19, 23, 27, 35, 42],
    }
    component_rarities = ["green", "blue", "purple", "purple+1", "gold", "gold+1", "red"]
    require_page_values(pages, 248, ["Component", "42%", "33%"])
    for effect, values in component_stats.items():
        for rarity, value in zip(component_rarities, values):
            debuff = effect.removesuffix("_damage") if effect in {"lacerated_damage", "weakened_damage", "chilled_damage", "poisoned_damage"} else None
            rows.append(row(data_type="mount_component_effect", system="mounts", subsystem="components",
                            name=f"Mount component {effect} {rarity}", item_id=f"mount_component_{effect}",
                            rarity=rarity, effect_type=effect, effect_value=value, effect_unit="percent",
                            damage_bucket="crit_rate" if effect == "crit_rate" else "damage_multiplier",
                            debuff_type=debuff, page=248, section="Mount Component Stat Matrix"))
    merge_rules = [("green", "blue", 2), ("blue", "purple", 2), ("purple", "purple+1", 2),
                   ("purple+1", "gold", 2), ("gold", "gold+1", 3), ("gold+1", "red", 3)]
    for before, after, count in merge_rules:
        rows.append(row(data_type="merge_rule", system="mounts", subsystem="components",
                        name=f"Merge {before} to {after}", item_id="mount_component", rarity=before,
                        cost=count, currency=f"mount_component_{before}", quantity=1,
                        effect_type="rarity_upgrade", applies_to=after, page=249,
                        section="Mount Component Merge Rules", ignore_for_dps=True))
    rows.append(row(data_type="rule", system="mounts", subsystem="components", name="Component merge rerolls stats",
                    item_id="mount_component", effect_type="reroll", page=249,
                    section="Mount Component Merge Rules", ignore_for_dps=True,
                    notes="Merging rerolls component stats unless a stat is locked."))

    # Exact SS gear damage facts. Defensive clauses from the same source are
    # retained separately by raw OCR and marked IgnoreForDPS there.
    ss_effects = [
        (71, "SS Necklace Clarity", "skill_damage", 20, None, "Clarity"),
        (71, "SS Necklace E1", "shield_damage", 10, None, "Target has a shield"),
        (73, "SS Gloves Amplify", "weakened_damage", 25, "weakened", "Target is weakened"),
        (73, "SS Gloves E1 Berserk", "skill_damage", 50, None, "Berserk active"),
        (74, "SS Armor Super Enhance", "skill_damage", 30, None, "Energy above 90"),
        (77, "SS Belt E1", "crit_damage", 30, None, "Crit rate above 100%"),
        (77, "SS Belt E3", "skill_damage", 30, None, "Crit rate above 130%"),
        (77, "SS Belt E5", "crit_damage", 100, None, "Crit rate above 150%"),
        (77, "SS Belt V4 charge", "crit_rate", 20, None, "Charge active"),
        (77, "SS Belt V4 stored damage", "stored_damage", 30, None, "Charge active"),
        (78, "SS Boots chilled damage", "chilled_damage", 15, "chilled", "Target is chilled"),
        (79, "SS Boots V2 chilled damage", "chilled_damage", 20, "chilled", "Target is chilled"),
        (79, "SS Boots V4 vulnerability", "vulnerability", 10, "chilled", "Target chilled; 10 seconds"),
    ]
    for page, name, effect, value, debuff, condition in ss_effects:
        require_page_values(pages, page, [str(value)])
        rows.append(row(data_type="gear_effect", system="ss_gear", subsystem="astral_forge",
                        name=name, item_id=slug(name.split()[1] + "_" + name.split()[2]),
                        effect_type=effect, effect_value=value, effect_unit="percent",
                        damage_bucket="crit_rate" if effect == "crit_rate" else "damage_multiplier",
                        debuff_type=debuff, unlock_condition=condition, page=page,
                        section="SS Gear Exact Effects"))

    # Crit sources, pp. 23-39. Values are stored separately so conditions and
    # global/active scopes cannot be accidentally collapsed.
    tmnt = ["Raphael", "Leonardo", "April", "Michelangelo", "Donatello", "Splinter"]
    require_page_values(pages, 23, ["Raphael", "Splinter", "+8%"])
    for survivor in tmnt:
        rows.append(row(data_type="crit_stat", system="survivors", subsystem="global_star_bonus",
                        name=f"{survivor} 5-star global crit rate", item_id=slug(survivor), star=5,
                        effect_type="crit_rate", effect_value=8, effect_unit="percent",
                        damage_bucket="crit_rate", applies_to="all_survivors", page=23,
                        section="TMNT Survivor Global Crit Rate", long_term_value=1.0))
    crit_entries = [
        (23, "Eternal Gloves excellent crit", "gear", "eternal_gloves", "crit_rate", 10, "equipped; excellent rarity"),
        (23, "Eternal Gloves legendary crit rate", "gear", "eternal_gloves", "crit_rate", 10, "equipped; legendary rarity"),
        (23, "Eternal Gloves legendary crit damage", "gear", "eternal_gloves", "crit_damage", 100, "equipped; legendary rarity"),
        (24, "Leather Gloves crit rate", "gear", "leather_gloves", "crit_rate", 5, "equipped"),
        (24, "Leather Gloves crit damage", "gear", "leather_gloves", "crit_damage", 50, "equipped"),
        (25, "King skill crit rate", "skills", "king_crit_skill", "crit_rate", 40, "skill selected"),
        (25, "King evolved skill crit rate", "skills", "king_evolved_crit_skill", "crit_rate", 50, "evolved skill active"),
        (25, "Master Yang Palm stance crit rate", "survivors", "master_yang", "crit_rate", 20, "Palm stance active"),
        (27, "Expose Weakness level 40 crit rate", "skills", "expose_weakness", "crit_rate", 8, "talent level 40; global"),
        (29, "Donatello Brotherhood crit rate per passive", "survivors", "donatello", "crit_rate", 3, "A1; per equipped TMNT passive; max 9%"),
        (30, "Default crit damage", "crit_stats", "default_crit_damage", "crit_damage_baseline", 100, "baseline; do not count as upgrade"),
        (33, "King evolved skill crit damage", "skills", "king_evolved_crit_skill", "crit_damage", 25, "evolved skill active"),
        (37, "Donatello Brotherhood crit damage per passive", "survivors", "donatello", "crit_damage", 3, "A2; per equipped TMNT passive; max 9%"),
    ]
    for page, name, system, item_id, effect, value, condition in crit_entries:
        rows.append(row(data_type="crit_stat", system=system, subsystem="crit_sources", name=name,
                        item_id=item_id, effect_type=effect, effect_value=value, effect_unit="percent",
                        damage_bucket="crit_rate" if effect == "crit_rate" else "crit_damage",
                        unlock_condition=condition, page=page, section="Crit Rate and Crit Damage Sources"))
    for effect, page, values in [
        ("crit_rate", 27, [(1, 5), (4, 10), (6, 15)]),
        ("crit_damage", 34, [(1, 5), (4, 10), (6, 25)]),
    ]:
        for star, value in values:
            rows.append(row(data_type="survivor_awakening", system="survivors", subsystem="master_yang",
                            name=f"Master Yang R{star} {effect}", item_id="master_yang", awakening=f"R{star}",
                            effect_type=effect, effect_value=value, effect_unit="percent",
                            damage_bucket=effect, applies_to="active_survivor", page=page,
                            section="Master Yang Awakening Crit"))
    harmony = [(1, 2), (3, 3), (6, 5), (9, 5), (12, 5), (15, 5), (18, 5)]
    for level, value in harmony:
        rows.append(row(data_type="crit_stat", system="survivors", subsystem="combat_harmony",
                        name=f"Combat Harmony level {level} crit rate", item_id="combat_harmony", level=level,
                        effect_type="crit_rate", effect_value=value, effect_unit="percent", damage_bucket="crit_rate",
                        applies_to="global", page=29, section="Combat Harmony Crit Rate", long_term_value=1.0))
    synergy = [(5, 5), (10, 5), (15, 10), (20, 10)]
    for level, value in synergy:
        rows.append(row(data_type="crit_stat", system="survivors", subsystem="survivor_synergy",
                        name=f"Survivor Synergy level {level} crit damage", item_id="survivor_synergy", level=level,
                        effect_type="crit_damage", effect_value=value, effect_unit="percent", damage_bucket="crit_damage",
                        applies_to="global", page=35, section="Survivor Synergy Crit Damage", long_term_value=1.0))
    for survivor, level, value in [
        ("King", 120, 5), ("Tsukuyomi", 120, 5), ("Worm", 120, 5), ("Metallia", 120, 10),
        ("Catnips", 120, 10), ("SpongeBob", 40, 5), ("Squidward", 40, 5),
        ("Patrick", 40, 5), ("Sandy", 40, 5),
    ]:
        rows.append(row(data_type="survivor_effect", system="survivors", subsystem="level_milestone",
                        name=f"{survivor} level {level} global crit damage", item_id=slug(survivor), level=level,
                        effect_type="crit_damage", effect_value=value, effect_unit="percent", damage_bucket="crit_damage",
                        applies_to="all_survivors", page=30 if survivor in {"King", "Tsukuyomi", "Worm"} else 31,
                        section="Survivor Level Crit Damage", long_term_value=1.0))
    for star, value in [(1, 20), (3, 20), (5, 20)]:
        rows.append(row(data_type="survivor_awakening", system="survivors", subsystem="metallia",
                        name=f"Metallia R{star} crit damage", item_id="metallia", awakening=f"R{star}",
                        effect_type="crit_damage", effect_value=value, effect_unit="percent", damage_bucket="crit_damage",
                        applies_to="active_survivor", page=32, section="Metallia Awakening Crit Damage"))
    for index in range(1, 11):
        rows.append(row(data_type="collectible_effect", system="collectibles", subsystem="crit_rate",
                        name=f"NamePending red crit-rate collectible {index:02}", item_id=f"collectible_cr_red_icon_{index:02}",
                        rarity="red", effect_type="crit_rate", effect_value=10, effect_unit="percent",
                        damage_bucket="crit_rate", applies_to="global", page=26, section="Icon-only Collectible Crit Rate",
                        needs_review=True, notes="Exact effect; collectible name/star threshold is icon-only and pending."))
    for index in range(1, 12):
        rows.append(row(data_type="collectible_effect", system="collectibles", subsystem="crit_rate",
                        name=f"NamePending yellow crit-rate collectible {index:02}", item_id=f"collectible_cr_yellow_icon_{index:02}",
                        rarity="yellow", effect_type="crit_rate", effect_value=5, effect_unit="percent",
                        damage_bucket="crit_rate", applies_to="global", page=26, section="Icon-only Collectible Crit Rate",
                        needs_review=True, notes="Exact effect; collectible name/star threshold is icon-only and pending."))
    for rarity, count, value in [("red", 10, 10), ("yellow", 13, 5)]:
        for index in range(1, count + 1):
            rows.append(row(data_type="collectible_effect", system="collectibles", subsystem="crit_damage",
                            name=f"NamePending {rarity} crit-damage collectible {index:02}",
                            item_id=f"collectible_cd_{rarity}_icon_{index:02}", rarity=rarity,
                            effect_type="crit_damage", effect_value=value, effect_unit="percent",
                            damage_bucket="crit_damage", applies_to="global", page=33,
                            section="Icon-only Collectible Crit Damage", needs_review=True,
                            notes="Exact effect; collectible name/star threshold is icon-only and pending."))

    collectible_sets = [
        (218, "Erudite Heirloom", "SS Weapon / Starforged Havoc", "thrust damage; Energy Overload stack damage; Disordered Death/Void Fissure damage"),
        (218, "Otherworld Treasure", "SS Necklace / shield", "Omnicombat shield requirement; Clarity skill damage; Reverse Scale skill damage"),
        (218, "Meaning of Life", "SS Gloves / Fury / Weaken", "Fury skill damage; damage to Weakened; Stealth Attack crit damage"),
        (218, "Interdimension Movement", "SS Armor / energy level", "Cabalistic shield boost; Suppression skill damage; Eternal Recharge multiplier"),
        (219, "Close to Creation", "Twinborn Durian/Caltrops", "spike damage; pair damage; shield damage"),
        (219, "Open Void Gate", "Twinborn Soccer/Quantum Ball", "duration; shield damage; skill damage; crit damage"),
        (219, "Transgalactic Tentacle", "Twinborn Drone/Destroyer", "missile count; single-shot damage"),
        (219, "Impression Idols", "Twinborn Forcefield/Force Barrier", "forcefield damage; vulnerability; crit damage; skill damage"),
        (219, "Multiverse Perspective", "Twinborn Drill/Frostfire", "pair damage; Fire Bomb damage"),
        (219, "Hyperrift Tech", "Twinborn RPG/Sharkmaw", "Sharkmaw damage; Cluster Arrows; lacerated damage"),
        (219, "Rewriting the Stars' Memories", "Twinborn Lightning/Maelstorm", "Maelstorm damage"),
        (219, "Goldfinger", "Twinborn Boomerang/Magvolt", "skill damage; vulnerability stacks; weakened damage"),
        (219, "Cyber Wonderland", "Twinborn Guardian/Defender", "Defender damage; skill damage; poisoned-target damage"),
        (219, "Try All Possibilities", "Twinborn Laser/Death Ray", "crit damage; shield damage; poisoned-target damage; skill damage"),
        (219, "Brewing Recipe", "Twinborn Brick/1-ton Iron", "Brick damage; shield damage; skill damage; crit damage"),
        (220, "Wind Totem", "Twinborn Molotov/Fuel Spray", "Deep Wound; Molotov damage"),
    ]
    for page, name, target, effects in collectible_sets:
        rows.append(row(data_type="collectible_set", system="collectibles", subsystem="set_bonus",
                        name=name, item_id=slug(name), applies_to=target, effect_type="collectible_set_bonus",
                        page=page, section="Collectible Set Associations", source_kind="inferred", confidence="high",
                        needs_review=True, damage_relevance="conditional", recommended_disposition="needs_review",
                        notes="Association/effect family is source-backed; exact numeric thresholds must come from the raw chart.",
                        metadata={"effect_summary": effects}))

    # Exact chest odds from the raw chart. The full-item and shard rows sum to
    # the displayed rarity totals (minor 0.2% rounding in the source chart).
    chest_source = "Collectible_Chest_Odds_2024-11-28_result.png"
    for outcome, probabilities in {
        "full_collectible": [24.9, 24.9, 12.5, 1.5, 0.0],
        "collectible_shards": [15.0, 12.0, 8.0, 1.0, 0.0],
    }.items():
        for rarity, probability in zip(["green", "blue", "purple", "yellow", "red"], probabilities):
            rows.append(raw_row(source_file=chest_source, data_type="chest_odds", system="chest_odds",
                                subsystem="collectible_chest", name=f"Collectible chest {outcome} {rarity}",
                                item_id=f"{outcome}_{rarity}", rarity=rarity, effect_type="drop_probability",
                                effect_value=probability, effect_unit="percent", applies_to="collectible_chest",
                                section="Collectible Chest Odds", extraction_method="visual_table_verified",
                                ignore_for_dps=True, damage_relevance="indirect", recommended_disposition="knowledge_only"))
    rows.append(raw_row(source_file=chest_source, data_type="chest", system="chests",
                        subsystem="collectibles", name="Collectible Chest", item_id="collectible_chest",
                        section="Collectible Chest Odds", extraction_method="visual_table_verified",
                        ignore_for_dps=True, damage_relevance="indirect", recommended_disposition="profile_dependent"))

    # Clan shop numeric table. Item identities are intentionally icon IDs.
    clan_source = "Clan_Shop_2025-02-17_result.png"
    clan_costs = [40000, 36000, 10000, 4000, 2000, 18000, 2200, 13500, 2500, 18000, 2200, 3600, 600, 600, 600]
    clan_qty = [1, 1, 10, 5, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
    clan_stock = {
        "1-11": [1, 1, 1, 20, 10, 1, 3, 2, 2, 2, 2, 1, 5, 5, 5],
        "12": [1, 2, 2, 20, 10, 1, 3, 2, 2, 2, 2, 4, 10, 10, 10],
        "13": [1, 2, 2, 20, 10, 1, 3, 2, 2, 4, 4, 4, 15, 15, 15],
        "14": [1, 2, 2, 20, 20, 2, 6, 2, 2, 4, 4, 4, 20, 20, 20],
        "15": [2, 2, 2, 20, 20, 2, 6, 4, 4, 4, 4, 4, 25, 25, 25],
        "16": [2, 2, 2, 20, 20, 2, 6, 4, 4, 4, 4, 4, 25, 25, 25],
    }
    for index, (cost, quantity) in enumerate(zip(clan_costs, clan_qty), 1):
        rows.append(raw_row(source_file=clan_source, data_type="shop_item", system="clan_shop",
                            subsystem="clan_shop", name=f"NamePending Clan Shop Icon {index:02}",
                            item_id=f"clan_shop_icon_{index:02}", cost=cost, currency="clan_coin", quantity=quantity,
                            unlock_condition="chapter 3 won; stock depends on clan level", section="Clan Shop Table",
                            extraction_method="visual_table_verified", needs_review=True, ignore_for_dps=True,
                            damage_relevance="unknown", recommended_disposition="needs_review",
                            notes="Cost, quantity, and stock are exact; icon-only item identity is pending.",
                            metadata={"stock_by_clan_level": {level: values[index - 1] for level, values in clan_stock.items()}}))
    rows.append(raw_row(source_file=clan_source, data_type="shop", system="clan_shop", subsystem="clan_shop",
                        name="Clan Shop", item_id="clan_shop", unlock_condition="chapter 3 won",
                        currency="clan_coin", section="Clan Shop Table", extraction_method="visual_table_verified",
                        ignore_for_dps=True, damage_relevance="indirect", recommended_disposition="profile_dependent"))

    # Timed Muster conversion table, exact raw-image rows for elite medals 61-100.
    muster_source = "Timed_Muster_Medal_Conversion_2024-12-12_result.png"
    conversions = {
        100: 82, 99: 80, 98: 79, 97: 78, 96: 77, 95: 76, 94: 76, 93: 75, 92: 74, 91: 73,
        90: 73, 89: 72, 88: 71, 87: 70, 86: 70, 85: 69, 84: 68, 83: 67, 82: 66, 81: 65,
        80: 64, 79: 64, 78: 63, 77: 62, 76: 61, 75: 60, 74: 59, 73: 58, 72: 56, 71: 55,
        70: 54, 69: 52, 68: 51, 67: 49, 66: 47, 65: 46, 64: 44, 63: 43, 62: 41, 61: 40,
    }
    for elite, timed in conversions.items():
        rows.append(raw_row(source_file=muster_source, data_type="exchange_path", system="conversions",
                            subsystem="timed_muster", name=f"Convert {elite} Elite medals to {timed} Timed Muster medals",
                            item_id="timed_muster_medal", cost=elite, currency="elite_medal", quantity=timed,
                            applies_to="timed_muster_medal", section="Timed Muster Medal Conversion",
                            extraction_method="visual_table_verified", ignore_for_dps=True,
                            damage_relevance="utility", recommended_disposition="profile_dependent"))

    # The item-value chart is an explicit approximate valuation source. Item
    # names/categories are exact; gem values remain opinion and non-executable.
    value_source = "Item_Values_2024-10-02_result.png"
    item_values = {
        "selectors": [("S Choice", 6000), ("Eternal Selector", 4000), ("Void Selector", 4500),
                      ("Chaos Selector", 3500), ("Random S", 2500), ("Excellent Selector", 200)],
        "cores": [("Relic Core Choice", 15000), ("Relic Core", 15000), ("10x Eternal", 3000),
                  ("10x Void", 3000), ("10x Chaos", 1500)],
        "tech_parts": [("Resonance Chip Choice", 10000), ("Resonance Chip", 10000),
                       ("Epic Choice", 5000), ("Random Epic Part", 2000)],
        "collectibles": [("Legendary Selector", 12500), ("Epic Selector", 3000), ("Random Epic", 1500),
                         ("Full Event Collectible (6x)", 6000), ("Excellent Selector", 400), ("Random Excellent", 200)],
        "pets": [("Pet Crystal Choice", 8000), ("Awakening Crystal", 300), ("Epic Pet Selector", 4000),
                 ("Random Epic Pet", 2000), ("Excellent Pet Selector", 500),
                 ("Choice Epic Pet Toy", 250), ("Random Epic Pet Toy", 150)],
        "survivors": [("Awakening Core Choice", 9000), ("Awakening Core", 6000), ("Survivor Outfit", 3000),
                      ("Yang/Metallia Shard", 300), ("Survivor Shard", 100), ("Reset Vial", 10000),
                      ("1000x Essence", 200)],
        "keys": [("S Key", 200), ("Collectible Key", 150), ("Tech Part Key", 150), ("EDF Key", 100),
                 ("Army Crate Key", 40), ("Powerful Pet Key", 100), ("Normal Pet Key", 40)],
    }
    for category, entries in item_values.items():
        for name, value in entries:
            item_id = slug(f"{category}_{name}")
            rows.append(raw_row(source_file=value_source, data_type="item", system="resources", subsystem=category,
                                name=name, item_id=item_id, section="Item Value Chart",
                                extraction_method="visual_table_verified", ignore_for_dps=True,
                                damage_relevance="indirect", recommended_disposition="profile_dependent",
                                notes="Item identity/category is exact; valuation is stored separately."))
            rows.append(raw_row(source_file=value_source, data_type="item_value", system="resources", subsystem=category,
                                name=f"Approximate gem value: {name}", item_id=item_id, effect_type="gem_equivalent",
                                effect_value=value, effect_unit="gems", section="Item Value Chart",
                                source_kind="opinion", confidence="high", extraction_method="visual_table_verified",
                                needs_review=True, ignore_for_dps=True, damage_relevance="utility",
                                recommended_disposition="needs_review",
                                notes="Approximate source valuation; never overrides exact costs or effects."))

    # Survivor Pass EXP rows, exact numeric table on p. 222.
    pass_exp = [
        ("Dailies", 130, 910, "3640"), ("Defeat monsters", 90, 630, "2520"),
        ("Clear chapters/challenges", 100, 700, "2800"), ("Regular Challenge", None, None, "1350-1500"),
        ("Path of Trials", 50, 350, "1400"), ("Ender's Echo", 60, 420, "1680"),
        ("Clan Expedition", None, 200, "800"), ("Special Ops", 50, 350, "1400"),
    ]
    require_page_values(pages, 222, ["15590", "Dailies", "Special Ops"])
    for mission, daily, weekly, season in pass_exp:
        rows.append(row(data_type="event_reward", system="events", subsystem="survivor_pass_exp",
                        name=f"Survivor Pass EXP: {mission}", item_id=slug(mission), quantity=daily,
                        effect_type="pass_exp", effect_value=float(weekly) if weekly is not None else None,
                        effect_unit="weekly_exp", page=222, section="Survivor Pass EXP",
                        ignore_for_dps=True, damage_relevance="utility", recommended_disposition="knowledge_only",
                        metadata={"daily_max": daily, "weekly_max": weekly, "season_max": season}))
    rows.append(row(data_type="xeno_pet", system="xeno_pets", subsystem="transmute",
                    name="EffectPending Xeno transmute rule", item_id="xeno_transmute_rule",
                    page=231, section="Final Database Schema List", source_kind="placeholder", confidence="low",
                    extraction_method="embedded_pdf_missing_table_marker", needs_review=True, ignore_for_dps=True,
                    damage_relevance="unknown", recommended_disposition="needs_review",
                    notes="The master map lists XenoTransmuteRules, but no readable numeric Xeno table is present in the supplied pack."))

    # The equipment-guide section explicitly contains recommendations/meta.
    rows.append(row(data_type="equipment_priority", system="gear", subsystem="recommendations",
                    name="Equipment guide priority section", page=195, section="Equipment Guide",
                    source_kind="opinion", confidence="medium", extraction_method="embedded_pdf_label",
                    needs_review=True, ignore_for_dps=True,
                    notes="Source labels this section as meta/priority guidance; it cannot override exact effects."))
    return rows


def import_legacy_evidence(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    imported: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            fact = json.loads(line)
            relevance = str(fact.get("damage_relevance", ""))
            raw = str(fact.get("raw_text") or fact.get("canonical_subject") or "Unlabeled OCR evidence")
            source_file = str(fact.get("source_file") or "unknown")
            record = row(
                data_type="raw_evidence",
                system=str(fact.get("system") or "unknown"),
                name=raw[:240],
                page=fact.get("source_page", 1),
                section="Legacy OCR evidence",
                source_kind="placeholder",
                confidence=str(fact.get("confidence") or "low") if fact.get("confidence") in {"high", "medium", "low"} else "low",
                extraction_method=str(fact.get("extraction_method") or "legacy_ocr"),
                ignore_for_dps=relevance != "damage",
                needs_review=True,
                notes=str(fact.get("notes") or "Unverified OCR evidence; excluded from actions and scoring."),
                original_source_file=source_file,
                metadata={
                    "legacy_fact_id": fact.get("id"),
                    "normalized_value": fact.get("normalized_value"),
                    "source_image_hash": fact.get("source_image_hash"),
                },
            )
            record["source"]["source_file"] = source_file
            record["row_id"] = str(fact.get("id") or record["row_id"])
            imported.append(record)
    return imported


def build_actions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str, str | None], list[dict[str, Any]]] = defaultdict(list)
    for record in rows:
        if record["source_kind"] == "exact" and not record["needs_review"]:
            by_key[(record["system"], str(record.get("item_id")), record.get("rarity"))].append(record)

    for (system, item_id, rarity), records in sorted(
        by_key.items(), key=lambda item: tuple("" if value is None else str(value) for value in item[0])
    ):
        if system == "mounts" and rarity and any(r["data_type"] == "mount_upgrade_cost" for r in records):
            costs = [{"resource_id": r["currency"], "amount": r["cost"]}
                     for r in records if r["data_type"] == "mount_upgrade_cost"]
            effect_row = next((r for r in records if r["data_type"] == "mount_sync_rate"), None)
            if effect_row:
                actions.append({
                    "action_id": f"upgrade_{item_id}_{slug(rarity)}",
                    "system": system,
                    "action_type": "upgrade_mount",
                    "target_id": item_id,
                    "requirements": {"current_rarity_precedes": rarity, "mount_owned": item_id},
                    "costs": costs,
                    "effects": [{"effect_type": "attack_sync_rate_target", "value": effect_row["effect_value"],
                                 "unit": "ratio", "damage_bucket": "attack"}],
                    "expected_dps_gain": None,
                    "breakpoint_distance": None,
                    "unlock_target": rarity,
                    "confidence": "exact",
                    "explanation_key": "mount_sync_rate_upgrade",
                    "aliases": [],
                    "source_row_ids": [r["row_id"] for r in records],
                    "enabled": True,
                    "disabled_reason": None,
                })
        if system == "tech_parts" and any(r["data_type"] == "tech_resonance_cost" for r in records):
            for record in records:
                if record["data_type"] != "tech_resonance_cost":
                    continue
                meta = record["metadata"]
                actions.append({
                    "action_id": f"resonance_multiplier_{str(meta['to_multiplier']).replace('.', '_')}",
                    "system": system,
                    "action_type": "upgrade_resonance_multiplier",
                    "target_id": "resonance_multiplier",
                    "requirements": {"current_multiplier": meta["from_multiplier"]},
                    "costs": [{"resource_id": record["currency"], "amount": record["cost"]}],
                    "effects": [{"effect_type": record["effect_type"], "value": record["effect_value"],
                                 "unit": record["effect_unit"], "damage_bucket": record["damage_bucket"]}],
                    "expected_dps_gain": None,
                    "breakpoint_distance": None,
                    "unlock_target": str(meta["to_multiplier"]),
                    "confidence": "exact",
                    "explanation_key": "resonance_multiplier_upgrade",
                    "aliases": [],
                    "source_row_ids": [record["row_id"]],
                    "enabled": True,
                    "disabled_reason": None,
                })
    deduped = {action["action_id"]: action for action in actions}
    return [deduped[key] for key in sorted(deduped)]


def compile_numeric(rows: list[dict[str, Any]], actions: list[dict[str, Any]]) -> dict[str, Any]:
    exact = [record for record in rows if record["source_kind"] == "exact" and not record["needs_review"]]
    ids = {
        "systems": sorted({r["system"] for r in rows}),
        "items": sorted({str(r["item_id"]) for r in rows if r.get("item_id")}),
        "resources": sorted({str(r["currency"]) for r in rows if r.get("currency")}),
        "currencies": sorted({str(r["currency"]) for r in rows if r.get("currency")}),
        "effects": sorted({str(r["effect_type"]) for r in rows if r.get("effect_type")}),
        "damage_buckets": sorted({str(r["damage_bucket"]) for r in rows if r.get("damage_bucket")}),
        "shops": sorted({str(r["subsystem"]) for r in rows if r["data_type"] == "shop_item"}),
        "chests": sorted({str(r.get("applies_to") or r.get("item_id")) for r in rows if r["data_type"] in {"chest", "chest_odds"}}),
    }
    maps = {key: {value: index for index, value in enumerate(values)} for key, values in ids.items()}
    damage_rows = [r for r in exact if r.get("effect_value") is not None and not r["ignore_for_dps"]]
    damage_matrix = [[
        maps["systems"].get(r["system"], -1), maps["items"].get(str(r.get("item_id")), -1),
        maps["effects"].get(str(r.get("effect_type")), -1),
        maps["damage_buckets"].get(str(r.get("damage_bucket")), -1), float(r["effect_value"]),
        1.0 if r.get("effect_unit") == "percent" else 0.0,
    ] for r in damage_rows]
    action_matrix = []
    for action in actions:
        cost_total = sum(float(cost["amount"]) for cost in action["costs"])
        dps_value = sum(float(effect["value"]) for effect in action["effects"])
        action_matrix.append([
            maps["systems"].get(action["system"], -1), maps["items"].get(action["target_id"], -1),
            cost_total, dps_value, 1.0 if action["enabled"] else 0.0,
        ])
    shop_rows = [record for record in rows if record["data_type"] == "shop_item"]
    shop_matrix = [[
        maps["shops"].get(str(record.get("subsystem")), -1),
        maps["items"].get(str(record.get("item_id")), -1),
        maps["currencies"].get(str(record.get("currency")), -1),
        float(record.get("cost") or 0), float(record.get("quantity") or 0), float(record.get("limit") or 0),
        1.0 if record["source_kind"] == "exact" and not record["needs_review"] else 0.0,
    ] for record in shop_rows]
    chest_rows = [record for record in rows if record["data_type"] == "chest_odds"]
    chest_matrix = [[
        maps["chests"].get(str(record.get("applies_to") or "collectible_chest"), -1),
        maps["items"].get(str(record.get("item_id")), -1),
        float(record.get("effect_value") or 0) / 100.0,
        float(record.get("quantity") or 1),
        1.0 if record["source_kind"] == "exact" and not record["needs_review"] else 0.0,
    ] for record in chest_rows]
    id_map_hash = hashlib.sha256(json.dumps(maps, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "version": 1,
        "id_maps": maps,
        "id_map_hash": id_map_hash,
        "profile_feature_columns": ["chapter", "attack", "crit_rate", "crit_damage", "skill_damage"],
        "profile_feature_matrix": [],
        "inventory_feature_columns": ids["resources"],
        "inventory_feature_matrix": [],
        "resource_feature_matrix": [[index, 1.0] for index, _ in enumerate(ids["resources"])],
        "action_template_columns": ["system_id", "target_item_id", "total_cost", "effect_value", "enabled"],
        "action_template_ids": [action["action_id"] for action in actions],
        "action_template_matrix": action_matrix,
        "unlock_requirement_columns": ["chapter"],
        "unlock_requirement_row_ids": [r["row_id"] for r in exact if r["data_type"] == "unlock"],
        "unlock_requirement_matrix": [[float(r["effect_value"])] for r in exact if r["data_type"] == "unlock"],
        "breakpoint_columns": ["value"],
        "breakpoint_row_ids": [r["row_id"] for r in exact if r["data_type"] == "breakpoint"],
        "breakpoint_matrix": [[float(r["effect_value"])] for r in exact if r["data_type"] == "breakpoint"],
        "damage_effect_columns": ["system_id", "item_id", "effect_id", "damage_bucket_id", "value", "is_percent"],
        "damage_effect_row_ids": [r["row_id"] for r in damage_rows],
        "damage_effect_matrix": damage_matrix,
        "shop_item_columns": ["shop_id", "item_id", "currency_id", "cost", "quantity", "limit", "usable"],
        "shop_item_row_ids": [record["row_id"] for record in shop_rows],
        "shop_item_matrix": shop_matrix,
        "chest_expected_value_columns": ["chest_id", "item_id", "probability", "quantity", "usable"],
        "chest_expected_value_row_ids": [record["row_id"] for record in chest_rows],
        "chest_expected_value_matrix": chest_matrix,
    }


def write_outputs(output: Path, rows: list[dict[str, Any]], actions: list[dict[str, Any]]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in rows:
        grouped[record["data_type"]].append(record)
    (output / "source_database.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tables = output / "tables"
    tables.mkdir(exist_ok=True)

    canonical = {
        "items": [r for r in rows if r["data_type"] == "item"],
        "resources": [r for r in rows if r["system"] == "resources"],
        "shops": [r for r in rows if r["data_type"] == "shop"],
        "shop_items": [r for r in rows if r["data_type"] == "shop_item"],
        "chests": [r for r in rows if r["data_type"] == "chest"],
        "chest_odds": [r for r in rows if r["data_type"] == "chest_odds"],
        "selectors": [r for r in rows if r.get("subsystem") == "selectors"],
        "gear": [r for r in rows if r["system"] in {"gear", "ss_gear"} and r["source_kind"] != "placeholder"],
        "forge_effects": [r for r in rows if r.get("subsystem") == "astral_forge"],
        "ss_gear": [r for r in rows if r["system"] == "ss_gear"],
        "tech_parts": [r for r in rows if r["system"] in {"tech_parts", "tech_resonance"} and r["source_kind"] != "placeholder"],
        "resonance": [r for r in rows if "resonance" in str(r.get("subsystem"))],
        "resonance_costs": [r for r in rows if r["data_type"] == "tech_resonance_cost"],
        "survivors": [r for r in rows if r["system"] == "survivors" and r["source_kind"] != "placeholder"],
        "survivor_energy_essence_costs": [r for r in rows if r["system"] == "survivor_energy_essence_costs" and r["source_kind"] != "placeholder"],
        "survivor_awakenings": [r for r in rows if r["data_type"] in {"survivor_awakening", "survivor_awakening_cost"}],
        "pets": [r for r in rows if r["system"] == "pets" and r["source_kind"] != "placeholder"],
        "pet_awakenings": [r for r in rows if r["data_type"].startswith("pet_awakening")],
        "pet_merging": [r for r in rows if r["data_type"] == "pet_merge_rule"],
        "xeno_pets": [r for r in rows if r["system"] == "xeno_pets"],
        "collectibles": [r for r in rows if r["system"] == "collectibles" and r["source_kind"] != "placeholder"],
        "collectible_sets": [r for r in rows if r["data_type"] == "collectible_set"],
        "mounts": [r for r in rows if r["system"] == "mounts"],
        "skills": [r for r in rows if r["system"] == "skills" and r["source_kind"] != "placeholder"],
        "unlocks": [r for r in rows if r["data_type"] == "unlock"],
        "chapters": [r for r in rows if r["data_type"] == "unlock"],
        "costs": [r for r in rows if r.get("cost") is not None and r["source_kind"] != "placeholder"],
        "breakpoints": [r for r in rows if r["data_type"] == "breakpoint"],
        "damage_buckets": [r for r in rows if r.get("damage_bucket") and r["source_kind"] != "placeholder"],
        "debuffs": [r for r in rows if r.get("debuff_type") and r["source_kind"] != "placeholder"],
        "currencies": [r for r in rows if r.get("currency") and r["source_kind"] != "placeholder"],
        "exchange_paths": [r for r in rows if r["data_type"] == "exchange_path"],
        "rules": [r for r in rows if r["data_type"] in {"rule", "merge_rule"}],
    }

    flat_rows = []
    for record in rows:
        flat = {key: value for key, value in record.items() if key not in {"source", "metadata"}}
        flat.update(record["source"])
        flat["metadata"] = json.dumps(record["metadata"], sort_keys=True)
        flat_rows.append(flat)
    columns = list(flat_rows[0]) if flat_rows else []
    with (output / "source_database.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(flat_rows)
    for name, records in sorted({**grouped, **canonical}.items()):
        (tables / f"{name}.json").write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        with (tables / f"{name}.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for record in records:
                flat = {key: value for key, value in record.items() if key not in {"source", "metadata"}}
                flat.update(record["source"])
                flat["metadata"] = json.dumps(record["metadata"], sort_keys=True)
                writer.writerow(flat)

    (output / "action_templates.json").write_text(json.dumps(actions, indent=2) + "\n", encoding="utf-8")

    legacy_manifest_path = ROOT / "data_sources" / "extracted" / "ocr" / "source_manifest.json"
    catalog = json.loads(legacy_manifest_path.read_text(encoding="utf-8")) if legacy_manifest_path.exists() else []
    catalog.append({
        "source_file": MASTER_NAME,
        "source_path": str(DEFAULT_PDF),
        "source_page_count": 259,
        "confidence": "exact",
        "extraction_method": "pymupdf_embedded_text",
        "needs_review": False,
        "systems": sorted({record["system"] for record in rows if record["source"]["source_file"] == MASTER_NAME}),
    })
    (output / "source_catalog.json").write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    gpu = ROOT / "knowledge" / "gpu_tables" / "source_pack"
    gpu.mkdir(parents=True, exist_ok=True)
    matrices = compile_numeric(rows, actions)
    (gpu / "numeric_tables.json").write_text(json.dumps(matrices, separators=(",", ":")) + "\n", encoding="utf-8")

    review = [record for record in rows if record["needs_review"]]
    review_dir = ROOT / "knowledge" / "review_queue"
    review_dir.mkdir(parents=True, exist_ok=True)
    with (review_dir / "source_pack_queue.jsonl").open("w", encoding="utf-8") as handle:
        for record in review:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    conflicts_path = ROOT / "data_sources" / "extracted" / "ocr" / "extraction_conflicts.json"
    conflicts = json.loads(conflicts_path.read_text(encoding="utf-8")) if conflicts_path.exists() else []
    review_report = {
        "missing_or_pending_names": [r["row_id"] for r in review if "pending" in r["name"].lower() or "unknown" in r["name"].lower()],
        "missing_costs": [r["row_id"] for r in review if "cost" in r["data_type"] and r.get("cost") is None],
        "missing_effects": [r["row_id"] for r in review if r["data_type"] not in {"raw_evidence", "item"} and not r.get("effect_type")],
        "ambiguous_icons": [r["row_id"] for r in review if "icon" in (r["name"] + r["notes"]).lower()],
        "conflict_record_count": len(conflicts) if isinstance(conflicts, list) else len(conflicts.get("conflicts", [])),
        "conflict_source": str(conflicts_path.relative_to(ROOT)) if conflicts_path.exists() else None,
        "duplicate_row_ids": [],
        "not_gpu_convertible_count": sum(
            r["source_kind"] != "exact" or r["needs_review"] or (r.get("effect_value") is None and r.get("cost") is None)
            for r in rows
        ),
        "policy": "Pending/icon-only/conflicting rows are excluded from executable actions and final scoring.",
    }
    (review_dir / "source_pack_review_report.json").write_text(json.dumps(review_report, indent=2) + "\n", encoding="utf-8")
    counts = Counter(record["system"] for record in rows)
    expected_systems = [
        "breakpoints", "chest_odds", "chests", "clan_shop", "collectibles", "conversions",
        "crit_stats", "events", "gear", "mounts", "pet_awakenings", "pet_merging", "pets",
        "resources", "skills", "ss_gear", "survivor_energy_essence_costs", "survivors",
        "tech_parts", "tech_resonance", "unlocks", "xeno_pets",
    ]
    exact_systems = {record["system"] for record in rows if record["source_kind"] == "exact"}
    exact_types = {record["data_type"] for record in rows if record["source_kind"] == "exact"}
    derived_domains = set(exact_systems)
    if "breakpoint" in exact_types:
        derived_domains.add("breakpoints")
    if "crit_stat" in exact_types:
        derived_domains.add("crit_stats")
    if "pet_awakening_cost" in exact_types:
        derived_domains.add("pet_awakenings")
    if "tech_resonance_cost" in exact_types:
        derived_domains.add("tech_resonance")
    report = {
        "row_count": len(rows),
        "exact_row_count": sum(r["source_kind"] == "exact" for r in rows),
        "review_row_count": len(review),
        "action_template_count": len(actions),
        "systems": dict(sorted(counts.items())),
        "expected_system_count": len(expected_systems),
        "represented_system_count": len(set(counts)),
        "systems_missing_any_evidence": sorted(set(expected_systems) - set(counts)),
        "systems_pending_exact_normalization": sorted(set(expected_systems) - derived_domains),
        "data_types": {key: len(value) for key, value in sorted(grouped.items())},
        "policy": {
            "executable_rows": "source_kind=exact AND needs_review=false",
            "opinion_override": "forbidden",
            "survival_scoring": "stored with ignore_for_dps=true",
        },
    }
    (output / "validation_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = f"""# Source-pack extraction report

- Master PDF pages extracted: 259
- Source-traced database rows: {len(rows)}
- Exact rows: {sum(r['source_kind'] == 'exact' for r in rows)}
- Inferred rows: {sum(r['source_kind'] == 'inferred' for r in rows)}
- Opinion rows: {sum(r['source_kind'] == 'opinion' for r in rows)}
- Review queue rows: {len(review)}
- Executable action templates: {len(actions)}
- Represented systems: {len(set(counts))}/{len(expected_systems)}

Exact normalized batches currently include pet awakening/affection, resonance energy and chip
costs, chapter unlocks, SS gear damage effects, crit sources, collectible chest odds, clan-shop
numeric rows with icon-safe IDs, timed-muster exchanges, mount costs/sync/components, and pass
EXP, pet merge recipes, survivor star costs, and the complete normal/S survivor level 1-120
energy-essence/ATK/HP chart. Raw OCR evidence for the remaining source pack is retained as review-only and cannot enter
recommendations. Priority/meta and gem-equivalent value charts are labeled opinion.

GPU tables include profile, inventory, resource, action, shop, unlock, breakpoint, chest expected
value, and damage-effect matrices with deterministic compact IDs.
"""
    (output / "extraction_report.md").write_text(summary, encoding="utf-8")
    training_output = ROOT / "reports" / "source_pack_extraction_report.md"
    training_output.parent.mkdir(exist_ok=True)
    training_output.write_text(summary, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--skip-legacy-ocr", action="store_true")
    args = parser.parse_args()
    pages = extract_pages(args.pdf.resolve(), args.output.resolve())
    if len(pages) != 259:
        raise ValueError(f"Expected 259 pages in master map; found {len(pages)}")
    rows = curated_rows(pages)
    correction_path = ROOT / "knowledge" / "source_pack" / "manual_corrections.json"
    if correction_path.exists():
        corrections = json.loads(correction_path.read_text(encoding="utf-8"))
        if not isinstance(corrections, list):
            raise ValueError(f"{correction_path} must contain a JSON array")
        rows.extend(corrections)
    if not args.skip_legacy_ocr:
        rows.extend(import_legacy_evidence(ROOT / "data_sources" / "extracted" / "ocr" / "structured_facts.jsonl"))
    actions = build_actions(rows)
    write_outputs(args.output.resolve(), rows, actions)
    print(json.dumps({"rows": len(rows), "actions": len(actions), "output": str(args.output.resolve())}))


if __name__ == "__main__":
    main()
