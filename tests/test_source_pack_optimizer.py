from __future__ import annotations

from optimizer.source_pack_optimizer import (
    clear_source_pack_cache,
    optimize_source_pack_actions,
    optimize_source_pack_batch,
)


def test_resonance_action_is_profile_and_resource_gated() -> None:
    clear_source_pack_cache()
    result = optimize_source_pack_actions({
        "resources": {"resonance_chip": 1},
        "tech_parts": {"resonance": {"multiplier": 1.0}},
    }, device="cpu")
    assert result["best"]["action_id"] == "resonance_multiplier_1_2"
    assert result["actionable_count"] == 1
    assert result["false_prunes"] == []


def test_mount_action_requires_owned_mount_and_exact_previous_rarity() -> None:
    result = optimize_source_pack_actions({
        "resources": {"electric_scooter_shard": 20},
        "mounts": {"electric_scooter": {"rarity": "Base", "sync_rate": 0.20}},
        "tech_parts": {"resonance": {"multiplier": 9.0}},
    }, device="cpu")
    assert [action["action_id"] for action in result["ranked_actions"]] == ["upgrade_electric_scooter_y1"]
    assert result["best"]["estimated_dps_value"] == 2.0


def test_unaffordable_templates_are_logged_not_pruned() -> None:
    result = optimize_source_pack_actions({}, device="cpu")
    assert result["best"] is None
    assert result["rejected_count"] == result["templates_considered"]
    assert result["pruning_policy"].startswith("disabled")


def test_profiles_are_ranked_in_one_grouped_numeric_batch() -> None:
    batch = optimize_source_pack_batch([
        {"resources": {"resonance_chip": 1}, "tech_parts": {"resonance": {"multiplier": 1.0}}},
        {
            "resources": {"hoverboard_shard": 10},
            "mounts": {"hoverboard": {"rarity": "Base", "sync_rate": 0.30}},
            "tech_parts": {"resonance": {"multiplier": 9.0}},
        },
    ], device="cpu")
    assert batch["numeric_backend"]["profiles_scored"] == 2
    assert batch["numeric_backend"]["batches"] == 1
    assert batch["profiles"][0]["best"]["action_id"] == "resonance_multiplier_1_2"
    assert batch["profiles"][1]["best"]["action_id"] == "upgrade_hoverboard_y1"
