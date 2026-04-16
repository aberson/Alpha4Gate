"""Rollback monitor: detect model regressions and revert to previous best."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from bots.v0.learning.database import TrainingDB

_log = logging.getLogger(__name__)


@dataclass
class RollbackConfig:
    """Configuration for the rollback monitor."""

    monitoring_window: int = 30
    regression_threshold: float = 0.15
    min_games_before_check: int = 10


@dataclass
class RollbackDecision:
    """Result of a regression check indicating a rollback is needed."""

    current_model: str
    revert_to: str
    current_win_rate: float
    promotion_win_rate: float
    games_played: int
    reason: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class RollbackMonitor:
    """Monitors promoted models for performance regression and triggers rollbacks.

    After a model is promoted, it monitors subsequent game results. If the
    model's win rate drops more than ``regression_threshold`` below its
    promotion-time win rate (over at least ``min_games_before_check`` games),
    a rollback is recommended.
    """

    def __init__(
        self,
        db: TrainingDB,
        config: RollbackConfig,
        checkpoint_dir: Path,
        history_path: Path | None = None,
    ) -> None:
        self._db = db
        self._config = config
        self._checkpoint_dir = checkpoint_dir
        if history_path is not None:
            self._history_path = history_path
        else:
            from orchestrator.registry import resolve_data_path

            self._history_path = resolve_data_path("promotion_history.json")

    def check_for_regression(self, current_best: str) -> RollbackDecision | None:
        """Check if the current best model is regressing.

        Compares the model's current win rate against the win rate recorded
        at promotion time. Returns a RollbackDecision if regression is
        detected, otherwise None.
        """
        from bots.v0.learning.checkpoints import _load_manifest

        # Get current stats from DB
        stats = self._db.get_win_rate_by_model(current_best)
        total_games = cast(int, stats["total"])
        current_win_rate = cast(float, stats["win_rate"])

        # Not enough games yet
        if total_games < self._config.min_games_before_check:
            _log.debug(
                "Rollback check: %s has %d games (need %d)",
                current_best, total_games, self._config.min_games_before_check,
            )
            return None

        # Get promotion-time win rate from history
        promotion_win_rate = self._get_promotion_win_rate(current_best)
        if promotion_win_rate is None:
            _log.debug(
                "Rollback check: no promotion record for %s, skipping", current_best
            )
            return None

        # Check for regression
        drop = promotion_win_rate - current_win_rate
        if drop > self._config.regression_threshold:
            # Find the previous best to revert to
            manifest = _load_manifest(self._checkpoint_dir)
            previous_best = manifest.get("previous_best")
            if previous_best is None:
                _log.warning(
                    "Regression detected for %s but no previous_best in manifest",
                    current_best,
                )
                return None

            reason = (
                f"regression detected: win rate {current_win_rate:.2%} is "
                f"{drop:.2%} below promotion rate {promotion_win_rate:.2%} "
                f"(threshold={self._config.regression_threshold:.2%}, "
                f"games={total_games})"
            )
            _log.warning("Rollback recommended: %s", reason)
            return RollbackDecision(
                current_model=current_best,
                revert_to=previous_best,
                current_win_rate=current_win_rate,
                promotion_win_rate=promotion_win_rate,
                games_played=total_games,
                reason=reason,
            )

        _log.debug(
            "Rollback check: %s OK (win_rate=%.2f%%, promo_rate=%.2f%%, drop=%.2f%%)",
            current_best, current_win_rate * 100, promotion_win_rate * 100, drop * 100,
        )
        return None

    def execute_rollback(self, decision: RollbackDecision) -> None:
        """Execute a rollback: update manifest and log the event.

        Sets manifest ``best`` back to ``decision.revert_to`` and logs
        the rollback as a special entry in promotion_history.json.
        """
        from bots.v0.learning.checkpoints import _load_manifest, _save_manifest

        # Update manifest
        manifest = _load_manifest(self._checkpoint_dir)
        manifest["previous_best"] = decision.current_model
        manifest["best"] = decision.revert_to
        _save_manifest(self._checkpoint_dir, manifest)

        _log.info(
            "Rollback executed: %s -> %s (%s)",
            decision.current_model, decision.revert_to, decision.reason,
        )

        # Log to promotion_history.json as a special rollback entry
        self._log_rollback(decision)

    def _get_promotion_win_rate(self, model: str) -> float | None:
        """Look up the win rate at which a model was promoted.

        Searches promotion_history.json for the most recent promotion of
        the given model. Returns the ``new_win_rate`` recorded at that time.
        """
        if not self._history_path.exists():
            return None

        entries: list[dict[str, Any]] = json.loads(
            self._history_path.read_text(encoding="utf-8")
        )

        # Search in reverse for the most recent promotion of this model
        for entry in reversed(entries):
            if (
                entry.get("new_checkpoint") == model
                and entry.get("promoted") is True
            ):
                win_rate = entry.get("new_win_rate")
                if win_rate is not None:
                    return float(win_rate)
        return None

    def _log_rollback(self, decision: RollbackDecision) -> None:
        """Append a rollback entry to promotion_history.json.

        Includes ``reason_code="rollback"`` so all writers to
        promotion_history.json share a consistent schema. The dashboard can
        then classify rollback entries alongside promotion entries without
        parsing the free-form ``reason`` string (Phase 4.6 Step 4 iter 2).
        """
        entry: dict[str, Any] = {
            "timestamp": decision.timestamp,
            "new_checkpoint": decision.revert_to,
            "old_best": decision.current_model,
            "new_win_rate": decision.current_win_rate,
            "old_win_rate": decision.promotion_win_rate,
            "delta": decision.current_win_rate - decision.promotion_win_rate,
            "eval_games_played": decision.games_played,
            "promoted": False,
            "reason": f"rollback: {decision.reason}",
            "reason_code": "rollback",
            "difficulty": 0,
            "action_distribution_shift": None,
        }

        entries: list[dict[str, Any]] = []
        if self._history_path.exists():
            entries = json.loads(self._history_path.read_text(encoding="utf-8"))

        entries.append(entry)
        self._history_path.parent.mkdir(parents=True, exist_ok=True)
        self._history_path.write_text(
            json.dumps(entries, indent=2) + "\n", encoding="utf-8"
        )

    def get_status(self) -> dict[str, Any]:
        """Return rollback monitor status for the API."""
        return {
            "config": asdict(self._config),
        }
