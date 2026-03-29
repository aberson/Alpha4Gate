"""Tests for the SQLite training database."""

from pathlib import Path

import numpy as np
import pytest

from alpha4gate.learning.database import TrainingDB
from alpha4gate.learning.features import FEATURE_DIM


@pytest.fixture()
def db(tmp_path: Path) -> TrainingDB:
    return TrainingDB(tmp_path / "test.db")


class TestStoreAndRetrieve:
    def test_store_game(self, db: TrainingDB) -> None:
        db.store_game("g1", "Simple64", 1, "win", 300.0, 5.5, "v0")
        assert db.get_game_count() == 1

    def test_store_multiple_games(self, db: TrainingDB) -> None:
        for i in range(5):
            db.store_game(f"g{i}", "Simple64", 1, "win" if i % 2 == 0 else "loss", 300.0, 1.0, "v0")
        assert db.get_game_count() == 5

    def test_store_transition(self, db: TrainingDB) -> None:
        db.store_game("g1", "Simple64", 1, "win", 300.0, 5.5, "v0")
        state = np.array([50, 100, 800, 400, 30, 22, 2, 1, 15, 300, 3, 1, 1, 2], dtype=np.float32)
        next_s = np.array([55, 100, 700, 350, 35, 23, 2, 0, 10, 322, 3, 1, 1, 2], dtype=np.float32)
        db.store_transition("g1", 0, 60.0, state, action=2, reward=0.1, next_state=next_s)
        assert db.get_transition_count() == 1

    def test_store_terminal_transition(self, db: TrainingDB) -> None:
        db.store_game("g1", "Simple64", 1, "win", 300.0, 5.5, "v0")
        state = np.zeros(FEATURE_DIM, dtype=np.float32)
        db.store_transition("g1", 0, 300.0, state, action=0, reward=10.0, done=True)
        assert db.get_transition_count() == 1


class TestSampling:
    def test_sample_batch_empty(self, db: TrainingDB) -> None:
        states, actions, rewards = db.sample_batch(10)
        assert states.shape == (0, FEATURE_DIM)
        assert actions.shape == (0,)
        assert rewards.shape == (0,)

    def test_sample_batch_returns_correct_shapes(self, db: TrainingDB) -> None:
        db.store_game("g1", "Simple64", 1, "win", 300.0, 5.5, "v0")
        for i in range(20):
            state = np.full(FEATURE_DIM, float(i), dtype=np.float32)
            db.store_transition("g1", i, float(i), state, action=i % 5, reward=0.1)
        states, actions, rewards = db.sample_batch(10)
        assert states.shape == (10, FEATURE_DIM)
        assert actions.shape == (10,)
        assert rewards.shape == (10,)

    def test_sample_batch_fewer_than_requested(self, db: TrainingDB) -> None:
        db.store_game("g1", "Simple64", 1, "win", 300.0, 5.5, "v0")
        state = np.zeros(FEATURE_DIM, dtype=np.float32)
        db.store_transition("g1", 0, 0.0, state, action=0, reward=0.1)
        states, actions, rewards = db.sample_batch(100)
        assert states.shape[0] == 1


class TestWinRate:
    def test_win_rate_empty(self, db: TrainingDB) -> None:
        assert db.get_recent_win_rate(10) == 0.0

    def test_win_rate_all_wins(self, db: TrainingDB) -> None:
        for i in range(5):
            db.store_game(f"g{i}", "Simple64", 1, "win", 300.0, 5.0, "v0")
        assert db.get_recent_win_rate(5) == 1.0

    def test_win_rate_mixed(self, db: TrainingDB) -> None:
        db.store_game("g0", "Simple64", 1, "win", 300.0, 5.0, "v0")
        db.store_game("g1", "Simple64", 1, "loss", 300.0, -5.0, "v0")
        assert db.get_recent_win_rate(10) == 0.5

    def test_win_rate_respects_limit(self, db: TrainingDB) -> None:
        # 3 old losses, then 2 recent wins
        for i in range(3):
            db.store_game(f"old{i}", "Simple64", 1, "loss", 300.0, -5.0, "v0")
        for i in range(2):
            db.store_game(f"new{i}", "Simple64", 1, "win", 300.0, 5.0, "v0")
        # Only look at 2 most recent → should be 100%
        assert db.get_recent_win_rate(2) == 1.0


class TestDbSize:
    def test_db_size_positive(self, db: TrainingDB) -> None:
        db.store_game("g1", "Simple64", 1, "win", 300.0, 5.5, "v0")
        assert db.get_db_size_bytes() > 0
