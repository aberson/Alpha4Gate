"""Probe scouting, enemy tracking, and threat assessment."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sc2.ids.unit_typeid import UnitTypeId

if TYPE_CHECKING:
    from sc2.bot_ai import BotAI


# Threat weights by unit type (supply cost as proxy; high-value units get bonus)
THREAT_WEIGHTS: dict[UnitTypeId, float] = {
    # Terran
    UnitTypeId.MARINE: 1.0,
    UnitTypeId.MARAUDER: 2.0,
    UnitTypeId.SIEGETANK: 3.0,
    UnitTypeId.SIEGETANKSIEGED: 4.0,
    UnitTypeId.MEDIVAC: 2.0,
    UnitTypeId.THOR: 6.0,
    UnitTypeId.BATTLECRUISER: 8.0,
    # Zerg
    UnitTypeId.ZERGLING: 0.5,
    UnitTypeId.BANELING: 1.0,
    UnitTypeId.ROACH: 2.0,
    UnitTypeId.HYDRALISK: 2.0,
    UnitTypeId.MUTALISK: 3.0,
    UnitTypeId.ULTRALISK: 6.0,
    UnitTypeId.BROODLORD: 6.0,
    # Protoss
    UnitTypeId.ZEALOT: 2.0,
    UnitTypeId.STALKER: 2.0,
    UnitTypeId.SENTRY: 1.5,
    UnitTypeId.IMMORTAL: 4.0,
    UnitTypeId.COLOSSUS: 6.0,
    UnitTypeId.VOIDRAY: 4.0,
    UnitTypeId.CARRIER: 8.0,
    UnitTypeId.ARCHON: 4.0,
}

# Default weight for unknown unit types
DEFAULT_THREAT_WEIGHT = 1.0

# Threat level thresholds
THREAT_LOW = 5.0
THREAT_MEDIUM = 15.0
THREAT_HIGH = 30.0


@dataclass
class EnemyComposition:
    """Summary of known enemy forces."""

    units: dict[str, int] = field(default_factory=dict)
    structures: dict[str, int] = field(default_factory=dict)
    threat_score: float = 0.0
    threat_level: str = "none"  # none, low, medium, high, critical

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "units": self.units,
            "structures": self.structures,
            "threat_score": round(self.threat_score, 1),
            "threat_level": self.threat_level,
        }


def compute_threat_score(enemy_units: list[Any]) -> float:
    """Compute a threat score from a list of enemy units.

    Args:
        enemy_units: List of SC2 Unit objects (or mocks with .type_id).

    Returns:
        Numeric threat score.
    """
    score = 0.0
    for unit in enemy_units:
        weight = THREAT_WEIGHTS.get(unit.type_id, DEFAULT_THREAT_WEIGHT)
        score += weight
    return score


def threat_level_from_score(score: float) -> str:
    """Convert a numeric threat score to a human-readable level.

    Args:
        score: Numeric threat score.

    Returns:
        One of: "none", "low", "medium", "high", "critical".
    """
    if score <= 0:
        return "none"
    if score <= THREAT_LOW:
        return "low"
    if score <= THREAT_MEDIUM:
        return "medium"
    if score <= THREAT_HIGH:
        return "high"
    return "critical"


def assess_enemy(bot: BotAI) -> EnemyComposition:
    """Assess known enemy forces and compute threat.

    Args:
        bot: BotAI instance with current game state.

    Returns:
        EnemyComposition with unit/structure counts and threat level.
    """
    # Count enemy units by name
    unit_counts: dict[str, int] = {}
    for unit in bot.enemy_units:
        name = unit.name
        unit_counts[name] = unit_counts.get(name, 0) + 1

    # Count enemy structures by name
    struct_counts: dict[str, int] = {}
    for struct in bot.enemy_structures:
        name = struct.name
        struct_counts[name] = struct_counts.get(name, 0) + 1

    score = compute_threat_score(list(bot.enemy_units))
    level = threat_level_from_score(score)

    return EnemyComposition(
        units=unit_counts,
        structures=struct_counts,
        threat_score=score,
        threat_level=level,
    )


class ScoutManager:
    """Manages probe scouting and enemy base tracking."""

    # Scout timing: first scout at this game-time (seconds)
    FIRST_SCOUT_TIME = 60.0
    # Re-scout interval
    RESCOUT_INTERVAL = 120.0

    def __init__(self) -> None:
        self._scout_tag: int | None = None
        self._last_scout_time: float = -self.RESCOUT_INTERVAL
        self._enemy_base_locations: list[Any] = []

    @property
    def scout_tag(self) -> int | None:
        """Tag of the probe currently used for scouting, or None."""
        return self._scout_tag

    @property
    def enemy_base_locations(self) -> list[Any]:
        """Known enemy base locations."""
        return self._enemy_base_locations

    def should_scout(self, game_time: float) -> bool:
        """Check if it's time to send a scout.

        Args:
            game_time: Current game time in seconds.

        Returns:
            True if a scout should be dispatched.
        """
        if game_time < self.FIRST_SCOUT_TIME:
            return False
        if self._scout_tag is not None:
            return False  # Already scouting
        if game_time - self._last_scout_time < self.RESCOUT_INTERVAL:
            return False
        return True

    def assign_scout(self, probe_tag: int, game_time: float) -> None:
        """Assign a probe as the active scout.

        Args:
            probe_tag: The unit tag of the probe to use.
            game_time: Current game time when scout is dispatched.
        """
        self._scout_tag = probe_tag
        self._last_scout_time = game_time

    def clear_scout(self) -> None:
        """Clear the scout assignment (probe died or returned)."""
        self._scout_tag = None

    def update_enemy_bases(self, locations: list[Any]) -> None:
        """Update known enemy base locations.

        Args:
            locations: List of Point2 positions for enemy townhalls.
        """
        self._enemy_base_locations = list(locations)
