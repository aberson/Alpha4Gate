"""Feature encoding: GameSnapshot → fixed-size float vector for neural network input."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from alpha4gate.decision_engine import GameSnapshot

FEATURE_DIM: int = 17

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
]


def encode(snapshot: GameSnapshot) -> NDArray[np.float32]:
    """Encode a GameSnapshot into a normalized float32 vector of length FEATURE_DIM.

    All values are clipped to [0, 1] after normalization.
    """
    raw: list[float] = []
    for field, divisor in _FEATURE_SPEC:
        value = getattr(snapshot, field)
        if isinstance(value, bool):
            value = float(value)
        raw.append(float(value) / divisor)
    arr = np.array(raw, dtype=np.float32)
    np.clip(arr, 0.0, 1.0, out=arr)
    return arr


def decode(vector: NDArray[np.float32]) -> GameSnapshot:
    """Decode a normalized feature vector back into a GameSnapshot (approximate).

    Useful for debugging. Integer fields are rounded; bool fields use > 0.5 threshold.
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
