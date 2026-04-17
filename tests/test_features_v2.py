"""Tests for Phase B unit-type histogram expansion in features.py."""

from __future__ import annotations

import pytest
from bots.v0.decision_engine import GameSnapshot
from bots.v0.learning.database import _STATE_COLS
from bots.v0.learning.features import (
    _FEATURE_SPEC,
    BASE_GAME_FEATURE_DIM,
    FEATURE_DIM,
    encode,
)

# ---------------------------------------------------------------------------
# Constant sanity checks
# ---------------------------------------------------------------------------


def test_feature_dim_is_39() -> None:
    assert FEATURE_DIM == 39


def test_base_game_feature_dim_is_32() -> None:
    assert BASE_GAME_FEATURE_DIM == 32


def test_feature_spec_length_matches_base_dim() -> None:
    assert len(_FEATURE_SPEC) == BASE_GAME_FEATURE_DIM


def test_state_cols_length_matches_base_dim() -> None:
    assert len(_STATE_COLS) == BASE_GAME_FEATURE_DIM


# ---------------------------------------------------------------------------
# Encoding tests — default snapshot (all zeros)
# ---------------------------------------------------------------------------


def test_default_snapshot_new_slots_are_zero() -> None:
    """Default GameSnapshot should produce all-zero values for the 15 new unit-type slots."""
    snap = GameSnapshot()
    vec = encode(snap)
    assert vec.shape == (FEATURE_DIM,)
    # The 15 new slots are indices 17..31 (0-based) in the base game features.
    for i in range(17, 32):
        assert vec[i] == pytest.approx(0.0), f"slot {i} should be 0.0"


# ---------------------------------------------------------------------------
# Encoding tests — non-zero unit counts
# ---------------------------------------------------------------------------

# Map of (field, value, divisor) for each new unit-type field.
_UNIT_FIELDS = [
    ("zealot_count", 10, 20.0),
    ("stalker_count", 5, 20.0),
    ("sentry_count", 3, 20.0),
    ("immortal_count", 4, 20.0),
    ("colossus_count", 2, 10.0),
    ("archon_count", 6, 20.0),
    ("high_templar_count", 3, 20.0),
    ("dark_templar_count", 2, 20.0),
    ("phoenix_count", 8, 20.0),
    ("void_ray_count", 4, 20.0),
    ("carrier_count", 3, 10.0),
    ("tempest_count", 2, 10.0),
    ("disruptor_count", 1, 10.0),
    ("warp_prism_count", 2, 5.0),
    ("observer_count", 3, 5.0),
]


def test_nonzero_unit_counts_encode_correctly() -> None:
    """Each new unit-type field should encode as value / divisor, clipped to [0,1]."""
    kwargs = {field: value for field, value, _ in _UNIT_FIELDS}
    snap = GameSnapshot(**kwargs)  # type: ignore[arg-type]
    vec = encode(snap)

    for idx, (field, value, divisor) in enumerate(_UNIT_FIELDS):
        slot = 17 + idx  # first 17 are the original base features
        expected = min(float(value) / divisor, 1.0)
        assert vec[slot] == pytest.approx(expected, abs=1e-6), (
            f"{field}: expected {expected}, got {vec[slot]}"
        )


def test_unit_counts_clip_to_one() -> None:
    """Values exceeding the divisor should clip to 1.0."""
    snap = GameSnapshot(zealot_count=40)  # 40/20 = 2.0 → clip to 1.0
    vec = encode(snap)
    assert vec[17] == pytest.approx(1.0)


def test_decode_round_trip_unit_type_fields() -> None:
    """decode(encode(snap)) preserves nonzero unit-type fields."""
    from bots.v0.learning.features import decode

    kwargs = {field: value for field, value, _ in _UNIT_FIELDS}
    snap = GameSnapshot(**kwargs)  # type: ignore[arg-type]
    restored = decode(encode(snap))

    for field, value, _ in _UNIT_FIELDS:
        assert getattr(restored, field) == value, (
            f"{field}: expected {value}, got {getattr(restored, field)}"
        )
