"""Observable completion of hosted regrouping; never issues an attack command."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from jev.contracts import Entity, JsonValue, Point

# Initial development hypotheses, measured in game time at every speed.
RECOVERY_SETTLE_SECONDS = 5.0
RECOVERY_HOME_FRACTION = 0.8


@dataclass
class RecoveryProgress:
    """Repeated answers share progress; completion stays latched at home.

    Leaving home, a new loss/damage, too few units, or a visible base threat
    rearms recovery. Completion releases only the hosted strategic override;
    local defense, small-army recovery and tactical intent still decide orders.
    """

    started: float | None = None
    settled_since: float | None = None
    completed: bool = False
    completed_at: float | None = None
    previous: dict[int, float] = field(default_factory=dict)
    facts: dict[str, JsonValue] = field(default_factory=dict)

    def update(
        self, *, game: float, active: bool, army: tuple[Entity, ...], home: Point,
        radius: float, minimum: int, threatened: bool,
    ) -> None:
        current = {unit.tag: unit.durability for unit in army}
        damaged = any(tag not in current or current[tag] < health
                      for tag, health in self.previous.items())
        self.previous = current
        at_home = sum(math.dist(unit.position, home) <= radius for unit in army)
        grouped = bool(army) and at_home >= math.ceil(len(army) * RECOVERY_HOME_FRACTION)
        ready = len(army) >= minimum and grouped and not threatened and not damaged
        reason = (
            "base_threat" if threatened else "new_losses_or_damage" if damaged
            else "rebuilding" if len(army) < minimum else "regrouping" if not grouped
            else "settling"
        )
        if not ready:
            self.completed = False
            self.completed_at = None
            self.settled_since = None
        if active and not self.completed:
            if self.started is None:
                self.started = game
            if ready:
                if self.settled_since is None:
                    self.settled_since = game
                if game - self.settled_since >= RECOVERY_SETTLE_SECONDS:
                    self.completed = True
                    self.completed_at = game
        elif not active and not self.completed:
            self.started = self.settled_since = None
        self.facts = {
            "status": "completed" if self.completed else reason if active else "available",
            "started_game_seconds": self.started,
            "elapsed_game_seconds": None if self.started is None else (
                (self.completed_at if self.completed_at is not None else game) - self.started
            ),
            "ready_combat_units": len(army), "units_at_home": at_home,
            "minimum_units": minimum, "home_fraction_required": RECOVERY_HOME_FRACTION,
            "home_radius": radius, "base_threatened": threatened,
            "new_losses_or_damage": damaged,
            "settled_game_seconds": 0.0 if self.settled_since is None else (
                (self.completed_at if self.completed_at is not None else game) - self.settled_since
            ),
            "settle_required_game_seconds": RECOVERY_SETTLE_SECONDS,
            "completed_game_seconds": self.completed_at,
        }
