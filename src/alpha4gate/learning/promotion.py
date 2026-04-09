"""Model promotion gate: evaluate new checkpoints and promote if better."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from alpha4gate.learning.evaluator import EvalResult, ModelEvaluator

_log = logging.getLogger(__name__)


@dataclass
class PromotionConfig:
    """Configuration for the promotion gate."""

    eval_games: int = 20
    win_rate_threshold: float = 0.05
    min_eval_games: int = 10


@dataclass
class PromotionDecision:
    """Result of a promotion evaluation."""

    new_checkpoint: str
    old_best: str
    new_eval: EvalResult
    old_eval: EvalResult | None
    promoted: bool
    reason: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class PromotionManager:
    """Evaluates new checkpoints against the current best and promotes if better.

    Uses the ModelEvaluator to run inference-only games, then compares win rates.
    The training database (training.db) is the source of truth for win rates --
    stats.json is NOT consulted.
    """

    def __init__(self, evaluator: ModelEvaluator, config: PromotionConfig) -> None:
        self._evaluator = evaluator
        self._config = config
        self._history: list[PromotionDecision] = []

    @property
    def history(self) -> list[PromotionDecision]:
        """Return the promotion decision history."""
        return list(self._history)

    def evaluate_and_promote(
        self,
        new_checkpoint: str,
        difficulty: int,
    ) -> PromotionDecision:
        """Evaluate a new checkpoint against the current best and promote if better.

        Args:
            new_checkpoint: Name of the newly trained checkpoint (e.g., "v5").
            difficulty: SC2 AI difficulty level for evaluation games.

        Returns:
            PromotionDecision with evaluation results and promotion outcome.
        """
        from alpha4gate.learning.checkpoints import get_best_name, promote_checkpoint

        checkpoint_dir = self._evaluator._checkpoint_dir
        old_best = get_best_name(checkpoint_dir)

        # If there's no current best, promote unconditionally
        if old_best is None:
            _log.info("No current best checkpoint -- promoting %s", new_checkpoint)
            new_eval = self._evaluator.evaluate(
                new_checkpoint, self._config.eval_games, difficulty
            )
            promote_checkpoint(checkpoint_dir, new_checkpoint)
            decision = PromotionDecision(
                new_checkpoint=new_checkpoint,
                old_best="none",
                new_eval=new_eval,
                old_eval=None,
                promoted=True,
                reason="no previous best checkpoint",
            )
            self._history.append(decision)
            return decision

        # Evaluate both checkpoints
        _log.info(
            "Evaluating promotion: %s vs %s (%d games, difficulty %d)",
            new_checkpoint, old_best, self._config.eval_games, difficulty,
        )
        new_eval = self._evaluator.evaluate(
            new_checkpoint, self._config.eval_games, difficulty
        )
        old_eval = self._evaluator.evaluate(
            old_best, self._config.eval_games, difficulty
        )

        # Check if new checkpoint is better by threshold
        delta = new_eval.win_rate - old_eval.win_rate
        total_games = new_eval.games_played + old_eval.games_played
        enough_games = total_games >= self._config.min_eval_games

        if delta > self._config.win_rate_threshold and enough_games:
            promoted = True
            reason = (
                f"new checkpoint wins: {new_eval.win_rate:.2%} vs "
                f"{old_eval.win_rate:.2%} (delta={delta:+.2%}, "
                f"threshold={self._config.win_rate_threshold:.2%})"
            )
            promote_checkpoint(checkpoint_dir, new_checkpoint)
            _log.info("Promoted %s -> %s: %s", old_best, new_checkpoint, reason)
        elif not enough_games:
            promoted = False
            reason = (
                f"insufficient eval games: {total_games} < "
                f"{self._config.min_eval_games}"
            )
            _log.info("Not promoted: %s", reason)
        else:
            promoted = False
            reason = (
                f"new checkpoint not better enough: {new_eval.win_rate:.2%} vs "
                f"{old_eval.win_rate:.2%} (delta={delta:+.2%}, "
                f"threshold={self._config.win_rate_threshold:.2%})"
            )
            _log.info("Not promoted: %s", reason)

        decision = PromotionDecision(
            new_checkpoint=new_checkpoint,
            old_best=old_best,
            new_eval=new_eval,
            old_eval=old_eval,
            promoted=promoted,
            reason=reason,
        )
        self._history.append(decision)
        return decision

    def manual_promote(self, checkpoint_name: str) -> PromotionDecision:
        """Manually promote a checkpoint without evaluation.

        Args:
            checkpoint_name: Name of the checkpoint to promote.

        Returns:
            PromotionDecision recording the manual promotion.
        """
        from alpha4gate.learning.checkpoints import get_best_name, promote_checkpoint

        checkpoint_dir = self._evaluator._checkpoint_dir
        old_best = get_best_name(checkpoint_dir) or "none"

        promote_checkpoint(checkpoint_dir, checkpoint_name)
        _log.info("Manual promotion: %s -> %s", old_best, checkpoint_name)

        decision = PromotionDecision(
            new_checkpoint=checkpoint_name,
            old_best=old_best,
            new_eval=EvalResult(
                checkpoint=checkpoint_name,
                games_played=0,
                wins=0,
                losses=0,
                win_rate=0.0,
                avg_reward=0.0,
                avg_duration=0.0,
                difficulty=0,
                action_distribution=None,
            ),
            old_eval=None,
            promoted=True,
            reason="manual promotion",
        )
        self._history.append(decision)
        return decision

    def get_history_dicts(self) -> list[dict[str, Any]]:
        """Return promotion history as a list of JSON-serialisable dicts."""
        result: list[dict[str, Any]] = []
        for d in self._history:
            entry = asdict(d)
            result.append(entry)
        return result
