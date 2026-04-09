"""Tests for bot transition recording with action probability persistence."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from alpha4gate.bot import Alpha4GateBot
from alpha4gate.decision_engine import GameSnapshot, StrategicState
from alpha4gate.learning.database import TrainingDB


def _make_bot_with_db(tmp_path: Path) -> tuple[Alpha4GateBot, TrainingDB]:
    """Create a bot with a real training DB for transition tests."""
    db = TrainingDB(tmp_path / "test.db")
    db.store_game("g1", "Simple64", 1, "win", 300.0, 5.5, "v0")

    bot = MagicMock(spec=Alpha4GateBot)
    bot._record_transition = Alpha4GateBot._record_transition.__get__(bot)
    bot._training_db = db
    bot._game_id = "g1"
    bot._transition_step = 0
    bot._prev_obs = None
    bot._prev_action = None
    bot._prev_snapshot = None
    bot._neural_engine = None

    # Mock reward calculator
    bot._reward_calc = MagicMock()
    bot._reward_calc.compute_step_reward.return_value = 0.5

    # _STATE_TO_ACTION mapping
    bot._STATE_TO_ACTION = Alpha4GateBot._STATE_TO_ACTION

    return bot, db


def _default_snapshot(game_time_seconds: float = 200.0) -> GameSnapshot:
    return GameSnapshot(
        supply_used=50,
        supply_cap=100,
        minerals=800,
        vespene=400,
        army_supply=30,
        worker_count=22,
        base_count=2,
        game_time_seconds=game_time_seconds,
    )


class TestRecordTransitionActionProbs:
    def test_no_neural_engine_stores_none(self, tmp_path: Path) -> None:
        bot, db = _make_bot_with_db(tmp_path)
        snap1 = _default_snapshot(game_time_seconds=60.0)
        snap2 = _default_snapshot(game_time_seconds=82.0)

        # First call sets prev state
        bot._record_transition(snap1, StrategicState.OPENING)
        # Second call stores the transition
        bot._record_transition(snap2, StrategicState.EXPAND)

        row = db._conn.execute("SELECT action_probs FROM transitions").fetchone()
        assert row is not None
        assert row[0] is None

    def test_neural_engine_probs_stored(self, tmp_path: Path) -> None:
        bot, db = _make_bot_with_db(tmp_path)

        # Mock neural engine with last_probabilities
        neural_engine = MagicMock()
        neural_engine.last_probabilities = [0.1, 0.2, 0.3, 0.25, 0.15]
        bot._neural_engine = neural_engine

        snap1 = _default_snapshot(game_time_seconds=60.0)
        snap2 = _default_snapshot(game_time_seconds=82.0)

        bot._record_transition(snap1, StrategicState.OPENING)
        bot._record_transition(snap2, StrategicState.ATTACK)

        import json

        row = db._conn.execute("SELECT action_probs FROM transitions").fetchone()
        assert row is not None
        assert json.loads(row[0]) == [0.1, 0.2, 0.3, 0.25, 0.15]

    def test_empty_probs_stores_none(self, tmp_path: Path) -> None:
        bot, db = _make_bot_with_db(tmp_path)

        neural_engine = MagicMock()
        neural_engine.last_probabilities = []  # Empty list
        bot._neural_engine = neural_engine

        snap1 = _default_snapshot(game_time_seconds=60.0)
        snap2 = _default_snapshot(game_time_seconds=82.0)

        bot._record_transition(snap1, StrategicState.OPENING)
        bot._record_transition(snap2, StrategicState.EXPAND)

        row = db._conn.execute("SELECT action_probs FROM transitions").fetchone()
        assert row is not None
        assert row[0] is None  # Empty probs treated as None
