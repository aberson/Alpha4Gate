"""Tests for the SQLite training database."""

import json
from pathlib import Path

import numpy as np
import pytest
from bots.v0.learning.database import TrainingDB
from bots.v0.learning.features import (
    BASE_GAME_FEATURE_DIM as FEATURE_DIM,  # DB uses game-state dims only
)


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
        vals = [50, 100, 800, 400, 30, 22, 2, 1, 15, 60.0, 300, 3, 1, 1, 2, 0, 0] + [0] * 23
        state = np.array(vals, dtype=np.float32)
        next_vals = [55, 100, 700, 350, 35, 23, 2, 0, 10, 82.0, 322, 3, 1, 1, 2, 0, 0] + [0] * 23
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


class TestGetGameResult:
    """Tests for ``TrainingDB.get_game_result`` (Phase 4.5 blocker #67)."""

    def test_returns_result_for_existing_row(self, db: TrainingDB) -> None:
        db.store_game("g1", "Simple64", 1, "win", 300.0, 5.0, "v0")
        assert db.get_game_result("g1") == "win"

    def test_returns_none_for_missing_row(self, db: TrainingDB) -> None:
        """Blocker #67: missing rows MUST return None, not a default 'loss'.

        Callers treat None as "unknown" and refuse to promote. The old
        silent-default-to-loss behavior corrupted promotion decisions by
        materializing crashed games as fake losses.
        """
        db.store_game("g1", "Simple64", 1, "win", 300.0, 5.0, "v0")
        assert db.get_game_result("nonexistent_game") is None

    def test_returns_none_on_empty_table(self, db: TrainingDB) -> None:
        assert db.get_game_result("any_game") is None


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


class TestWinRateByModel:
    def test_empty_db(self, db: TrainingDB) -> None:
        result = db.get_win_rate_by_model("v0")
        assert result == {"wins": 0, "losses": 0, "total": 0, "win_rate": 0.0}

    def test_single_model_all_wins(self, db: TrainingDB) -> None:
        for i in range(3):
            db.store_game(f"g{i}", "Simple64", 1, "win", 300.0, 5.0, "v1")
        result = db.get_win_rate_by_model("v1")
        assert result["wins"] == 3
        assert result["losses"] == 0
        assert result["total"] == 3
        assert result["win_rate"] == 1.0

    def test_single_model_mixed(self, db: TrainingDB) -> None:
        db.store_game("g0", "Simple64", 1, "win", 300.0, 5.0, "v1")
        db.store_game("g1", "Simple64", 1, "loss", 300.0, -5.0, "v1")
        result = db.get_win_rate_by_model("v1")
        assert result["wins"] == 1
        assert result["losses"] == 1
        assert result["total"] == 2
        assert result["win_rate"] == 0.5

    def test_filters_by_model(self, db: TrainingDB) -> None:
        db.store_game("g0", "Simple64", 1, "win", 300.0, 5.0, "v1")
        db.store_game("g1", "Simple64", 1, "loss", 300.0, -5.0, "v2")
        result_v1 = db.get_win_rate_by_model("v1")
        result_v2 = db.get_win_rate_by_model("v2")
        assert result_v1["total"] == 1
        assert result_v1["win_rate"] == 1.0
        assert result_v2["total"] == 1
        assert result_v2["win_rate"] == 0.0

    def test_nonexistent_model(self, db: TrainingDB) -> None:
        db.store_game("g0", "Simple64", 1, "win", 300.0, 5.0, "v1")
        result = db.get_win_rate_by_model("v99")
        assert result["total"] == 0
        assert result["win_rate"] == 0.0


class TestAllModelStats:
    def test_empty_db(self, db: TrainingDB) -> None:
        assert db.get_all_model_stats() == []

    def test_single_model(self, db: TrainingDB) -> None:
        db.store_game("g0", "Simple64", 1, "win", 300.0, 5.0, "v1")
        db.store_game("g1", "Simple64", 1, "loss", 300.0, -5.0, "v1")
        stats = db.get_all_model_stats()
        assert len(stats) == 1
        assert stats[0]["model_version"] == "v1"
        assert stats[0]["wins"] == 1
        assert stats[0]["losses"] == 1
        assert stats[0]["total"] == 2
        assert stats[0]["win_rate"] == 0.5
        assert stats[0]["first_game"] is not None
        assert stats[0]["last_game"] is not None

    def test_multiple_models_chronological_order(self, db: TrainingDB) -> None:
        db.store_game("g0", "Simple64", 1, "win", 300.0, 5.0, "v1")
        db.store_game("g1", "Simple64", 1, "loss", 300.0, -5.0, "v1")
        db.store_game("g2", "Simple64", 2, "win", 300.0, 5.0, "v2")
        db.store_game("g3", "Simple64", 2, "win", 300.0, 5.0, "v2")
        db.store_game("g4", "Simple64", 2, "loss", 300.0, -5.0, "v2")
        stats = db.get_all_model_stats()
        assert len(stats) == 2
        assert stats[0]["model_version"] == "v1"
        assert stats[0]["win_rate"] == 0.5
        assert stats[1]["model_version"] == "v2"
        assert stats[1]["wins"] == 2
        assert stats[1]["losses"] == 1
        assert abs(stats[1]["win_rate"] - 2 / 3) < 1e-9


class TestLegacySchemaMigration:
    """Phase 4.5 F7 regression guard.

    A DB file created before later columns were added must get those
    columns ALTERed in on next open. ``CREATE TABLE IF NOT EXISTS`` will
    NOT add columns to an existing table, so the migration must walk the
    expected-later list explicitly.
    """

    def _create_legacy_db(self, path: Path) -> None:
        """Create a transitions table with the original (pre-cannon_count) schema.

        This is the schema that shipped before any of the ``_LATER_ADDED_COLS``
        existed: original (s, a, r, s') with the 9 base state features + their
        next_* counterparts, no game_time_secs, no structure counts, no
        action_probs.
        """
        import sqlite3

        conn = sqlite3.connect(str(path))
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
        conn.close()

    def test_legacy_db_gets_all_later_columns_added(self, tmp_path: Path) -> None:
        from bots.v0.learning.database import _LATER_ADDED_COLS

        legacy_path = tmp_path / "legacy.db"
        self._create_legacy_db(legacy_path)

        # Pre-condition: legacy table has none of the later columns
        import sqlite3

        conn = sqlite3.connect(str(legacy_path))
        before = {r[1] for r in conn.execute("PRAGMA table_info(transitions)").fetchall()}
        conn.close()
        for col_name, _ in _LATER_ADDED_COLS:
            assert col_name not in before, f"legacy DB should not have {col_name}"

        # Open via TrainingDB — migration should run
        db = TrainingDB(legacy_path)
        try:
            conn = sqlite3.connect(str(legacy_path))
            after = {
                r[1] for r in conn.execute("PRAGMA table_info(transitions)").fetchall()
            }
            conn.close()
            for col_name, _ in _LATER_ADDED_COLS:
                assert col_name in after, f"migration should have added {col_name}"
        finally:
            db.close()

    def test_legacy_db_can_store_new_transition_after_migration(
        self, tmp_path: Path
    ) -> None:
        """The migrated legacy DB must accept the full 32-feature transition."""
        legacy_path = tmp_path / "legacy.db"
        self._create_legacy_db(legacy_path)

        db = TrainingDB(legacy_path)
        try:
            db.store_game("g1", "Simple64", 1, "win", 60.0, 1.0, "v0")
            vals = [50, 100, 800, 400, 30, 22, 2, 1, 15, 60.0, 3, 1, 1, 2, 0, 5, 2] + [0] * 23
            assert len(vals) == FEATURE_DIM
            state = np.array(vals, dtype=np.float32)
            db.store_transition("g1", 0, 60.0, state, action=2, reward=0.1)
            assert db.get_transition_count() == 1
        finally:
            db.close()

    def test_migration_is_idempotent(self, tmp_path: Path) -> None:
        """Calling migrate twice (e.g. opening DB twice) must not fail."""
        legacy_path = tmp_path / "legacy.db"
        self._create_legacy_db(legacy_path)

        db1 = TrainingDB(legacy_path)
        db1.close()
        # Second open re-runs migration on the now-already-migrated table
        db2 = TrainingDB(legacy_path)
        db2.close()

    def test_winprob_populates_on_write_after_migration(
        self, tmp_path: Path
    ) -> None:
        """After migrating a legacy DB, store_transition must persist win_prob.

        Phase N Step 2 "Done when": the new ``win_prob`` column not only
        gets ALTERed in by ``_migrate_columns`` but also accepts writes
        through ``store_transition(win_prob=...)``.
        """
        import sqlite3

        legacy_path = tmp_path / "legacy.db"
        self._create_legacy_db(legacy_path)

        db = TrainingDB(legacy_path)
        try:
            db.store_game("g1", "Simple64", 1, "win", 60.0, 1.0, "v0")
            state = np.zeros(FEATURE_DIM, dtype=np.float32)
            db.store_transition(
                "g1", 0, 60.0, state, action=2, reward=0.1, win_prob=0.55
            )
        finally:
            db.close()

        conn = sqlite3.connect(str(legacy_path))
        try:
            row = conn.execute(
                "SELECT win_prob FROM transitions WHERE game_id = 'g1'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[0] == pytest.approx(0.55)
