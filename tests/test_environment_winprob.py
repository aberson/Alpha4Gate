"""Tests for the ``win_prob`` write-path: env alias + DB persistence.

The env side of Phase N Step 2 binds ``score`` from ``winprob_heuristic`` as
``_winprob_score`` and calls it once per decision step.  The DB side accepts
the result via ``store_transition(win_prob=...)``.  These tests pin both
edges of that wiring without standing up an SC2 game thread.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pytest
from bots.v0.learning.database import TrainingDB
from bots.v0.learning.features import (
    BASE_GAME_FEATURE_DIM as FEATURE_DIM,  # DB uses game-state dims only
)


def test_environment_winprob_alias_resolves_to_heuristic_score() -> None:
    """``SC2Env`` calls ``winprob_heuristic.score`` (no wrapper, no drift).

    Pins ``environment._winprob_score is winprob_heuristic.score`` so a
    future refactor that swaps the alias to a wrapper / mock / hand-rolled
    re-implementation trips this test instead of silently changing the
    semantics of the column written every decision step.
    """
    from bots.v0.learning import environment
    from bots.v0.learning.winprob_heuristic import score

    assert environment._winprob_score is score


@pytest.fixture()
def db(tmp_path: Path) -> TrainingDB:
    return TrainingDB(tmp_path / "test.db")


def test_store_transition_persists_win_prob_value(db: TrainingDB, tmp_path: Path) -> None:
    db.store_game("g1", "Simple64", 1, "win", 300.0, 5.5, "v0")
    state = np.zeros(FEATURE_DIM, dtype=np.float32)
    db.store_transition(
        "g1", 0, 60.0, state, action=2, reward=0.1, win_prob=0.42
    )
    db.close()

    conn = sqlite3.connect(str(tmp_path / "test.db"))
    try:
        row = conn.execute(
            "SELECT win_prob FROM transitions WHERE game_id = 'g1'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == pytest.approx(0.42)


def test_store_transition_default_win_prob_is_null(
    db: TrainingDB, tmp_path: Path
) -> None:
    db.store_game("g1", "Simple64", 1, "win", 300.0, 5.5, "v0")
    state = np.zeros(FEATURE_DIM, dtype=np.float32)
    # Note: win_prob NOT passed — uses default None.
    db.store_transition("g1", 0, 60.0, state, action=2, reward=0.1)
    db.close()

    conn = sqlite3.connect(str(tmp_path / "test.db"))
    try:
        row = conn.execute(
            "SELECT win_prob FROM transitions WHERE game_id = 'g1'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] is None


def test_bot_record_transition_passes_win_prob_to_store(
    db: TrainingDB, tmp_path: Path
) -> None:
    """``Alpha4GateBot._record_transition`` must thread ``winprob`` to the DB.

    Phase N Step 2 wired the env-mediated path (``SC2Env.step`` →
    ``info["win_prob"]`` → ``store_transition``).  The solo entry
    (``python -m bots.v0 --role solo``) records via
    ``Alpha4GateBot._record_transition`` instead, which calls
    ``store_transition`` directly.  This test pins the bot-side path so
    a missing ``win_prob=`` kwarg surfaces here, not as silent NULLs in
    a real soak (the gap that motivated this test, found in N.6 smoke).
    """
    from bots.v0.bot import Alpha4GateBot
    from bots.v0.decision_engine import GameSnapshot, StrategicState
    from bots.v0.learning.rewards import RewardCalculator

    bot = Alpha4GateBot.__new__(Alpha4GateBot)
    bot._training_db = db
    bot._game_id = "solo-test"
    bot._transition_step = 0
    bot._reward_calc = RewardCalculator()
    bot._neural_engine = None
    bot._prev_obs = np.zeros(FEATURE_DIM, dtype=np.float32)
    bot._prev_action = 0
    bot._prev_snapshot = GameSnapshot()

    db.store_game("solo-test", "Simple64", 1, "win", 300.0, 0.0, "v0")
    bot._record_transition(GameSnapshot(supply_used=20), StrategicState.OPENING, 0.42)

    row = db._conn.execute(
        "SELECT win_prob FROM transitions WHERE game_id = 'solo-test'"
    ).fetchone()
    assert row is not None
    assert row[0] == pytest.approx(0.42)
