"""Feature encoding: GameSnapshot → fixed-size float vector for neural network input."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from bots.v7.decision_engine import GameSnapshot

# 40 base game-state features + 7 advisor features = 47 total.
# Phase 4.8: added advisor features so PPO can see "what Claude recommends"
# and learn to follow when it correlates with winning (Approach B from #89).
# Phase B: added 15 own-army unit-type count features (histogram expansion).
# Phase B Step 2: added 8 enemy threat-class count features.
FEATURE_DIM: int = 47

# Number of base game-state features (without advisor). Exported for tests
# and for _snapshot_to_raw which stores only game-state features in the DB.
BASE_GAME_FEATURE_DIM: int = 40

# Advisor command action types we track as binary features.
_ADVISOR_ACTIONS: tuple[str, ...] = (
    "scout", "build", "expand", "attack", "defend", "upgrade",
)

# Urgency string → float mapping for the advisor urgency feature.
_URGENCY_MAP: dict[str, float] = {
    "low": 0.25,
    "medium": 0.5,
    "high": 0.75,
    "critical": 1.0,
}

# (field_name, normalization_divisor) — order matches the plan's feature vector spec
_FEATURE_SPEC: list[tuple[str, float]] = [
    ("supply_used", 200.0),
    ("supply_cap", 200.0),
    ("minerals", 2000.0),
    ("vespene", 2000.0),
    ("army_supply", 200.0),
    ("worker_count", 80.0),
    ("base_count", 5.0),
    ("enemy_army_near_base", 1.0),  # bool → 0/1
    ("enemy_army_supply_visible", 200.0),
    ("game_time_seconds", 1200.0),
    ("gateway_count", 10.0),
    ("robo_count", 4.0),
    ("forge_count", 2.0),
    ("upgrade_count", 10.0),
    ("enemy_structure_count", 50.0),
    ("cannon_count", 10.0),
    ("battery_count", 10.0),
    # Own-army unit-type counts (Phase B histogram expansion)
    ("zealot_count", 20.0),
    ("stalker_count", 20.0),
    ("sentry_count", 20.0),
    ("immortal_count", 20.0),
    ("colossus_count", 10.0),
    ("archon_count", 20.0),
    ("high_templar_count", 20.0),
    ("dark_templar_count", 20.0),
    ("phoenix_count", 20.0),
    ("void_ray_count", 20.0),
    ("carrier_count", 10.0),
    ("tempest_count", 10.0),
    ("disruptor_count", 10.0),
    ("warp_prism_count", 5.0),
    ("observer_count", 5.0),
    # Enemy threat-class counts (Phase B Step 2)
    ("enemy_light_count", 20.0),
    ("enemy_armored_count", 20.0),
    ("enemy_siege_count", 20.0),
    ("enemy_support_count", 20.0),
    ("enemy_air_harass_count", 20.0),
    ("enemy_heavy_count", 20.0),
    ("enemy_capital_count", 20.0),
    ("enemy_cloak_count", 20.0),
]


def encode(
    snapshot: GameSnapshot,
    advisor_commands: list[dict[str, str]] | None = None,
    advisor_urgency: str | None = None,
) -> NDArray[np.float32]:
    """Encode a GameSnapshot into a normalized float32 vector of length FEATURE_DIM.

    All values are clipped to [0, 1] after normalization.

    Args:
        snapshot: The current game state.
        advisor_commands: The Claude advisor's most recent command list. Each
            dict should have an ``"action"`` key (e.g. ``"scout"``, ``"build"``,
            ``"attack"``). ``None`` when no advisor is available or no
            recommendation has been made yet — advisor features are all 0.0.
        advisor_urgency: The advisor's urgency string (``"low"`` / ``"medium"``
            / ``"high"`` / ``"critical"``). ``None`` → 0.0.

    Returns:
        Float32 vector of length FEATURE_DIM (47).
    """
    raw: list[float] = []
    for field, divisor in _FEATURE_SPEC:
        value = getattr(snapshot, field)
        if isinstance(value, bool):
            value = float(value)
        raw.append(float(value) / divisor)

    # Advisor features: 6 binary action indicators + 1 urgency float.
    if advisor_commands:
        action_set = {cmd.get("action", "") for cmd in advisor_commands}
        for action_name in _ADVISOR_ACTIONS:
            raw.append(1.0 if action_name in action_set else 0.0)
    else:
        raw.extend([0.0] * len(_ADVISOR_ACTIONS))

    raw.append(_URGENCY_MAP.get(advisor_urgency or "", 0.0))

    arr = np.array(raw, dtype=np.float32)
    np.clip(arr, 0.0, 1.0, out=arr)
    return arr


def decode(vector: NDArray[np.float32]) -> GameSnapshot:
    """Decode a normalized feature vector back into a GameSnapshot (approximate).

    Useful for debugging. Integer fields are rounded; bool fields use > 0.5 threshold.
    The 7 advisor features at the end of the vector are ignored — they are
    ephemeral context that doesn't map back to a GameSnapshot field.
    """
    if vector.shape != (FEATURE_DIM,):
        msg = f"Expected shape ({FEATURE_DIM},), got {vector.shape}"
        raise ValueError(msg)

    values: dict[str, int | float | bool] = {}
    for i, (field, divisor) in enumerate(_FEATURE_SPEC):
        raw = float(vector[i]) * divisor
        if field == "enemy_army_near_base":
            values[field] = raw > 0.5
        elif field == "game_time_seconds":
            values[field] = raw
        else:
            values[field] = int(round(raw))
    return GameSnapshot(**values)  # type: ignore[arg-type]
