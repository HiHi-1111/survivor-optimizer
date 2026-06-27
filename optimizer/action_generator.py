"""Generate candidate optimizer actions."""

from __future__ import annotations

from dataclasses import dataclass


CORE_SELECTOR_OPTIONS = ("astral_core", "xeno_core", "resonance_chip")


@dataclass(frozen=True)
class CoreSelectorSplit:
    action_type: str
    allocation: dict[str, int]

    @property
    def id(self) -> str:
        parts = [
            f"{resource}_{count}"
            for resource, count in self.allocation.items()
            if count
        ]
        return "core_selector:" + ",".join(parts)


def generate_core_selector_splits(num_chests: int) -> list[CoreSelectorSplit]:
    if num_chests < 0:
        raise ValueError("num_chests must be non-negative")

    splits: list[CoreSelectorSplit] = []
    for astral in range(num_chests + 1):
        for xeno in range(num_chests - astral + 1):
            resonance = num_chests - astral - xeno
            splits.append(
                CoreSelectorSplit(
                    action_type="use_core_selector_chest",
                    allocation={
                        "astral_core": astral,
                        "xeno_core": xeno,
                        "resonance_chip": resonance,
                    },
                )
            )
    return splits
