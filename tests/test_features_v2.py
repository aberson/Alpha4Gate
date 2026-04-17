"""Tests for Phase B unit-type histogram expansion in features.py."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from bots.v0.decision_engine import GameSnapshot
from bots.v0.learning.database import _LATER_ADDED_COLS, _STATE_COLS, TrainingDB
from bots.v0.learning.features import (
    _FEATURE_SPEC,
    BASE_GAME_FEATURE_DIM,
    FEATURE_DIM,
    decode,
    encode,
)

# ---------------------------------------------------------------------------
# Constant sanity checks
# ---------------------------------------------------------------------------


def test_feature_dim_is_47() -> None:
    assert FEATURE_DIM == 47


def test_base_game_feature_dim_is_40() -> None:
    assert BASE_GAME_FEATURE_DIM == 40


def test_feature_spec_length_matches_base_dim() -> None:
    assert len(_FEATURE_SPEC) == BASE_GAME_FEATURE_DIM


def test_state_cols_length_matches_base_dim() -> None:
    assert len(_STATE_COLS) == BASE_GAME_FEATURE_DIM


# ---------------------------------------------------------------------------
# Encoding tests — default snapshot (all zeros)
# ---------------------------------------------------------------------------


def test_default_snapshot_new_slots_are_zero() -> None:
    """Default snapshot should produce all-zero for the 23 new unit + enemy slots."""
    snap = GameSnapshot()
    vec = encode(snap)
    assert vec.shape == (FEATURE_DIM,)
    # The 23 new slots are indices 17..39 (0-based) in the base game features.
    for i in range(17, 40):
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


# ---------------------------------------------------------------------------
# Enemy threat-class encoding tests (Phase B Step 2)
# ---------------------------------------------------------------------------

_ENEMY_FIELDS = [
    ("enemy_light_count", 12, 20.0),
    ("enemy_armored_count", 8, 20.0),
    ("enemy_siege_count", 3, 20.0),
    ("enemy_support_count", 5, 20.0),
    ("enemy_air_harass_count", 6, 20.0),
    ("enemy_heavy_count", 2, 20.0),
    ("enemy_capital_count", 1, 20.0),
    ("enemy_cloak_count", 4, 20.0),
]


def test_enemy_threat_class_encode_correctly() -> None:
    """Each enemy threat-class field should encode as value / divisor."""
    kwargs = {field: value for field, value, _ in _ENEMY_FIELDS}
    snap = GameSnapshot(**kwargs)  # type: ignore[arg-type]
    vec = encode(snap)

    for idx, (field, value, divisor) in enumerate(_ENEMY_FIELDS):
        slot = 32 + idx
        expected = min(float(value) / divisor, 1.0)
        assert vec[slot] == pytest.approx(expected, abs=1e-6), (
            f"{field}: expected {expected}, got {vec[slot]}"
        )


def test_enemy_threat_class_clips_to_one() -> None:
    """Values exceeding the divisor should clip to 1.0."""
    snap = GameSnapshot(enemy_light_count=50)  # 50/20 = 2.5 → clip to 1.0
    vec = encode(snap)
    assert vec[32] == pytest.approx(1.0)


def test_decode_round_trip_enemy_fields() -> None:
    """decode(encode(snap)) preserves nonzero enemy threat-class fields."""
    from bots.v0.learning.features import decode

    kwargs = {field: value for field, value, _ in _ENEMY_FIELDS}
    snap = GameSnapshot(**kwargs)  # type: ignore[arg-type]
    restored = decode(encode(snap))

    for field, value, _ in _ENEMY_FIELDS:
        assert getattr(restored, field) == value, (
            f"{field}: expected {value}, got {getattr(restored, field)}"
        )


# ---------------------------------------------------------------------------
# THREAT_CLASS_MAP consistency check
# ---------------------------------------------------------------------------


def test_threat_class_map_values_are_valid_snapshot_fields() -> None:
    """Every value in THREAT_CLASS_MAP must be a valid GameSnapshot attribute."""
    from bots.v0.learning.threat_classes import THREAT_CLASS_MAP

    for unit_id, field_name in THREAT_CLASS_MAP.items():
        assert hasattr(GameSnapshot, field_name), (
            f"THREAT_CLASS_MAP[{unit_id}] = {field_name!r} "
            f"is not a valid GameSnapshot field"
        )


# ---------------------------------------------------------------------------
# Diagnostic state fixtures — realistic mid-game compositions
# ---------------------------------------------------------------------------

_FOUR_GATE_RUSH = GameSnapshot(
    supply_used=58,
    supply_cap=62,
    minerals=150,
    vespene=50,
    army_supply=38,
    worker_count=20,
    base_count=1,
    enemy_army_near_base=False,
    enemy_army_supply_visible=0,
    game_time_seconds=300.0,  # ~5 min
    gateway_count=4,
    robo_count=0,
    forge_count=0,
    upgrade_count=0,
    enemy_structure_count=0,
    cannon_count=0,
    battery_count=1,
    zealot_count=8,
    stalker_count=4,
    sentry_count=1,
)

_ROBO_COLOSSUS_TIMING = GameSnapshot(
    supply_used=78,
    supply_cap=94,
    minerals=300,
    vespene=200,
    army_supply=50,
    worker_count=28,
    base_count=2,
    enemy_army_near_base=False,
    enemy_army_supply_visible=15,
    game_time_seconds=600.0,  # ~10 min
    gateway_count=3,
    robo_count=2,
    forge_count=1,
    upgrade_count=2,
    enemy_structure_count=5,
    cannon_count=2,
    battery_count=2,
    stalker_count=4,
    sentry_count=2,
    immortal_count=1,
    colossus_count=2,
    observer_count=2,
)

_LATE_GAME_DEATHBALL = GameSnapshot(
    supply_used=190,
    supply_cap=200,
    minerals=800,
    vespene=600,
    army_supply=120,
    worker_count=60,
    base_count=4,
    enemy_army_near_base=True,
    enemy_army_supply_visible=80,
    game_time_seconds=900.0,  # ~15 min
    gateway_count=8,
    robo_count=2,
    forge_count=2,
    upgrade_count=6,
    enemy_structure_count=15,
    cannon_count=4,
    battery_count=3,
    zealot_count=4,
    stalker_count=8,
    high_templar_count=3,
    archon_count=2,
    colossus_count=3,
    carrier_count=1,
    observer_count=2,
    enemy_light_count=10,
    enemy_armored_count=5,
    enemy_siege_count=3,
)

_DIAGNOSTIC_SNAPSHOTS = [
    ("four_gate_rush", _FOUR_GATE_RUSH),
    ("robo_colossus_timing", _ROBO_COLOSSUS_TIMING),
    ("late_game_deathball", _LATE_GAME_DEATHBALL),
]


class TestDiagnosticStateFixtures:
    """Verify realistic mid-game compositions encode to valid feature vectors."""

    @pytest.mark.parametrize("name,snap", _DIAGNOSTIC_SNAPSHOTS)
    def test_encode_produces_correct_dim(
        self, name: str, snap: GameSnapshot
    ) -> None:
        vec = encode(snap)
        assert vec.shape == (FEATURE_DIM,), f"{name}: wrong shape {vec.shape}"

    @pytest.mark.parametrize("name,snap", _DIAGNOSTIC_SNAPSHOTS)
    def test_all_values_in_zero_one(
        self, name: str, snap: GameSnapshot
    ) -> None:
        vec = encode(snap)
        assert float(np.min(vec)) >= 0.0, f"{name}: has negative values"
        assert float(np.max(vec)) <= 1.0, f"{name}: has values > 1"

    @pytest.mark.parametrize("name,snap", _DIAGNOSTIC_SNAPSHOTS)
    def test_nonzero_unit_counts_produce_nonzero_encoded(
        self, name: str, snap: GameSnapshot
    ) -> None:
        """Non-zero unit counts should produce non-zero encoded values."""
        vec = encode(snap)
        for idx, (field, _divisor) in enumerate(_FEATURE_SPEC):
            raw_val = getattr(snap, field)
            if isinstance(raw_val, bool):
                raw_val = int(raw_val)
            if raw_val > 0:
                assert vec[idx] > 0.0, (
                    f"{name}: {field}={raw_val} but vec[{idx}]={vec[idx]}"
                )

    @pytest.mark.parametrize("name,snap", _DIAGNOSTIC_SNAPSHOTS)
    def test_decode_approximately_recovers_snapshot(
        self, name: str, snap: GameSnapshot
    ) -> None:
        """decode(encode(snap)) should approximately recover the original."""
        restored = decode(encode(snap))
        for field, divisor in _FEATURE_SPEC:
            original = getattr(snap, field)
            recovered = getattr(restored, field)
            if field == "enemy_army_near_base":
                assert original == recovered, (
                    f"{name}: {field} mismatch: {original} vs {recovered}"
                )
            elif field == "game_time_seconds":
                # float field: allow rounding within one unit of divisor
                assert abs(original - recovered) < divisor * 0.01, (
                    f"{name}: {field} drift: {original} vs {recovered}"
                )
            else:
                # integer fields: encode clips at 1.0, so capped values
                # won't round-trip exactly; just check unclipped ones
                if float(original) / divisor <= 1.0:
                    assert recovered == original, (
                        f"{name}: {field} mismatch: {original} vs {recovered}"
                    )

    def test_four_gate_rush_army_heavy(self) -> None:
        """4-gate rush should have heavy army relative to supply."""
        vec = encode(_FOUR_GATE_RUSH)
        # army_supply / 200 should be reasonably large (~0.19)
        army_idx = 4  # army_supply index
        assert vec[army_idx] > 0.1

    def test_late_game_deathball_enemy_scouted(self) -> None:
        """Late-game should have nonzero enemy threat slots."""
        vec = encode(_LATE_GAME_DEATHBALL)
        # enemy_light_count is at index 32
        assert vec[32] > 0.0  # enemy_light_count = 10
        assert vec[33] > 0.0  # enemy_armored_count = 5
        assert vec[34] > 0.0  # enemy_siege_count = 3


# ---------------------------------------------------------------------------
# DB pipeline integration smoke tests — 40-column schema
# ---------------------------------------------------------------------------


class TestDBPipelineSmokeTest:
    """Smoke-gate: full 40-column round-trip through TrainingDB."""

    def test_store_and_read_40col_transition(self, tmp_path: Path) -> None:
        """Store a 40-element state vector, read it back via sample_batch."""
        db = TrainingDB(tmp_path / "smoke.db")
        try:
            db.store_game("g1", "Simple64", 3, "win", 300.0, 5.0, "v0")

            # Build a realistic 40-element state from the 4-gate rush fixture
            state = np.array(
                [
                    58, 62, 150, 50, 38, 20, 1, 0, 0,      # original 9
                    300.0, 4, 0, 0, 0, 0, 0, 1,             # structure cols
                    8, 4, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  # 15 unit counts
                    0, 0, 0, 0, 0, 0, 0, 0,                  # 8 enemy threat
                ],
                dtype=np.float32,
            )
            assert len(state) == BASE_GAME_FEATURE_DIM

            db.store_transition("g1", 0, 60.0, state, action=2, reward=0.5)
            states, actions, rewards = db.sample_batch(10)

            assert states.shape == (1, BASE_GAME_FEATURE_DIM)
            assert actions.shape == (1,)
            assert rewards.shape == (1,)
            assert int(actions[0]) == 2
            assert float(rewards[0]) == pytest.approx(0.5)
            # Verify state values round-trip
            np.testing.assert_array_almost_equal(states[0], state, decimal=0)
        finally:
            db.close()

    def test_legacy_migration_then_full_40col_store(
        self, tmp_path: Path
    ) -> None:
        """Simulate old-format (9-column) DB, migrate, then store 40-col row."""
        import sqlite3

        legacy_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(legacy_path))
        conn.executescript(
            """
            CREATE TABLE games (
                game_id TEXT PRIMARY KEY,
                map_name TEXT NOT NULL,
                difficulty INTEGER NOT NULL,
                result TEXT NOT NULL,
                duration_secs REAL NOT NULL,
                total_reward REAL NOT NULL,
                model_version TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE transitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                game_time REAL NOT NULL,
                supply_used INTEGER NOT NULL,
                supply_cap INTEGER NOT NULL,
                minerals INTEGER NOT NULL,
                vespene INTEGER NOT NULL,
                army_supply INTEGER NOT NULL,
                worker_count INTEGER NOT NULL,
                base_count INTEGER NOT NULL,
                enemy_near INTEGER NOT NULL,
                enemy_supply INTEGER NOT NULL,
                action INTEGER NOT NULL,
                reward REAL NOT NULL,
                next_supply_used INTEGER,
                next_supply_cap INTEGER,
                next_minerals INTEGER,
                next_vespene INTEGER,
                next_army_supply INTEGER,
                next_worker_count INTEGER,
                next_base_count INTEGER,
                next_enemy_near INTEGER,
                next_enemy_supply INTEGER,
                done INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        conn.commit()

        # Verify pre-migration: later-added columns are absent
        before = {
            r[1]
            for r in conn.execute("PRAGMA table_info(transitions)").fetchall()
        }
        conn.close()
        for col_name, _ in _LATER_ADDED_COLS:
            assert col_name not in before, f"legacy should not have {col_name}"

        # Open with TrainingDB — triggers migration
        db = TrainingDB(legacy_path)
        try:
            # Verify migration added the columns
            raw_conn = sqlite3.connect(str(legacy_path))
            after = {
                r[1]
                for r in raw_conn.execute(
                    "PRAGMA table_info(transitions)"
                ).fetchall()
            }
            raw_conn.close()
            for col_name, _ in _LATER_ADDED_COLS:
                assert col_name in after, f"migration should add {col_name}"

            # Store a full 40-element transition in the migrated DB
            db.store_game("g1", "Simple64", 3, "win", 300.0, 5.0, "v0")
            state = np.zeros(BASE_GAME_FEATURE_DIM, dtype=np.float32)
            state[0] = 58   # supply_used
            state[1] = 62   # supply_cap
            state[17] = 8   # zealot_count
            state[18] = 4   # stalker_count
            db.store_transition("g1", 0, 60.0, state, action=1, reward=0.3)

            # Read back and verify shape
            states, actions, rewards = db.sample_batch(10)
            assert states.shape == (1, BASE_GAME_FEATURE_DIM)
            assert int(actions[0]) == 1
            assert float(rewards[0]) == pytest.approx(0.3)
            # Verify the unit counts survived
            assert states[0, 17] == pytest.approx(8.0)
            assert states[0, 18] == pytest.approx(4.0)
        finally:
            db.close()
