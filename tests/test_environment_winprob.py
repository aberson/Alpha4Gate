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
