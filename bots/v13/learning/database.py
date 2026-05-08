"""SQLite training database for storing game transitions and metadata."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import struct
import threading
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from bots.v13.learning.features import BASE_GAME_FEATURE_DIM

_log = logging.getLogger(__name__)

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
    cannon_count      INTEGER NOT NULL DEFAULT 0,
    battery_count     INTEGER NOT NULL DEFAULT 0,
    zealot_count      INTEGER NOT NULL DEFAULT 0,
    stalker_count     INTEGER NOT NULL DEFAULT 0,
    sentry_count      INTEGER NOT NULL DEFAULT 0,
    immortal_count    INTEGER NOT NULL DEFAULT 0,
    colossus_count    INTEGER NOT NULL DEFAULT 0,
    archon_count      INTEGER NOT NULL DEFAULT 0,
    high_templar_count INTEGER NOT NULL DEFAULT 0,
    dark_templar_count INTEGER NOT NULL DEFAULT 0,
    phoenix_count     INTEGER NOT NULL DEFAULT 0,
    void_ray_count    INTEGER NOT NULL DEFAULT 0,
    carrier_count     INTEGER NOT NULL DEFAULT 0,
    tempest_count     INTEGER NOT NULL DEFAULT 0,
    disruptor_count   INTEGER NOT NULL DEFAULT 0,
    warp_prism_count  INTEGER NOT NULL DEFAULT 0,
    observer_count    INTEGER NOT NULL DEFAULT 0,
    enemy_light_count     INTEGER NOT NULL DEFAULT 0,
    enemy_armored_count   INTEGER NOT NULL DEFAULT 0,
    enemy_siege_count     INTEGER NOT NULL DEFAULT 0,
    enemy_support_count   INTEGER NOT NULL DEFAULT 0,
    enemy_air_harass_count INTEGER NOT NULL DEFAULT 0,
    enemy_heavy_count     INTEGER NOT NULL DEFAULT 0,
    enemy_capital_count   INTEGER NOT NULL DEFAULT 0,
    enemy_cloak_count     INTEGER NOT NULL DEFAULT 0,
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
    next_cannon_count     INTEGER DEFAULT 0,
    next_battery_count    INTEGER DEFAULT 0,
    next_zealot_count     INTEGER DEFAULT 0,
    next_stalker_count    INTEGER DEFAULT 0,
    next_sentry_count     INTEGER DEFAULT 0,
    next_immortal_count   INTEGER DEFAULT 0,
    next_colossus_count   INTEGER DEFAULT 0,
    next_archon_count     INTEGER DEFAULT 0,
    next_high_templar_count INTEGER DEFAULT 0,
    next_dark_templar_count INTEGER DEFAULT 0,
    next_phoenix_count    INTEGER DEFAULT 0,
    next_void_ray_count   INTEGER DEFAULT 0,
    next_carrier_count    INTEGER DEFAULT 0,
    next_tempest_count    INTEGER DEFAULT 0,
    next_disruptor_count  INTEGER DEFAULT 0,
    next_warp_prism_count INTEGER DEFAULT 0,
    next_observer_count   INTEGER DEFAULT 0,
    next_enemy_light_count     INTEGER DEFAULT 0,
    next_enemy_armored_count   INTEGER DEFAULT 0,
    next_enemy_siege_count     INTEGER DEFAULT 0,
    next_enemy_support_count   INTEGER DEFAULT 0,
    next_enemy_air_harass_count INTEGER DEFAULT 0,
    next_enemy_heavy_count     INTEGER DEFAULT 0,
    next_enemy_capital_count   INTEGER DEFAULT 0,
    next_enemy_cloak_count     INTEGER DEFAULT 0,
    done          INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_transitions_game ON transitions(game_id);
CREATE INDEX IF NOT EXISTS idx_transitions_action ON transitions(action);
CREATE INDEX IF NOT EXISTS idx_games_result ON games(result);
CREATE INDEX IF NOT EXISTS idx_games_model ON games(model_version);
"""

# Column names for the 40 state features in transitions table (matches feature vector order)
_STATE_COLS = [
    "supply_used", "supply_cap", "minerals", "vespene", "army_supply",
    "worker_count", "base_count", "enemy_near", "enemy_supply",
    "game_time_secs", "gateway_count", "robo_count", "forge_count",
    "upgrade_count", "enemy_structure_count", "cannon_count", "battery_count",
    "zealot_count", "stalker_count", "sentry_count", "immortal_count",
    "colossus_count", "archon_count", "high_templar_count", "dark_templar_count",
    "phoenix_count", "void_ray_count", "carrier_count", "tempest_count",
    "disruptor_count", "warp_prism_count", "observer_count",
    "enemy_light_count", "enemy_armored_count", "enemy_siege_count",
    "enemy_support_count", "enemy_air_harass_count", "enemy_heavy_count",
    "enemy_capital_count", "enemy_cloak_count",
]

_NEXT_STATE_COLS = [f"next_{c}" for c in _STATE_COLS]


# Columns added to ``transitions`` AFTER the original schema shipped. Each
# entry is ``(column_name, sql_type_with_default)``. ``__init__`` walks this
# list and ALTER-TABLE-ADD-COLUMNs any that are missing — SQLite's
# ``CREATE TABLE IF NOT EXISTS`` is a no-op for existing tables, so a fresh
# schema string in this file does NOT migrate older DB files. Phase 4.5
# Step 2 found this the hard way (finding F7): a smoke test crashed because
# ``data/training.db`` was created before ``cannon_count`` was added.
#
# Adding a new column to the ``transitions`` schema also requires adding it
# here. Tests in test_database.py exercise the migration on a synthetic
# legacy DB to catch drift.
_LATER_ADDED_COLS: list[tuple[str, str]] = [
    ("game_time_secs", "REAL DEFAULT 0.0"),
    ("gateway_count", "INTEGER DEFAULT 0"),
    ("robo_count", "INTEGER DEFAULT 0"),
    ("forge_count", "INTEGER DEFAULT 0"),
    ("upgrade_count", "INTEGER DEFAULT 0"),
    ("enemy_structure_count", "INTEGER DEFAULT 0"),
    ("cannon_count", "INTEGER DEFAULT 0"),
    ("battery_count", "INTEGER DEFAULT 0"),
    ("next_game_time_secs", "REAL DEFAULT 0.0"),
    ("next_gateway_count", "INTEGER DEFAULT 0"),
    ("next_robo_count", "INTEGER DEFAULT 0"),
    ("next_forge_count", "INTEGER DEFAULT 0"),
    ("next_upgrade_count", "INTEGER DEFAULT 0"),
    ("next_enemy_structure_count", "INTEGER DEFAULT 0"),
    ("next_cannon_count", "INTEGER DEFAULT 0"),
    ("next_battery_count", "INTEGER DEFAULT 0"),
    ("zealot_count", "INTEGER DEFAULT 0"),
    ("stalker_count", "INTEGER DEFAULT 0"),
    ("sentry_count", "INTEGER DEFAULT 0"),
    ("immortal_count", "INTEGER DEFAULT 0"),
    ("colossus_count", "INTEGER DEFAULT 0"),
    ("archon_count", "INTEGER DEFAULT 0"),
    ("high_templar_count", "INTEGER DEFAULT 0"),
    ("dark_templar_count", "INTEGER DEFAULT 0"),
    ("phoenix_count", "INTEGER DEFAULT 0"),
    ("void_ray_count", "INTEGER DEFAULT 0"),
    ("carrier_count", "INTEGER DEFAULT 0"),
    ("tempest_count", "INTEGER DEFAULT 0"),
    ("disruptor_count", "INTEGER DEFAULT 0"),
    ("warp_prism_count", "INTEGER DEFAULT 0"),
    ("observer_count", "INTEGER DEFAULT 0"),
    ("next_zealot_count", "INTEGER DEFAULT 0"),
    ("next_stalker_count", "INTEGER DEFAULT 0"),
    ("next_sentry_count", "INTEGER DEFAULT 0"),
    ("next_immortal_count", "INTEGER DEFAULT 0"),
    ("next_colossus_count", "INTEGER DEFAULT 0"),
    ("next_archon_count", "INTEGER DEFAULT 0"),
    ("next_high_templar_count", "INTEGER DEFAULT 0"),
    ("next_dark_templar_count", "INTEGER DEFAULT 0"),
    ("next_phoenix_count", "INTEGER DEFAULT 0"),
    ("next_void_ray_count", "INTEGER DEFAULT 0"),
    ("next_carrier_count", "INTEGER DEFAULT 0"),
    ("next_tempest_count", "INTEGER DEFAULT 0"),
    ("next_disruptor_count", "INTEGER DEFAULT 0"),
    ("next_warp_prism_count", "INTEGER DEFAULT 0"),
    ("next_observer_count", "INTEGER DEFAULT 0"),
    ("enemy_light_count", "INTEGER DEFAULT 0"),
    ("enemy_armored_count", "INTEGER DEFAULT 0"),
    ("enemy_siege_count", "INTEGER DEFAULT 0"),
    ("enemy_support_count", "INTEGER DEFAULT 0"),
    ("enemy_air_harass_count", "INTEGER DEFAULT 0"),
    ("enemy_heavy_count", "INTEGER DEFAULT 0"),
    ("enemy_capital_count", "INTEGER DEFAULT 0"),
    ("enemy_cloak_count", "INTEGER DEFAULT 0"),
    ("next_enemy_light_count", "INTEGER DEFAULT 0"),
    ("next_enemy_armored_count", "INTEGER DEFAULT 0"),
    ("next_enemy_siege_count", "INTEGER DEFAULT 0"),
    ("next_enemy_support_count", "INTEGER DEFAULT 0"),
    ("next_enemy_air_harass_count", "INTEGER DEFAULT 0"),
    ("next_enemy_heavy_count", "INTEGER DEFAULT 0"),
    ("next_enemy_capital_count", "INTEGER DEFAULT 0"),
    ("next_enemy_cloak_count", "INTEGER DEFAULT 0"),
    ("action_probs", "TEXT DEFAULT NULL"),
    ("win_prob", "REAL DEFAULT NULL"),
]


def _coerce_action(v: Any) -> int:
    # Historical rows stored action as np.int64.tobytes() (sqlite3 default
    # adapter for numpy scalars writes BLOB via buffer protocol). Decode
    # little-endian int64; pass through regular ints unchanged.
    if isinstance(v, (bytes, memoryview)):
        b = bytes(v)
        if len(b) == 8:
            return int(struct.unpack("<q", b)[0])
        return int.from_bytes(b, "little", signed=True)
    return int(v)


class TrainingDB:
    """SQLite database for storing training game data and transitions."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # ``check_same_thread=False`` lets the connection be shared across
        # threads (eval/training games run in worker threads spawned by the
        # SC2 environment). Cross-thread safety is enforced by ``self._lock``
        # below — EVERY method that touches ``self._conn`` (reads AND writes,
        # plus ``close``) takes the lock first. Read methods take the lock
        # too because Python's sqlite3 module is not safe for concurrent use
        # of a single connection across threads, even with
        # ``check_same_thread=False``; the GIL protects individual C calls
        # but not multi-step cursor operations (``execute`` + ``fetchall``),
        # and a reader running while ``close()`` is called would crash with
        # ``ProgrammingError: Cannot operate on a closed database``.
        # WAL mode is still enabled for crash safety and because separate
        # future connections could read concurrently, but WAL does not
        # make a SHARED connection safe across threads on its own.
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        # PRAGMA journal_mode returns the mode actually set. SQLite can
        # silently fall back (e.g. on networked filesystems) — log a warning
        # if we didn't actually get WAL.
        result = self._conn.execute("PRAGMA journal_mode=WAL").fetchone()
        if result is None or str(result[0]).lower() != "wal":
            _log.warning(
                "TrainingDB: PRAGMA journal_mode=WAL fell back to %r at %s",
                result[0] if result else None,
                self._path,
            )
        self._conn.executescript(_SCHEMA)
        self._migrate_columns()

    def _migrate_columns(self) -> None:
        """ALTER any columns from ``_LATER_ADDED_COLS`` that are missing.

        ``CREATE TABLE IF NOT EXISTS`` does NOT add new columns to a table
        that already exists, so DB files created before a column was added
        to the schema will be missing that column even though the .py
        schema string is correct. This walks the expected-later list and
        adds any missing columns. Idempotent — safe to call on every open.
        """
        with self._lock:
            cursor = self._conn.execute("PRAGMA table_info(transitions)")
            existing = {row[1] for row in cursor.fetchall()}
            for col_name, col_def in _LATER_ADDED_COLS:
                if col_name not in existing:
                    self._conn.execute(f"ALTER TABLE transitions ADD COLUMN {col_name} {col_def}")
            self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
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
        with self._lock:
            self._conn.execute(
                "INSERT INTO games (game_id, map_name, difficulty, result, "
                "duration_secs, total_reward, model_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    game_id,
                    map_name,
                    difficulty,
                    result,
                    duration_secs,
                    total_reward,
                    model_version,
                ),
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
        action_probs: list[float] | None = None,
        win_prob: float | None = None,
    ) -> None:
        """Insert a single (s, a, r, s') transition.

        state and next_state are raw (un-normalized) integer feature vectors
        matching the 40-column order in _STATE_COLS.

        action_probs: optional list of action probabilities from the neural
        engine, stored as JSON text.
        win_prob: optional heuristic P(win) for the (s, a) pair, in [0, 1].
        """
        values: list[Any] = [game_id, step_index, game_time]
        values.extend(int(v) for v in state)
        values.append(int(action))
        values.append(float(reward))
        if next_state is not None:
            values.extend(int(v) for v in next_state)
        else:
            values.extend([None] * BASE_GAME_FEATURE_DIM)
        values.append(1 if done else 0)
        values.append(json.dumps(action_probs) if action_probs is not None else None)
        values.append(float(win_prob) if win_prob is not None else None)

        cols = (
            ["game_id", "step_index", "game_time"]
            + _STATE_COLS
            + ["action", "reward"]
            + _NEXT_STATE_COLS
            + ["done", "action_probs", "win_prob"]
        )
        placeholders = ", ".join("?" * len(values))
        col_str = ", ".join(cols)
        with self._lock:
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
            states: shape (n, BASE_GAME_FEATURE_DIM) — raw integer values (not normalized)
            actions: shape (n,)
            rewards: shape (n,)
        """
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {', '.join(_STATE_COLS)}, action, reward "
                "FROM transitions ORDER BY RANDOM() LIMIT ?",
                (n,),
            ).fetchall()
        if not rows:
            return (
                np.zeros((0, BASE_GAME_FEATURE_DIM), dtype=np.float32),
                np.zeros(0, dtype=np.int64),
                np.zeros(0, dtype=np.float32),
            )
        states = np.array([r[:BASE_GAME_FEATURE_DIM] for r in rows], dtype=np.float32)
        actions = np.array([_coerce_action(r[BASE_GAME_FEATURE_DIM]) for r in rows], dtype=np.int64)
        rewards = np.array([r[BASE_GAME_FEATURE_DIM + 1] for r in rows], dtype=np.float32)
        return states, actions, rewards

    def get_game_result(self, game_id: str) -> str | None:
        """Look up a specific game's recorded result.

        Returns the result string ("win", "loss", etc.) if a row exists for
        this game_id, or None if no row was recorded (e.g., the game crashed
        before the writer ran). Callers MUST treat None as "unknown" -- do
        NOT default to "loss" silently. See Phase 4.5 blocker #67.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT result FROM games WHERE game_id = ?", (game_id,)
            ).fetchone()
        if row is None:
            return None
        return str(row[0])

    def get_recent_win_rate(self, n_games: int) -> float:
        """Win rate over the most recent n_games. Returns 0.0 if no games."""
        with self._lock:
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
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM games").fetchone()
        return int(row[0]) if row else 0

    def get_transition_count(self) -> int:
        """Total number of transitions recorded."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM transitions").fetchone()
        return int(row[0]) if row else 0

    def get_action_distribution(
        self, model_version: str, n_games: int
    ) -> list[float] | None:
        """Return average action probabilities for a given model version.

        Averages the stored action_probs across the most recent ``n_games``
        games for the specified ``model_version``. Returns None if no
        transitions with action_probs data are found.
        """
        # Hold the lock across BOTH SELECTs so the game_id list and the
        # transition rows are read from a consistent snapshot (and so no
        # writer can interleave between them at the C level).
        with self._lock:
            # Get game_ids for the most recent n_games of this model version
            game_rows = self._conn.execute(
                "SELECT game_id FROM games WHERE model_version = ? "
                "ORDER BY rowid DESC LIMIT ?",
                (model_version, n_games),
            ).fetchall()
            if not game_rows:
                return None
            game_ids = [r[0] for r in game_rows]
            placeholders = ", ".join("?" * len(game_ids))
            rows = self._conn.execute(
                f"SELECT action_probs FROM transitions "
                f"WHERE game_id IN ({placeholders}) AND action_probs IS NOT NULL",
                game_ids,
            ).fetchall()

        if not rows:
            return None

        sums: list[float] | None = None
        count = 0
        for (raw,) in rows:
            probs = json.loads(raw)
            if sums is None:
                sums = [0.0] * len(probs)
            for i, p in enumerate(probs):
                sums[i] += p
            count += 1

        if sums is None or count == 0:
            return None
        return [s / count for s in sums]

    def get_win_rate_by_model(self, model_version: str) -> dict[str, object]:
        """Win rate and game counts for a specific model version.

        Returns:
            Dict with keys: wins, losses, total, win_rate.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT result FROM games WHERE model_version = ?",
                (model_version,),
            ).fetchall()
        total = len(rows)
        wins = sum(1 for r in rows if r[0] == "win")
        losses = total - wins
        return {
            "wins": wins,
            "losses": losses,
            "total": total,
            "win_rate": wins / total if total > 0 else 0.0,
        }

    def get_all_model_stats(self) -> list[dict[str, object]]:
        """Per-model stats ordered by first game timestamp (chronological).

        Returns:
            List of dicts with keys: model_version, wins, losses, total,
            win_rate, first_game, last_game.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT model_version, "
                "  SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) AS wins, "
                "  SUM(CASE WHEN result != 'win' THEN 1 ELSE 0 END) AS losses, "
                "  COUNT(*) AS total, "
                "  MIN(created_at) AS first_game, "
                "  MAX(created_at) AS last_game "
                "FROM games GROUP BY model_version ORDER BY MIN(created_at)",
            ).fetchall()
        result: list[dict[str, object]] = []
        for row in rows:
            mv, wins, losses, total, first_game, last_game = row
            result.append({
                "model_version": mv,
                "wins": wins,
                "losses": losses,
                "total": total,
                "win_rate": wins / total if total > 0 else 0.0,
                "first_game": first_game,
                "last_game": last_game,
            })
        return result

    def get_db_size_bytes(self) -> int:
        """Size of the database file on disk."""
        if self._path.exists():
            return os.path.getsize(self._path)
        return 0
