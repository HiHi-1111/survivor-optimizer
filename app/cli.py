"""Run a sample optimization from the command line."""

from __future__ import annotations

from pprint import pprint

from optimizer.main import optimize


def sample_player() -> dict:
    return {
        "build_stats": {
            "atk": 500000,
            "crit_rate": 0.75,
            "crit_damage": 2.5,
            "skill_damage": 1.2,
            "vulnerability": 0,
            "shield_damage": 0,
            "damage_to_chilled": 0,
            "damage_to_poisoned": 0,
            "boss_damage": 0,
            "all_damage": 0,
            "final_damage": 0,
        },
        "inventory": {"core_selector_chests": 3},
        "resources": {"astral_core": 1, "xeno_core": 0, "resonance_chip": 4},
        "goal_scenario": "scenario_1",
    }


def main() -> None:
    pprint(optimize(sample_player()), sort_dicts=False)


if __name__ == "__main__":
    main()
