"""SQLite training database for storing game transitions and metadata."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from alpha4gate.learning.features import FEATURE_DIM

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS games (
    game_id       TEXT PRIMARY KEY,
    map_name      TEXT NOT NULL,
    difficulty     INTEGER NOT NULL,
    result         TEXT NOT NULL,
    duration_secs  REAL NOT NULL,
    total_reward   REAL NOT NULL,
    model_version  TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transitions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id       TEXT NOT NULL REFERENCES games(game_id),
    step_index    INTEGER NOT NULL,
    game_time     REAL NOT NULL,
    supply_used   INTEGER NOT NULL,
    supply_cap    INTEGER NOT NULL,
    minerals      INTEGER NOT NULL,
    vespene       INTEGER NOT NULL,
    army_supply   INTEGER NOT NULL,
    worker_count  INTEGER NOT NULL,
    base_count    INTEGER NOT NULL,
    enemy_near    INTEGER NOT NULL,
    enemy_supply  INTEGER NOT NULL,
    game_time_secs    REAL NOT NULL DEFAULT 0.0,
    gateway_count     INTEGER NOT NULL DEFAULT 0,
    robo_count        INTEGER NOT NULL DEFAULT 0,
    forge_count       INTEGER NOT NULL DEFAULT 0,
    upgrade_count     INTEGER NOT NULL DEFAULT 0,
    enemy_structure_count INTEGER NOT NULL DEFAULT 0,
    action        INTEGER NOT NULL,
    reward        REAL NOT NULL,
    next_supply_used   INTEGER,
    next_supply_cap    INTEGER,
    next_minerals      INTEGER,
    next_vespene       INTEGER,
    next_army_supply   INTEGER,
    next_worker_count  INTEGER,
    next_base_count    INTEGER,
    next_enemy_near    INTEGER,
    next_enemy_supply  INTEGER,
    next_game_time_secs    REAL DEFAULT 0.0,
    next_gateway_count     INTEGER DEFAULT 0,
    next_robo_count        INTEGER DEFAULT 0,
    next_forge_count       INTEGER DEFAULT 0,
    next_upgrade_count     INTEGER DEFAULT 0,
    next_enemy_structure_count INTEGER DEFAULT 0,
    done          INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_transitions_game ON transitions(game_id);
CREATE INDEX IF NOT EXISTS idx_transitions_action ON transitions(action);
CREATE INDEX IF NOT EXISTS idx_games_result ON games(result);
CREATE INDEX IF NOT EXISTS idx_games_model ON games(model_version);
"""

# Column names for the 15 state features in transitions table (matches feature vector order)
_STATE_COLS = [
    "supply_used", "supply_cap", "minerals", "vespene", "army_supply",
    "worker_count", "base_count", "enemy_near", "enemy_supply",
    "game_time_secs", "gateway_count", "robo_count", "forge_count",
    "upgrade_count", "enemy_structure_count",
]

_NEXT_STATE_COLS = [f"next_{c}" for c in _STATE_COLS]


class TrainingDB:
    """SQLite database for storing training game data and transitions."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def store_game(
        self,
        game_id: str,
        map_name: str,
        difficulty: int,
        result: str,
        duration_secs: float,
        total_reward: float,
        model_version: str,
    ) -> None:
        """Insert a game record."""
        self._conn.execute(
            "INSERT INTO games (game_id, map_name, difficulty, result, "
            "duration_secs, total_reward, model_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (game_id, map_name, difficulty, result, duration_secs, total_reward, model_version),
        )
        self._conn.commit()

    def store_transition(
        self,
        game_id: str,
        step_index: int,
        game_time: float,
        state: NDArray[np.float32],
        action: int,
        reward: float,
        next_state: NDArray[np.float32] | None = None,
        done: bool = False,
    ) -> None:
        """Insert a single (s, a, r, s') transition.

        state and next_state are raw (un-normalized) integer feature vectors
        matching the 14-column order in _STATE_COLS.
        """
        values: list[Any] = [game_id, step_index, game_time]
        values.extend(int(v) for v in state)
        values.append(action)
        values.append(reward)
        if next_state is not None:
            values.extend(int(v) for v in next_state)
        else:
            values.extend([None] * FEATURE_DIM)
        values.append(1 if done else 0)

        placeholders = ", ".join("?" * len(values))
        cols = (
            ["game_id", "step_index", "game_time"]
            + _STATE_COLS
            + ["action", "reward"]
            + _NEXT_STATE_COLS
            + ["done"]
        )
        col_str = ", ".join(cols)
        self._conn.execute(
            f"INSERT INTO transitions ({col_str}) VALUES ({placeholders})",
            values,
        )
        self._conn.commit()

    def sample_batch(
        self, n: int
    ) -> tuple[NDArray[np.float32], NDArray[np.int64], NDArray[np.float32]]:
        """Sample n random transitions, returning (states, actions, rewards).

        Returns:
            states: shape (n, FEATURE_DIM) — raw integer values (not normalized)
            actions: shape (n,)
            rewards: shape (n,)
        """
        rows = self._conn.execute(
            f"SELECT {', '.join(_STATE_COLS)}, action, reward "
            "FROM transitions ORDER BY RANDOM() LIMIT ?",
            (n,),
        ).fetchall()
        if not rows:
            return (
                np.zeros((0, FEATURE_DIM), dtype=np.float32),
                np.zeros(0, dtype=np.int64),
                np.zeros(0, dtype=np.float32),
            )
        states = np.array([r[:FEATURE_DIM] for r in rows], dtype=np.float32)
        actions = np.array([r[FEATURE_DIM] for r in rows], dtype=np.int64)
        rewards = np.array([r[FEATURE_DIM + 1] for r in rows], dtype=np.float32)
        return states, actions, rewards

    def get_recent_win_rate(self, n_games: int) -> float:
        """Win rate over the most recent n_games. Returns 0.0 if no games."""
        rows = self._conn.execute(
            "SELECT result FROM games ORDER BY rowid DESC LIMIT ?",
            (n_games,),
        ).fetchall()
        if not rows:
            return 0.0
        wins = sum(1 for r in rows if r[0] == "win")
        return wins / len(rows)

    def get_game_count(self) -> int:
        """Total number of games recorded."""
        row = self._conn.execute("SELECT COUNT(*) FROM games").fetchone()
        return int(row[0]) if row else 0

    def get_transition_count(self) -> int:
        """Total number of transitions recorded."""
        row = self._conn.execute("SELECT COUNT(*) FROM transitions").fetchone()
        return int(row[0]) if row else 0

    def get_db_size_bytes(self) -> int:
        """Size of the database file on disk."""
        if self._path.exists():
            return os.path.getsize(self._path)
        return 0
