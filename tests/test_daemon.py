"""Tests for the training daemon."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from alpha4gate.api import app, configure
from alpha4gate.config import Settings
from alpha4gate.learning.daemon import (
    DaemonConfig,
    TrainingDaemon,
    load_daemon_config,
    save_daemon_config,
)

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
            "alpha4gate.learning.trainer.TrainingOrchestrator",
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
            "alpha4gate.learning.trainer.TrainingOrchestrator",
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
    from alpha4gate.learning.database import TrainingDB

    db = TrainingDB(db_path)
    db.store_game("g0", "Simple64", 1, "win", 300.0, 5.0, "v1")
    state = np.zeros(17, dtype=np.float32)
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
        from alpha4gate.learning.database import TrainingDB

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

    def test_last_transition_count_updates_after_should_train(
        self, tmp_path: Path
    ) -> None:
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
        updated = daemon.update_config({
            "min_transitions": 200,
            "cycles_per_run": 3,
        })
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

    def test_triggers_with_transitions(
        self, daemon_client: TestClient, tmp_path: Path
    ) -> None:
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
        from alpha4gate.runner import build_parser

        parser = build_parser()
        args = parser.parse_args(["--serve", "--daemon"])
        assert args.daemon is True

    def test_daemon_flag_default_false(self) -> None:
        from alpha4gate.runner import build_parser

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
        cfg = DaemonConfig(
            check_interval_seconds=1, current_difficulty=2, max_difficulty=10
        )
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
            "alpha4gate.learning.trainer.TrainingOrchestrator",
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
            "alpha4gate.learning.trainer.TrainingOrchestrator",
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

        from alpha4gate.learning.evaluator import EvalResult

        mock_decision = MagicMock()
        mock_decision.promoted = True
        mock_decision.reason = "better"
        mock_decision.new_eval = EvalResult(
            checkpoint="v5",
            games_played=20,
            wins=17,
            losses=3,
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
            "alpha4gate.learning.trainer.TrainingOrchestrator",
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
        advancement_entries = [
            e for e in entries if e.get("type") == "curriculum_advancement"
        ]
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

        from alpha4gate.learning.evaluator import EvalResult

        mock_decision = MagicMock()
        mock_decision.promoted = True
        mock_decision.reason = "better"
        mock_decision.new_eval = EvalResult(
            checkpoint="v5",
            games_played=20,
            wins=12,
            losses=8,
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
            "alpha4gate.learning.trainer.TrainingOrchestrator",
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

        from alpha4gate.learning.evaluator import EvalResult

        mock_decision = MagicMock()
        mock_decision.promoted = True
        mock_decision.reason = "better"
        mock_decision.new_eval = EvalResult(
            checkpoint="v5",
            games_played=20,
            wins=18,
            losses=2,
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
            "alpha4gate.learning.trainer.TrainingOrchestrator",
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
            json.dumps([
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
            ]),
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

        from alpha4gate.learning.rollback import RollbackDecision

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
            "alpha4gate.learning.trainer.TrainingOrchestrator",
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

        from alpha4gate.learning.rollback import RollbackDecision

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
            "alpha4gate.learning.trainer.TrainingOrchestrator",
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

    def test_put_curriculum_persists(
        self, daemon_client: TestClient, tmp_path: Path
    ) -> None:
        """PUT persists to daemon_config.json on disk."""
        daemon_client.put(
            "/api/training/curriculum",
            json={"current_difficulty": 7},
        )
        config_path = tmp_path / "data" / "daemon_config.json"
        assert config_path.exists()
        loaded = load_daemon_config(config_path)
        assert loaded.current_difficulty == 7
