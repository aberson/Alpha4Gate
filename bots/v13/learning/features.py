"""Feature encoding: GameSnapshot → fixed-size float vector for neural network input."""

from __future__ import annotations

import logging
import os

import numpy as np
from numpy.typing import NDArray

from bots.v13.decision_engine import GameSnapshot

_logger = logging.getLogger(__name__)

# 40 scalar game-state features + 8 z-slot one-hot features +
# 7 advisor features = 55 total.
# Phase 4.8: added advisor features so PPO can see "what Claude recommends"
# and learn to follow when it correlates with winning (Approach B from #89).
# Phase B: added 15 own-army unit-type count features (histogram expansion).
# Phase B Step 2: added 8 enemy threat-class count features.
# Phase D Step D.5: appended 8 z-slot one-hot features (build-order identifier).
#   Slot 0 is the "none" bucket; slots 1-7 cover the first 7 trajectories from
#   the alphabetical build-orders registry.
FEATURE_DIM: int = 55

# Number of "base" feature-vector entries the encoder emits, i.e.
# ``len(_FEATURE_SPEC)`` (40 scalar game-state + 8 z-slot one-hot). Exported
# for tests and for the imitation-learner padding logic.
BASE_GAME_FEATURE_DIM: int = 48

# Width of the z-slot one-hot block (1 "none" bucket + 7 trajectory slots).
# Stable PPO input shape across future trajectory additions: an 8th
# alphabetical trajectory file is logged as a warning and silently decoded
# to slot 0 (the "none" bucket).
Z_SLOT_COUNT: int = 8

# Number of scalar game-state features stored as individual columns in
# ``transitions``. Distinct from :data:`BASE_GAME_FEATURE_DIM` because the
# 8 z slots ride along as a single TEXT column (``current_build_order``)
# in the DB, not as 8 separate numeric columns.
_DB_STATE_FEATURE_COUNT: int = 40

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
    # Build-order identifier z (Phase D Step D.5). Fixed-width 8-slot one-hot
    # appended after the scalar game-state block. Slot 0 = "none" bucket;
    # slots 1-7 = first 7 alphabetical trajectory files. These entries do
    # NOT correspond to attributes on ``GameSnapshot`` — :func:`encode`
    # fills them via :func:`_resolve_z_index` from
    # ``GameSnapshot.current_build_order``. Divisor is 1.0 (one-hot, no
    # normalization needed).
    ("z_slot_0", 1.0),
    ("z_slot_1", 1.0),
    ("z_slot_2", 1.0),
    ("z_slot_3", 1.0),
    ("z_slot_4", 1.0),
    ("z_slot_5", 1.0),
    ("z_slot_6", 1.0),
    ("z_slot_7", 1.0),
]


def _resolve_z_index(name: str | None, registry: list[str]) -> int:
    """Map a build-order name to its z-slot index in ``[0, 7]``.

    Args:
        name: The active build-order name (e.g. ``"4-gate-aggression"``), or
            ``None`` when no build-order is active.
        registry: Sorted list of registered build-order names (filename stems
            from ``bots/<v>/data/build_orders/``, ``_schema`` excluded). Only
            the first 7 entries are addressable as slots 1-7; an 8th+ entry
            falls back to slot 0 (the "none" bucket) — defensive default
            that keeps the encoder width stable.

    Returns:
        ``0`` for the "none" bucket (``name is None`` or unknown), or
        ``registry.index(name) + 1`` when ``name`` is in ``registry[:7]``.
    """
    if name is None:
        return 0
    addressable = registry[:Z_SLOT_COUNT - 1]
    if name not in addressable:
        return 0
    return addressable.index(name) + 1


def _load_z_registry() -> list[str]:
    """Build the build-order registry from ``bots/<v>/data/build_orders/``.

    Returns:
        Alphabetically sorted list of trajectory names (filename stems,
        ``.json`` stripped), excluding ``_schema``. Empty list when the
        directory does not exist (pre-seed case).

    Logs a warning when the registry has more than ``Z_SLOT_COUNT - 1 = 7``
    entries — the 8th and later trajectories will decode to slot 0
    (the "none" bucket) because the one-hot block has no room for them.
    Bumping :data:`Z_SLOT_COUNT` is a future phase.
    """
    # Import lazily so test environments that don't have orchestrator on
    # the path (e.g. micro-isolated feature tests) can still import this
    # module — they just get an empty registry.
    from orchestrator.registry import resolve_data_path

    schema_path = resolve_data_path("build_orders/_schema.json")
    build_orders_dir = schema_path.parent
    if not build_orders_dir.is_dir():
        return []
    names: list[str] = []
    for entry in sorted(os.listdir(build_orders_dir)):
        if not entry.endswith(".json"):
            continue
        if entry == "_schema.json":
            continue
        names.append(entry[: -len(".json")])
    if len(names) > Z_SLOT_COUNT - 1:
        _logger.warning(
            "Build-order registry has %d entries but only %d z-slots are "
            "addressable; trajectories beyond #%d will decode to slot 0 "
            "(the 'none' bucket): %s",
            len(names),
            Z_SLOT_COUNT - 1,
            Z_SLOT_COUNT - 1,
            names[Z_SLOT_COUNT - 1 :],
        )
    return names


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
        Float32 vector of length FEATURE_DIM (55).
    """
    raw: list[float] = []
    # Scalar game-state features (first 40 entries of _FEATURE_SPEC).
    for field, divisor in _FEATURE_SPEC[:_DB_STATE_FEATURE_COUNT]:
        value = getattr(snapshot, field)
        if isinstance(value, bool):
            value = float(value)
        raw.append(float(value) / divisor)

    # Z-slot one-hot block (next 8 entries). Filled from
    # ``snapshot.current_build_order`` via the registry resolver — the
    # ``z_slot_<i>`` names in _FEATURE_SPEC are documentation/order
    # markers, not attributes on GameSnapshot.
    z_index = _resolve_z_index(snapshot.current_build_order, _load_z_registry())
    z_one_hot = [0.0] * Z_SLOT_COUNT
    z_one_hot[z_index] = 1.0
    raw.extend(z_one_hot)

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
    The 8 z-slot one-hot features and the 7 advisor features at the end of the
    vector are ignored — they don't map back to scalar GameSnapshot fields
    (the z block is categorical via ``current_build_order``; advisor is
    ephemeral context).
    """
    if vector.shape != (FEATURE_DIM,):
        msg = f"Expected shape ({FEATURE_DIM},), got {vector.shape}"
        raise ValueError(msg)

    values: dict[str, int | float | bool] = {}
    for i, (field, divisor) in enumerate(_FEATURE_SPEC[:_DB_STATE_FEATURE_COUNT]):
        raw = float(vector[i]) * divisor
        if field == "enemy_army_near_base":
            values[field] = raw > 0.5
        elif field == "game_time_seconds":
            values[field] = raw
        else:
            values[field] = int(round(raw))
    return GameSnapshot(**values)  # type: ignore[arg-type]
