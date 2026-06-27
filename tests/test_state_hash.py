from optimizer.player_state import PlayerState
from optimizer.state_hash import search_state_fingerprint, state_fingerprint
from optimizer.state_transition import apply_action


def test_search_fingerprint_tracks_every_mutable_transition_branch():
    state = PlayerState(resources={"gems": 10}, metadata={"progress": {}})
    future = apply_action(state, {
        "action_id": "test:upgrade",
        "consumed_items": {"gems": 1},
        "produced_items": {"part": 1},
        "metadata": {"sets_breakpoints": ["test_breakpoint"]},
        "supported": True,
    })
    assert search_state_fingerprint(future) != search_state_fingerprint(state)


def test_search_fingerprint_omits_search_invariant_branches_only():
    first = PlayerState(build_stats={"atk": 1}, resources={"gems": 10})
    second = PlayerState(build_stats={"atk": 2}, resources={"gems": 10})
    assert search_state_fingerprint(first) == search_state_fingerprint(second)
    assert state_fingerprint(first) != state_fingerprint(second)
