"""Phase D Step D.6.5 smoke gate — env + encoder + RewardCalculator + DB
wired end-to-end against the v13 stack.

This test exists because D.5 bumped ``FEATURE_DIM`` 47→55, added the
``GameSnapshot.current_build_order`` field, and added a new SQLite TEXT
column; D.6 wired a new ``build_order_reward`` summand into
``RewardCalculator``. Each of those changes is unit-tested in isolation,
but the producer→consumer chain has not been exercised together. Per
``feedback_duplicate_shape_constants`` and
``feedback_buildstep_require_integration_test``, this is exactly the
bug class that wastes hours of M1 soak time — a 3-line tensor-shape or
missing-column bug invisible to every unit test but caught by a
60-second smoke.

The smoke runs the three producer-consumer pairs that D.5/D.6 created:

1. ``encode(snapshot)`` — shape ``(55,)`` and the z-slot one-hot lands
   on the expected slot for both ``current_build_order`` set and ``None``.
2. ``RewardCalculator.compute_step_reward(state_dict)`` — finite float,
   no exception, internal ``_active_build_order`` shows the build-order
   path was reached when the flag and label are both set.
3. ``store_transition(..., current_build_order=...)`` against a fresh
   ``tmp_path`` SQLite file — INSERT succeeds and the TEXT column
   round-trips both a real label and ``NULL``.

The test follows the active bot via ``bots.current`` so the assertions
track ``bots/current/current.txt`` (currently → ``bots/v13``) and
survive a future re-pointer without code changes.

Excludes any SC2 client startup and any torch model load — both are
multi-second imports; the producer-consumer chains under test live
entirely in encoder + reward + DB. The full suite should complete in
well under 60 seconds.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from bots.current.decision_engine import GameSnapshot
from bots.current.learning.database import TrainingDB
from bots.current.learning.features import (
    _DB_STATE_FEATURE_COUNT,
    FEATURE_DIM,
    Z_SLOT_COUNT,
    encode,
)
from bots.current.learning.rewards import RewardCalculator

from orchestrator.registry import resolve_data_path

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

RULES_PATH = resolve_data_path("reward_rules.json")


def _smoke_snapshot(**overrides: Any) -> GameSnapshot:
    """Minimal-but-realistic snapshot for the smoke chain.

    Field values are sentinel-realistic: they pass the encoder's type
    checks, sit inside the [0, divisor] clipping band, and exercise a
    non-trivial subset of the count fields the reward calculator reads
    via ``_COUNT_FIELD_TO_ACTION``. Game-physics accuracy is not the
    point — silent shape drift is.
    """
    base = GameSnapshot(
        supply_used=20,
        supply_cap=30,
        minerals=200,
        vespene=50,
        army_supply=5,
        worker_count=12,
        base_count=1,
        enemy_army_near_base=False,
        enemy_army_supply_visible=0,
        game_time_seconds=30.0,
        gateway_count=1,
        robo_count=0,
        forge_count=0,
        upgrade_count=0,
        enemy_structure_count=0,
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def _build_state_vector_for_db(snapshot: GameSnapshot) -> np.ndarray:
    """Construct the 40-int state vector that ``store_transition`` expects.

    ``store_transition`` coerces each element via ``int(v)`` and expects
    exactly ``_DB_STATE_FEATURE_COUNT`` entries (the 40 game-state DB
    columns — distinct from FEATURE_DIM=55 which includes the 8 z-slot
    one-hot block that lives on a separate TEXT column).
    """
    # Use a deterministic non-zero pattern that survives ``int(v)``
    # coercion. The DB doesn't care about the encoder's normalization;
    # it stores raw integer feature values.
    vec = np.arange(_DB_STATE_FEATURE_COUNT, dtype=np.float32)
    return vec


# ---------------------------------------------------------------------------
# Producer-consumer chain (a): encoder
# ---------------------------------------------------------------------------


class TestEncoderChain:
    """``encode(snapshot)`` end-to-end with and without a build-order label."""

    def test_with_build_order_shape_is_55(self) -> None:
        snap = _smoke_snapshot(current_build_order="4-gate-aggression")
        vec = encode(snap)
        assert vec.shape == (FEATURE_DIM,)
        assert vec.dtype == np.float32

    def test_with_build_order_z_slot_one_hot(self) -> None:
        """``4-gate-aggression`` is alphabetical-first in the registry, so
        ``_resolve_z_index`` returns 1; the 8-slot block should have slot 1
        hot and slots 0 and 2-7 cold.
        """
        snap = _smoke_snapshot(current_build_order="4-gate-aggression")
        vec = encode(snap)
        z_start = _DB_STATE_FEATURE_COUNT
        # Slot 0 (none) is cold because a build-order is set.
        assert vec[z_start + 0] == pytest.approx(0.0)
        # Slot 1 (4-gate-aggression) is hot.
        assert vec[z_start + 1] == pytest.approx(1.0)
        # Slots 2..7 are cold.
        for i in range(2, Z_SLOT_COUNT):
            assert vec[z_start + i] == pytest.approx(0.0), (
                f"slot {i} should be cold, was {vec[z_start + i]}"
            )

    def test_without_build_order_legacy_path_unaffected(self) -> None:
        """``current_build_order=None`` must keep slot 0 hot and slots 1-7
        cold — the pre-D.5 default behavior, preserved.
        """
        snap = _smoke_snapshot(current_build_order=None)
        vec = encode(snap)
        assert vec.shape == (FEATURE_DIM,)
        z_start = _DB_STATE_FEATURE_COUNT
        assert vec[z_start + 0] == pytest.approx(1.0)
        for i in range(1, Z_SLOT_COUNT):
            assert vec[z_start + i] == pytest.approx(0.0), (
                f"slot {i} should be cold, was {vec[z_start + i]}"
            )


# ---------------------------------------------------------------------------
# Producer-consumer chain (b): RewardCalculator
# ---------------------------------------------------------------------------


class TestRewardCalculatorChain:
    """``RewardCalculator.compute_step_reward`` end-to-end with the
    build-order summand active.
    """

    def test_compute_step_reward_returns_finite_with_build_order(self) -> None:
        """Flag ON + label set → no exception, finite float, and the
        build-order path was actually reached (``_active_build_order`` is
        populated after the call).
        """
        calc = RewardCalculator(
            RULES_PATH,
            hyperparams={
                "use_build_order_reward": True,
                "build_order_reward_alpha": 1.0,
            },
        )
        snap = _smoke_snapshot(current_build_order="4-gate-aggression")
        state = asdict(snap)
        reward = calc.compute_step_reward(state)
        assert isinstance(reward, float)
        assert np.isfinite(reward)
        # The build-order summand path was actually reached for this
        # snapshot — ``_active_build_order`` is None on a fresh calculator
        # and only gets populated inside ``_build_order_summand`` when
        # both the flag and the label are set. If shape/wiring drift had
        # short-circuited the path silently, this would still be None.
        assert calc._active_build_order is not None, (
            "build-order path was not exercised — "
            "_active_build_order is still None after compute_step_reward"
        )
        assert calc._active_build_order.name == "4-gate-aggression"

    def test_all_new_count_fields_register_events(self) -> None:
        """Producer→consumer assertion for the 5 GameSnapshot count fields
        added by the snapshot-counts follow-up: a 0→positive delta on each
        new field must flow through ``_COUNT_FIELD_TO_ACTION`` and land an
        executed-action event in ``_executed_actions``.

        Each field is exercised in ISOLATION — a fresh ``RewardCalculator``,
        a zero-count baseline call (seeds ``_prev_counts``), then a delta
        call bumping only that one field 0→1. This is stronger than bumping
        all five at once: a target *swap* between two fields (e.g.
        ``pylon_count`` → ``("build", "assimilator")``) would survive an
        all-at-once set-membership check but fails here, because the
        isolated field's expected event would be absent and the swapped-in
        event would leak. ``_executed_actions`` stores
        ``(action, target, game_time)`` tuples, so membership is asserted on
        the ``(action, target)`` pair.
        """
        new_field_events = {
            "pylon_count": ("build", "pylon"),
            "assimilator_count": ("build", "assimilator"),
            "cyberneticscore_count": ("build", "cyberneticscore"),
            "roboticsbay_count": ("build", "roboticsbay"),
            "warp_gate_research_count": ("research", "warp_gate_research"),
        }
        all_new_events = set(new_field_events.values())
        for field, expected in new_field_events.items():
            calc = RewardCalculator(
                RULES_PATH,
                hyperparams={
                    "use_build_order_reward": True,
                    "build_order_reward_alpha": 1.0,
                },
            )
            # Baseline: every new count defaults to 0 — seeds ``_prev_counts``.
            baseline = _smoke_snapshot(current_build_order="4-gate-aggression")
            calc.compute_step_reward(asdict(baseline))

            # Delta: only ``field`` goes 0→1, so it is the sole positive delta
            # among the new count fields.
            delta = _smoke_snapshot(
                current_build_order="4-gate-aggression",
                game_time_seconds=120.0,
                **{field: 1},
            )
            calc.compute_step_reward(asdict(delta))

            pairs = {(action, target) for action, target, _ in calc._executed_actions}
            assert expected in pairs, (
                f"{field}: {expected!r} not registered in _executed_actions; "
                f"got pairs={sorted(pairs)}"
            )
            # Swap guard: bumping only ``field`` must not register any OTHER
            # new-field event.
            leaked = (all_new_events - {expected}) & pairs
            assert not leaked, (
                f"{field}: bumping only {field} leaked other new-field events {sorted(leaked)}"
            )

    def test_compute_step_reward_returns_finite_without_build_order(self) -> None:
        """Flag ON + ``current_build_order=None`` → still finite float,
        no exception, and the build-order path correctly skips (no
        trajectory loaded).
        """
        calc = RewardCalculator(
            RULES_PATH,
            hyperparams={
                "use_build_order_reward": True,
                "build_order_reward_alpha": 1.0,
            },
        )
        snap = _smoke_snapshot(current_build_order=None)
        state = asdict(snap)
        reward = calc.compute_step_reward(state)
        assert isinstance(reward, float)
        assert np.isfinite(reward)
        # No label → summand short-circuited to None → no trajectory
        # loaded. This is the legacy path preserved by D.6.
        assert calc._active_build_order is None


# ---------------------------------------------------------------------------
# Producer-consumer chain (c): SQLite store_transition round-trip
# ---------------------------------------------------------------------------


class TestDatabaseChain:
    """``store_transition(..., current_build_order=...)`` INSERT + SELECT
    round-trip against a fresh ``tmp_path`` SQLite file.

    Catches the silent column-mismatch class: a new TEXT column added by
    D.5 that isn't reachable from the write path, or a SELECT site
    expecting NULL but getting an empty string, etc.
    """

    def test_store_with_build_order_round_trips(self, tmp_path: Path) -> None:
        db = TrainingDB(tmp_path / "smoke_with_bo.db")
        try:
            db.store_game(
                "g_with_bo", "Simple64", 1, "win", 60.0, 0.0, "v13"
            )
            snap = _smoke_snapshot(current_build_order="4-gate-aggression")
            state_vec = _build_state_vector_for_db(snap)
            db.store_transition(
                "g_with_bo",
                0,
                30.0,
                state_vec,
                action=0,
                reward=0.1,
                current_build_order="4-gate-aggression",
            )
            assert db.get_transition_count() == 1

            # SELECT through a raw connection (avoids holding db's lock).
            with sqlite3.connect(str(tmp_path / "smoke_with_bo.db")) as conn:
                row = conn.execute(
                    "SELECT current_build_order FROM transitions "
                    "WHERE game_id = ?",
                    ("g_with_bo",),
                ).fetchone()
            assert row is not None
            assert row[0] == "4-gate-aggression"
        finally:
            db.close()

    def test_store_without_build_order_round_trips_as_null(
        self, tmp_path: Path
    ) -> None:
        db = TrainingDB(tmp_path / "smoke_no_bo.db")
        try:
            db.store_game(
                "g_no_bo", "Simple64", 1, "win", 60.0, 0.0, "v13"
            )
            snap = _smoke_snapshot(current_build_order=None)
            state_vec = _build_state_vector_for_db(snap)
            db.store_transition(
                "g_no_bo",
                0,
                30.0,
                state_vec,
                action=0,
                reward=0.1,
                current_build_order=None,
            )
            assert db.get_transition_count() == 1

            with sqlite3.connect(str(tmp_path / "smoke_no_bo.db")) as conn:
                row = conn.execute(
                    "SELECT current_build_order FROM transitions "
                    "WHERE game_id = ?",
                    ("g_no_bo",),
                ).fetchone()
            assert row is not None
            # NULL TEXT column → Python None.
            assert row[0] is None
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Cross-chain integration: one snapshot drives all three chains
# ---------------------------------------------------------------------------


class TestUnifiedFlow:
    """One ``GameSnapshot`` → encoder + RewardCalculator + DB. Catches the
    case where each chain works in isolation but the snapshot-level
    integration drops or corrupts the new field somewhere.
    """

    def test_unified_flow_with_build_order(self, tmp_path: Path) -> None:
        snap = _smoke_snapshot(current_build_order="4-gate-aggression")

        # Chain (a): encoder
        vec = encode(snap)
        assert vec.shape == (FEATURE_DIM,)

        # Chain (b): reward calculator
        calc = RewardCalculator(
            RULES_PATH,
            hyperparams={
                "use_build_order_reward": True,
                "build_order_reward_alpha": 1.0,
            },
        )
        reward = calc.compute_step_reward(asdict(snap))
        assert np.isfinite(reward)

        # Chain (c): DB round-trip
        db = TrainingDB(tmp_path / "smoke_unified.db")
        try:
            db.store_game(
                "g_unified", "Simple64", 1, "win", 60.0, 0.0, "v13"
            )
            db.store_transition(
                "g_unified",
                0,
                snap.game_time_seconds,
                _build_state_vector_for_db(snap),
                action=0,
                reward=reward,
                current_build_order=snap.current_build_order,
            )
            with sqlite3.connect(str(tmp_path / "smoke_unified.db")) as conn:
                row = conn.execute(
                    "SELECT current_build_order FROM transitions "
                    "WHERE game_id = ?",
                    ("g_unified",),
                ).fetchone()
            assert row is not None
            assert row[0] == snap.current_build_order
        finally:
            db.close()

    def test_unified_flow_legacy_path(self, tmp_path: Path) -> None:
        """Same as above with ``current_build_order=None``. Confirms the
        legacy (pre-D.5) snapshot shape still flows end-to-end without
        the new field corrupting any of the three chains.
        """
        snap = _smoke_snapshot(current_build_order=None)

        vec = encode(snap)
        assert vec.shape == (FEATURE_DIM,)
        # Z-slot 0 (none bucket) is hot.
        assert vec[_DB_STATE_FEATURE_COUNT] == pytest.approx(1.0)

        calc = RewardCalculator(
            RULES_PATH,
            hyperparams={
                "use_build_order_reward": True,
                "build_order_reward_alpha": 1.0,
            },
        )
        reward = calc.compute_step_reward(asdict(snap))
        assert np.isfinite(reward)
        assert calc._active_build_order is None

        db = TrainingDB(tmp_path / "smoke_unified_legacy.db")
        try:
            db.store_game(
                "g_unified_legacy", "Simple64", 1, "win", 60.0, 0.0, "v13"
            )
            db.store_transition(
                "g_unified_legacy",
                0,
                snap.game_time_seconds,
                _build_state_vector_for_db(snap),
                action=0,
                reward=reward,
                current_build_order=None,
            )
            with sqlite3.connect(
                str(tmp_path / "smoke_unified_legacy.db")
            ) as conn:
                row = conn.execute(
                    "SELECT current_build_order FROM transitions "
                    "WHERE game_id = ?",
                    ("g_unified_legacy",),
                ).fetchone()
            assert row is not None
            assert row[0] is None
        finally:
            db.close()
