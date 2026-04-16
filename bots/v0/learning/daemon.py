"""Training daemon: background thread that periodically triggers RL training."""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bots.v0.config import Settings

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
    # Issue #73: per-cycle crash visibility watchdog.
    # While TrainingOrchestrator.run(...) is blocked inside SB3's
    # ``.learn()`` loop, the post-orchestrator bookkeeping that sets
    # ``_last_error`` (see #71) cannot run. The watchdog polls the
    # process-wide ``ErrorLogBuffer`` during active training and
    # surfaces an interim ``_last_error`` as soon as the number of
    # ERROR-level records observed since training started exceeds
    # ``watchdog_error_threshold``.
    watchdog_poll_seconds: float = 5.0
    watchdog_error_threshold: int = 5


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
        self._promotion_logger: Any = None

        # Rollback monitor (created lazily)
        self._rollback_monitor: Any = None
        self._last_rollback: dict[str, Any] | None = None

        # Curriculum tracking
        self._last_advancement: str | None = None

        # Issue #73: per-cycle crash visibility watchdog.
        # Spawned inside ``_run_training`` for the exact duration of a
        # training pass, so there is no lifecycle beyond what the main
        # training path already manages.
        self._watchdog_thread: threading.Thread | None = None
        self._watchdog_stop_event: threading.Event = threading.Event()

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
        from bots.v0.learning.database import TrainingDB

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
            from bots.v0.learning.database import TrainingDB
            from bots.v0.learning.rollback import RollbackConfig, RollbackMonitor

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
            from bots.v0.learning.database import TrainingDB
            from bots.v0.learning.evaluator import ModelEvaluator
            from bots.v0.learning.promotion import PromotionConfig, PromotionManager

            db_path = self._settings.data_dir / "training.db"
            db = TrainingDB(db_path)
            evaluator = ModelEvaluator(self._settings, db)
            self._promotion_manager = PromotionManager(evaluator, PromotionConfig())
        return self._promotion_manager

    def _get_promotion_logger(self) -> Any:
        """Get or create the PromotionLogger for persisting decisions.

        Writes to ``<data_dir>/promotion_history.json`` -- the same file the
        API's ``/api/training/promotions/history`` endpoint reads from.
        The wiki path is intentionally pointed at a non-existent file inside
        ``data_dir`` so ``_append_wiki_row`` short-circuits: the wiki doc is
        maintained by hand, not by the running daemon.
        """
        if self._promotion_logger is None:
            from bots.v0.learning.promotion import PromotionLogger

            self._promotion_logger = PromotionLogger(
                history_path=self._settings.data_dir / "promotion_history.json",
                wiki_path=self._settings.data_dir / "_daemon_no_wiki.md",
            )
        return self._promotion_logger

    def _persist_config(self) -> None:
        """Write current daemon config to disk."""
        config_path = self._settings.data_dir / "daemon_config.json"
        save_daemon_config(self._config, config_path)

    # ------------------------------------------------------------------
    # Issue #73: per-cycle crash visibility watchdog
    # ------------------------------------------------------------------

    def _start_watchdog(self) -> None:
        """Start the per-cycle crash visibility watchdog thread.

        Captures a baseline of the process-wide ``ErrorLogBuffer``
        count at training start, then polls that count on a short
        cadence. If the delta exceeds
        ``DaemonConfig.watchdog_error_threshold`` the watchdog writes
        an interim ``_last_error`` through ``self._lock`` so
        ``/api/training/daemon`` and the Alerts tab surface the
        failure mid-training — before the orchestrator returns and
        the #71 post-training bookkeeping runs.

        Scope: observability only. The watchdog never transitions
        state, never kills the trainer, and never touches fields it
        does not own (``_state``, ``_runs_completed``, etc.).
        """
        from bots.v0.error_log import get_error_log_buffer

        # If a previous watchdog thread is still alive (shouldn't
        # happen, but defensive) stop it first so we never stack
        # watchdogs for a single training run.
        if self._watchdog_thread is not None and self._watchdog_thread.is_alive():
            self._stop_watchdog()

        buffer = get_error_log_buffer()
        baseline_count, _ = buffer.snapshot()

        self._watchdog_stop_event.clear()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            args=(baseline_count,),
            daemon=True,
            name="training-daemon-watchdog",
        )
        self._watchdog_thread.start()

    def _stop_watchdog(self) -> None:
        """Signal the watchdog to exit and join it.

        Safe to call multiple times and from the ``finally`` block of
        ``_run_training``. Uses a bounded join so a wedged watchdog
        thread cannot hang daemon shutdown.

        Issue #73 iter-2 (M3): if the join times out (watchdog stuck
        acquiring ``self._lock`` or inside ``buffer.snapshot()``), the
        thread reference is INTENTIONALLY kept (not cleared to
        ``None``) so a subsequent ``_stop_watchdog`` call can observe
        and retry — and so the zombie thread cannot masquerade as "no
        watchdog is running" to a later ``_start_watchdog`` call. This
        matters when ``watchdog_poll_seconds`` is configured above the
        5s join timeout.
        """
        self._watchdog_stop_event.set()
        if self._watchdog_thread is not None:
            self._watchdog_thread.join(timeout=5.0)
            if self._watchdog_thread.is_alive():
                _log.warning(
                    "Daemon watchdog thread did not exit within 5s; "
                    "leaving reference in place for retry"
                )
            else:
                self._watchdog_thread = None

    def _watchdog_loop(self, baseline_count: int) -> None:
        """Poll ``ErrorLogBuffer`` until training ends or stop is signalled.

        Exit conditions (any of):
        - ``self._watchdog_stop_event`` is set (normal path — called
          from ``_run_training`` after ``orchestrator.run`` returns).
        - ``self._stop_event`` is set (whole-daemon shutdown via
          ``TrainingDaemon.stop``).
        - ``self._training_active`` flipped to False (belt-and-braces
          for the exception path).
        """
        from bots.v0.error_log import get_error_log_buffer

        buffer = get_error_log_buffer()
        threshold = self._config.watchdog_error_threshold
        poll_seconds = self._config.watchdog_poll_seconds

        while not self._watchdog_stop_event.is_set():
            if self._stop_event.is_set():
                return
            if not self._training_active:
                return

            total, _records = buffer.snapshot()
            delta = total - baseline_count
            if delta >= threshold:
                error_msg = (
                    f"Watchdog: {delta} ERROR-level log records observed "
                    f"since training started (threshold={threshold}); "
                    "training may be stuck in a per-cycle crash loop. "
                    "Daemon state will be finalised when the orchestrator "
                    "returns."
                )
                with self._lock:
                    # Do not clobber a pre-existing error (e.g. if the
                    # caller pre-seeded one for some reason). The
                    # post-orchestrator bookkeeping owns the final
                    # authoritative value; the watchdog's job is only
                    # to make the failure visible during training.
                    if self._last_error is None:
                        self._last_error = error_msg
                _log.error(
                    "Daemon watchdog: %d errors since training started "
                    "(threshold=%d)",
                    delta,
                    threshold,
                )
                # One-shot: once we've raised the alarm, stop polling.
                # A stream of watchdog-generated ERROR log records
                # would otherwise feed back into the very buffer we
                # are watching.
                return

            # Sleep on the watchdog stop event so ``_stop_watchdog``
            # wakes us promptly. Also respond to whole-daemon stop.
            if self._watchdog_stop_event.wait(timeout=poll_seconds):
                return
            if self._stop_event.is_set():
                return

    def _run_training(self) -> None:
        """Create a TrainingOrchestrator and run one training pass."""
        from bots.v0.learning.database import TrainingDB
        from bots.v0.learning.trainer import TrainingOrchestrator

        _log.info(
            "Daemon: starting training (%d cycles, %d games/cycle, difficulty=%d)",
            self._config.cycles_per_run,
            self._config.games_per_cycle,
            self._config.current_difficulty,
        )
        self._training_active = True
        with self._lock:
            self._state = "training"
            # Issue #73 iter-2 (M1): clear any residual ``_last_error``
            # from a prior failed run. The watchdog's "do not clobber
            # a pre-existing error" guard in ``_watchdog_loop`` would
            # otherwise be dead-lettered by stale state — the previous
            # pass's bookkeeping message would block the new pass's
            # watchdog from ever writing. The previous run's error has
            # already been surfaced to the operator by the time the
            # daemon decides to run again (#73 soak-3 scenario).
            self._last_error = None

        try:
            # Issue #73: start the crash-visibility watchdog before
            # the (potentially long-blocking) orchestrator.run() call.
            # The watchdog runs concurrently in a daemon thread and
            # surfaces a mid-training ``_last_error`` if
            # per-environment ERROR log records pile up faster than
            # the threshold. It is joined BEFORE the post-orchestrator
            # bookkeeping below so there is no race between the two
            # writers of ``_last_error``. Phase 4.7 Step 2 (#83)
            # widens the protected window to include the eval /
            # promotion-gate and rollback-check blocks — the
            # watchdog now stops right before the bookkeeping, not
            # right after ``orchestrator.run`` returns — so ERROR
            # records emitted from those blocks are also observable
            # as ``_last_error``.
            #
            # Issue #73 iter-2 (M2): ``_start_watchdog`` is inside the
            # ``try:`` block (not above it) so that if ``Thread.start``
            # itself raises — RuntimeError, OS-level OOM, anything —
            # the exception lands in the ``finally`` below, which
            # clears ``_training_active`` and calls ``_stop_watchdog``.
            # Starting above the ``try:`` would leave the daemon
            # wedged in ``state="training", _training_active=True``
            # with no actual training running.
            self._start_watchdog()

            reward_rules = self._settings.data_dir / "reward_rules.json"
            hyperparams = self._settings.data_dir / "hyperparams.json"
            # Phase 4.8 Approach B: TrainingAdvisorBridge runs Claude
            # CLI in its own daemon thread with its own event loop,
            # avoiding the CancelledError that the old ClaudeAdvisor
            # caused when shared across game threads.
            from bots.v0.learning.advisor_bridge import (
                TrainingAdvisorBridge,
            )

            bridge = TrainingAdvisorBridge(
                model="sonnet",
                rate_limit_seconds=60.0,
            )
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
                replay_dir=self._settings.replay_dir,
                advisor_bridge=bridge,
            )
            result = orchestrator.run(
                n_cycles=self._config.cycles_per_run,
                games_per_cycle=self._config.games_per_cycle,
                resume=True,
            )

            # Phase 4.7 Step 2 (#83): the watchdog used to join here,
            # BEFORE the promotion-gate and rollback-check blocks
            # below. Soak-2026-04-11b showed that the eval/promotion
            # phase also emits ERROR-level log records on failure
            # (18 backend errors accumulated during the post-training
            # eval while ``daemon.last_error`` stayed ``None`` the
            # whole time). The watchdog's protected window must
            # therefore extend across the full promotion + rollback
            # path so any ``_log.error`` / ``_log.exception`` that
            # fires inside those blocks is counted by the watchdog
            # and can trip ``_last_error``. The explicit
            # ``_stop_watchdog()`` call is moved further down, right
            # before the ``_last_result`` / ``_last_error``
            # bookkeeping block — still joining before the
            # bookkeeping so there is no race between the watchdog
            # and the post-orchestrator bookkeeping (the SAME race
            # reason the #73 iter-2 comment above describes, just
            # with a wider protected window). The ``finally`` block
            # at the end of ``_run_training`` still calls
            # ``_stop_watchdog`` as a belt-and-braces cleanup for
            # the exception path.

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
            crashed_cycles = [
                c for c in cycle_results if c.get("status") == "crashed"
            ]
            # Decide whether the run as a whole is a failure.
            #
            # Failure conditions (issue #71):
            #   1. cycle_results non-empty AND every entry is "crashed".
            #   2. cycle_results empty AND cycles_completed == 0 — the
            #      orchestrator returned without starting any cycle (e.g.
            #      early env-build failure). Treat as all-crashed so the
            #      daemon-level failure is observable instead of silently
            #      ticking runs_completed. If cycle_results is empty but
            #      cycles_completed > 0 we trust the aggregate count and
            #      treat it as success (defensive default; no observed
            #      orchestrator path returns that shape today).
            cycles_completed = int(result.get("cycles_completed", 0))
            if cycle_results:
                all_crashed = not successful_cycles
            else:
                all_crashed = cycles_completed == 0
            if successful_cycles:
                latest = successful_cycles[-1]
                latest_checkpoint = latest["checkpoint"]
                current_difficulty = latest["difficulty"]
                try:
                    pm = self._get_promotion_manager()
                    decision = pm.evaluate_and_promote(
                        latest_checkpoint,
                        current_difficulty,
                        cancel_check=self._stop_event.is_set,
                    )
                    _log.info(
                        "Promotion decision: %s (promoted=%s, reason=%s)",
                        latest_checkpoint, decision.promoted, decision.reason,
                    )

                    # Persist EVERY decision (promoted OR rejected) to
                    # promotion_history.json so it shows up in
                    # ``/api/training/promotions/history`` -- the endpoint the
                    # dashboard Improvements tab polls. Previously only
                    # curriculum advancements made it to disk, so the first
                    # auto-promote ("no previous best") was logged but
                    # invisible to the UI (Phase 4.6 Step 4 / soak-2026-04-11).
                    try:
                        logger = self._get_promotion_logger()
                        logger.log_decision(decision)
                    except Exception:
                        _log.exception(
                            "Failed to persist promotion decision for %s",
                            latest_checkpoint,
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
                from bots.v0.learning.checkpoints import get_best_name

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

            # Update trigger tracking regardless of per-cycle outcome.
            #
            # We intentionally refresh _last_run_time even on all-crashed
            # so the time trigger does not fire again inside
            # ``min_hours_since_last``. The alternative — leaving the
            # timestamp stale — would cause the daemon to retry every
            # check interval and crash-loop against the same underlying
            # fault (e.g. observation-space mismatch in issue #71's
            # original soak-2 observation). The daemon-level ERROR log
            # below and the persistent ``_last_error`` field are the
            # supported observability surface for the failure; operators
            # see it on the Alerts tab and can intervene before the next
            # natural retry. (Issue #71 question 1.)
            db_path = self._settings.data_dir / "training.db"
            if db_path.exists():
                db = TrainingDB(db_path)
                self._last_transition_count = db.get_transition_count()
                db.close()
            self._last_run_time = datetime.now(UTC)

            # Phase 4.7 Step 2 (#83): join the watchdog here, AFTER
            # the eval/promotion and rollback-check blocks but
            # BEFORE the ``_last_result`` / ``_last_error``
            # bookkeeping. See the wider comment above ``orchestrator.run``
            # for the rationale — the watchdog must cover the full
            # promotion + rollback path, but it must still stop
            # before the bookkeeping block claims authoritative
            # ownership of ``_last_error`` (same race-avoidance
            # invariant as #73 iter-2).
            self._stop_watchdog()

            with self._lock:
                self._last_result = result
                self._last_run = datetime.now(UTC)
                if all_crashed:
                    crash_count = len(crashed_cycles) if crashed_cycles else cycles_completed
                    first_error = (
                        crashed_cycles[0].get("error", "unknown")
                        if crashed_cycles
                        else "orchestrator returned no cycle results"
                    )
                    error_msg = (
                        f"All {crash_count} training cycles crashed; "
                        f"first error: {first_error}"
                    )
                    self._last_error = error_msg
                else:
                    self._last_error = None
                    self._runs_completed += 1
            if all_crashed:
                # ERROR-level so the root ``_ErrorBufferHandler`` routes
                # this into ``ErrorLogBuffer`` and ``/api/training/status.
                # recent_errors`` surfaces a daemon-level entry — not
                # just the per-cycle trainer exceptions that fire from
                # inside the orchestrator loop.
                _log.error("Daemon: training failed -- %s", error_msg)
            else:
                _log.info("Daemon: training complete -- %s", result)
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
                self._last_run = datetime.now(UTC)
            _log.exception("Daemon: training failed")
        finally:
            # Shut down the advisor bridge thread cleanly.
            try:
                bridge.shutdown()
            except Exception:
                _log.debug("Advisor bridge shutdown raised", exc_info=True)
            # Stop the watchdog BEFORE clearing ``_training_active`` so
            # the watchdog does not observe an off-state mid-poll.
            self._stop_watchdog()
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
