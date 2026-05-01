"""Model promotion gate: evaluate new checkpoints and promote if better."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bots.v8.learning.evaluator import EvalResult, ModelEvaluator

_log = logging.getLogger(__name__)


@dataclass
class PromotionConfig:
    """Configuration for the promotion gate."""

    eval_games: int = 20
    win_rate_threshold: float = 0.05
    min_eval_games: int = 10
    # Maximum number of crashed eval games tolerated per EvalResult. Any
    # crashed games are a strong signal that the evaluator cannot trust
    # the reported win_rate -- crashed games used to be silently counted
    # as losses (Phase 4.5 blocker #67). Default 0: refuse to promote if
    # ANY eval game crashed on either the new or the old checkpoint.
    max_crashed: int = 0


@dataclass
class PromotionDecision:
    """Result of a promotion evaluation.

    ``reason`` is a human-readable string (historical). ``reason_code`` is a
    stable machine-readable classifier so the dashboard can label entries
    differently without parsing free-form text. Known codes:

    - ``first_baseline``           first-ever promotion (no prior best)
    - ``win_rate_gate``            accepted by the win-rate delta check
    - ``rejected_not_better``      new checkpoint not better enough
    - ``rejected_insufficient_games`` eval had too few games
    - ``rejected_crashed``         eval had too many crashed games
    - ``manual``                   manual promotion via API
    """

    new_checkpoint: str
    old_best: str
    new_eval: EvalResult
    old_eval: EvalResult | None
    promoted: bool
    reason: str
    difficulty: int = 0
    action_distribution_shift: float | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    reason_code: str = ""


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
        cancel_check: Callable[[], bool] | None = None,
        allow_bootstrap: bool = True,
    ) -> PromotionDecision:
        """Evaluate a new checkpoint against the current best and promote if better.

        Args:
            new_checkpoint: Name of the newly trained checkpoint (e.g., "v5").
            difficulty: SC2 AI difficulty level for evaluation games.
            cancel_check: Optional callable returning True to signal cancel.
                Threaded through to each underlying ``evaluate()`` call so the
                daemon's stop signal halts promotion evals between games. A
                partial eval will fail the ``min_eval_games`` gate and the
                promotion will be refused (no unsafe promotion from truncated
                data).
            allow_bootstrap: If True (default, legacy behavior), promote
                unconditionally when there is no current best checkpoint.
                If False, raise ``ValueError`` instead -- forces the caller
                to pre-seed the manifest so the WR-delta comparison path
                is actually exercised. Flipped to False by callers once
                Phase 1.8 seeds ``bots/v0/manifest.json`` (always-up
                Finding #11).

        Returns:
            PromotionDecision with evaluation results and promotion outcome.

        Raises:
            ValueError: if ``allow_bootstrap=False`` and no current best
                checkpoint exists -- the manifest is unseeded and the gate
                refuses to bootstrap-promote.
        """
        from bots.v8.learning.checkpoints import get_best_name, promote_checkpoint

        checkpoint_dir = self._evaluator._checkpoint_dir
        old_best = get_best_name(checkpoint_dir)

        if old_best is None and not allow_bootstrap:
            raise ValueError(
                "manifest not seeded -- refusing to bootstrap-promote "
                f"{new_checkpoint!r} (set allow_bootstrap=True if this is "
                "a fresh training run with no prior best)"
            )

        # If there's no current best, promote unconditionally -- UNLESS
        # the eval run itself had too many crashes, in which case the
        # reported win_rate is untrustworthy (Phase 4.5 blocker #67).
        if old_best is None:
            _log.info("No current best checkpoint -- promoting %s", new_checkpoint)
            new_eval = self._evaluator.evaluate(
                new_checkpoint,
                self._config.eval_games,
                difficulty,
                cancel_check=cancel_check,
            )
            if new_eval.crashed > self._config.max_crashed:
                reason = (
                    f"too many crashed eval games: new={new_eval.crashed} "
                    f"(max_crashed={self._config.max_crashed})"
                )
                _log.warning("Not promoted (no previous best): %s", reason)
                decision = PromotionDecision(
                    new_checkpoint=new_checkpoint,
                    old_best="none",
                    new_eval=new_eval,
                    old_eval=None,
                    promoted=False,
                    reason=reason,
                    difficulty=difficulty,
                    reason_code="rejected_crashed",
                )
                self._history.append(decision)
                return decision
            promote_checkpoint(checkpoint_dir, new_checkpoint)
            decision = PromotionDecision(
                new_checkpoint=new_checkpoint,
                old_best="none",
                new_eval=new_eval,
                old_eval=None,
                promoted=True,
                reason="no previous best checkpoint",
                difficulty=difficulty,
                reason_code="first_baseline",
            )
            self._history.append(decision)
            return decision

        # Evaluate both checkpoints
        _log.info(
            "Evaluating promotion: %s vs %s (%d games, difficulty %d)",
            new_checkpoint,
            old_best,
            self._config.eval_games,
            difficulty,
        )
        new_eval = self._evaluator.evaluate(
            new_checkpoint,
            self._config.eval_games,
            difficulty,
            cancel_check=cancel_check,
        )
        old_eval = self._evaluator.evaluate(
            old_best,
            self._config.eval_games,
            difficulty,
            cancel_check=cancel_check,
        )

        # Refuse to promote if either eval had too many crashes. Crashed
        # games used to be silently counted as losses (Phase 4.5 blocker
        # #67) -- now they are surfaced and a promotion decision built on
        # a partially-crashed eval run is not trustworthy. Check this
        # BEFORE the win-rate comparison so a crashed-eval rejection
        # short-circuits the happy path.
        if (
            new_eval.crashed > self._config.max_crashed
            or old_eval.crashed > self._config.max_crashed
        ):
            reason = (
                f"too many crashed eval games: new={new_eval.crashed}, "
                f"old={old_eval.crashed} (max_crashed="
                f"{self._config.max_crashed})"
            )
            _log.warning("Not promoted: %s", reason)
            shift = compute_action_distribution_shift(
                old_eval.action_distribution,
                new_eval.action_distribution,
            )
            decision = PromotionDecision(
                new_checkpoint=new_checkpoint,
                old_best=old_best,
                new_eval=new_eval,
                old_eval=old_eval,
                promoted=False,
                reason=reason,
                difficulty=difficulty,
                action_distribution_shift=shift,
                reason_code="rejected_crashed",
            )
            self._history.append(decision)
            return decision

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
            reason_code = "win_rate_gate"
            promote_checkpoint(checkpoint_dir, new_checkpoint)
            _log.info("Promoted %s -> %s: %s", old_best, new_checkpoint, reason)
        elif not enough_games:
            promoted = False
            reason = f"insufficient eval games: {total_games} < {self._config.min_eval_games}"
            reason_code = "rejected_insufficient_games"
            _log.info("Not promoted: %s", reason)
        else:
            promoted = False
            reason = (
                f"new checkpoint not better enough: {new_eval.win_rate:.2%} vs "
                f"{old_eval.win_rate:.2%} (delta={delta:+.2%}, "
                f"threshold={self._config.win_rate_threshold:.2%})"
            )
            reason_code = "rejected_not_better"
            _log.info("Not promoted: %s", reason)

        # Compute action distribution shift if both evals have distributions
        shift = compute_action_distribution_shift(
            old_eval.action_distribution,
            new_eval.action_distribution,
        )

        decision = PromotionDecision(
            new_checkpoint=new_checkpoint,
            old_best=old_best,
            new_eval=new_eval,
            old_eval=old_eval,
            promoted=promoted,
            reason=reason,
            difficulty=difficulty,
            action_distribution_shift=shift,
            reason_code=reason_code,
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
        from bots.v8.learning.checkpoints import get_best_name, promote_checkpoint

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
                crashed=0,
                win_rate=0.0,
                avg_reward=0.0,
                avg_duration=0.0,
                difficulty=0,
                action_distribution=None,
            ),
            old_eval=None,
            promoted=True,
            reason="manual promotion",
            reason_code="manual",
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


def compute_action_distribution_shift(
    old_dist: list[float] | None,
    new_dist: list[float] | None,
) -> float | None:
    """Compute L1 distance between two action distributions.

    Returns None if either distribution is unavailable.
    """
    if old_dist is None or new_dist is None:
        return None
    if len(old_dist) != len(new_dist):
        return None
    return sum(abs(a - b) for a, b in zip(old_dist, new_dist, strict=True))


# Default paths (relative to project root; overridable in constructor)
_DEFAULT_WIKI_PATH = Path("documentation/wiki/promotions.md")


def _default_history_path() -> Path:
    """Resolve the default promotion-history path via the registry.

    Lazy to avoid importing orchestrator at module load (which would chain
    through ``bots.current``'s MetaPathFinder for any ``bots.<v>``-hosted
    caller and make import-time failures harder to diagnose).
    """
    from orchestrator.registry import resolve_data_path

    return resolve_data_path("promotion_history.json")


class PromotionLogger:
    """Logs promotion decisions to a JSON file and appends to the wiki page."""

    def __init__(
        self,
        history_path: Path | None = None,
        wiki_path: Path | None = None,
    ) -> None:
        self._history_path = history_path or _default_history_path()
        self._wiki_path = wiki_path or _DEFAULT_WIKI_PATH

    def _read_history(self) -> list[dict[str, Any]]:
        """Read existing history from JSON file, or return empty list.

        Self-heals if the file is corrupt (invalid JSON from a partial write,
        concurrent-writer race, or manual edit gone wrong). Without this, a
        single corrupt byte would silently swallow every subsequent
        ``log_decision`` call -- the daemon wraps this in ``try/except`` and
        every new entry would be lost without any signal on the Alerts tab.

        On corruption we rotate the bad file out of the way (preserving it
        for forensics) and return an empty list so the next write starts
        fresh.
        """
        if not self._history_path.exists():
            return []
        raw = self._history_path.read_text(encoding="utf-8")
        try:
            data: list[dict[str, Any]] = json.loads(raw)
            return data
        except json.JSONDecodeError as exc:
            suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
            corrupt_path = self._history_path.with_name(
                f"{self._history_path.stem}.corrupt.{suffix}.json"
            )
            try:
                self._history_path.rename(corrupt_path)
            except OSError:
                _log.exception(
                    "Failed to rotate corrupt promotion history file %s",
                    self._history_path,
                )
            _log.warning(
                "promotion_history.json at %s was corrupt (%s); "
                "rotated to %s and starting fresh",
                self._history_path,
                exc,
                corrupt_path,
            )
            return []

    def _write_history(self, entries: list[dict[str, Any]]) -> None:
        """Write history list to JSON file (pretty-printed)."""
        self._history_path.parent.mkdir(parents=True, exist_ok=True)
        self._history_path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")

    def log_decision(self, decision: PromotionDecision) -> dict[str, Any]:
        """Append a decision to the JSON log and update the wiki page.

        Returns the serialised decision dict that was logged.
        """
        entry = self._decision_to_dict(decision)

        # Append to JSON file
        entries = self._read_history()
        entries.append(entry)
        self._write_history(entries)

        # Append row to wiki page
        self._append_wiki_row(decision)

        _log.info(
            "Logged promotion decision: %s -> %s (%s)",
            decision.old_best,
            decision.new_checkpoint,
            "promoted" if decision.promoted else "rejected",
        )
        return entry

    def get_history(self) -> list[dict[str, Any]]:
        """Return the full promotion history from the JSON file."""
        return self._read_history()

    def get_latest(self) -> dict[str, Any] | None:
        """Return the most recent promotion decision, or None if empty."""
        entries = self._read_history()
        return entries[-1] if entries else None

    @staticmethod
    def _decision_to_dict(decision: PromotionDecision) -> dict[str, Any]:
        """Convert a PromotionDecision to a flat, JSON-serialisable dict."""
        new_wr = decision.new_eval.win_rate
        old_wr = decision.old_eval.win_rate if decision.old_eval else None
        delta = (new_wr - old_wr) if old_wr is not None else None
        eval_games = decision.new_eval.games_played
        if decision.old_eval:
            eval_games += decision.old_eval.games_played

        return {
            "timestamp": decision.timestamp,
            "new_checkpoint": decision.new_checkpoint,
            "old_best": decision.old_best,
            "new_win_rate": new_wr,
            "old_win_rate": old_wr,
            "delta": delta,
            "eval_games_played": eval_games,
            "promoted": decision.promoted,
            "reason": decision.reason,
            "reason_code": decision.reason_code,
            "difficulty": decision.difficulty,
            "action_distribution_shift": decision.action_distribution_shift,
        }

    def _append_wiki_row(self, decision: PromotionDecision) -> None:
        """Append a new row to the promotions wiki table."""
        if not self._wiki_path.exists():
            return

        # Parse timestamp to a readable date
        try:
            dt = datetime.fromisoformat(decision.timestamp)
            date_str = dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            date_str = decision.timestamp[:16]

        old_wr = f"{decision.old_eval.win_rate:.0%}" if decision.old_eval else "\u2014"
        new_wr = f"{decision.new_eval.win_rate:.0%}"
        win_rate_str = f"{old_wr}\u2192{new_wr}"

        eval_games = decision.new_eval.games_played
        if decision.old_eval:
            eval_games += decision.old_eval.games_played

        outcome = "promoted" if decision.promoted else "rejected"

        row = (
            f"| {date_str} | {decision.old_best} | {decision.new_checkpoint} "
            f"| {win_rate_str} | {eval_games} "
            f"| {decision.difficulty} | {decision.reason} | {outcome} |"
        )

        content = self._wiki_path.read_text(encoding="utf-8")
        content = content.rstrip("\n") + "\n" + row + "\n"
        self._wiki_path.write_text(content, encoding="utf-8")
