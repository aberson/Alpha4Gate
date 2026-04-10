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
    current_difficulty: int = 1
    max_difficulty: int = 10
    win_rate_threshold: float = 0.8


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

        # Trigger tracking
        self._last_transition_count: int = 0
        self._last_run_time: datetime = datetime.min
        self._training_active: bool = False

        # Promotion manager (created lazily in _run_training)
        self._promotion_manager: Any = None

        # Rollback monitor (created lazily)
        self._rollback_monitor: Any = None
        self._last_rollback: dict[str, Any] | None = None

        # Curriculum tracking
        self._last_advancement: str | None = None

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
                "last_rollback": self._last_rollback,
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

        Two OR conditions (either triggers training):
        1. Transition count: enough new transitions since last run.
        2. Time: enough hours since last run.

        Safety gates: never trigger if no transitions exist or training is active.
        """
        trigger_state = self._evaluate_triggers()
        return bool(trigger_state["would_trigger"])

    def _evaluate_triggers(self) -> dict[str, Any]:
        """Compute trigger state without side effects.

        Returns a dict with: transitions_since_last, hours_since_last,
        would_trigger, reason.
        """
        from alpha4gate.learning.database import TrainingDB

        db_path = self._settings.data_dir / "training.db"

        # Default: no data
        if not db_path.exists():
            return {
                "transitions_since_last": 0,
                "hours_since_last": 0.0,
                "would_trigger": False,
                "reason": "no database file",
            }

        db = TrainingDB(db_path)
        try:
            total_transitions = db.get_transition_count()
        finally:
            db.close()

        transitions_since_last = total_transitions - self._last_transition_count

        now = datetime.now(UTC)
        # _last_run_time uses datetime.min (no tzinfo) for "never ran" semantics
        if self._last_run_time == datetime.min:
            hours_since_last = float("inf")
        else:
            delta = now - self._last_run_time
            hours_since_last = delta.total_seconds() / 3600.0

        # Safety gate: no transitions at all
        if total_transitions == 0:
            return {
                "transitions_since_last": 0,
                "hours_since_last": hours_since_last,
                "would_trigger": False,
                "reason": "no transitions in database",
            }

        # Safety gate: training already in progress
        if self._training_active:
            return {
                "transitions_since_last": transitions_since_last,
                "hours_since_last": hours_since_last,
                "would_trigger": False,
                "reason": "training already in progress",
            }

        # Transition count trigger
        if transitions_since_last >= self._config.min_transitions:
            return {
                "transitions_since_last": transitions_since_last,
                "hours_since_last": hours_since_last,
                "would_trigger": True,
                "reason": (
                    f"transition count trigger: {transitions_since_last} >= "
                    f"{self._config.min_transitions}"
                ),
            }

        # Time trigger
        if hours_since_last >= self._config.min_hours_since_last:
            return {
                "transitions_since_last": transitions_since_last,
                "hours_since_last": hours_since_last,
                "would_trigger": True,
                "reason": (
                    f"time trigger: {hours_since_last:.1f}h >= "
                    f"{self._config.min_hours_since_last}h"
                ),
            }

        # Neither trigger met
        return {
            "transitions_since_last": transitions_since_last,
            "hours_since_last": hours_since_last,
            "would_trigger": False,
            "reason": "no trigger condition met",
        }

    def get_trigger_state(self) -> dict[str, Any]:
        """Return the current trigger evaluation state (for the API)."""
        return self._evaluate_triggers()

    def update_config(self, updates: dict[str, Any]) -> DaemonConfig:
        """Update daemon config fields at runtime.

        Only known DaemonConfig fields are applied; unknown keys are ignored.
        Returns the updated config.
        """
        valid_fields = DaemonConfig.__dataclass_fields__
        for key, value in updates.items():
            if key in valid_fields:
                setattr(self._config, key, value)
        return self._config

    def get_curriculum_status(self) -> dict[str, Any]:
        """Return the current curriculum state."""
        return {
            "current_difficulty": self._config.current_difficulty,
            "max_difficulty": self._config.max_difficulty,
            "win_rate_threshold": self._config.win_rate_threshold,
            "last_advancement": self._last_advancement,
        }

    def set_curriculum(
        self,
        current_difficulty: int | None = None,
        max_difficulty: int | None = None,
        win_rate_threshold: float | None = None,
    ) -> dict[str, Any]:
        """Manually set curriculum fields and persist to disk."""
        if current_difficulty is not None:
            self._config.current_difficulty = current_difficulty
        if max_difficulty is not None:
            self._config.max_difficulty = max_difficulty
        if win_rate_threshold is not None:
            self._config.win_rate_threshold = win_rate_threshold
        # Persist
        config_path = self._settings.data_dir / "daemon_config.json"
        save_daemon_config(self._config, config_path)
        return self.get_curriculum_status()

    def _get_rollback_monitor(self) -> Any:
        """Get or create the RollbackMonitor instance."""
        if self._rollback_monitor is None:
            from alpha4gate.learning.database import TrainingDB
            from alpha4gate.learning.rollback import RollbackConfig, RollbackMonitor

            db_path = self._settings.data_dir / "training.db"
            db = TrainingDB(db_path)
            checkpoint_dir = self._settings.data_dir / "checkpoints"
            history_path = self._settings.data_dir / "promotion_history.json"
            self._rollback_monitor = RollbackMonitor(
                db=db,
                config=RollbackConfig(),
                checkpoint_dir=checkpoint_dir,
                history_path=history_path,
            )
        return self._rollback_monitor

    def _get_promotion_manager(self) -> Any:
        """Get or create the PromotionManager instance."""
        if self._promotion_manager is None:
            from alpha4gate.learning.database import TrainingDB
            from alpha4gate.learning.evaluator import ModelEvaluator
            from alpha4gate.learning.promotion import PromotionConfig, PromotionManager

            db_path = self._settings.data_dir / "training.db"
            db = TrainingDB(db_path)
            evaluator = ModelEvaluator(self._settings, db)
            self._promotion_manager = PromotionManager(evaluator, PromotionConfig())
        return self._promotion_manager

    def _persist_config(self) -> None:
        """Write current daemon config to disk."""
        config_path = self._settings.data_dir / "daemon_config.json"
        save_daemon_config(self._config, config_path)

    def _run_training(self) -> None:
        """Create a TrainingOrchestrator and run one training pass."""
        from alpha4gate.learning.database import TrainingDB
        from alpha4gate.learning.trainer import TrainingOrchestrator

        _log.info(
            "Daemon: starting training (%d cycles, %d games/cycle, difficulty=%d)",
            self._config.cycles_per_run,
            self._config.games_per_cycle,
            self._config.current_difficulty,
        )
        self._training_active = True
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
                initial_difficulty=self._config.current_difficulty,
                max_difficulty=self._config.max_difficulty,
                win_rate_threshold=self._config.win_rate_threshold,
            )
            result = orchestrator.run(
                n_cycles=self._config.cycles_per_run,
                games_per_cycle=self._config.games_per_cycle,
                resume=True,
            )

            # Persist final difficulty from the orchestrator back to config
            final_difficulty = result.get("final_difficulty")
            if final_difficulty is not None:
                self._config.current_difficulty = final_difficulty
                self._persist_config()
                _log.info(
                    "Daemon: persisted final difficulty %d", final_difficulty
                )

            # Run promotion gate on the latest *successful* checkpoint.
            # Crashed cycles have status="crashed" and no "checkpoint" field;
            # they must not feed the promotion gate (a phantom checkpoint
            # would silently advance the curriculum on zero training).
            cycle_results = result.get("cycle_results", [])
            successful_cycles = [
                c for c in cycle_results if c.get("status") != "crashed"
            ]
            if successful_cycles:
                latest = successful_cycles[-1]
                latest_checkpoint = latest["checkpoint"]
                current_difficulty = latest["difficulty"]
                try:
                    pm = self._get_promotion_manager()
                    decision = pm.evaluate_and_promote(
                        latest_checkpoint, current_difficulty
                    )
                    _log.info(
                        "Promotion decision: %s (promoted=%s, reason=%s)",
                        latest_checkpoint, decision.promoted, decision.reason,
                    )

                    # Curriculum-aware promotion: auto-advance difficulty
                    if decision.promoted:
                        win_rate = decision.new_eval.win_rate
                        if (
                            win_rate >= self._config.win_rate_threshold
                            and self._config.current_difficulty
                            < self._config.max_difficulty
                        ):
                            old_diff = self._config.current_difficulty
                            self._config.current_difficulty += 1
                            self._last_advancement = datetime.now(UTC).isoformat()
                            self._persist_config()
                            _log.info(
                                "Curriculum advancement: difficulty %d -> %d "
                                "(win_rate=%.2f >= threshold=%.2f)",
                                old_diff,
                                self._config.current_difficulty,
                                win_rate,
                                self._config.win_rate_threshold,
                            )
                            # Log advancement in promotion_history.json
                            self._log_curriculum_advancement(
                                latest_checkpoint,
                                old_diff,
                                self._config.current_difficulty,
                                win_rate,
                            )
                except Exception:
                    _log.exception("Promotion gate failed for %s", latest_checkpoint)

            # Run rollback check on the current best
            try:
                from alpha4gate.learning.checkpoints import get_best_name

                cp_dir = self._settings.data_dir / "checkpoints"
                current_best = get_best_name(cp_dir)
                if current_best is not None:
                    monitor = self._get_rollback_monitor()
                    rollback_decision = monitor.check_for_regression(current_best)
                    if rollback_decision is not None:
                        # Revert difficulty to the level of the model we're
                        # rolling back to (difficulty floor).
                        reverted_difficulty = self._get_model_difficulty(
                            rollback_decision.revert_to
                        )
                        monitor.execute_rollback(rollback_decision)
                        if reverted_difficulty is not None:
                            old_diff = self._config.current_difficulty
                            self._config.current_difficulty = reverted_difficulty
                            self._persist_config()
                            _log.info(
                                "Difficulty reverted: %d -> %d (rollback to %s)",
                                old_diff,
                                reverted_difficulty,
                                rollback_decision.revert_to,
                            )
                        with self._lock:
                            self._last_rollback = {
                                "current_model": rollback_decision.current_model,
                                "revert_to": rollback_decision.revert_to,
                                "reason": rollback_decision.reason,
                                "timestamp": rollback_decision.timestamp,
                            }
                        _log.info(
                            "Rollback executed: %s -> %s",
                            rollback_decision.current_model,
                            rollback_decision.revert_to,
                        )
            except Exception:
                _log.exception("Rollback check failed")

            # Update trigger tracking after successful run
            db_path = self._settings.data_dir / "training.db"
            if db_path.exists():
                db = TrainingDB(db_path)
                self._last_transition_count = db.get_transition_count()
                db.close()
            self._last_run_time = datetime.now(UTC)

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
        finally:
            self._training_active = False

    def _log_curriculum_advancement(
        self,
        checkpoint: str,
        old_difficulty: int,
        new_difficulty: int,
        win_rate: float,
    ) -> None:
        """Append a curriculum advancement entry to promotion_history.json."""
        import json

        history_path = self._settings.data_dir / "promotion_history.json"
        entries: list[dict[str, Any]] = []
        if history_path.exists():
            entries = json.loads(history_path.read_text(encoding="utf-8"))

        entries.append({
            "timestamp": datetime.now(UTC).isoformat(),
            "type": "curriculum_advancement",
            "checkpoint": checkpoint,
            "old_difficulty": old_difficulty,
            "new_difficulty": new_difficulty,
            "win_rate": win_rate,
        })
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history_path.write_text(
            json.dumps(entries, indent=2) + "\n", encoding="utf-8"
        )

    def _get_model_difficulty(self, model: str) -> int | None:
        """Look up the difficulty at which a model was promoted.

        Searches promotion_history.json for the most recent promotion of
        the given model. Returns the ``difficulty`` recorded at that time.
        """
        import json

        history_path = self._settings.data_dir / "promotion_history.json"
        if not history_path.exists():
            return None

        entries: list[dict[str, Any]] = json.loads(
            history_path.read_text(encoding="utf-8")
        )
        for entry in reversed(entries):
            if (
                entry.get("new_checkpoint") == model
                and entry.get("promoted") is True
            ):
                diff = entry.get("difficulty")
                if diff is not None:
                    return int(diff)
        return None
