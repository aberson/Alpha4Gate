"""Training daemon: background thread that periodically triggers RL training."""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alpha4gate.config import Settings

_log = logging.getLogger(__name__)


@dataclass
class DaemonConfig:
    """Configuration for the training daemon."""

    check_interval_seconds: int = 60
    min_transitions: int = 500
    min_hours_since_last: float = 1.0
    cycles_per_run: int = 5
    games_per_cycle: int = 10


def load_daemon_config(path: Path) -> DaemonConfig:
    """Load daemon config from a JSON file, falling back to defaults."""
    import json

    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return DaemonConfig(
            **{k: v for k, v in data.items() if k in DaemonConfig.__dataclass_fields__}
        )
    return DaemonConfig()


def save_daemon_config(config: DaemonConfig, path: Path) -> None:
    """Save daemon config to a JSON file."""
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), indent=2) + "\n", encoding="utf-8")


class TrainingDaemon:
    """Background training daemon that periodically checks and triggers RL training.

    Runs as a daemon thread inside the API server process. Uses
    ``threading.Event`` for clean start/stop control.
    """

    def __init__(self, settings: Settings, config: DaemonConfig) -> None:
        self._settings = settings
        self._config = config
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        # Observable state
        self._state: str = "idle"  # idle | checking | training
        self._last_run: datetime | None = None
        self._next_check: datetime | None = None
        self._runs_completed: int = 0
        self._last_result: dict[str, Any] | None = None
        self._last_error: str | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start the daemon loop in a background thread.

        No-op if already running.
        """
        if self.is_running():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="training-daemon"
        )
        self._thread.start()
        _log.info(
            "Training daemon started (interval=%ds)", self._config.check_interval_seconds
        )

    def stop(self) -> None:
        """Signal the daemon to stop and wait for it to finish."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)
            self._thread = None
        with self._lock:
            self._state = "idle"
            self._next_check = None
        _log.info("Training daemon stopped")

    def is_running(self) -> bool:
        """Return True if the daemon thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def get_status(self) -> dict[str, Any]:
        """Return current daemon status as a JSON-serialisable dict."""
        with self._lock:
            return {
                "running": self.is_running(),
                "state": self._state,
                "last_run": self._last_run.isoformat() if self._last_run else None,
                "next_check": (
                    self._next_check.isoformat() if self._next_check else None
                ),
                "runs_completed": self._runs_completed,
                "last_result": self._last_result,
                "last_error": self._last_error,
                "config": asdict(self._config),
            }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        """Main daemon loop: sleep -> check -> maybe train -> repeat."""
        while not self._stop_event.is_set():
            with self._lock:
                self._next_check = datetime.now(UTC)
            # Sleep in small increments so stop is responsive
            if self._interruptible_sleep(self._config.check_interval_seconds):
                break  # stop requested

            with self._lock:
                self._state = "checking"

            if self._should_train():
                self._run_training()

            with self._lock:
                self._state = "idle"

    def _interruptible_sleep(self, seconds: int) -> bool:
        """Sleep for *seconds*, returning True immediately if stop is signalled."""
        return self._stop_event.wait(timeout=float(seconds))

    def _should_train(self) -> bool:
        """Evaluate whether training should be triggered.

        Stub for Step 1 -- always returns False.
        Full trigger logic (transition count, hours since last run) is Step 2.
        """
        return False

    def _run_training(self) -> None:
        """Create a TrainingOrchestrator and run one training pass."""
        from alpha4gate.learning.trainer import TrainingOrchestrator

        _log.info(
            "Daemon: starting training (%d cycles, %d games/cycle)",
            self._config.cycles_per_run,
            self._config.games_per_cycle,
        )
        with self._lock:
            self._state = "training"

        try:
            reward_rules = self._settings.data_dir / "reward_rules.json"
            hyperparams = self._settings.data_dir / "hyperparams.json"
            orchestrator = TrainingOrchestrator(
                checkpoint_dir=self._settings.data_dir / "checkpoints",
                db_path=self._settings.data_dir / "training.db",
                reward_rules_path=(
                    reward_rules if reward_rules.exists() else None
                ),
                hyperparams_path=(
                    hyperparams if hyperparams.exists() else None
                ),
            )
            result = orchestrator.run(
                n_cycles=self._config.cycles_per_run,
                games_per_cycle=self._config.games_per_cycle,
                resume=True,
            )
            with self._lock:
                self._last_result = result
                self._last_error = None
                self._runs_completed += 1
                self._last_run = datetime.now(UTC)
            _log.info("Daemon: training complete -- %s", result)
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
                self._last_run = datetime.now(UTC)
            _log.exception("Daemon: training failed")
