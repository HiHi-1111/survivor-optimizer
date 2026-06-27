from __future__ import annotations

from typing import Any

def generate_actions(player_state: Any, knowledge: dict[str, Any], options: dict[str, Any] | None = None):
    # Resource catalog rows describe currencies; they are not actions by
    # themselves. Dedicated core/resonance/pet/survivor generators own spends.
    # A single cross-system hold candidate is emitted by save_hold.py.
    return []
