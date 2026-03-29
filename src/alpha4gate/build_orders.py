"""Build order definitions, sequencer, and Spawning Tool integration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class BuildStep:
    """A single step in a build order."""

    supply: int
    action: str  # "build", "train", or "research"
    target: str  # UnitTypeId or UpgradeId name

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {"supply": self.supply, "action": self.action, "target": self.target}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BuildStep:
        """Deserialize from dict."""
        return cls(supply=data["supply"], action=data["action"], target=data["target"])


@dataclass
class BuildOrder:
    """A named build order with step sequence."""

    id: str
    name: str
    source: str = "manual"
    steps: list[BuildStep] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BuildOrder:
        """Deserialize from dict."""
        return cls(
            id=data["id"],
            name=data["name"],
            source=data.get("source", "manual"),
            steps=[BuildStep.from_dict(s) for s in data.get("steps", [])],
        )


class BuildSequencer:
    """Tracks progress through a build order based on current supply."""

    def __init__(self, order: BuildOrder) -> None:
        self._order = order
        self._current_index = 0

    @property
    def order(self) -> BuildOrder:
        """The build order being sequenced."""
        return self._order

    @property
    def current_index(self) -> int:
        """Index of the current step."""
        return self._current_index

    @property
    def is_complete(self) -> bool:
        """Whether all steps have been executed."""
        return self._current_index >= len(self._order.steps)

    @property
    def current_step(self) -> BuildStep | None:
        """The current step to execute, or None if complete."""
        if self.is_complete:
            return None
        return self._order.steps[self._current_index]

    @property
    def total_steps(self) -> int:
        """Total number of steps in the build order."""
        return len(self._order.steps)

    def should_execute(self, current_supply: int) -> bool:
        """Check if the current step should be executed at the given supply.

        Args:
            current_supply: The player's current supply count.

        Returns:
            True if current supply >= step's trigger supply and not complete.
        """
        step = self.current_step
        if step is None:
            return False
        return current_supply >= step.supply

    def advance(self) -> BuildStep | None:
        """Advance to the next step after executing the current one.

        Returns:
            The step that was just completed, or None if already complete.
        """
        if self.is_complete:
            return None
        completed = self._order.steps[self._current_index]
        self._current_index += 1
        return completed

    def reset(self) -> None:
        """Reset the sequencer to the beginning."""
        self._current_index = 0


def slug_from_name(name: str) -> str:
    """Generate a URL-safe slug from a build order name.

    Args:
        name: Human-readable name like "4-Gate Timing Push".

    Returns:
        Slug like "4-gate-timing-push".
    """
    return name.lower().replace(" ", "-")


def load_build_orders(path: Path) -> list[BuildOrder]:
    """Load build orders from a JSON file.

    Args:
        path: Path to build_orders.json.

    Returns:
        List of BuildOrder instances. Empty list if file doesn't exist.
    """
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [BuildOrder.from_dict(o) for o in data.get("orders", [])]


def save_build_orders(orders: list[BuildOrder], path: Path) -> None:
    """Save build orders to a JSON file.

    Args:
        orders: List of BuildOrder instances.
        path: Path to write build_orders.json.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"orders": [o.to_dict() for o in orders]}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def default_4gate() -> BuildOrder:
    """Return the default 4-Gate Timing Push build order."""
    return BuildOrder(
        id="4gate",
        name="4-Gate Timing Push",
        source="manual",
        steps=[
            BuildStep(supply=14, action="build", target="Pylon"),
            BuildStep(supply=16, action="build", target="Gateway"),
            BuildStep(supply=16, action="build", target="Assimilator"),
            BuildStep(supply=19, action="build", target="Nexus"),
            BuildStep(supply=20, action="build", target="CyberneticsCore"),
            BuildStep(supply=21, action="build", target="Pylon"),
            BuildStep(supply=23, action="build", target="Gateway"),
            BuildStep(supply=25, action="build", target="Gateway"),
            BuildStep(supply=27, action="build", target="Gateway"),
        ],
    )
