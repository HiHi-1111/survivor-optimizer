"""Generate synthetic player profiles for optimizer stress tests.

The generated account states are fake, but IDs are sampled from the local
knowledge files so this does not invent Survivor.io item/resource IDs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


KNOWLEDGE_DIR = ROOT / "knowledge"
DEFAULT_OUTPUT = ROOT / "training_outputs" / "raw" / "synthetic_profiles.jsonl"
STAGES = ("beginner", "early_midgame", "midgame", "late_midgame", "late_game", "endgame")
ARCHETYPES = (
    "f2p", "low_spender", "gem_heavy", "gem_poor", "chest_heavy", "selector_heavy",
    "pet_heavy", "xeno_heavy", "gear_heavy", "ss_heavy", "tech_heavy",
    "collectible_heavy", "event_heavy", "clan_shop_heavy", "near_breakpoint",
    "far_from_breakpoint", "shard_heavy", "messy_inventory", "saved_resources",
    "bad_upgrade_history", "unusual_bottleneck",
)


def _read_records(section: str, knowledge_dir: Path = KNOWLEDGE_DIR) -> list[dict[str, Any]]:
    path = knowledge_dir / f"{section}.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def _ids(section: str, knowledge_dir: Path = KNOWLEDGE_DIR) -> list[str]:
    return sorted(str(record["id"]) for record in _read_records(section, knowledge_dir) if isinstance(record, dict) and record.get("id"))


def load_id_pools(knowledge_dir: Path = KNOWLEDGE_DIR) -> dict[str, list[str]]:
    return {
        "resources": _ids("resources", knowledge_dir),
        "gear": _ids("gear", knowledge_dir),
        "skills": _ids("skills", knowledge_dir),
        "pets": _ids("pets", knowledge_dir),
        "xeno_pets": _ids("xeno_pets", knowledge_dir),
        "tech_parts": _ids("tech_parts", knowledge_dir),
        "collectibles": _ids("collectibles", knowledge_dir),
        "survivors": _ids("survivors", knowledge_dir),
        "survivor_awakenings": _ids("survivor_awakenings", knowledge_dir),
        "chests": _ids("chests", knowledge_dir),
        "event_shops": _ids("event_shops", knowledge_dir),
        "clan_shop": _ids("clan_shop", knowledge_dir),
        "universal_exchange": _ids("universal_exchange", knowledge_dir),
        "breakpoints": _ids("breakpoints", knowledge_dir),
        "scenarios": _ids("scenarios", knowledge_dir),
    }


def _stage_ranges(stage: str) -> dict[str, tuple[int, int] | tuple[float, float]]:
    ranges = {
        "beginner": {
            "atk": (1000, 50000),
            "core_selector_chests": (0, 2),
            "resource": (0, 3),
            "owned": (0, 3),
            "crit_rate": (0.05, 0.35),
        },
        "early_midgame": {
            "atk": (25000, 150000),
            "core_selector_chests": (0, 4),
            "resource": (0, 6),
            "owned": (1, 6),
            "crit_rate": (0.15, 0.5),
        },
        "midgame": {
            "atk": (50000, 300000),
            "core_selector_chests": (0, 5),
            "resource": (0, 8),
            "owned": (1, 8),
            "crit_rate": (0.2, 0.65),
        },
        "late_midgame": {
            "atk": (150000, 700000),
            "core_selector_chests": (1, 7),
            "resource": (1, 12),
            "owned": (2, 12),
            "crit_rate": (0.35, 0.8),
        },
        "late_game": {
            "atk": (300000, 1200000),
            "core_selector_chests": (1, 9),
            "resource": (1, 16),
            "owned": (3, 15),
            "crit_rate": (0.45, 0.9),
        },
        "endgame": {
            "atk": (1200000, 5000000),
            "core_selector_chests": (2, 15),
            "resource": (2, 30),
            "owned": (5, 25),
            "crit_rate": (0.65, 1.0),
        },
    }
    return ranges[stage]


def _sample_many(rng: random.Random, values: list[str], min_count: int, max_count: int) -> list[str]:
    if not values or max_count <= 0:
        return []
    count = min(len(values), rng.randint(min_count, max_count))
    return sorted(rng.sample(values, count)) if count else []


def generate_profile(index: int, rng: random.Random, stage: str, pools: dict[str, list[str]]) -> dict[str, Any]:
    selected_stage = rng.choice(STAGES) if stage == "mixed" else stage
    archetype = rng.choice(ARCHETYPES)
    ranges = _stage_ranges(selected_stage)
    owned_min, owned_max = ranges["owned"]  # type: ignore[assignment]
    resource_min, resource_max = ranges["resource"]  # type: ignore[assignment]
    atk_min, atk_max = ranges["atk"]  # type: ignore[assignment]
    crit_min, crit_max = ranges["crit_rate"]  # type: ignore[assignment]

    resources = {
        resource_id: rng.randint(int(resource_min), int(resource_max))
        for resource_id in pools["resources"]
    }
    resources.setdefault("astral_core", rng.randint(int(resource_min), int(resource_max)))
    resources.setdefault("xeno_core", rng.randint(int(resource_min), int(resource_max)))
    resources.setdefault("resonance_chip", rng.randint(int(resource_min), int(resource_max)))
    resources.setdefault("relic_core", rng.randint(0, int(resource_max)))
    if archetype in {"saved_resources", "ss_heavy", "xeno_heavy", "tech_heavy"}:
        multiplier = 2 if archetype == "saved_resources" else 1.5
        resources = {key: int(value * multiplier) for key, value in resources.items()}
    if "gem" in resources:
        if archetype == "gem_heavy":
            resources["gem"] = max(resources["gem"], int(resource_max) * 20)
        elif archetype in {"gem_poor", "f2p"}:
            resources["gem"] = min(resources["gem"], 2)

    owned_items = sorted(
        set(
            _sample_many(rng, pools["gear"], int(owned_min), int(owned_max))
            + _sample_many(rng, pools["skills"], 0, max(1, int(owned_max) // 4))
            + _sample_many(rng, pools["pets"], 0, max(0, int(owned_max) // 3))
            + _sample_many(rng, pools["xeno_pets"], 0, max(0, int(owned_max) // 4))
            + _sample_many(rng, pools["tech_parts"], 0, max(0, int(owned_max) // 2))
            + _sample_many(rng, pools["collectibles"], 0, max(0, int(owned_max)))
            + _sample_many(rng, pools["survivors"], 0, max(0, int(owned_max) // 3))
            + _sample_many(rng, pools["survivor_awakenings"], 0, 1)
            + _sample_many(rng, pools["chests"], 0, max(1, int(owned_max) // 3))
            + _sample_many(rng, pools["event_shops"] + pools["clan_shop"] + pools["universal_exchange"], 0, max(0, int(owned_max) // 3))
            + _sample_many(rng, pools["breakpoints"], 0, max(0, int(owned_max) // 2))
        )
    )

    scenarios = pools["scenarios"] or ["scenario_1", "scenario_2", "scenario_3"]
    scenario_by_archetype = {
        "f2p": "scenario_f2p_gems", "gem_poor": "scenario_f2p_gems", "event_heavy": "scenario_event_shop",
        "clan_shop_heavy": "scenario_clan_shop", "pet_heavy": "scenario_pet_xeno", "xeno_heavy": "scenario_pet_xeno",
        "gear_heavy": "scenario_gear_ss", "ss_heavy": "scenario_gear_ss", "collectible_heavy": "scenario_collectibles",
    }
    scenario = scenario_by_archetype.get(archetype)
    if scenario not in scenarios:
        scenario = rng.choice(scenarios)
    chest_min, chest_max = ranges["core_selector_chests"]  # type: ignore[misc]
    chest_count = rng.randint(int(chest_min), int(chest_max))
    if archetype in {"chest_heavy", "selector_heavy"}:
        chest_count = max(chest_count, int(chest_max))
    near_breakpoint = archetype == "near_breakpoint" or (archetype != "far_from_breakpoint" and bool(rng.getrandbits(1)))
    profile = {
        "id": f"synthetic_{selected_stage}_{index:08d}_{rng.randrange(10**9):09d}",
        "stage": selected_stage,
        "player_state": {
            "build_stats": {
                "atk": rng.randint(int(atk_min), int(atk_max)),
                "crit_rate": round(rng.uniform(float(crit_min), float(crit_max)), 3),
                "crit_damage": round(rng.uniform(1.5, 4.5), 3),
                "skill_damage": round(rng.uniform(0.0, 2.0), 3),
                "vulnerability": round(rng.uniform(0.0, 0.8), 3),
                "boss_damage": round(rng.uniform(0.0, 1.5), 3),
                "all_damage": round(rng.uniform(0.0, 1.5), 3),
                "final_damage": round(rng.uniform(0.0, 1.0), 3),
                "hp": rng.randint(1000, 10000000),
                "healing": rng.randint(0, 5000),
            },
            "inventory": {
                "core_selector_chests": chest_count,
                "selector_chests": {},
                "items": {
                    item_id: {
                        "count": rng.randint(1, max(1, int(resource_max))),
                        "rarity": rng.choice(["excellent", "epic", "legendary", "red", "unknown"]),
                        "duplicates": rng.randint(0, 5),
                        "forge_level": rng.randint(0, 5),
                    }
                    for item_id in owned_items
                },
            },
            "resources": resources,
            "owned_items": owned_items,
            "gear": {
                "weapon": {"id": rng.choice(pools["gear"]) if pools["gear"] else None, "rarity": "unknown", "forge_level": rng.randint(0, 5)},
                "necklace": {"id": rng.choice(pools["gear"]) if pools["gear"] else None, "rarity": "unknown", "forge_level": rng.randint(0, 5)},
                "gloves": {"id": rng.choice(pools["gear"]) if pools["gear"] else None, "rarity": "unknown", "forge_level": rng.randint(0, 5)},
                "armor": {"id": rng.choice(pools["gear"]) if pools["gear"] else None, "rarity": "unknown", "forge_level": rng.randint(0, 5)},
                "belt": {"id": rng.choice(pools["gear"]) if pools["gear"] else None, "rarity": "unknown", "forge_level": rng.randint(0, 5)},
                "boots": {"id": rng.choice(pools["gear"]) if pools["gear"] else None, "rarity": "unknown", "forge_level": rng.randint(0, 5)},
            },
            "pets": {
                "main_pet": rng.choice(pools["pets"]) if pools["pets"] else None,
                "assisting_pets": _sample_many(rng, pools["pets"], 0, 2),
                "awakened": {pet_id: rng.randint(0, 6) for pet_id in _sample_many(rng, pools["pets"] + pools["xeno_pets"], 0, 4)},
            },
            "tech_parts": {
                "equipped": _sample_many(rng, pools["tech_parts"], 0, 6),
                "resonance": {tech_id: rng.randint(0, 10) for tech_id in _sample_many(rng, pools["tech_parts"], 0, 4)},
            },
            "collectibles": {
                "owned": {collectible_id: rng.randint(1, 5) for collectible_id in _sample_many(rng, pools["collectibles"], 0, int(owned_max))},
                "salvage": {},
            },
            "survivor": {
                "id": rng.choice(pools["survivors"]) if pools["survivors"] else None,
                "awakening": rng.randint(0, 6),
                "passives": [],
            },
            "close_to_breakpoint": near_breakpoint,
            "goal_scenario": scenario,
            "metadata": {
                "archetype": archetype,
                "spending_profile": "f2p" if archetype == "f2p" else ("low_spender" if archetype == "low_spender" else "unknown"),
                "near_breakpoint": near_breakpoint,
                "close_to_xeno_breakpoint": near_breakpoint and archetype in {"xeno_heavy", "pet_heavy", "near_breakpoint"},
                "close_to_astral_forge_breakpoint": near_breakpoint and archetype in {"gear_heavy", "ss_heavy", "near_breakpoint"},
                "close_to_tech_resonance_breakpoint": near_breakpoint and archetype in {"tech_heavy", "near_breakpoint"},
                "close_to_collectible_set_breakpoint": near_breakpoint and archetype in {"collectible_heavy", "near_breakpoint"},
                "bottlenecked": archetype in {"unusual_bottleneck", "gem_poor", "far_from_breakpoint"},
            },
            "notes": f"synthetic profile for {selected_stage} optimizer stress testing",
        },
    }
    return profile


def generate_profiles(count: int, seed: int, stage: str, knowledge_dir: Path = KNOWLEDGE_DIR) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    pools = load_id_pools(knowledge_dir)
    profiles: list[dict[str, Any]] = []
    seen_states: set[str] = set()
    attempts = 0
    max_attempts = max(count * 20, 100)
    while len(profiles) < count and attempts < max_attempts:
        attempts += 1
        profile = generate_profile(attempts, rng, stage, pools)
        key = json.dumps(profile["player_state"], sort_keys=True)
        if key in seen_states and len(seen_states) < count:
            continue
        seen_states.add(key)
        profiles.append(profile)
    return profiles


def write_profiles(profiles: list[dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for profile in profiles:
            handle.write(json.dumps(profile, ensure_ascii=False, sort_keys=True) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate synthetic optimizer player profiles.")
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260618)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--stage", choices=[*STAGES, "mixed"], default="mixed")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    profiles = generate_profiles(args.count, args.seed, args.stage)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    write_profiles(profiles, output)
    print(f"wrote {len(profiles)} profiles to {output.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
