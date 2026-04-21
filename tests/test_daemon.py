"""Tests for the training daemon."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from bots.v0.api import app, configure
from bots.v0.config import Settings
from bots.v0.learning.daemon import (
    DaemonConfig,
    TrainingDaemon,
    load_daemon_config,
    save_daemon_config,
)
from fastapi.testclient import TestClient

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_settings(tmp_path: Path) -> Settings:
    for d in ("data", "logs", "replays"):
        (tmp_path / d).mkdir(exist_ok=True)
    return Settings(
        sc2_path=tmp_path,
        log_dir=tmp_path / "logs",
        replay_dir=tmp_path / "replays",
        data_dir=tmp_path / "data",
        web_ui_port=0,
        anthropic_api_key="",
        spawning_tool_api_key="",
    )


# ------------------------------------------------------------------
# DaemonConfig tests
# ------------------------------------------------------------------


class TestDaemonConfig:
    def test_defaults(self) -> None:
        cfg = DaemonConfig()
        assert cfg.check_interval_seconds == 60
        assert cfg.min_transitions == 500
        assert cfg.min_hours_since_last == 1.0
        assert cfg.cycles_per_run == 5
        assert cfg.games_per_cycle == 10
        assert cfg.current_difficulty == 1
        assert cfg.max_difficulty == 10
        assert cfg.win_rate_threshold == 0.8
        # max_runs defaults to None (unbounded) — the bounded bound is
        # opt-in from the caller (e.g. evolve's --post-training-cycles).
        assert cfg.max_runs is None

    def test_custom_values(self) -> None:
        cfg = DaemonConfig(check_interval_seconds=30, min_transitions=100)
        assert cfg.check_interval_seconds == 30
        assert cfg.min_transitions == 100

    def test_load_missing_file(self, tmp_path: Path) -> None:
        cfg = load_daemon_config(tmp_path / "nope.json")
        assert cfg == DaemonConfig()

    def test_save_and_load(self, tmp_path: Path) -> None:
        path = tmp_path / "cfg.json"
        original = DaemonConfig(check_interval_seconds=15, cycles_per_run=3)
        save_daemon_config(original, path)
        loaded = load_daemon_config(path)
        assert loaded == original

    def test_load_ignores_unknown_keys(self, tmp_path: Path) -> None:
        path = tmp_path / "cfg.json"
        path.write_text(json.dumps({"check_interval_seconds": 5, "unknown_key": 99}))
        cfg = load_daemon_config(path)
        assert cfg.check_interval_seconds == 5


# ------------------------------------------------------------------
# TrainingDaemon unit tests
# ------------------------------------------------------------------


class TestTrainingDaemon:
    def test_start_and_stop(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        daemon = TrainingDaemon(settings, DaemonConfig(check_interval_seconds=1))
        assert not daemon.is_running()

        daemon.start()
        assert daemon.is_running()

        daemon.stop()
        assert not daemon.is_running()

    def test_start_idempotent(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        daemon = TrainingDaemon(settings, DaemonConfig(check_interval_seconds=1))
        daemon.start()
        daemon.start()  # should be a no-op
        assert daemon.is_running()
        daemon.stop()

    def test_get_status_idle(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        daemon = TrainingDaemon(settings, DaemonConfig())
        status = daemon.get_status()
        assert status["running"] is False
        assert status["state"] == "idle"
        assert status["runs_completed"] == 0
        assert status["last_run"] is None
        assert status["config"]["check_interval_seconds"] == 60

    def test_get_status_running(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        daemon = TrainingDaemon(settings, DaemonConfig(check_interval_seconds=60))
        daemon.start()
        try:
            status = daemon.get_status()
            assert status["running"] is True
        finally:
            daemon.stop()

    def test_should_train_no_db_returns_false(self, tmp_path: Path) -> None:
        """No database file -> should not train."""
        settings = _make_settings(tmp_path)
        daemon = TrainingDaemon(settings, DaemonConfig())
        assert daemon._should_train() is False

    def test_run_training_called_when_should_train_true(self, tmp_path: Path) -> None:
        """When _should_train returns True, _run_training is called."""
        settings = _make_settings(tmp_path)
        daemon = TrainingDaemon(settings, DaemonConfig(check_interval_seconds=1))

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.return_value = {"cycles_completed": 1}

        call_count = 0

        def should_train_once() -> bool:
            nonlocal call_count
            call_count += 1
            return call_count == 1

        with patch(
            "bots.v0.learning.trainer.TrainingOrchestrator",
            return_value=mock_orchestrator,
        ):
            daemon._should_train = should_train_once  # type: ignore[assignment]
            daemon.start()
            # Give the daemon time to wake, check, and run
            time.sleep(3)
            daemon.stop()

        # Verify training was triggered
        assert mock_orchestrator.run.called
        status = daemon.get_status()
        assert status["runs_completed"] == 1
        assert status["last_result"] == {"cycles_completed": 1}
        assert status["last_error"] is None

    def test_max_runs_self_stops_after_n_runs(self, tmp_path: Path) -> None:
        """Daemon with max_runs=N stops itself after the N-th completed run.

        Mirrors the evolve post-promotion use case: start a bounded
        training burst, let it self-terminate, no runaway daemon.
        """
        settings = _make_settings(tmp_path)
        daemon = TrainingDaemon(
            settings,
            DaemonConfig(check_interval_seconds=1, max_runs=2),
        )

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.return_value = {"cycles_completed": 1}

        # Always returns True so the daemon would train on every check.
        # The max_runs bound is what stops it.
        def always_train() -> bool:
            return True

        with patch(
            "bots.v0.learning.trainer.TrainingOrchestrator",
            return_value=mock_orchestrator,
        ):
            daemon._should_train = always_train  # type: ignore[assignment]
            daemon.start()
            # Allow more than 2 check intervals so the bound has to kick
            # in — without max_runs, the daemon would keep going.
            time.sleep(5)

        # Daemon should have stopped itself without a .stop() call.
        assert not daemon.is_running(), (
            "daemon with max_runs=2 failed to self-stop"
        )
        status = daemon.get_status()
        assert status["runs_completed"] == 2, (
            f"expected 2 runs, got {status['runs_completed']}"
        )

    def test_start_resets_runs_completed(self, tmp_path: Path) -> None:
        """``start()`` zeroes ``_runs_completed`` so max_runs bounds are
        interpreted relative to this start, not process lifetime."""
        settings = _make_settings(tmp_path)
        daemon = TrainingDaemon(settings, DaemonConfig(check_interval_seconds=60))
        # Simulate a prior run leaving the counter non-zero.
        daemon._runs_completed = 7
        daemon.start()
        try:
            assert daemon._runs_completed == 0
        finally:
            daemon.stop()

    def test_run_training_exception_does_not_crash_daemon(self, tmp_path: Path) -> None:
        """Daemon survives a training exception and keeps running."""
        settings = _make_settings(tmp_path)
        daemon = TrainingDaemon(settings, DaemonConfig(check_interval_seconds=1))

        call_count = 0

        def should_train_once() -> bool:
            nonlocal call_count
            call_count += 1
            return call_count == 1

        with patch(
            "bots.v0.learning.trainer.TrainingOrchestrator",
            side_effect=RuntimeError("SC2 not found"),
        ):
            daemon._should_train = should_train_once  # type: ignore[assignment]
            daemon.start()
            time.sleep(3)
            # Daemon should still be alive
            assert daemon.is_running()
            daemon.stop()

        status = daemon.get_status()
        assert status["last_error"] is not None
        assert "SC2 not found" in status["last_error"]


# ------------------------------------------------------------------
# API endpoint tests
# ------------------------------------------------------------------


@pytest.fixture()
def daemon_client(tmp_path: Path) -> TestClient:
    """Create a test client with daemon configured."""
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"
    replay_dir = tmp_path / "replays"
    data_dir.mkdir()
    log_dir.mkdir()
    replay_dir.mkdir()
    configure(data_dir, log_dir, replay_dir, daemon_config=DaemonConfig())
    return TestClient(app)


class TestDaemonEndpoints:
    def test_daemon_status(self, daemon_client: TestClient) -> None:
        resp = daemon_client.get("/api/training/daemon")
        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is False
        assert data["state"] == "idle"

    def test_start_daemon(self, daemon_client: TestClient) -> None:
        resp = daemon_client.post("/api/training/start", json={})
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"

        # Verify running
        status = daemon_client.get("/api/training/daemon").json()
        assert status["running"] is True

        # Stop for cleanup
        daemon_client.post("/api/training/stop")

    def test_start_already_running(self, daemon_client: TestClient) -> None:
        daemon_client.post("/api/training/start", json={})
        resp = daemon_client.post("/api/training/start", json={})
        assert resp.json()["status"] == "already_running"
        daemon_client.post("/api/training/stop")

    def test_stop_not_running(self, daemon_client: TestClient) -> None:
        resp = daemon_client.post("/api/training/stop")
        assert resp.json()["status"] == "not_running"

    def test_stop_running_daemon(self, daemon_client: TestClient) -> None:
        daemon_client.post("/api/training/start", json={})
        resp = daemon_client.post("/api/training/stop")
        assert resp.json()["status"] == "stopped"

        status = daemon_client.get("/api/training/daemon").json()
        assert status["running"] is False


# ------------------------------------------------------------------
# Trigger logic tests (mocked DB)
# ------------------------------------------------------------------


def _seed_transitions(db_path: Path, count: int) -> None:
    """Create a DB with *count* transitions and 1 game."""
    from bots.v0.learning.database import TrainingDB

    db = TrainingDB(db_path)
    db.store_game("g0", "Simple64", 1, "win", 300.0, 5.0, "v1")
    state = np.zeros(40, dtype=np.float32)
    for i in range(count):
        db.store_transition("g0", i, float(i), state, 0, 0.1)
    db.close()


class TestTriggerLogic:
    """Unit tests for _should_train / _evaluate_triggers."""

    def test_no_db_file(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        daemon = TrainingDaemon(settings, DaemonConfig(min_transitions=10))
        state = daemon.get_trigger_state()
        assert state["would_trigger"] is False
        assert "no database" in state["reason"]

    def test_zero_transitions(self, tmp_path: Path) -> None:
        """DB exists but has 0 transitions -> never trigger."""
        from bots.v0.learning.database import TrainingDB

        settings = _make_settings(tmp_path)
        db_path = settings.data_dir / "training.db"
        db = TrainingDB(db_path)
        db.close()

        daemon = TrainingDaemon(settings, DaemonConfig(min_transitions=10))
        state = daemon.get_trigger_state()
        assert state["would_trigger"] is False
        assert state["transitions_since_last"] == 0

    def test_transition_count_trigger(self, tmp_path: Path) -> None:
        """Enough new transitions triggers training."""
        settings = _make_settings(tmp_path)
        _seed_transitions(settings.data_dir / "training.db", 20)

        daemon = TrainingDaemon(
            settings,
            DaemonConfig(min_transitions=10, min_hours_since_last=9999.0),
        )
        state = daemon.get_trigger_state()
        assert state["would_trigger"] is True
        assert "transition count trigger" in state["reason"]
        assert state["transitions_since_last"] == 20

    def test_transition_count_not_enough(self, tmp_path: Path) -> None:
        """Not enough new transitions and time threshold not met."""
        settings = _make_settings(tmp_path)
        _seed_transitions(settings.data_dir / "training.db", 5)

        daemon = TrainingDaemon(
            settings,
            DaemonConfig(min_transitions=10, min_hours_since_last=9999.0),
        )
        # Simulate a recent run so the time trigger won't fire
        daemon._last_run_time = datetime.now(UTC)
        state = daemon.get_trigger_state()
        assert state["would_trigger"] is False
        assert state["transitions_since_last"] == 5

    def test_time_trigger_first_run(self, tmp_path: Path) -> None:
        """First run (last_run_time=datetime.min) always triggers if transitions exist."""
        settings = _make_settings(tmp_path)
        _seed_transitions(settings.data_dir / "training.db", 3)

        daemon = TrainingDaemon(
            settings,
            DaemonConfig(min_transitions=9999, min_hours_since_last=1.0),
        )
        # _last_run_time defaults to datetime.min -> hours_since_last = inf
        state = daemon.get_trigger_state()
        assert state["would_trigger"] is True
        assert "time trigger" in state["reason"]

    def test_time_trigger_after_interval(self, tmp_path: Path) -> None:
        """Time trigger fires after enough hours since last run."""
        settings = _make_settings(tmp_path)
        _seed_transitions(settings.data_dir / "training.db", 3)

        daemon = TrainingDaemon(
            settings,
            DaemonConfig(min_transitions=9999, min_hours_since_last=0.5),
        )
        # Simulate last run 2 hours ago
        from datetime import timedelta

        daemon._last_run_time = datetime.now(UTC) - timedelta(hours=2)
        state = daemon.get_trigger_state()
        assert state["would_trigger"] is True
        assert "time trigger" in state["reason"]
        assert state["hours_since_last"] >= 2.0

    def test_safety_gate_training_active(self, tmp_path: Path) -> None:
        """Never trigger if training is already in progress."""
        settings = _make_settings(tmp_path)
        _seed_transitions(settings.data_dir / "training.db", 1000)

        daemon = TrainingDaemon(
            settings,
            DaemonConfig(min_transitions=10),
        )
        daemon._training_active = True
        state = daemon.get_trigger_state()
        assert state["would_trigger"] is False
        assert "already in progress" in state["reason"]

    def test_last_transition_count_updates_after_should_train(self, tmp_path: Path) -> None:
        """After _run_training, _last_transition_count is updated."""
        settings = _make_settings(tmp_path)
        _seed_transitions(settings.data_dir / "training.db", 50)

        daemon = TrainingDaemon(
            settings,
            DaemonConfig(min_transitions=10),
        )
        # First check: should trigger
        assert daemon._should_train() is True
        # Manually set last_transition_count as _run_training would
        daemon._last_transition_count = 50
        daemon._last_run_time = datetime.now(UTC)
        # Now it should not trigger (no new transitions, recent run)
        assert daemon._should_train() is False


class TestUpdateConfig:
    def test_update_known_fields(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        daemon = TrainingDaemon(settings, DaemonConfig())
        updated = daemon.update_config(
            {
                "min_transitions": 200,
                "cycles_per_run": 3,
            }
        )
        assert updated.min_transitions == 200
        assert updated.cycles_per_run == 3
        # Other fields unchanged
        assert updated.check_interval_seconds == 60

    def test_update_ignores_unknown_fields(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        daemon = TrainingDaemon(settings, DaemonConfig())
        updated = daemon.update_config({"bogus_field": 42, "min_transitions": 100})
        assert updated.min_transitions == 100


class TestExistingTrainingEndpointsStillWork:
    """Ensure old training endpoints still return 200."""

    def test_training_status(self, daemon_client: TestClient) -> None:
        resp = daemon_client.get("/api/training/status")
        assert resp.status_code == 200

    def test_training_history(self, daemon_client: TestClient) -> None:
        resp = daemon_client.get("/api/training/history")
        assert resp.status_code == 200

    def test_training_checkpoints(self, daemon_client: TestClient) -> None:
        resp = daemon_client.get("/api/training/checkpoints")
        assert resp.status_code == 200


# ------------------------------------------------------------------
# Trigger & config API endpoint tests
# ------------------------------------------------------------------


class TestTriggerEndpoint:
    def test_triggers_no_db(self, daemon_client: TestClient) -> None:
        resp = daemon_client.get("/api/training/triggers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["would_trigger"] is False
        assert isinstance(data["transitions_since_last"], int)
        assert isinstance(data["reason"], str)

    def test_triggers_with_transitions(self, daemon_client: TestClient, tmp_path: Path) -> None:
        _seed_transitions(tmp_path / "data" / "training.db", 600)
        resp = daemon_client.get("/api/training/triggers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["would_trigger"] is True
        assert data["transitions_since_last"] == 600


class TestDaemonConfigEndpoint:
    def test_update_config(self, daemon_client: TestClient) -> None:
        resp = daemon_client.put(
            "/api/training/daemon/config",
            json={"min_transitions": 200, "cycles_per_run": 3},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"
        assert data["config"]["min_transitions"] == 200
        assert data["config"]["cycles_per_run"] == 3

    def test_update_config_ignores_unknown(self, daemon_client: TestClient) -> None:
        resp = daemon_client.put(
            "/api/training/daemon/config",
            json={"bogus": 42, "min_transitions": 100},
        )
        assert resp.status_code == 200
        assert resp.json()["config"]["min_transitions"] == 100

    def test_config_reflected_in_status(self, daemon_client: TestClient) -> None:
        daemon_client.put(
            "/api/training/daemon/config",
            json={"games_per_cycle": 20},
        )
        status = daemon_client.get("/api/training/daemon").json()
        assert status["config"]["games_per_cycle"] == 20


# ------------------------------------------------------------------
# Runner --daemon flag test
# ------------------------------------------------------------------


class TestRunnerDaemonFlag:
    def test_daemon_flag_exists(self) -> None:
        from bots.v0.runner import build_parser

        parser = build_parser()
        args = parser.parse_args(["--serve", "--daemon"])
        assert args.daemon is True

    def test_daemon_flag_default_false(self) -> None:
        from bots.v0.runner import build_parser

        parser = build_parser()
        args = parser.parse_args(["--serve"])
        assert args.daemon is False


# ------------------------------------------------------------------
# Curriculum persistence tests
# ------------------------------------------------------------------


class TestCurriculumConfig:
    def test_defaults_include_curriculum_fields(self) -> None:
        cfg = DaemonConfig()
        assert cfg.current_difficulty == 1
        assert cfg.max_difficulty == 10
        assert cfg.win_rate_threshold == 0.8

    def test_save_and_load_curriculum(self, tmp_path: Path) -> None:
        path = tmp_path / "cfg.json"
        original = DaemonConfig(current_difficulty=3, max_difficulty=7, win_rate_threshold=0.75)
        save_daemon_config(original, path)
        loaded = load_daemon_config(path)
        assert loaded.current_difficulty == 3
        assert loaded.max_difficulty == 7
        assert loaded.win_rate_threshold == 0.75


class TestCurriculumPersistenceAcrossRestarts:
    """Curriculum state persists in daemon_config.json across daemon restarts."""

    def test_difficulty_persisted_after_training(self, tmp_path: Path) -> None:
        """After a training run, final difficulty is written to daemon_config.json."""
        settings = _make_settings(tmp_path)
        cfg = DaemonConfig(check_interval_seconds=1, current_difficulty=2, max_difficulty=10)
        daemon = TrainingDaemon(settings, cfg)

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.return_value = {
            "cycles_completed": 1,
            "final_difficulty": 4,
            "cycle_results": [],
            "total_games": 10,
            "stopped": False,
            "stop_reason": "",
        }

        with patch(
            "bots.v0.learning.trainer.TrainingOrchestrator",
            return_value=mock_orchestrator,
        ):
            daemon._run_training()

        # Config should be updated in memory
        assert daemon._config.current_difficulty == 4

        # Config should be persisted to disk
        config_path = settings.data_dir / "daemon_config.json"
        assert config_path.exists()
        loaded = load_daemon_config(config_path)
        assert loaded.current_difficulty == 4

    def test_difficulty_loaded_on_new_daemon(self, tmp_path: Path) -> None:
        """A new daemon picks up persisted difficulty from config file."""
        settings = _make_settings(tmp_path)
        config_path = settings.data_dir / "daemon_config.json"
        save_daemon_config(
            DaemonConfig(current_difficulty=5, max_difficulty=10),
            config_path,
        )

        loaded_cfg = load_daemon_config(config_path)
        daemon = TrainingDaemon(settings, loaded_cfg)
        assert daemon._config.current_difficulty == 5

    def test_orchestrator_receives_daemon_difficulty(self, tmp_path: Path) -> None:
        """TrainingOrchestrator is created with the daemon's curriculum state."""
        settings = _make_settings(tmp_path)
        cfg = DaemonConfig(
            check_interval_seconds=1,
            current_difficulty=3,
            max_difficulty=8,
            win_rate_threshold=0.75,
        )
        daemon = TrainingDaemon(settings, cfg)

        captured_kwargs: dict[str, Any] = {}

        class FakeOrchestrator:
            def __init__(self, **kwargs: Any) -> None:
                captured_kwargs.update(kwargs)

            def run(self, **_: Any) -> dict[str, Any]:
                return {
                    "cycles_completed": 0,
                    "final_difficulty": 3,
                    "cycle_results": [],
                    "total_games": 0,
                    "stopped": False,
                    "stop_reason": "",
                }

        with patch(
            "bots.v0.learning.trainer.TrainingOrchestrator",
            FakeOrchestrator,
        ):
            daemon._run_training()

        assert captured_kwargs["initial_difficulty"] == 3
        assert captured_kwargs["max_difficulty"] == 8
        assert captured_kwargs["win_rate_threshold"] == 0.75


class TestCurriculumAwarePromotion:
    """When a model is promoted with high win rate, difficulty auto-advances."""

    def test_auto_advance_on_promotion(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        cfg = DaemonConfig(
            check_interval_seconds=1,
            current_difficulty=3,
            max_difficulty=10,
            win_rate_threshold=0.8,
        )
        daemon = TrainingDaemon(settings, cfg)

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.return_value = {
            "cycles_completed": 1,
            "final_difficulty": 3,
            "cycle_results": [
                {"checkpoint": "v5", "difficulty": 3, "win_rate": 0.85},
            ],
            "total_games": 10,
            "stopped": False,
            "stop_reason": "",
        }

        from bots.v0.learning.evaluator import EvalResult

        mock_decision = MagicMock()
        mock_decision.promoted = True
        mock_decision.reason = "better"
        mock_decision.new_eval = EvalResult(
            checkpoint="v5",
            games_played=20,
            wins=17,
            losses=3,
            crashed=0,
            win_rate=0.85,
            avg_reward=1.0,
            avg_duration=300.0,
            difficulty=3,
            action_distribution=None,
        )

        mock_pm = MagicMock()
        mock_pm.evaluate_and_promote.return_value = mock_decision
        daemon._promotion_manager = mock_pm

        with patch(
            "bots.v0.learning.trainer.TrainingOrchestrator",
            return_value=mock_orchestrator,
        ):
            daemon._run_training()

        # Difficulty should have advanced from 3 to 4
        assert daemon._config.current_difficulty == 4
        assert daemon._last_advancement is not None

        # Should be logged in promotion_history.json
        history_path = settings.data_dir / "promotion_history.json"
        assert history_path.exists()
        entries = json.loads(history_path.read_text(encoding="utf-8"))
        advancement_entries = [e for e in entries if e.get("type") == "curriculum_advancement"]
        assert len(advancement_entries) == 1
        assert advancement_entries[0]["old_difficulty"] == 3
        assert advancement_entries[0]["new_difficulty"] == 4

    def test_no_advance_below_threshold(self, tmp_path: Path) -> None:
        """Difficulty does not advance if win rate is below threshold."""
        settings = _make_settings(tmp_path)
        cfg = DaemonConfig(
            check_interval_seconds=1,
            current_difficulty=3,
            max_difficulty=10,
            win_rate_threshold=0.8,
        )
        daemon = TrainingDaemon(settings, cfg)

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.return_value = {
            "cycles_completed": 1,
            "final_difficulty": 3,
            "cycle_results": [
                {"checkpoint": "v5", "difficulty": 3, "win_rate": 0.6},
            ],
            "total_games": 10,
            "stopped": False,
            "stop_reason": "",
        }

        from bots.v0.learning.evaluator import EvalResult

        mock_decision = MagicMock()
        mock_decision.promoted = True
        mock_decision.reason = "better"
        mock_decision.new_eval = EvalResult(
            checkpoint="v5",
            games_played=20,
            wins=12,
            losses=8,
            crashed=0,
            win_rate=0.6,
            avg_reward=1.0,
            avg_duration=300.0,
            difficulty=3,
            action_distribution=None,
        )

        mock_pm = MagicMock()
        mock_pm.evaluate_and_promote.return_value = mock_decision
        daemon._promotion_manager = mock_pm

        with patch(
            "bots.v0.learning.trainer.TrainingOrchestrator",
            return_value=mock_orchestrator,
        ):
            daemon._run_training()

        # Difficulty should NOT have advanced
        assert daemon._config.current_difficulty == 3
        assert daemon._last_advancement is None

    def test_no_advance_at_max(self, tmp_path: Path) -> None:
        """Difficulty does not advance if already at max."""
        settings = _make_settings(tmp_path)
        cfg = DaemonConfig(
            check_interval_seconds=1,
            current_difficulty=10,
            max_difficulty=10,
            win_rate_threshold=0.8,
        )
        daemon = TrainingDaemon(settings, cfg)

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.return_value = {
            "cycles_completed": 1,
            "final_difficulty": 10,
            "cycle_results": [
                {"checkpoint": "v5", "difficulty": 10, "win_rate": 0.9},
            ],
            "total_games": 10,
            "stopped": False,
            "stop_reason": "",
        }

        from bots.v0.learning.evaluator import EvalResult

        mock_decision = MagicMock()
        mock_decision.promoted = True
        mock_decision.reason = "better"
        mock_decision.new_eval = EvalResult(
            checkpoint="v5",
            games_played=20,
            wins=18,
            losses=2,
            crashed=0,
            win_rate=0.9,
            avg_reward=1.0,
            avg_duration=300.0,
            difficulty=10,
            action_distribution=None,
        )

        mock_pm = MagicMock()
        mock_pm.evaluate_and_promote.return_value = mock_decision
        daemon._promotion_manager = mock_pm

        with patch(
            "bots.v0.learning.trainer.TrainingOrchestrator",
            return_value=mock_orchestrator,
        ):
            daemon._run_training()

        # Difficulty should stay at max
        assert daemon._config.current_difficulty == 10


class TestDifficultyRevertOnRollback:
    """Difficulty reverts to the rolled-back model's training difficulty."""

    def test_difficulty_reverts_on_rollback(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        cfg = DaemonConfig(
            check_interval_seconds=1,
            current_difficulty=5,
            max_difficulty=10,
        )
        daemon = TrainingDaemon(settings, cfg)

        # Write promotion history so _get_model_difficulty can find v3's diff
        history_path = settings.data_dir / "promotion_history.json"
        history_path.write_text(
            json.dumps(
                [
                    {
                        "timestamp": "2026-01-01T00:00:00+00:00",
                        "new_checkpoint": "v3",
                        "old_best": "v2",
                        "new_win_rate": 0.7,
                        "old_win_rate": 0.5,
                        "promoted": True,
                        "reason": "better",
                        "difficulty": 3,
                    }
                ]
            ),
            encoding="utf-8",
        )

        # Set up checkpoint manifest
        cp_dir = settings.data_dir / "checkpoints"
        cp_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "checkpoints": [],
            "best": "v5",
            "previous_best": "v3",
        }
        (cp_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.return_value = {
            "cycles_completed": 1,
            "final_difficulty": 5,
            "cycle_results": [],
            "total_games": 10,
            "stopped": False,
            "stop_reason": "",
        }

        from bots.v0.learning.rollback import RollbackDecision

        mock_rollback_decision = RollbackDecision(
            current_model="v5",
            revert_to="v3",
            current_win_rate=0.2,
            promotion_win_rate=0.8,
            games_played=15,
            reason="regression detected",
        )

        mock_monitor = MagicMock()
        mock_monitor.check_for_regression.return_value = mock_rollback_decision
        daemon._rollback_monitor = mock_monitor

        with patch(
            "bots.v0.learning.trainer.TrainingOrchestrator",
            return_value=mock_orchestrator,
        ):
            daemon._run_training()

        # Difficulty should have reverted to v3's difficulty (3)
        assert daemon._config.current_difficulty == 3

        # Persisted to disk
        config_path = settings.data_dir / "daemon_config.json"
        loaded = load_daemon_config(config_path)
        assert loaded.current_difficulty == 3

    def test_no_revert_if_no_history(self, tmp_path: Path) -> None:
        """If no promotion history exists for the revert target, difficulty stays."""
        settings = _make_settings(tmp_path)
        cfg = DaemonConfig(
            check_interval_seconds=1,
            current_difficulty=5,
            max_difficulty=10,
        )
        daemon = TrainingDaemon(settings, cfg)

        # No promotion history file
        cp_dir = settings.data_dir / "checkpoints"
        cp_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "checkpoints": [],
            "best": "v5",
            "previous_best": "v3",
        }
        (cp_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.return_value = {
            "cycles_completed": 1,
            "final_difficulty": 5,
            "cycle_results": [],
            "total_games": 10,
            "stopped": False,
            "stop_reason": "",
        }

        from bots.v0.learning.rollback import RollbackDecision

        mock_monitor = MagicMock()
        mock_monitor.check_for_regression.return_value = RollbackDecision(
            current_model="v5",
            revert_to="v3",
            current_win_rate=0.2,
            promotion_win_rate=0.8,
            games_played=15,
            reason="regression",
        )
        daemon._rollback_monitor = mock_monitor

        with patch(
            "bots.v0.learning.trainer.TrainingOrchestrator",
            return_value=mock_orchestrator,
        ):
            daemon._run_training()

        # Difficulty unchanged (no history to look up)
        assert daemon._config.current_difficulty == 5


# ------------------------------------------------------------------
# Curriculum API endpoint tests
# ------------------------------------------------------------------


class TestCurriculumEndpoints:
    def test_get_curriculum_defaults(self, daemon_client: TestClient) -> None:
        resp = daemon_client.get("/api/training/curriculum")
        assert resp.status_code == 200
        data = resp.json()
        assert data["current_difficulty"] == 1
        assert data["max_difficulty"] == 10
        assert data["win_rate_threshold"] == 0.8
        assert data["last_advancement"] is None

    def test_put_curriculum(self, daemon_client: TestClient) -> None:
        resp = daemon_client.put(
            "/api/training/curriculum",
            json={"current_difficulty": 5, "max_difficulty": 8},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["current_difficulty"] == 5
        assert data["max_difficulty"] == 8

        # Verify reflected in GET
        resp2 = daemon_client.get("/api/training/curriculum")
        assert resp2.json()["current_difficulty"] == 5
        assert resp2.json()["max_difficulty"] == 8

    def test_put_curriculum_partial(self, daemon_client: TestClient) -> None:
        """Only specified fields are updated."""
        daemon_client.put(
            "/api/training/curriculum",
            json={"current_difficulty": 3},
        )
        resp = daemon_client.get("/api/training/curriculum")
        data = resp.json()
        assert data["current_difficulty"] == 3
        # Others remain at defaults
        assert data["max_difficulty"] == 10

    def test_put_curriculum_persists(self, daemon_client: TestClient, tmp_path: Path) -> None:
        """PUT persists to daemon_config.json on disk."""
        daemon_client.put(
            "/api/training/curriculum",
            json={"current_difficulty": 7},
        )
        config_path = tmp_path / "data" / "daemon_config.json"
        assert config_path.exists()
        loaded = load_daemon_config(config_path)
        assert loaded.current_difficulty == 7


class TestCrashedCyclesSkipPromotion:
    """Phase 4.5 Step 2 finding F2 regression guard.

    The daemon must NOT run the promotion gate against a crashed cycle.
    Crashed cycles have ``status="crashed"`` and no ``checkpoint`` key;
    feeding them to the evaluator would launch real SC2 games against an
    unchanged model and silently advance the curriculum.
    """

    def test_promotion_skipped_when_only_crashed_cycles(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        cfg = DaemonConfig(current_difficulty=3)
        daemon = TrainingDaemon(settings, cfg)

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.return_value = {
            "cycles_completed": 2,
            "final_difficulty": 3,
            "cycle_results": [
                {
                    "cycle": 1,
                    "difficulty": 3,
                    "status": "crashed",
                    "error": "ValueError: space mismatch",
                },
                {
                    "cycle": 2,
                    "difficulty": 3,
                    "status": "crashed",
                    "error": "ValueError: space mismatch",
                },
            ],
            "total_games": 0,
            "stopped": False,
            "stop_reason": "",
        }

        mock_pm = MagicMock()
        daemon._promotion_manager = mock_pm

        with patch(
            "bots.v0.learning.trainer.TrainingOrchestrator",
            return_value=mock_orchestrator,
        ):
            daemon._run_training()

        # Promotion gate must NOT have been called
        mock_pm.evaluate_and_promote.assert_not_called()
        # Curriculum must NOT have advanced
        assert daemon._config.current_difficulty == 3

    def test_promotion_uses_latest_successful_cycle(self, tmp_path: Path) -> None:
        """If a run has [success, crash], promotion runs against the success."""
        settings = _make_settings(tmp_path)
        cfg = DaemonConfig(current_difficulty=2)
        daemon = TrainingDaemon(settings, cfg)

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.return_value = {
            "cycles_completed": 2,
            "final_difficulty": 2,
            "cycle_results": [
                {
                    "cycle": 1,
                    "checkpoint": "v7",
                    "difficulty": 2,
                    "win_rate": 0.5,
                },
                {
                    "cycle": 2,
                    "difficulty": 2,
                    "status": "crashed",
                    "error": "boom",
                },
            ],
            "total_games": 5,
            "stopped": False,
            "stop_reason": "",
        }

        from bots.v0.learning.evaluator import EvalResult

        mock_decision = MagicMock()
        mock_decision.promoted = False
        mock_decision.reason = "not better"
        mock_decision.new_eval = EvalResult(
            checkpoint="v7",
            games_played=20,
            wins=10,
            losses=10,
            crashed=0,
            win_rate=0.5,
            avg_reward=0.0,
            avg_duration=300.0,
            difficulty=2,
            action_distribution=None,
        )
        mock_pm = MagicMock()
        mock_pm.evaluate_and_promote.return_value = mock_decision
        daemon._promotion_manager = mock_pm

        with patch(
            "bots.v0.learning.trainer.TrainingOrchestrator",
            return_value=mock_orchestrator,
        ):
            daemon._run_training()

        # Promotion gate called against the SUCCESSFUL checkpoint, not the
        # crashed one (which has no checkpoint key at all)
        assert mock_pm.evaluate_and_promote.call_count == 1
        call = mock_pm.evaluate_and_promote.call_args
        assert call.args == ("v7", 2)
        # Daemon's stop_event.is_set is threaded through so POST
        # /api/training/stop halts in-flight promotion evals too.
        assert call.kwargs.get("cancel_check") == daemon._stop_event.is_set


class TestAllCrashedTrainingRunIsFailure:
    """Phase 4.5 #71 regression guard.

    When every cycle of a training run crashes, the daemon must report
    the run as a failure — it must NOT increment ``_runs_completed``, it
    MUST set ``_last_error`` to a descriptive string, log at ERROR
    level, and the record MUST reach ``ErrorLogBuffer`` so
    ``/api/training/status.recent_errors`` surfaces a daemon-level
    entry. See issue #71 for the full root cause analysis.
    """

    def _install_buffer(self) -> tuple[Any, Any]:
        """Install a fresh handler + buffer on the root logger.

        Returns ``(buffer, handler)`` so the caller can assert against
        the buffer and detach the handler in a ``finally`` clause.
        The module-level singleton in ``error_log`` is shared across
        tests, so we attach a local handler bound to a *new* buffer
        instance to avoid cross-test contamination.
        """
        from bots.v0.error_log import ErrorLogBuffer, _ErrorBufferHandler

        buffer = ErrorLogBuffer()
        handler = _ErrorBufferHandler(buffer)
        logging.getLogger().addHandler(handler)
        return buffer, handler

    def test_all_crashed_run_does_not_increment_runs_completed(self, tmp_path: Path) -> None:
        """Acceptance criterion 1: the three-part daemon-level failure.

        - ``_runs_completed`` MUST NOT advance
        - ``_last_error`` MUST name the crash count and first error
        - an ERROR record MUST reach the ``ErrorLogBuffer``
        """
        settings = _make_settings(tmp_path)
        cfg = DaemonConfig(current_difficulty=3)
        daemon = TrainingDaemon(settings, cfg)
        assert daemon._runs_completed == 0

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.return_value = {
            "cycles_completed": 5,
            "final_difficulty": 3,
            "total_games": 0,
            "stopped": False,
            "stop_reason": "",
            "cycle_results": [
                {
                    "cycle": i + 1,
                    "difficulty": 3,
                    "status": "crashed",
                    "error": "ValueError: Observation spaces do not match",
                }
                for i in range(5)
            ],
        }

        buffer, handler = self._install_buffer()
        try:
            with patch(
                "bots.v0.learning.trainer.TrainingOrchestrator",
                return_value=mock_orchestrator,
            ):
                daemon._run_training()
        finally:
            logging.getLogger().removeHandler(handler)

        # (1) runs_completed did NOT tick up.
        assert daemon._runs_completed == 0

        # (2) last_error is a non-empty string naming the crash count
        # and carrying the first per-cycle error message.
        assert daemon._last_error is not None
        assert "5" in daemon._last_error
        assert "crashed" in daemon._last_error.lower()
        assert "Observation spaces do not match" in daemon._last_error

        # (3) an ERROR-level record reached ErrorLogBuffer with the
        # same message (not merely the per-cycle trainer.py exceptions).
        total, records = buffer.snapshot()
        assert total >= 1
        daemon_errors = [r for r in records if r["logger"] == "bots.v0.learning.daemon"]
        assert len(daemon_errors) == 1, f"expected one daemon-level ERROR; got {daemon_errors}"
        assert daemon_errors[0]["level"] == "ERROR"
        assert "Observation spaces do not match" in daemon_errors[0]["message"]

        # Status surface reports the failure.
        status = daemon.get_status()
        assert status["runs_completed"] == 0
        assert status["last_error"] is not None
        assert "5" in status["last_error"]

    def test_all_crashed_run_does_not_call_promotion_gate(self, tmp_path: Path) -> None:
        """All-crashed guard still skips the promotion gate (#67 / #71)."""
        settings = _make_settings(tmp_path)
        daemon = TrainingDaemon(settings, DaemonConfig(current_difficulty=2))

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.return_value = {
            "cycles_completed": 2,
            "final_difficulty": 2,
            "total_games": 0,
            "stopped": False,
            "stop_reason": "",
            "cycle_results": [
                {"cycle": 1, "difficulty": 2, "status": "crashed", "error": "boom"},
                {"cycle": 2, "difficulty": 2, "status": "crashed", "error": "boom"},
            ],
        }
        mock_pm = MagicMock()
        daemon._promotion_manager = mock_pm

        buffer, handler = self._install_buffer()
        try:
            with patch(
                "bots.v0.learning.trainer.TrainingOrchestrator",
                return_value=mock_orchestrator,
            ):
                daemon._run_training()
        finally:
            logging.getLogger().removeHandler(handler)

        mock_pm.evaluate_and_promote.assert_not_called()
        assert daemon._runs_completed == 0

    def test_successful_run_clears_last_error_and_increments_counter(self, tmp_path: Path) -> None:
        """Acceptance criterion 5: unchanged behaviour for a good run.

        Regression guard: confirm the all-crashed branch did not
        accidentally break the success path.
        """
        settings = _make_settings(tmp_path)
        daemon = TrainingDaemon(settings, DaemonConfig(current_difficulty=2))
        # Pre-seed a stale error to prove it is cleared on success.
        daemon._last_error = "stale error from a previous run"

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.return_value = {
            "cycles_completed": 1,
            "final_difficulty": 2,
            "total_games": 10,
            "stopped": False,
            "stop_reason": "",
            "cycle_results": [
                {"cycle": 1, "checkpoint": "v9", "difficulty": 2, "win_rate": 0.5},
            ],
        }

        from bots.v0.learning.evaluator import EvalResult

        mock_decision = MagicMock()
        mock_decision.promoted = False
        mock_decision.reason = "not better"
        mock_decision.new_eval = EvalResult(
            checkpoint="v9",
            games_played=10,
            wins=5,
            losses=5,
            crashed=0,
            win_rate=0.5,
            avg_reward=0.0,
            avg_duration=300.0,
            difficulty=2,
            action_distribution=None,
        )
        mock_pm = MagicMock()
        mock_pm.evaluate_and_promote.return_value = mock_decision
        daemon._promotion_manager = mock_pm

        with patch(
            "bots.v0.learning.trainer.TrainingOrchestrator",
            return_value=mock_orchestrator,
        ):
            daemon._run_training()

        assert daemon._runs_completed == 1
        assert daemon._last_error is None
        assert daemon._last_result is not None
        assert daemon._last_result["cycles_completed"] == 1

    def test_empty_cycle_results_with_zero_cycles_treated_as_failure(self, tmp_path: Path) -> None:
        """Acceptance criterion 3: empty cycle_results + cycles_completed=0.

        Documented choice: when the orchestrator returns before any
        cycle started (no per-cycle breakdown, 0 cycles completed),
        treat it as all-crashed so the daemon-level failure is
        observable rather than silently advancing the runs counter.
        """
        settings = _make_settings(tmp_path)
        daemon = TrainingDaemon(settings, DaemonConfig(current_difficulty=2))

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.return_value = {
            "cycles_completed": 0,
            "final_difficulty": 2,
            "total_games": 0,
            "stopped": True,
            "stop_reason": "early failure",
            "cycle_results": [],
        }

        buffer, handler = self._install_buffer()
        try:
            with patch(
                "bots.v0.learning.trainer.TrainingOrchestrator",
                return_value=mock_orchestrator,
            ):
                daemon._run_training()
        finally:
            logging.getLogger().removeHandler(handler)

        assert daemon._runs_completed == 0
        assert daemon._last_error is not None

        _, records = buffer.snapshot()
        daemon_errors = [r for r in records if r["logger"] == "bots.v0.learning.daemon"]
        assert len(daemon_errors) == 1
        assert daemon_errors[0]["level"] == "ERROR"


class TestWatchdogPerCycleCrashVisibility:
    """Issue #73 regression guard.

    While ``TrainingOrchestrator.run(...)`` is still iterating (e.g. stuck
    inside SB3's ``.learn()`` retry loop) the #71 post-orchestrator
    bookkeeping cannot run, which left ``daemon._last_error`` at ``None``
    even while per-cycle ERROR log records piled up in
    ``ErrorLogBuffer``. A short-lived watchdog thread spawned inside
    ``_run_training`` now surfaces the failure mid-training so the Alerts
    tab and ``/api/training/daemon`` reflect reality within bounded time.
    """

    def _install_buffer(self) -> tuple[Any, Any]:
        """Install a fresh handler on the PROCESS-WIDE singleton buffer.

        The watchdog reads the module-level singleton returned by
        ``get_error_log_buffer()`` — not a test-local instance — so
        tests must emit through the same buffer the watchdog polls.
        ``buffer.reset()`` is safe because the singleton is isolated
        to this test process and we restore state in ``finally``.
        """
        from bots.v0.error_log import _ErrorBufferHandler, get_error_log_buffer

        buffer = get_error_log_buffer()
        buffer.reset()
        handler = _ErrorBufferHandler(buffer)
        logging.getLogger().addHandler(handler)
        return buffer, handler

    def test_watchdog_surfaces_per_cycle_errors_during_orchestrator_run(
        self, tmp_path: Path
    ) -> None:
        """Acceptance criterion #1: while orchestrator.run() is still
        iterating, the daemon must surface per-environment ERROR log
        records as a daemon-level ``_last_error`` within a bounded time.

        The test uses a fast watchdog poll interval (0.05s) and a long
        mock orchestrator (0.7s) so the watchdog has multiple opportunities
        to tick before the post-orchestrator bookkeeping runs.
        """
        settings = _make_settings(tmp_path)
        cfg = DaemonConfig(
            current_difficulty=3,
            watchdog_poll_seconds=0.05,
            watchdog_error_threshold=3,
        )
        daemon = TrainingDaemon(settings, cfg)

        per_cycle_log = logging.getLogger("bots.v0.learning.environment")

        observed_last_error: list[str | None] = []

        def fake_run(**_kwargs: Any) -> dict[str, Any]:
            # Emit enough per-cycle ERROR records to exceed the
            # threshold, then sleep long enough for the watchdog to
            # tick and surface the failure.
            for i in range(5):
                per_cycle_log.error("Game thread crashed (fake #%d)", i)
            # Busy-wait until either the watchdog sets _last_error OR
            # we've waited 2s (safety cap so the test cannot hang if
            # the watchdog is broken).
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                with daemon._lock:
                    current = daemon._last_error
                if current is not None:
                    observed_last_error.append(current)
                    break
                time.sleep(0.02)
            # Return a SUCCESSFUL result so post-orchestrator bookkeeping
            # does NOT overwrite the watchdog-set _last_error (the
            # all-crashed branch would clobber it; the success branch
            # clears it to None — which is the behaviour covered by the
            # other test below).
            return {
                "cycles_completed": 1,
                "final_difficulty": 3,
                "total_games": 10,
                "stopped": False,
                "stop_reason": "",
                "cycle_results": [
                    {
                        "cycle": 1,
                        "checkpoint": "v9",
                        "difficulty": 3,
                        "win_rate": 0.5,
                    }
                ],
            }

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.side_effect = fake_run

        # Suppress promotion gate side effects.
        mock_decision = MagicMock()
        mock_decision.promoted = False
        mock_decision.reason = "not better"
        from bots.v0.learning.evaluator import EvalResult

        mock_decision.new_eval = EvalResult(
            checkpoint="v9",
            games_played=10,
            wins=5,
            losses=5,
            crashed=0,
            win_rate=0.5,
            avg_reward=0.0,
            avg_duration=300.0,
            difficulty=3,
            action_distribution=None,
        )
        mock_pm = MagicMock()
        mock_pm.evaluate_and_promote.return_value = mock_decision
        daemon._promotion_manager = mock_pm

        buffer, handler = self._install_buffer()
        try:
            with patch(
                "bots.v0.learning.trainer.TrainingOrchestrator",
                return_value=mock_orchestrator,
            ):
                daemon._run_training()
        finally:
            logging.getLogger().removeHandler(handler)
            buffer.reset()

        # The watchdog saw _last_error set mid-training.
        assert observed_last_error, (
            "watchdog did not set _last_error while orchestrator.run() was still iterating"
        )
        watchdog_msg = observed_last_error[0]
        assert watchdog_msg is not None
        assert "Watchdog" in watchdog_msg
        assert "threshold" in watchdog_msg

    def test_watchdog_does_not_trigger_below_threshold(self, tmp_path: Path) -> None:
        """Emitting FEWER errors than the threshold must not cause the
        watchdog to set ``_last_error`` during a successful run."""
        settings = _make_settings(tmp_path)
        cfg = DaemonConfig(
            current_difficulty=2,
            watchdog_poll_seconds=0.05,
            watchdog_error_threshold=10,  # high threshold
        )
        daemon = TrainingDaemon(settings, cfg)

        per_cycle_log = logging.getLogger("bots.v0.learning.environment")

        def fake_run(**_kwargs: Any) -> dict[str, Any]:
            # Only emit 2 errors — well below the threshold of 10.
            per_cycle_log.error("Game thread crashed (below-threshold #1)")
            per_cycle_log.error("Game thread crashed (below-threshold #2)")
            # Let the watchdog tick a few times.
            time.sleep(0.3)
            return {
                "cycles_completed": 1,
                "final_difficulty": 2,
                "total_games": 10,
                "stopped": False,
                "stop_reason": "",
                "cycle_results": [
                    {
                        "cycle": 1,
                        "checkpoint": "v9",
                        "difficulty": 2,
                        "win_rate": 0.5,
                    }
                ],
            }

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.side_effect = fake_run

        mock_decision = MagicMock()
        mock_decision.promoted = False
        mock_decision.reason = "not better"
        from bots.v0.learning.evaluator import EvalResult

        mock_decision.new_eval = EvalResult(
            checkpoint="v9",
            games_played=10,
            wins=5,
            losses=5,
            crashed=0,
            win_rate=0.5,
            avg_reward=0.0,
            avg_duration=300.0,
            difficulty=2,
            action_distribution=None,
        )
        mock_pm = MagicMock()
        mock_pm.evaluate_and_promote.return_value = mock_decision
        daemon._promotion_manager = mock_pm

        buffer, handler = self._install_buffer()
        try:
            with patch(
                "bots.v0.learning.trainer.TrainingOrchestrator",
                return_value=mock_orchestrator,
            ):
                daemon._run_training()
        finally:
            logging.getLogger().removeHandler(handler)
            buffer.reset()

        # Below-threshold errors leave _last_error at None after
        # a successful run (post-orchestrator bookkeeping clears it).
        assert daemon._last_error is None
        assert daemon._runs_completed == 1

    def test_watchdog_post_training_bookkeeping_wins_on_all_crashed(self, tmp_path: Path) -> None:
        """Post-orchestrator bookkeeping is the authoritative final writer
        of ``_last_error``. When the orchestrator returns with every cycle
        crashed, the daemon's own bookkeeping message replaces whatever
        the watchdog set mid-training — there must not be two sources of
        truth fighting.
        """
        settings = _make_settings(tmp_path)
        cfg = DaemonConfig(
            current_difficulty=3,
            watchdog_poll_seconds=0.05,
            watchdog_error_threshold=1,
        )
        daemon = TrainingDaemon(settings, cfg)

        per_cycle_log = logging.getLogger("bots.v0.learning.environment")

        def fake_run(**_kwargs: Any) -> dict[str, Any]:
            per_cycle_log.error("Game thread crashed")
            per_cycle_log.error("Game thread crashed")
            # Wait long enough for the watchdog to fire (poll=0.05s,
            # threshold=1, so one tick is enough).
            time.sleep(0.2)
            return {
                "cycles_completed": 2,
                "final_difficulty": 3,
                "total_games": 0,
                "stopped": False,
                "stop_reason": "",
                "cycle_results": [
                    {
                        "cycle": 1,
                        "difficulty": 3,
                        "status": "crashed",
                        "error": "ValueError: Observation spaces do not match",
                    },
                    {
                        "cycle": 2,
                        "difficulty": 3,
                        "status": "crashed",
                        "error": "ValueError: Observation spaces do not match",
                    },
                ],
            }

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.side_effect = fake_run

        buffer, handler = self._install_buffer()
        try:
            with patch(
                "bots.v0.learning.trainer.TrainingOrchestrator",
                return_value=mock_orchestrator,
            ):
                daemon._run_training()
        finally:
            logging.getLogger().removeHandler(handler)
            buffer.reset()

        # Final _last_error is the #71 bookkeeping message, NOT the
        # watchdog message. The watchdog's words do not appear.
        assert daemon._last_error is not None
        assert "Observation spaces do not match" in daemon._last_error
        assert "Watchdog" not in daemon._last_error
        assert daemon._runs_completed == 0

    def test_watchdog_thread_cleans_up_after_run(self, tmp_path: Path) -> None:
        """The watchdog thread must have exited by the time
        ``_run_training`` returns; no orphaned threads."""
        settings = _make_settings(tmp_path)
        cfg = DaemonConfig(
            current_difficulty=2,
            watchdog_poll_seconds=0.05,
            watchdog_error_threshold=100,
        )
        daemon = TrainingDaemon(settings, cfg)

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.return_value = {
            "cycles_completed": 1,
            "final_difficulty": 2,
            "total_games": 10,
            "stopped": False,
            "stop_reason": "",
            "cycle_results": [
                {
                    "cycle": 1,
                    "checkpoint": "v9",
                    "difficulty": 2,
                    "win_rate": 0.5,
                }
            ],
        }

        mock_decision = MagicMock()
        mock_decision.promoted = False
        mock_decision.reason = "not better"
        from bots.v0.learning.evaluator import EvalResult

        mock_decision.new_eval = EvalResult(
            checkpoint="v9",
            games_played=10,
            wins=5,
            losses=5,
            crashed=0,
            win_rate=0.5,
            avg_reward=0.0,
            avg_duration=300.0,
            difficulty=2,
            action_distribution=None,
        )
        mock_pm = MagicMock()
        mock_pm.evaluate_and_promote.return_value = mock_decision
        daemon._promotion_manager = mock_pm

        buffer, handler = self._install_buffer()
        try:
            with patch(
                "bots.v0.learning.trainer.TrainingOrchestrator",
                return_value=mock_orchestrator,
            ):
                daemon._run_training()
        finally:
            logging.getLogger().removeHandler(handler)
            buffer.reset()

        # The handle was cleared by _stop_watchdog.
        assert daemon._watchdog_thread is None
        # No thread named "training-daemon-watchdog" is alive.
        live = [
            t
            for t in threading.enumerate()
            if t.name == "training-daemon-watchdog" and t.is_alive()
        ]
        assert live == [], f"watchdog thread(s) leaked: {live}"

    def test_watchdog_clears_stale_last_error_on_new_run(self, tmp_path: Path) -> None:
        """Issue #73 iter-2 (M1): a stale ``_last_error`` from a prior
        failed run must NOT block the watchdog on a subsequent run.

        The watchdog's "do not clobber a pre-existing error" guard
        would otherwise be dead-lettered by the previous pass's
        bookkeeping message, leaving the dashboard showing the OLD
        error while the NEW run silently accumulates failures. This
        is exactly the soak-3 scenario: retry after a previous
        all-crashed pass.
        """
        settings = _make_settings(tmp_path)
        cfg = DaemonConfig(
            current_difficulty=3,
            watchdog_poll_seconds=0.05,
            watchdog_error_threshold=3,
        )
        daemon = TrainingDaemon(settings, cfg)

        # Pre-seed a stale error string as if a prior run had failed.
        daemon._last_error = "stale error from prior run"

        per_cycle_log = logging.getLogger("bots.v0.learning.environment")

        observed_last_error: list[str | None] = []

        def fake_run(**_kwargs: Any) -> dict[str, Any]:
            for i in range(5):
                per_cycle_log.error("Game thread crashed (fake #%d)", i)
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                with daemon._lock:
                    current = daemon._last_error
                if current is not None and "Watchdog" in current:
                    observed_last_error.append(current)
                    break
                time.sleep(0.02)
            # Return a SUCCESSFUL result so post-orchestrator
            # bookkeeping does not clobber the observed watchdog msg.
            return {
                "cycles_completed": 1,
                "final_difficulty": 3,
                "total_games": 10,
                "stopped": False,
                "stop_reason": "",
                "cycle_results": [
                    {
                        "cycle": 1,
                        "checkpoint": "v9",
                        "difficulty": 3,
                        "win_rate": 0.5,
                    }
                ],
            }

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.side_effect = fake_run

        mock_decision = MagicMock()
        mock_decision.promoted = False
        mock_decision.reason = "not better"
        from bots.v0.learning.evaluator import EvalResult

        mock_decision.new_eval = EvalResult(
            checkpoint="v9",
            games_played=10,
            wins=5,
            losses=5,
            crashed=0,
            win_rate=0.5,
            avg_reward=0.0,
            avg_duration=300.0,
            difficulty=3,
            action_distribution=None,
        )
        mock_pm = MagicMock()
        mock_pm.evaluate_and_promote.return_value = mock_decision
        daemon._promotion_manager = mock_pm

        buffer, handler = self._install_buffer()
        try:
            with patch(
                "bots.v0.learning.trainer.TrainingOrchestrator",
                return_value=mock_orchestrator,
            ):
                daemon._run_training()
        finally:
            logging.getLogger().removeHandler(handler)
            buffer.reset()

        # The watchdog observed the NEW error message, not the stale one.
        assert observed_last_error, (
            "watchdog did not set _last_error on the new run; stale "
            "value from a prior run blocked the watchdog write"
        )
        watchdog_msg = observed_last_error[0]
        assert watchdog_msg is not None
        assert "Watchdog" in watchdog_msg
        assert "stale error from prior run" not in watchdog_msg

    def test_watchdog_is_one_shot_per_run(self, tmp_path: Path) -> None:
        """Issue #73 iter-2 (M4): once the watchdog has raised the
        alarm it must stop polling. Otherwise a stream of
        watchdog-generated ERROR records would feed back into the
        very buffer the watchdog is watching.
        """
        settings = _make_settings(tmp_path)
        cfg = DaemonConfig(
            current_difficulty=3,
            watchdog_poll_seconds=0.02,
            watchdog_error_threshold=3,
        )
        daemon = TrainingDaemon(settings, cfg)

        per_cycle_log = logging.getLogger("bots.v0.learning.environment")

        def fake_run(**_kwargs: Any) -> dict[str, Any]:
            # Emit well above threshold, then pause long enough for
            # multiple poll ticks so a non-one-shot watchdog would
            # fire several times.
            for i in range(10):
                per_cycle_log.error("Game thread crashed (one-shot #%d)", i)
            time.sleep(0.3)
            return {
                "cycles_completed": 1,
                "final_difficulty": 3,
                "total_games": 10,
                "stopped": False,
                "stop_reason": "",
                "cycle_results": [
                    {
                        "cycle": 1,
                        "checkpoint": "v9",
                        "difficulty": 3,
                        "win_rate": 0.5,
                    }
                ],
            }

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.side_effect = fake_run

        mock_decision = MagicMock()
        mock_decision.promoted = False
        mock_decision.reason = "not better"
        from bots.v0.learning.evaluator import EvalResult

        mock_decision.new_eval = EvalResult(
            checkpoint="v9",
            games_played=10,
            wins=5,
            losses=5,
            crashed=0,
            win_rate=0.5,
            avg_reward=0.0,
            avg_duration=300.0,
            difficulty=3,
            action_distribution=None,
        )
        mock_pm = MagicMock()
        mock_pm.evaluate_and_promote.return_value = mock_decision
        daemon._promotion_manager = mock_pm

        # Install a capturing handler on the daemon logger so we can
        # count how many watchdog-level ERROR records were emitted.
        daemon_log = logging.getLogger("bots.v0.learning.daemon")

        class _CaptureHandler(logging.Handler):
            def __init__(self) -> None:
                super().__init__(level=logging.ERROR)
                self.records: list[logging.LogRecord] = []

            def emit(self, record: logging.LogRecord) -> None:
                self.records.append(record)

        capture = _CaptureHandler()
        daemon_log.addHandler(capture)

        buffer, handler = self._install_buffer()
        try:
            with patch(
                "bots.v0.learning.trainer.TrainingOrchestrator",
                return_value=mock_orchestrator,
            ):
                daemon._run_training()
        finally:
            daemon_log.removeHandler(capture)
            logging.getLogger().removeHandler(handler)
            buffer.reset()

        watchdog_records = [r for r in capture.records if "Daemon watchdog:" in r.getMessage()]
        assert len(watchdog_records) == 1, (
            "watchdog fired more than once: expected exactly ONE "
            f"'Daemon watchdog:' ERROR record, got {len(watchdog_records)}"
        )

    def test_watchdog_exits_promptly_on_daemon_stop_event(self, tmp_path: Path) -> None:
        """Issue #73 iter-2 (M5): ``_watchdog_loop`` must exit promptly
        when the daemon-wide ``_stop_event`` is set, independently of
        the watchdog-specific stop event.
        """
        settings = _make_settings(tmp_path)
        cfg = DaemonConfig(
            current_difficulty=2,
            watchdog_poll_seconds=0.1,
            watchdog_error_threshold=1000,  # effectively never fires
        )
        daemon = TrainingDaemon(settings, cfg)

        buffer, handler = self._install_buffer()
        try:
            # Pretend training is active so _watchdog_loop does not
            # early-return on its ``_training_active`` check.
            daemon._training_active = True
            daemon._start_watchdog()

            # The watchdog should now be alive and sleeping in
            # ``_watchdog_stop_event.wait(timeout=0.1)``.
            assert daemon._watchdog_thread is not None
            assert daemon._watchdog_thread.is_alive()

            # Signal the DAEMON-WIDE stop event (not the watchdog
            # stop event — we are exercising the separate exit
            # branch at the top of the poll loop).
            start = time.monotonic()
            daemon._stop_event.set()

            # The watchdog should observe ``_stop_event`` within a
            # couple of poll ticks and return.
            daemon._watchdog_thread.join(timeout=0.5)
            elapsed = time.monotonic() - start

            assert not daemon._watchdog_thread.is_alive(), (
                "watchdog did not exit on daemon-wide _stop_event"
            )
            assert elapsed < 0.5, (
                f"watchdog took {elapsed:.3f}s to observe _stop_event (expected < 0.5s)"
            )
        finally:
            # Clean up: clear training_active and stop watchdog
            # explicitly so no leaked state bleeds into later tests.
            daemon._training_active = False
            daemon._stop_event.clear()
            daemon._stop_watchdog()
            logging.getLogger().removeHandler(handler)
            buffer.reset()

    def test_watchdog_fires_on_eval_phase_errors(self, tmp_path: Path) -> None:
        """Phase 4.7 Step 2 (#83): the watchdog's protected window now
        covers the eval / promotion-gate block as well as
        ``orchestrator.run``. ERROR-level log records emitted from inside
        ``PromotionManager.evaluate_and_promote`` must trip the watchdog
        and surface ``_last_error`` as a ``Watchdog:`` message.

        Soak-2026-04-11b showed the soak-era daemon accumulating 18
        backend errors during the eval phase while ``daemon.last_error``
        stayed ``None`` — the watchdog had already stopped polling
        before ``evaluate_and_promote`` ran. This test guards the wider
        window by letting ``orchestrator.run`` return cleanly and
        pushing the failures through a patched ``evaluate_and_promote``
        that emits ERRORs via the real Python logging API (not
        ``buffer.emit`` — Phase 4.5 #68 iter-2 lesson).
        """
        from bots.v0.learning.evaluator import EvalResult
        from bots.v0.learning.promotion import PromotionDecision

        settings = _make_settings(tmp_path)
        cfg = DaemonConfig(
            current_difficulty=3,
            watchdog_poll_seconds=0.05,
            watchdog_error_threshold=3,
        )
        daemon = TrainingDaemon(settings, cfg)

        promotion_log = logging.getLogger("bots.v0.learning.promotion")

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.return_value = {
            "cycles_completed": 1,
            "final_difficulty": 3,
            "total_games": 10,
            "stopped": False,
            "stop_reason": "",
            "cycle_results": [
                {
                    "cycle": 1,
                    "checkpoint": "v9",
                    "difficulty": 3,
                    "win_rate": 0.5,
                }
            ],
        }

        observed_last_error: list[str | None] = []

        def fake_evaluate_and_promote(
            _checkpoint: str, _difficulty: int, **_kw: Any
        ) -> Any:
            # Emit enough eval-phase ERROR records to exceed the
            # watchdog threshold, then busy-wait until either the
            # watchdog fires or the safety cap expires.
            for i in range(5):
                promotion_log.error("Eval game crashed (fake #%d)", i)
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                with daemon._lock:
                    current = daemon._last_error
                if current is not None and "Watchdog" in current:
                    observed_last_error.append(current)
                    break
                time.sleep(0.02)
            # Return a SUCCESSFUL promotion decision so post-orchestrator
            # bookkeeping does NOT clobber the watchdog-set _last_error.
            # (The success branch clears _last_error to None only if
            # all_crashed is False AND the watchdog has not set it —
            # but our bookkeeping block always clears on the success
            # branch, so the watchdog must win the race by being
            # joined BEFORE bookkeeping. That is the Phase 4.7 Step 2
            # invariant the widened window preserves.)
            return PromotionDecision(
                new_checkpoint="v9",
                old_best="none",
                new_eval=EvalResult(
                    checkpoint="v9",
                    games_played=10,
                    wins=5,
                    losses=5,
                    crashed=0,
                    win_rate=0.5,
                    avg_reward=0.0,
                    avg_duration=300.0,
                    difficulty=3,
                    action_distribution=None,
                ),
                old_eval=None,
                promoted=False,
                reason="not better",
                difficulty=3,
                reason_code="rejected_not_better",
            )

        mock_pm = MagicMock()
        mock_pm.evaluate_and_promote.side_effect = fake_evaluate_and_promote
        daemon._promotion_manager = mock_pm

        buffer, handler = self._install_buffer()
        try:
            with patch(
                "bots.v0.learning.trainer.TrainingOrchestrator",
                return_value=mock_orchestrator,
            ):
                daemon._run_training()
        finally:
            logging.getLogger().removeHandler(handler)
            buffer.reset()

        # The watchdog observed eval-phase errors mid-training and
        # set _last_error while ``evaluate_and_promote`` was still
        # running. That is the Phase 4.7 Step 2 invariant this test
        # guards — the final value of ``_last_error`` is then
        # overwritten by the post-orchestrator bookkeeping (the
        # success-branch clear, since our mock ``orchestrator.run``
        # returns a non-crashed result), exactly mirroring the
        # ``test_watchdog_surfaces_per_cycle_errors_during_orchestrator_run``
        # contract one class up. The mid-training surfacing is what
        # matters — operators hitting the Alerts tab during a live
        # soak see the failure within bounded time.
        assert observed_last_error, (
            "watchdog did not set _last_error during the eval/promotion "
            "phase; the Phase 4.7 Step 2 widened protected window is "
            "not covering evaluate_and_promote"
        )
        watchdog_msg = observed_last_error[0]
        assert watchdog_msg is not None
        assert "Watchdog" in watchdog_msg
        assert "threshold" in watchdog_msg

    def test_watchdog_silent_on_happy_path_eval(self, tmp_path: Path) -> None:
        """Phase 4.7 Step 2 (#83): false-positive guard. A normal eval
        with zero ERROR records must NOT set ``_last_error`` even though
        the watchdog is now polling across the full eval/promotion and
        rollback-check window. The happy-path audit posted at
        https://github.com/aberson/Alpha4Gate/issues/83#issuecomment-4229917132
        enumerated every ERROR site in the promotion + rollback path
        and confirmed zero of them fire on the success path; this test
        asserts that invariant end-to-end.
        """
        settings = _make_settings(tmp_path)
        cfg = DaemonConfig(
            current_difficulty=3,
            watchdog_poll_seconds=0.05,
            watchdog_error_threshold=3,
        )
        daemon = TrainingDaemon(settings, cfg)

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.return_value = {
            "cycles_completed": 1,
            "final_difficulty": 3,
            "total_games": 10,
            "stopped": False,
            "stop_reason": "",
            "cycle_results": [
                {
                    "cycle": 1,
                    "checkpoint": "v9",
                    "difficulty": 3,
                    "win_rate": 0.5,
                }
            ],
        }

        from bots.v0.learning.evaluator import EvalResult
        from bots.v0.learning.promotion import PromotionDecision

        happy_decision = PromotionDecision(
            new_checkpoint="v9",
            old_best="none",
            new_eval=EvalResult(
                checkpoint="v9",
                games_played=10,
                wins=5,
                losses=5,
                crashed=0,
                win_rate=0.5,
                avg_reward=0.0,
                avg_duration=300.0,
                difficulty=3,
                action_distribution=None,
            ),
            old_eval=None,
            promoted=True,
            reason="no previous best checkpoint",
            difficulty=3,
            reason_code="first_baseline",
        )
        mock_pm = MagicMock()
        mock_pm.evaluate_and_promote.return_value = happy_decision
        daemon._promotion_manager = mock_pm

        buffer, handler = self._install_buffer()
        try:
            with patch(
                "bots.v0.learning.trainer.TrainingOrchestrator",
                return_value=mock_orchestrator,
            ):
                daemon._run_training()
        finally:
            logging.getLogger().removeHandler(handler)
            buffer.reset()

        # Happy path: no ERROR records emitted, so the watchdog
        # never fires and the post-orchestrator bookkeeping clears
        # _last_error to None on the success branch.
        assert daemon._last_error is None
        assert daemon._runs_completed == 1
        # And no watchdog thread leaked across the widened window.
        assert daemon._watchdog_thread is None
