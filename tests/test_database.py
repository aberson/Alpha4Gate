"""Tests for the SQLite training database."""

import json
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
        vals = [50, 100, 800, 400, 30, 22, 2, 1, 15, 60.0, 300, 3, 1, 1, 2, 0, 0]
        state = np.array(vals, dtype=np.float32)
        next_vals = [55, 100, 700, 350, 35, 23, 2, 0, 10, 82.0, 322, 3, 1, 1, 2, 0, 0]
        next_s = np.array(next_vals, dtype=np.float32)
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


class TestActionProbs:
    """Tests for action probability persistence."""

    def test_store_transition_with_action_probs(self, db: TrainingDB) -> None:
        db.store_game("g1", "Simple64", 1, "win", 300.0, 5.5, "v0")
        state = np.zeros(FEATURE_DIM, dtype=np.float32)
        probs = [0.1, 0.2, 0.3, 0.25, 0.15]
        db.store_transition(
            "g1", 0, 60.0, state, action=2, reward=0.1, action_probs=probs,
        )
        assert db.get_transition_count() == 1
        # Verify stored value via raw query
        row = db._conn.execute(
            "SELECT action_probs FROM transitions WHERE game_id = 'g1'"
        ).fetchone()
        assert row is not None
        assert json.loads(row[0]) == probs

    def test_store_transition_without_action_probs(self, db: TrainingDB) -> None:
        db.store_game("g1", "Simple64", 1, "win", 300.0, 5.5, "v0")
        state = np.zeros(FEATURE_DIM, dtype=np.float32)
        db.store_transition("g1", 0, 60.0, state, action=2, reward=0.1)
        row = db._conn.execute(
            "SELECT action_probs FROM transitions WHERE game_id = 'g1'"
        ).fetchone()
        assert row is not None
        assert row[0] is None

    def test_migration_idempotent(self, tmp_path: Path) -> None:
        """Opening the DB twice should not fail (column already exists)."""
        path = tmp_path / "test.db"
        db1 = TrainingDB(path)
        db1.close()
        db2 = TrainingDB(path)  # Second open triggers migration again
        db2.close()

    def test_migration_adds_column_to_existing_db(self, tmp_path: Path) -> None:
        """Simulate an old DB without the column, then open with new code."""
        import sqlite3

        path = tmp_path / "old.db"
        conn = sqlite3.connect(str(path))
        # Create schema without action_probs
        conn.execute(
            "CREATE TABLE IF NOT EXISTS games ("
            "game_id TEXT PRIMARY KEY, map_name TEXT, difficulty INTEGER, "
            "result TEXT, duration_secs REAL, total_reward REAL, "
            "model_version TEXT, created_at TEXT DEFAULT (datetime('now')))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS transitions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, game_id TEXT, "
            "step_index INTEGER, game_time REAL, action INTEGER, reward REAL, "
            "done INTEGER DEFAULT 0)"
        )
        conn.commit()
        conn.close()

        # Open with new code — migration should add the column
        db = TrainingDB(path)
        cursor = db._conn.execute("PRAGMA table_info(transitions)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "action_probs" in columns
        db.close()


class TestGetActionDistribution:
    """Tests for get_action_distribution query."""

    def test_no_data_returns_none(self, db: TrainingDB) -> None:
        assert db.get_action_distribution("v0", 10) is None

    def test_no_probs_returns_none(self, db: TrainingDB) -> None:
        db.store_game("g1", "Simple64", 1, "win", 300.0, 5.5, "v0")
        state = np.zeros(FEATURE_DIM, dtype=np.float32)
        db.store_transition("g1", 0, 60.0, state, action=2, reward=0.1)
        assert db.get_action_distribution("v0", 10) is None

    def test_single_transition_returns_same_probs(self, db: TrainingDB) -> None:
        db.store_game("g1", "Simple64", 1, "win", 300.0, 5.5, "v0")
        state = np.zeros(FEATURE_DIM, dtype=np.float32)
        probs = [0.1, 0.2, 0.3, 0.25, 0.15]
        db.store_transition(
            "g1", 0, 60.0, state, action=2, reward=0.1, action_probs=probs,
        )
        result = db.get_action_distribution("v0", 10)
        assert result is not None
        for a, b in zip(result, probs, strict=True):
            assert abs(a - b) < 1e-6

    def test_averages_multiple_transitions(self, db: TrainingDB) -> None:
        db.store_game("g1", "Simple64", 1, "win", 300.0, 5.5, "v0")
        state = np.zeros(FEATURE_DIM, dtype=np.float32)
        db.store_transition(
            "g1", 0, 60.0, state, action=2, reward=0.1,
            action_probs=[0.0, 0.0, 1.0, 0.0, 0.0],
        )
        db.store_transition(
            "g1", 1, 82.0, state, action=1, reward=0.2,
            action_probs=[0.0, 1.0, 0.0, 0.0, 0.0],
        )
        result = db.get_action_distribution("v0", 10)
        assert result is not None
        assert abs(result[1] - 0.5) < 1e-6
        assert abs(result[2] - 0.5) < 1e-6

    def test_filters_by_model_version(self, db: TrainingDB) -> None:
        db.store_game("g1", "Simple64", 1, "win", 300.0, 5.5, "v1")
        db.store_game("g2", "Simple64", 1, "win", 300.0, 5.5, "v2")
        state = np.zeros(FEATURE_DIM, dtype=np.float32)
        db.store_transition(
            "g1", 0, 60.0, state, action=2, reward=0.1,
            action_probs=[0.0, 0.0, 1.0, 0.0, 0.0],
        )
        db.store_transition(
            "g2", 0, 60.0, state, action=1, reward=0.2,
            action_probs=[0.0, 1.0, 0.0, 0.0, 0.0],
        )
        result = db.get_action_distribution("v1", 10)
        assert result is not None
        assert abs(result[2] - 1.0) < 1e-6  # Only v1 data
