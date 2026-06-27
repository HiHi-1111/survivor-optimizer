from optimizer.player_state import PlayerState


def test_player_state_validates_sample_player():
    player = PlayerState(
        build_stats={
            "atk": 500000,
            "crit_rate": 0.75,
            "crit_damage": 2.5,
            "skill_damage": 1.2,
        },
        inventory={"core_selector_chests": 3},
        resources={
            "astral_core": 1,
            "xeno_core": 0,
            "resonance_chip": 4,
        },
        goal_scenario="scenario_1",
    )
    assert player.build_stats.atk == 500000
    assert player.inventory.core_selector_chests == 3
