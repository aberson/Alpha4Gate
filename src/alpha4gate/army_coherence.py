"""Army coherence manager: staging, grouping, and engagement decisions.

Provides per-game randomized parameters for attack/retreat thresholds,
army coherence checking, and staging point calculation. All parameters
are logged for training data correlation with win/loss outcomes.
"""

from __future__ import annotations

import math
import random
from typing import Any


class ArmyCoherenceManager:
    """Manages army grouping, staging, and engagement decisions.

    All threshold parameters are randomized per game to generate diverse
    training data. Parameters are rolled once at construction and exposed
    via ``get_params_dict()`` for logging.
    """

    # Parameter ranges: (min, max)
    _RANGES: dict[str, tuple[float, float]] = {
        "attack_supply_ratio": (1.0, 1.5),
        "attack_supply_floor": (15.0, 25.0),
        "retreat_supply_ratio": (0.4, 0.7),
        "coherence_pct": (0.60, 0.80),
        "coherence_distance": (6.0, 10.0),
        "staging_distance": (12.0, 20.0),
    }

    # Staging timeout: push even if not coherent after this many seconds
    STAGING_TIMEOUT_SECONDS: float = 60.0

    # Hysteresis multiplier: after retreat, require attack_ratio * this to re-engage
    HYSTERESIS_MULTIPLIER: float = 1.2

    def __init__(self, seed: int | None = None) -> None:
        rng = random.Random(seed)

        # Roll continuous parameters
        self.attack_supply_ratio: float = rng.uniform(*self._RANGES["attack_supply_ratio"])
        self.attack_supply_floor: float = rng.uniform(*self._RANGES["attack_supply_floor"])
        self.retreat_supply_ratio: float = rng.uniform(*self._RANGES["retreat_supply_ratio"])
        self.coherence_pct: float = rng.uniform(*self._RANGES["coherence_pct"])
        self.coherence_distance: float = rng.uniform(*self._RANGES["coherence_distance"])
        self.staging_distance: float = rng.uniform(*self._RANGES["staging_distance"])

        # Roll boolean parameter
        self.retreat_to_staging: bool = rng.random() < 0.5

        # Validate: floor must allow attacking even with no enemy visible
        # If attack_supply_floor > 25 somehow, cap it (shouldn't happen with range)
        self.attack_supply_floor = min(self.attack_supply_floor, 25.0)

        # State tracking
        self._recently_retreated: bool = False
        self._staging_start_time: float | None = None

    def get_params_dict(self) -> dict[str, float | bool]:
        """Return all rolled parameters as a dict for logging."""
        return {
            "attack_supply_ratio": round(self.attack_supply_ratio, 3),
            "attack_supply_floor": round(self.attack_supply_floor, 1),
            "retreat_supply_ratio": round(self.retreat_supply_ratio, 3),
            "coherence_pct": round(self.coherence_pct, 3),
            "coherence_distance": round(self.coherence_distance, 1),
            "staging_distance": round(self.staging_distance, 1),
            "retreat_to_staging": self.retreat_to_staging,
        }

    # ------------------------------------------------------------------ #
    #  Centroid & coherence
    # ------------------------------------------------------------------ #

    @staticmethod
    def compute_centroid(units: list[Any]) -> tuple[float, float]:
        """Compute the average position (centroid) of a list of units.

        Args:
            units: List of unit objects with ``.position.x`` and ``.position.y``.

        Returns:
            (x, y) centroid. Returns (0, 0) if the list is empty.
        """
        if not units:
            return (0.0, 0.0)
        sx = sum(u.position.x for u in units)
        sy = sum(u.position.y for u in units)
        n = len(units)
        return (sx / n, sy / n)

    def is_coherent(self, units: list[Any]) -> bool:
        """Check if enough of the army is grouped near the centroid.

        Returns True when at least ``coherence_pct`` of units are within
        ``coherence_distance`` of the army centroid.
        """
        if len(units) <= 1:
            return True
        cx, cy = self.compute_centroid(units)
        near = sum(
            1
            for u in units
            if math.hypot(u.position.x - cx, u.position.y - cy) <= self.coherence_distance
        )
        return near / len(units) >= self.coherence_pct

    # ------------------------------------------------------------------ #
    #  Staging point
    # ------------------------------------------------------------------ #

    @staticmethod
    def compute_staging_point(
        own_base: tuple[float, float],
        enemy_structures: list[tuple[float, float]],
        enemy_start: tuple[float, float],
        staging_distance: float,
    ) -> tuple[float, float]:
        """Compute a staging point that is ``staging_distance`` away from the
        nearest known enemy structure, along the line from own base.

        Falls back to 70% of the distance to enemy start if no enemy
        structures are known.

        Args:
            own_base: (x, y) of our main base.
            enemy_structures: List of (x, y) positions of known enemy structures.
            enemy_start: (x, y) of enemy start location.
            staging_distance: How far from the nearest enemy structure to stage.

        Returns:
            (x, y) staging point.
        """
        # Pick the nearest enemy reference point
        if enemy_structures:
            # Find closest enemy structure to our base
            target = min(
                enemy_structures,
                key=lambda s: math.hypot(s[0] - own_base[0], s[1] - own_base[1]),
            )
        else:
            target = enemy_start

        dx = target[0] - own_base[0]
        dy = target[1] - own_base[1]
        dist = math.hypot(dx, dy)

        if dist == 0:
            return own_base

        if not enemy_structures:
            # Fallback: 70% of distance to enemy start
            ratio = 0.7
            return (own_base[0] + dx * ratio, own_base[1] + dy * ratio)

        # Stage at staging_distance from the target, along the line from base to target
        if dist <= staging_distance:
            # Too close — stage at midpoint
            return (own_base[0] + dx * 0.5, own_base[1] + dy * 0.5)

        # Point along the line that is staging_distance away from target
        ratio = (dist - staging_distance) / dist
        return (own_base[0] + dx * ratio, own_base[1] + dy * ratio)

    # ------------------------------------------------------------------ #
    #  Attack / retreat decisions
    # ------------------------------------------------------------------ #

    def should_attack(self, own_supply: float, enemy_visible_supply: float) -> bool:
        """Return True if army is strong enough to push.

        Uses hysteresis: after a retreat, requires ``attack_supply_ratio * 1.2``
        before re-engaging.
        """
        # Always attack if we meet the floor and enemy is tiny/unscouted
        if own_supply >= self.attack_supply_floor and enemy_visible_supply == 0:
            self._recently_retreated = False
            return True

        required_ratio = self.attack_supply_ratio
        if self._recently_retreated:
            required_ratio *= self.HYSTERESIS_MULTIPLIER

        if enemy_visible_supply > 0 and own_supply >= enemy_visible_supply * required_ratio:
            self._recently_retreated = False
            return True

        if own_supply >= self.attack_supply_floor and enemy_visible_supply == 0:
            self._recently_retreated = False
            return True

        return False

    def should_retreat(self, own_supply: float, enemy_visible_supply: float) -> bool:
        """Return True if army should pull back."""
        if enemy_visible_supply == 0:
            return False
        if own_supply < enemy_visible_supply * self.retreat_supply_ratio:
            self._recently_retreated = True
            return True
        return False

    # ------------------------------------------------------------------ #
    #  Staging timeout
    # ------------------------------------------------------------------ #

    def update_staging_timer(self, game_time: float, is_staging: bool) -> bool:
        """Track how long the army has been staging. Returns True if timed out.

        Call this each step when the army is in staging mode. Resets when
        not staging.
        """
        if not is_staging:
            self._staging_start_time = None
            return False

        if self._staging_start_time is None:
            self._staging_start_time = game_time
            return False

        return (game_time - self._staging_start_time) >= self.STAGING_TIMEOUT_SECONDS

    def reset_retreat_flag(self) -> None:
        """Manually clear the retreat hysteresis flag."""
        self._recently_retreated = False
