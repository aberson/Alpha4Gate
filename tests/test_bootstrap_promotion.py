"""Finding #11 — PromotionManager refuses to bootstrap-promote when asked not to.

Context: the default PromotionManager short-circuits to "promote" when
`get_best_name(checkpoint_dir)` returns None. That default exists for the
very first promotion of a fresh training run. The problem surfaced in the
always-up Phase 4.5 soak: the comparison path (the whole point of the gate)
was never exercised because every run's manifest was effectively unseeded.

Fix: an `allow_bootstrap` flag on `evaluate_and_promote`. Default True
preserves current behavior; Phase 1.8 will flip the default to False once
`bots/v0/manifest.json` is pre-seeded with a real `best`/`previous_best`,
so the first post-migration promotion goes through the real WR-delta path.

These tests verify the flag's three-way contract:
  (a) allow_bootstrap=False + no prior best -> raises ValueError
  (b) allow_bootstrap=False + prior best -> runs comparison path
  (c) allow_bootstrap=True (default) + no prior best -> unconditional promote
      (current behavior preserved for backward compat)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from bots.v0.learning.evaluator import EvalResult
from bots.v0.learning.promotion import PromotionConfig, PromotionManager

if TYPE_CHECKING:
    pass


def _make_evaluator(
    checkpoint_dir: Path, *, new_wr: float = 0.8, old_wr: float = 0.5
) -> MagicMock:
    """Build a mock ModelEvaluator with a side_effect that returns EvalResults.

    First call (new checkpoint) returns new_wr; second (old best) returns old_wr.
    """
    evaluator = MagicMock()
    evaluator._checkpoint_dir = checkpoint_dir

    def _eval(ckpt: str, games: int, difficulty: int, **_kwargs: object) -> EvalResult:
        wr = new_wr if "new" in ckpt or ckpt.startswith("v") else old_wr
        return EvalResult(
            checkpoint=ckpt,
            games_played=games,
            wins=int(games * wr),
            losses=games - int(games * wr),
            crashed=0,
            win_rate=wr,
            avg_reward=0.0,
            avg_duration=0.0,
            difficulty=difficulty,
            action_distribution=None,
        )

    evaluator.evaluate.side_effect = _eval
    return evaluator


class TestFindingEleven:
    def test_a_refuses_bootstrap_when_not_allowed(self, tmp_path: Path) -> None:
        """allow_bootstrap=False with no prior best raises a clear ValueError."""
        evaluator = _make_evaluator(tmp_path)
        manager = PromotionManager(evaluator, PromotionConfig())

        with pytest.raises(ValueError, match="manifest not seeded"):
            manager.evaluate_and_promote(
                new_checkpoint="v1",
                difficulty=3,
                allow_bootstrap=False,
            )

        # No evaluator call should have happened -- we fail fast before evaling.
        evaluator.evaluate.assert_not_called()

    def test_b_runs_comparison_when_seeded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """allow_bootstrap=False with a prior best runs the real comparison path."""
        # Simulate a seeded checkpoint state.
        (tmp_path / "best.zip").write_bytes(b"fake")
        monkeypatch.setattr(
            "bots.v0.learning.checkpoints.get_best_name",
            lambda _d: "best",
        )
        monkeypatch.setattr(
            "bots.v0.learning.checkpoints.promote_checkpoint",
            lambda _d, _n: None,
        )

        evaluator = _make_evaluator(tmp_path, new_wr=0.8, old_wr=0.5)
        manager = PromotionManager(evaluator, PromotionConfig())

        decision = manager.evaluate_and_promote(
            new_checkpoint="v1",
            difficulty=3,
            allow_bootstrap=False,
        )

        # Ran the real comparison: both checkpoints evaluated.
        assert evaluator.evaluate.call_count == 2
        # Win-rate delta path was taken.
        assert decision.reason_code == "win_rate_gate"
        assert decision.promoted is True
        assert decision.old_best == "best"

    def test_c_default_preserves_legacy_bootstrap_behavior(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """allow_bootstrap default is True: no prior best -> unconditional promote."""
        monkeypatch.setattr(
            "bots.v0.learning.checkpoints.get_best_name",
            lambda _d: None,
        )
        monkeypatch.setattr(
            "bots.v0.learning.checkpoints.promote_checkpoint",
            lambda _d, _n: None,
        )

        evaluator = _make_evaluator(tmp_path)
        manager = PromotionManager(evaluator, PromotionConfig())

        decision = manager.evaluate_and_promote(
            new_checkpoint="v1",
            difficulty=3,
        )

        assert decision.promoted is True
        assert decision.reason_code == "first_baseline"
        assert decision.old_best == "none"
