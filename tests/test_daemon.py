"""Tests for the training daemon."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

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

    def test_should_train_stub_returns_false(self, tmp_path: Path) -> None:
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
