"""Tests for the model promotion gate."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from bots.v0.learning.checkpoints import (
    get_best_name,
    promote_checkpoint,
    save_checkpoint,
)
from bots.v0.learning.evaluator import EvalResult
from bots.v0.learning.promotion import (
    PromotionConfig,
    PromotionDecision,
    PromotionLogger,
    PromotionManager,
    compute_action_distribution_shift,
)


def _mock_model() -> MagicMock:
    """Create a mock SB3 model."""
    model = MagicMock()

    def save_side_effect(path: str) -> None:
        p = path if path.endswith(".zip") else path + ".zip"
        Path(p).touch()

    model.save.side_effect = save_side_effect
    return model


def _make_eval_result(
    checkpoint: str,
    win_rate: float,
    games: int = 20,
    crashed: int = 0,
) -> EvalResult:
    wins = int(games * win_rate)
    return EvalResult(
        checkpoint=checkpoint,
        games_played=games,
        wins=wins,
        losses=games - wins,
        crashed=crashed,
        win_rate=win_rate,
        avg_reward=1.0,
        avg_duration=300.0,
        difficulty=1,
        action_distribution=None,
    )


class TestPromoteCheckpoint:
    def test_promote_sets_best(self, tmp_path: Path) -> None:
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1")
        save_checkpoint(model, tmp_path, "v2")
        promote_checkpoint(tmp_path, "v2")
        assert get_best_name(tmp_path) == "v2"

    def test_promote_overwrites_previous_best(self, tmp_path: Path) -> None:
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1", is_best=True)
        assert get_best_name(tmp_path) == "v1"
        promote_checkpoint(tmp_path, "v2")
        assert get_best_name(tmp_path) == "v2"


class TestPromotionConfig:
    def test_defaults(self) -> None:
        config = PromotionConfig()
        assert config.eval_games == 20
        assert config.win_rate_threshold == 0.05
        assert config.min_eval_games == 10

    def test_custom(self) -> None:
        config = PromotionConfig(eval_games=50, win_rate_threshold=0.10, min_eval_games=20)
        assert config.eval_games == 50
        assert config.win_rate_threshold == 0.10


class TestPromotionDecision:
    def test_dataclass_fields(self) -> None:
        eval_result = _make_eval_result("v1", 0.6)
        d = PromotionDecision(
            new_checkpoint="v2",
            old_best="v1",
            new_eval=eval_result,
            old_eval=eval_result,
            promoted=True,
            reason="test",
        )
        assert d.promoted is True
        assert d.new_checkpoint == "v2"
        assert d.timestamp  # auto-generated


class TestPromotionManagerPromotes:
    def test_promotes_when_no_previous_best(self, tmp_path: Path) -> None:
        """When there's no best checkpoint, the new one should be promoted."""
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1", is_best=False)

        evaluator = MagicMock()
        evaluator._checkpoint_dir = tmp_path
        evaluator.evaluate.return_value = _make_eval_result("v1", 0.6)

        pm = PromotionManager(evaluator, PromotionConfig())
        decision = pm.evaluate_and_promote("v1", difficulty=1)

        assert decision.promoted is True
        assert decision.reason == "no previous best checkpoint"
        assert get_best_name(tmp_path) == "v1"

    def test_promotes_when_new_is_better(self, tmp_path: Path) -> None:
        """When new checkpoint beats old by more than threshold, promote."""
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1", is_best=True)
        save_checkpoint(model, tmp_path, "v2")

        evaluator = MagicMock()
        evaluator._checkpoint_dir = tmp_path

        # v2 has 0.7 win rate, v1 has 0.5 -- delta of 0.2 > 0.05 threshold
        def eval_side_effect(
            checkpoint: str, n_games: int, difficulty: int, **_kw: Any
        ) -> EvalResult:
            if checkpoint == "v2":
                return _make_eval_result("v2", 0.7, n_games)
            return _make_eval_result("v1", 0.5, n_games)

        evaluator.evaluate.side_effect = eval_side_effect

        pm = PromotionManager(evaluator, PromotionConfig())
        decision = pm.evaluate_and_promote("v2", difficulty=1)

        assert decision.promoted is True
        assert "new checkpoint wins" in decision.reason
        assert get_best_name(tmp_path) == "v2"

    def test_does_not_promote_when_not_better(self, tmp_path: Path) -> None:
        """When new checkpoint doesn't beat old by threshold, don't promote."""
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1", is_best=True)
        save_checkpoint(model, tmp_path, "v2")

        evaluator = MagicMock()
        evaluator._checkpoint_dir = tmp_path

        # Both have same win rate -- delta of 0.0, below 0.05 threshold
        evaluator.evaluate.return_value = _make_eval_result("v1", 0.5)

        pm = PromotionManager(evaluator, PromotionConfig())
        decision = pm.evaluate_and_promote("v2", difficulty=1)

        assert decision.promoted is False
        assert "not better enough" in decision.reason
        assert get_best_name(tmp_path) == "v1"  # unchanged

    def test_does_not_promote_when_worse(self, tmp_path: Path) -> None:
        """When new checkpoint is worse, don't promote."""
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1", is_best=True)
        save_checkpoint(model, tmp_path, "v2")

        evaluator = MagicMock()
        evaluator._checkpoint_dir = tmp_path

        def eval_side_effect(
            checkpoint: str, n_games: int, difficulty: int, **_kw: Any
        ) -> EvalResult:
            if checkpoint == "v2":
                return _make_eval_result("v2", 0.3, n_games)
            return _make_eval_result("v1", 0.7, n_games)

        evaluator.evaluate.side_effect = eval_side_effect

        pm = PromotionManager(evaluator, PromotionConfig())
        decision = pm.evaluate_and_promote("v2", difficulty=1)

        assert decision.promoted is False
        assert get_best_name(tmp_path) == "v1"

    def test_does_not_promote_insufficient_games(self, tmp_path: Path) -> None:
        """When total eval games < min_eval_games, don't promote."""
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1", is_best=True)
        save_checkpoint(model, tmp_path, "v2")

        evaluator = MagicMock()
        evaluator._checkpoint_dir = tmp_path

        # Use only 2 games each (total 4 < min_eval_games=10)
        def eval_side_effect(
            checkpoint: str, n_games: int, difficulty: int, **_kw: Any
        ) -> EvalResult:
            if checkpoint == "v2":
                return _make_eval_result("v2", 1.0, games=2)
            return _make_eval_result("v1", 0.0, games=2)

        evaluator.evaluate.side_effect = eval_side_effect

        pm = PromotionManager(evaluator, PromotionConfig(eval_games=2))
        decision = pm.evaluate_and_promote("v2", difficulty=1)

        assert decision.promoted is False
        assert "insufficient eval games" in decision.reason


class TestPromotionGateCrashRefusal:
    """Phase 4.5 blocker #67: refuse to promote if either eval had crashes.

    The win_rate on a partially-crashed eval run cannot be trusted -- the
    old behavior silently counted crashes as losses. The promotion gate
    now requires both the new and old eval results to have
    ``crashed <= max_crashed`` (default 0) before even looking at the
    win_rate delta.
    """

    def test_refuses_when_new_eval_has_crashes(self, tmp_path: Path) -> None:
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1", is_best=True)
        save_checkpoint(model, tmp_path, "v2")

        evaluator = MagicMock()
        evaluator._checkpoint_dir = tmp_path

        def eval_side_effect(
            checkpoint: str, n_games: int, difficulty: int, **_kw: Any
        ) -> EvalResult:
            if checkpoint == "v2":
                return _make_eval_result("v2", 0.9, crashed=2)
            return _make_eval_result("v1", 0.5, crashed=0)

        evaluator.evaluate.side_effect = eval_side_effect

        pm = PromotionManager(evaluator, PromotionConfig())
        decision = pm.evaluate_and_promote("v2", difficulty=1)

        assert decision.promoted is False
        assert "crashed" in decision.reason
        # The old best MUST still be v1 (the promotion was refused, not
        # just delayed).
        assert get_best_name(tmp_path) == "v1"

    def test_refuses_when_old_eval_has_crashes(self, tmp_path: Path) -> None:
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1", is_best=True)
        save_checkpoint(model, tmp_path, "v2")

        evaluator = MagicMock()
        evaluator._checkpoint_dir = tmp_path

        def eval_side_effect(
            checkpoint: str, n_games: int, difficulty: int, **_kw: Any
        ) -> EvalResult:
            if checkpoint == "v2":
                return _make_eval_result("v2", 0.9, crashed=0)
            return _make_eval_result("v1", 0.5, crashed=3)

        evaluator.evaluate.side_effect = eval_side_effect

        pm = PromotionManager(evaluator, PromotionConfig())
        decision = pm.evaluate_and_promote("v2", difficulty=1)

        assert decision.promoted is False
        assert "crashed" in decision.reason
        assert get_best_name(tmp_path) == "v1"

    def test_refuses_first_checkpoint_with_crashes(self, tmp_path: Path) -> None:
        """Even the 'no previous best' fast path must refuse on crashes."""
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1", is_best=False)

        evaluator = MagicMock()
        evaluator._checkpoint_dir = tmp_path
        evaluator.evaluate.return_value = _make_eval_result("v1", 0.6, crashed=1)

        pm = PromotionManager(evaluator, PromotionConfig())
        decision = pm.evaluate_and_promote("v1", difficulty=1)

        assert decision.promoted is False
        assert "crashed" in decision.reason
        # Nothing was promoted.
        assert get_best_name(tmp_path) is None

    def test_promotes_when_crashes_within_tolerance(self, tmp_path: Path) -> None:
        """A non-zero ``max_crashed`` lets small crash counts through."""
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1", is_best=True)
        save_checkpoint(model, tmp_path, "v2")

        evaluator = MagicMock()
        evaluator._checkpoint_dir = tmp_path

        def eval_side_effect(
            checkpoint: str, n_games: int, difficulty: int, **_kw: Any
        ) -> EvalResult:
            if checkpoint == "v2":
                return _make_eval_result("v2", 0.9, crashed=1)
            return _make_eval_result("v1", 0.5, crashed=0)

        evaluator.evaluate.side_effect = eval_side_effect

        pm = PromotionManager(evaluator, PromotionConfig(max_crashed=2))
        decision = pm.evaluate_and_promote("v2", difficulty=1)

        assert decision.promoted is True
        assert get_best_name(tmp_path) == "v2"


class TestPromotionCancelCheck:
    """cancel_check passed to evaluate_and_promote threads through to both
    underlying evaluate() calls. The daemon's self._stop_event.is_set is the
    production caller, so POST /api/training/stop also halts promotion evals.
    """

    def test_cancel_check_forwarded_no_previous_best(
        self, tmp_path: Path
    ) -> None:
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1", is_best=False)

        evaluator = MagicMock()
        evaluator._checkpoint_dir = tmp_path
        evaluator.evaluate.return_value = _make_eval_result("v1", 0.6)

        sentinel: Any = lambda: False  # noqa: E731
        pm = PromotionManager(evaluator, PromotionConfig())
        pm.evaluate_and_promote("v1", difficulty=1, cancel_check=sentinel)

        evaluator.evaluate.assert_called_once()
        assert evaluator.evaluate.call_args.kwargs["cancel_check"] is sentinel

    def test_cancel_check_forwarded_to_both_evals(self, tmp_path: Path) -> None:
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1", is_best=True)
        save_checkpoint(model, tmp_path, "v2")

        evaluator = MagicMock()
        evaluator._checkpoint_dir = tmp_path

        def side_effect(
            checkpoint: str, n_games: int, difficulty: int, **_kw: Any
        ) -> EvalResult:
            return _make_eval_result(checkpoint, 0.5, n_games)

        evaluator.evaluate.side_effect = side_effect

        sentinel: Any = lambda: False  # noqa: E731
        pm = PromotionManager(evaluator, PromotionConfig())
        pm.evaluate_and_promote("v2", difficulty=1, cancel_check=sentinel)

        assert evaluator.evaluate.call_count == 2
        for call in evaluator.evaluate.call_args_list:
            assert call.kwargs["cancel_check"] is sentinel


class TestPromotionManagerManual:
    def test_manual_promote(self, tmp_path: Path) -> None:
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1", is_best=True)
        save_checkpoint(model, tmp_path, "v2")

        evaluator = MagicMock()
        evaluator._checkpoint_dir = tmp_path

        pm = PromotionManager(evaluator, PromotionConfig())
        decision = pm.manual_promote("v2")

        assert decision.promoted is True
        assert decision.reason == "manual promotion"
        assert decision.old_best == "v1"
        assert get_best_name(tmp_path) == "v2"


class TestPromotionHistory:
    def test_history_accumulates(self, tmp_path: Path) -> None:
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1")

        evaluator = MagicMock()
        evaluator._checkpoint_dir = tmp_path
        evaluator.evaluate.return_value = _make_eval_result("v1", 0.5)

        pm = PromotionManager(evaluator, PromotionConfig())
        pm.evaluate_and_promote("v1", difficulty=1)

        assert len(pm.history) == 1

        save_checkpoint(model, tmp_path, "v2")
        pm.manual_promote("v2")
        assert len(pm.history) == 2

    def test_get_history_dicts(self, tmp_path: Path) -> None:
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1")

        evaluator = MagicMock()
        evaluator._checkpoint_dir = tmp_path
        evaluator.evaluate.return_value = _make_eval_result("v1", 0.5)

        pm = PromotionManager(evaluator, PromotionConfig())
        pm.evaluate_and_promote("v1", difficulty=1)

        dicts = pm.get_history_dicts()
        assert len(dicts) == 1
        assert dicts[0]["promoted"] is True
        assert "timestamp" in dicts[0]


class TestTrainerNoLongerMarksBest:
    """Verify that trainer.py saves with is_best=False."""

    @patch("bots.v0.learning.trainer.TrainingOrchestrator._make_env")
    @patch("bots.v0.learning.trainer.TrainingOrchestrator._init_or_resume_model")
    def test_trainer_saves_is_best_false(
        self, mock_init: MagicMock, mock_env: MagicMock, tmp_path: Path
    ) -> None:
        from bots.v0.learning.database import TrainingDB
        from bots.v0.learning.trainer import TrainingOrchestrator

        mock_init.return_value = _mock_model()
        mock_env.return_value = MagicMock()
        db_path = tmp_path / "train.db"
        db = TrainingDB(db_path)
        # Seed wins so win rate > 0
        db.store_game("g1", "Simple64", 1, "win", 60.0, 1.0, "v0")
        db.store_game("g2", "Simple64", 1, "win", 60.0, 1.0, "v0")
        db.close()

        orch = TrainingOrchestrator(
            checkpoint_dir=str(tmp_path / "cp"),
            db_path=str(db_path),
        )
        orch.run(n_cycles=2, games_per_cycle=1)

        # Trainer should NOT set any checkpoint as best
        assert get_best_name(tmp_path / "cp") is None


class TestPromotionApiEndpoints:
    def test_get_promotions_empty(self, tmp_path: Path) -> None:
        from bots.v0.api import app, configure
        from fastapi.testclient import TestClient

        data_dir = tmp_path / "data"
        log_dir = tmp_path / "logs"
        replay_dir = tmp_path / "replays"
        data_dir.mkdir()
        log_dir.mkdir()
        replay_dir.mkdir()
        configure(data_dir, log_dir, replay_dir)

        # Reset the module-level promotion manager to avoid state leaks
        import bots.v0.api as api_mod

        api_mod._promotion_manager = None

        client = TestClient(app)
        resp = client.get("/api/training/promotions")
        assert resp.status_code == 200
        assert resp.json()["promotions"] == []

    def test_manual_promote_requires_checkpoint(self, tmp_path: Path) -> None:
        from bots.v0.api import app, configure
        from fastapi.testclient import TestClient

        data_dir = tmp_path / "data"
        log_dir = tmp_path / "logs"
        replay_dir = tmp_path / "replays"
        data_dir.mkdir()
        log_dir.mkdir()
        replay_dir.mkdir()
        configure(data_dir, log_dir, replay_dir)

        import bots.v0.api as api_mod

        api_mod._promotion_manager = None

        client = TestClient(app)
        resp = client.post("/api/training/promote", json={})
        assert resp.status_code == 400
        assert "checkpoint is required" in resp.json()["error"]

    def test_manual_promote_success(self, tmp_path: Path) -> None:
        from bots.v0.api import app, configure
        from fastapi.testclient import TestClient

        data_dir = tmp_path / "data"
        log_dir = tmp_path / "logs"
        replay_dir = tmp_path / "replays"
        data_dir.mkdir()
        log_dir.mkdir()
        replay_dir.mkdir()
        configure(data_dir, log_dir, replay_dir)

        import bots.v0.api as api_mod

        api_mod._promotion_manager = None
        api_mod._evaluator = None

        # Create a checkpoint to promote
        cp_dir = data_dir / "checkpoints"
        cp_dir.mkdir()
        model = _mock_model()
        save_checkpoint(model, cp_dir, "v5")

        client = TestClient(app)
        resp = client.post("/api/training/promote", json={"checkpoint": "v5"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "promoted"
        assert data["checkpoint"] == "v5"

        # Verify it shows in promotions history
        resp2 = client.get("/api/training/promotions")
        assert len(resp2.json()["promotions"]) == 1


class TestActionDistributionShift:
    def test_l1_distance_identical(self) -> None:
        dist = [0.2, 0.3, 0.5]
        assert compute_action_distribution_shift(dist, dist) == 0.0

    def test_l1_distance_different(self) -> None:
        old = [0.2, 0.3, 0.5]
        new = [0.4, 0.1, 0.5]
        # |0.2-0.4| + |0.3-0.1| + |0.5-0.5| = 0.2 + 0.2 + 0.0 = 0.4
        result = compute_action_distribution_shift(old, new)
        assert result is not None
        assert abs(result - 0.4) < 1e-9

    def test_returns_none_when_missing(self) -> None:
        assert compute_action_distribution_shift(None, [0.5, 0.5]) is None
        assert compute_action_distribution_shift([0.5, 0.5], None) is None
        assert compute_action_distribution_shift(None, None) is None

    def test_returns_none_for_length_mismatch(self) -> None:
        assert compute_action_distribution_shift([0.5, 0.5], [0.3, 0.3, 0.4]) is None

    def test_set_on_decision_by_manager(self, tmp_path: Path) -> None:
        """PromotionManager populates action_distribution_shift when distributions exist."""
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1", is_best=True)
        save_checkpoint(model, tmp_path, "v2")

        evaluator = MagicMock()
        evaluator._checkpoint_dir = tmp_path

        def eval_side_effect(
            checkpoint: str, n_games: int, difficulty: int, **_kw: Any
        ) -> EvalResult:
            dist = [0.6, 0.4] if checkpoint == "v2" else [0.3, 0.7]
            return EvalResult(
                checkpoint=checkpoint,
                games_played=n_games,
                wins=(int(n_games * 0.7) if checkpoint == "v2" else int(n_games * 0.5)),
                losses=(
                    n_games - int(n_games * 0.7)
                    if checkpoint == "v2"
                    else n_games - int(n_games * 0.5)
                ),
                crashed=0,
                win_rate=0.7 if checkpoint == "v2" else 0.5,
                avg_reward=1.0,
                avg_duration=300.0,
                difficulty=1,
                action_distribution=dist,
            )

        evaluator.evaluate.side_effect = eval_side_effect

        pm = PromotionManager(evaluator, PromotionConfig())
        decision = pm.evaluate_and_promote("v2", difficulty=1)

        assert decision.action_distribution_shift is not None
        # |0.3-0.6| + |0.7-0.4| = 0.3 + 0.3 = 0.6
        assert abs(decision.action_distribution_shift - 0.6) < 1e-9
        assert decision.difficulty == 1


class TestPromotionLogger:
    def test_log_decision_creates_json(self, tmp_path: Path) -> None:
        logger = PromotionLogger(
            history_path=tmp_path / "history.json",
            wiki_path=tmp_path / "promotions.md",
        )
        # Create a minimal wiki file so append works
        wiki = tmp_path / "promotions.md"
        wiki.write_text(
            "| Date | From | To | Win Rate (Old\u2192New) |"
            " Games | Difficulty | Reason | Outcome |\n"
            "|------|------|----|-------------------|"
            "-------|------------|--------|--------|\n",
            encoding="utf-8",
        )

        decision = PromotionDecision(
            new_checkpoint="v2",
            old_best="v1",
            new_eval=_make_eval_result("v2", 0.7),
            old_eval=_make_eval_result("v1", 0.5),
            promoted=True,
            reason="new checkpoint wins",
            difficulty=1,
            action_distribution_shift=0.4,
        )

        entry = logger.log_decision(decision)

        # Verify JSON file
        history_path = tmp_path / "history.json"
        assert history_path.exists()
        data = json.loads(history_path.read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["new_checkpoint"] == "v2"
        assert data[0]["promoted"] is True
        assert data[0]["difficulty"] == 1
        assert data[0]["action_distribution_shift"] == 0.4

        # Verify the returned entry
        assert entry["new_win_rate"] == 0.7
        assert entry["old_win_rate"] == 0.5

    def test_log_decision_appends(self, tmp_path: Path) -> None:
        logger = PromotionLogger(
            history_path=tmp_path / "history.json",
            wiki_path=tmp_path / "promotions.md",
        )
        wiki = tmp_path / "promotions.md"
        wiki.write_text("| Date |\n|------|\n", encoding="utf-8")

        d1 = PromotionDecision(
            new_checkpoint="v1",
            old_best="none",
            new_eval=_make_eval_result("v1", 0.6),
            old_eval=None,
            promoted=True,
            reason="first",
        )
        d2 = PromotionDecision(
            new_checkpoint="v2",
            old_best="v1",
            new_eval=_make_eval_result("v2", 0.8),
            old_eval=_make_eval_result("v1", 0.6),
            promoted=True,
            reason="better",
        )

        logger.log_decision(d1)
        logger.log_decision(d2)

        data = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
        assert len(data) == 2
        assert data[0]["new_checkpoint"] == "v1"
        assert data[1]["new_checkpoint"] == "v2"

    def test_wiki_row_appended(self, tmp_path: Path) -> None:
        wiki = tmp_path / "promotions.md"
        wiki.write_text(
            "| Date | From | To | Win Rate (Old\u2192New) |"
            " Games | Difficulty | Reason | Outcome |\n"
            "|------|------|----|-------------------|"
            "-------|------------|--------|--------|\n",
            encoding="utf-8",
        )

        logger = PromotionLogger(
            history_path=tmp_path / "history.json",
            wiki_path=wiki,
        )

        decision = PromotionDecision(
            new_checkpoint="v3",
            old_best="v2",
            new_eval=_make_eval_result("v3", 0.8),
            old_eval=_make_eval_result("v2", 0.6),
            promoted=True,
            reason="improved",
            difficulty=2,
        )
        logger.log_decision(decision)

        content = wiki.read_text(encoding="utf-8")
        lines = content.strip().split("\n")
        last_line = lines[-1]
        assert "| v2 |" in last_line
        assert "| v3 |" in last_line
        assert "promoted" in last_line

    def test_get_history_empty(self, tmp_path: Path) -> None:
        logger = PromotionLogger(history_path=tmp_path / "history.json")
        assert logger.get_history() == []

    def test_get_latest_none(self, tmp_path: Path) -> None:
        logger = PromotionLogger(history_path=tmp_path / "history.json")
        assert logger.get_latest() is None

    def test_get_latest_returns_last(self, tmp_path: Path) -> None:
        logger = PromotionLogger(
            history_path=tmp_path / "history.json",
            wiki_path=tmp_path / "promotions.md",
        )
        wiki = tmp_path / "promotions.md"
        wiki.write_text("| Date |\n|------|\n", encoding="utf-8")

        for name in ["v1", "v2", "v3"]:
            d = PromotionDecision(
                new_checkpoint=name,
                old_best="prev",
                new_eval=_make_eval_result(name, 0.7),
                old_eval=None,
                promoted=True,
                reason="test",
            )
            logger.log_decision(d)

        latest = logger.get_latest()
        assert latest is not None
        assert latest["new_checkpoint"] == "v3"

    def test_json_serialization_round_trip(self, tmp_path: Path) -> None:
        """Verify the JSON file is valid and can be deserialized."""
        logger = PromotionLogger(
            history_path=tmp_path / "history.json",
            wiki_path=tmp_path / "promotions.md",
        )
        wiki = tmp_path / "promotions.md"
        wiki.write_text("# Promotions\n", encoding="utf-8")

        decision = PromotionDecision(
            new_checkpoint="v5",
            old_best="v4",
            new_eval=_make_eval_result("v5", 0.75),
            old_eval=_make_eval_result("v4", 0.65),
            promoted=True,
            reason="delta sufficient",
            difficulty=3,
            action_distribution_shift=0.15,
        )
        logger.log_decision(decision)

        raw = (tmp_path / "history.json").read_text(encoding="utf-8")
        entries = json.loads(raw)
        assert len(entries) == 1
        e = entries[0]
        assert e["timestamp"] == decision.timestamp
        assert e["new_checkpoint"] == "v5"
        assert e["old_best"] == "v4"
        assert abs(e["new_win_rate"] - 0.75) < 1e-9
        assert abs(e["old_win_rate"] - 0.65) < 1e-9
        assert abs(e["delta"] - 0.10) < 1e-9
        assert e["eval_games_played"] == 40  # 20 + 20
        assert e["promoted"] is True
        assert e["difficulty"] == 3
        assert abs(e["action_distribution_shift"] - 0.15) < 1e-9


class TestPromotionHistoryApiEndpoints:
    def _setup_api(self, tmp_path: Path) -> Any:  # noqa: ANN401
        from bots.v0.api import app, configure
        from fastapi.testclient import TestClient

        data_dir = tmp_path / "data"
        log_dir = tmp_path / "logs"
        replay_dir = tmp_path / "replays"
        data_dir.mkdir()
        log_dir.mkdir()
        replay_dir.mkdir()
        configure(data_dir, log_dir, replay_dir)

        import bots.v0.api as api_mod

        api_mod._promotion_manager = None
        api_mod._promotion_logger = None

        return TestClient(app)

    def test_history_empty(self, tmp_path: Path) -> None:
        client = self._setup_api(tmp_path)
        resp = client.get("/api/training/promotions/history")
        assert resp.status_code == 200
        assert resp.json()["history"] == []

    def test_latest_empty(self, tmp_path: Path) -> None:
        client = self._setup_api(tmp_path)
        resp = client.get("/api/training/promotions/latest")
        assert resp.status_code == 200
        assert resp.json()["latest"] is None

    def test_history_with_data(self, tmp_path: Path) -> None:
        client = self._setup_api(tmp_path)

        # Write a history file directly
        history_path = tmp_path / "data" / "promotion_history.json"
        entries = [
            {
                "timestamp": "2026-04-09T12:00:00+00:00",
                "new_checkpoint": "v2",
                "old_best": "v1",
                "new_win_rate": 0.7,
                "old_win_rate": 0.5,
                "delta": 0.2,
                "eval_games_played": 40,
                "promoted": True,
                "reason": "test",
                "difficulty": 1,
                "action_distribution_shift": None,
            }
        ]
        history_path.write_text(json.dumps(entries), encoding="utf-8")

        resp = client.get("/api/training/promotions/history")
        assert resp.status_code == 200
        data = resp.json()["history"]
        assert len(data) == 1
        assert data[0]["new_checkpoint"] == "v2"

    def test_latest_with_data(self, tmp_path: Path) -> None:
        client = self._setup_api(tmp_path)

        history_path = tmp_path / "data" / "promotion_history.json"
        entries = [
            {"new_checkpoint": "v1", "promoted": True},
            {"new_checkpoint": "v2", "promoted": False},
        ]
        history_path.write_text(json.dumps(entries), encoding="utf-8")

        resp = client.get("/api/training/promotions/latest")
        assert resp.status_code == 200
        assert resp.json()["latest"]["new_checkpoint"] == "v2"


class TestPromotionReasonCodes:
    """Each promotion path stamps a stable machine-readable ``reason_code``.

    The free-form ``reason`` string is preserved for humans, but the dashboard
    classifies entries using ``reason_code`` so it can label ``first_baseline``
    entries differently from ``win_rate_gate`` promotions without parsing
    the free-form text (Phase 4.6 Step 4).
    """

    def test_first_baseline_reason_code(self, tmp_path: Path) -> None:
        """No prior best -> reason_code='first_baseline'."""
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1", is_best=False)

        evaluator = MagicMock()
        evaluator._checkpoint_dir = tmp_path
        evaluator.evaluate.return_value = _make_eval_result("v1", 0.6)

        pm = PromotionManager(evaluator, PromotionConfig())
        decision = pm.evaluate_and_promote("v1", difficulty=1)

        assert decision.promoted is True
        assert decision.reason_code == "first_baseline"

    def test_win_rate_gate_reason_code(self, tmp_path: Path) -> None:
        """Win rate beats threshold -> reason_code='win_rate_gate'."""
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1", is_best=True)
        save_checkpoint(model, tmp_path, "v2")

        evaluator = MagicMock()
        evaluator._checkpoint_dir = tmp_path

        def eval_side_effect(
            checkpoint: str, n_games: int, difficulty: int, **_kw: Any
        ) -> EvalResult:
            if checkpoint == "v2":
                return _make_eval_result("v2", 0.9, n_games)
            return _make_eval_result("v1", 0.5, n_games)

        evaluator.evaluate.side_effect = eval_side_effect

        pm = PromotionManager(evaluator, PromotionConfig())
        decision = pm.evaluate_and_promote("v2", difficulty=1)

        assert decision.promoted is True
        assert decision.reason_code == "win_rate_gate"

    def test_rejected_not_better_reason_code(self, tmp_path: Path) -> None:
        """Win rate delta below threshold -> reason_code='rejected_not_better'."""
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1", is_best=True)
        save_checkpoint(model, tmp_path, "v2")

        evaluator = MagicMock()
        evaluator._checkpoint_dir = tmp_path
        evaluator.evaluate.return_value = _make_eval_result("v1", 0.5)

        pm = PromotionManager(evaluator, PromotionConfig())
        decision = pm.evaluate_and_promote("v2", difficulty=1)

        assert decision.promoted is False
        assert decision.reason_code == "rejected_not_better"

    def test_rejected_insufficient_games_reason_code(self, tmp_path: Path) -> None:
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1", is_best=True)
        save_checkpoint(model, tmp_path, "v2")

        evaluator = MagicMock()
        evaluator._checkpoint_dir = tmp_path

        def eval_side_effect(
            checkpoint: str, n_games: int, difficulty: int, **_kw: Any
        ) -> EvalResult:
            if checkpoint == "v2":
                return _make_eval_result("v2", 1.0, games=2)
            return _make_eval_result("v1", 0.0, games=2)

        evaluator.evaluate.side_effect = eval_side_effect

        pm = PromotionManager(evaluator, PromotionConfig(eval_games=2))
        decision = pm.evaluate_and_promote("v2", difficulty=1)

        assert decision.promoted is False
        assert decision.reason_code == "rejected_insufficient_games"

    def test_rejected_crashed_reason_code(self, tmp_path: Path) -> None:
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1", is_best=True)
        save_checkpoint(model, tmp_path, "v2")

        evaluator = MagicMock()
        evaluator._checkpoint_dir = tmp_path

        def eval_side_effect(
            checkpoint: str, n_games: int, difficulty: int, **_kw: Any
        ) -> EvalResult:
            if checkpoint == "v2":
                return _make_eval_result("v2", 0.9, crashed=2)
            return _make_eval_result("v1", 0.5, crashed=0)

        evaluator.evaluate.side_effect = eval_side_effect

        pm = PromotionManager(evaluator, PromotionConfig())
        decision = pm.evaluate_and_promote("v2", difficulty=1)

        assert decision.promoted is False
        assert decision.reason_code == "rejected_crashed"

    def test_first_baseline_crashed_reason_code(self, tmp_path: Path) -> None:
        """No prior best + crashed eval -> reason_code='rejected_crashed'."""
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1", is_best=False)

        evaluator = MagicMock()
        evaluator._checkpoint_dir = tmp_path
        evaluator.evaluate.return_value = _make_eval_result("v1", 0.6, crashed=1)

        pm = PromotionManager(evaluator, PromotionConfig())
        decision = pm.evaluate_and_promote("v1", difficulty=1)

        assert decision.promoted is False
        assert decision.reason_code == "rejected_crashed"

    def test_manual_promote_reason_code(self, tmp_path: Path) -> None:
        model = _mock_model()
        save_checkpoint(model, tmp_path, "v1", is_best=True)
        save_checkpoint(model, tmp_path, "v2")

        evaluator = MagicMock()
        evaluator._checkpoint_dir = tmp_path

        pm = PromotionManager(evaluator, PromotionConfig())
        decision = pm.manual_promote("v2")

        assert decision.reason_code == "manual"

    def test_reason_code_flows_through_logger_to_json(self, tmp_path: Path) -> None:
        """PromotionLogger persists ``reason_code`` to promotion_history.json.

        This is the link between the in-memory decision and the dashboard:
        the JSON file is what ``/api/training/promotions/history`` reads.
        """
        logger = PromotionLogger(
            history_path=tmp_path / "history.json",
            wiki_path=tmp_path / "_no_wiki.md",  # non-existent -> no-op
        )

        decision = PromotionDecision(
            new_checkpoint="v1",
            old_best="none",
            new_eval=_make_eval_result("v1", 0.6),
            old_eval=None,
            promoted=True,
            reason="no previous best checkpoint",
            difficulty=1,
            reason_code="first_baseline",
        )
        entry = logger.log_decision(decision)

        assert entry["reason_code"] == "first_baseline"
        raw = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
        assert raw[0]["reason_code"] == "first_baseline"


class TestDaemonPersistsPromotionDecisions:
    """The training daemon must call ``PromotionLogger.log_decision`` after every
    ``PromotionManager.evaluate_and_promote`` call -- otherwise the first-baseline
    auto-promote gets logged to stdout but never reaches
    ``/api/training/promotions/history`` (soak-2026-04-11 bug).
    """

    def _make_daemon_settings(self, tmp_path: Path) -> Any:  # noqa: ANN401
        from bots.v0.config import Settings

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

    def test_first_baseline_promotion_is_persisted_and_api_visible(
        self, tmp_path: Path
    ) -> None:
        """soak-2026-04-11 regression: first-ever auto-promote shows in the API."""
        from bots.v0.api import app, configure
        from bots.v0.learning.daemon import DaemonConfig, TrainingDaemon
        from fastapi.testclient import TestClient

        settings = self._make_daemon_settings(tmp_path)
        daemon = TrainingDaemon(
            settings,
            DaemonConfig(
                check_interval_seconds=1,
                current_difficulty=1,
                max_difficulty=10,
                win_rate_threshold=0.8,
            ),
        )

        # Mock the trainer so it returns a successful cycle without touching SC2.
        mock_orchestrator = MagicMock()
        mock_orchestrator.run.return_value = {
            "cycles_completed": 1,
            "final_difficulty": 1,
            "cycle_results": [
                {"checkpoint": "v5", "difficulty": 1, "win_rate": 0.6},
            ],
            "total_games": 10,
            "stopped": False,
            "stop_reason": "",
        }

        # Mock the PromotionManager so we don't spin up an actual evaluator.
        # This is exactly the first-baseline scenario: no prior best.
        mock_decision = PromotionDecision(
            new_checkpoint="v5",
            old_best="none",
            new_eval=_make_eval_result("v5", 0.6),
            old_eval=None,
            promoted=True,
            reason="no previous best checkpoint",
            difficulty=1,
            reason_code="first_baseline",
        )
        mock_pm = MagicMock()
        mock_pm.evaluate_and_promote.return_value = mock_decision
        daemon._promotion_manager = mock_pm

        with patch(
            "bots.v0.learning.trainer.TrainingOrchestrator",
            return_value=mock_orchestrator,
        ):
            daemon._run_training()

        # 1. The decision was persisted to the same JSON file the API reads.
        history_path = settings.data_dir / "promotion_history.json"
        assert history_path.exists()
        entries = json.loads(history_path.read_text(encoding="utf-8"))
        promo_entries = [e for e in entries if e.get("new_checkpoint") == "v5"]
        assert len(promo_entries) == 1
        assert promo_entries[0]["reason_code"] == "first_baseline"
        assert promo_entries[0]["promoted"] is True

        # 2. The API actually returns it via /api/training/promotions/history.
        configure(settings.data_dir, settings.log_dir, settings.replay_dir)
        import bots.v0.api as api_mod

        api_mod._promotion_manager = None
        api_mod._promotion_logger = None
        client = TestClient(app)
        resp = client.get("/api/training/promotions/history")
        assert resp.status_code == 200
        history = resp.json()["history"]
        v5_entries = [e for e in history if e.get("new_checkpoint") == "v5"]
        assert len(v5_entries) == 1
        assert v5_entries[0]["reason_code"] == "first_baseline"

    def test_win_rate_gate_promotion_is_persisted(self, tmp_path: Path) -> None:
        """Standard win-rate-gate promotion also lands in promotion_history.json."""
        from bots.v0.learning.daemon import DaemonConfig, TrainingDaemon

        settings = self._make_daemon_settings(tmp_path)
        daemon = TrainingDaemon(
            settings,
            DaemonConfig(
                check_interval_seconds=1,
                current_difficulty=1,
                max_difficulty=10,
                win_rate_threshold=0.8,
            ),
        )

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.return_value = {
            "cycles_completed": 1,
            "final_difficulty": 1,
            "cycle_results": [
                {"checkpoint": "v6", "difficulty": 1, "win_rate": 0.9},
            ],
            "total_games": 10,
            "stopped": False,
            "stop_reason": "",
        }

        mock_decision = PromotionDecision(
            new_checkpoint="v6",
            old_best="v5",
            new_eval=_make_eval_result("v6", 0.9),
            old_eval=_make_eval_result("v5", 0.5),
            promoted=True,
            reason="new checkpoint wins: 90.00% vs 50.00%",
            difficulty=1,
            reason_code="win_rate_gate",
        )
        mock_pm = MagicMock()
        mock_pm.evaluate_and_promote.return_value = mock_decision
        daemon._promotion_manager = mock_pm

        with patch(
            "bots.v0.learning.trainer.TrainingOrchestrator",
            return_value=mock_orchestrator,
        ):
            daemon._run_training()

        history_path = settings.data_dir / "promotion_history.json"
        entries = json.loads(history_path.read_text(encoding="utf-8"))
        v6_entries = [e for e in entries if e.get("new_checkpoint") == "v6"]
        assert len(v6_entries) == 1
        assert v6_entries[0]["reason_code"] == "win_rate_gate"
        assert v6_entries[0]["promoted"] is True

    def test_rejected_promotion_is_still_persisted(self, tmp_path: Path) -> None:
        """Rejected attempts also persist so the dashboard can show them as rejected."""
        from bots.v0.learning.daemon import DaemonConfig, TrainingDaemon

        settings = self._make_daemon_settings(tmp_path)
        daemon = TrainingDaemon(
            settings,
            DaemonConfig(
                check_interval_seconds=1,
                current_difficulty=1,
                max_difficulty=10,
                win_rate_threshold=0.8,
            ),
        )

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.return_value = {
            "cycles_completed": 1,
            "final_difficulty": 1,
            "cycle_results": [
                {"checkpoint": "v7", "difficulty": 1, "win_rate": 0.55},
            ],
            "total_games": 10,
            "stopped": False,
            "stop_reason": "",
        }

        mock_decision = PromotionDecision(
            new_checkpoint="v7",
            old_best="v6",
            new_eval=_make_eval_result("v7", 0.55),
            old_eval=_make_eval_result("v6", 0.53),
            promoted=False,
            reason="new checkpoint not better enough",
            difficulty=1,
            reason_code="rejected_not_better",
        )
        mock_pm = MagicMock()
        mock_pm.evaluate_and_promote.return_value = mock_decision
        daemon._promotion_manager = mock_pm

        with patch(
            "bots.v0.learning.trainer.TrainingOrchestrator",
            return_value=mock_orchestrator,
        ):
            daemon._run_training()

        history_path = settings.data_dir / "promotion_history.json"
        entries = json.loads(history_path.read_text(encoding="utf-8"))
        v7_entries = [e for e in entries if e.get("new_checkpoint") == "v7"]
        assert len(v7_entries) == 1
        assert v7_entries[0]["promoted"] is False
        assert v7_entries[0]["reason_code"] == "rejected_not_better"


class TestPromotionLoggerCorruptJsonSelfHeal:
    """``_read_history`` must self-heal a corrupt ``promotion_history.json``.

    Without this, a single partial write (e.g. a concurrent writer racing
    with ``PromotionLogger._write_history``) would leave the file in an
    invalid state. The daemon wraps ``log_decision`` in ``try/except`` and
    silently swallows the ``JSONDecodeError``, so every subsequent decision
    would be lost with no signal on the Alerts tab. Rotating the bad file
    out of the way and starting fresh keeps the logger working and
    preserves the corrupt content for forensics.
    """

    def test_corrupt_history_is_rotated_and_new_decision_persisted(
        self, tmp_path: Path, caplog: Any
    ) -> None:
        history_path = tmp_path / "promotion_history.json"
        wiki_path = tmp_path / "_no_wiki.md"

        # Pre-seed an invalid-JSON file.
        history_path.write_text("{not valid json", encoding="utf-8")

        logger = PromotionLogger(history_path=history_path, wiki_path=wiki_path)

        decision = PromotionDecision(
            new_checkpoint="v9",
            old_best="none",
            new_eval=_make_eval_result("v9", 0.6),
            old_eval=None,
            promoted=True,
            reason="no previous best checkpoint",
            difficulty=1,
            reason_code="first_baseline",
        )

        with caplog.at_level(logging.WARNING, logger="bots.v0.learning.promotion"):
            logger.log_decision(decision)

        # 1. The corrupt file was rotated out of the way.
        corrupt_files = sorted(tmp_path.glob("promotion_history.corrupt.*.json"))
        assert len(corrupt_files) == 1
        assert corrupt_files[0].read_text(encoding="utf-8") == "{not valid json"

        # 2. A fresh, valid history now exists with exactly the new decision.
        assert history_path.exists()
        entries = json.loads(history_path.read_text(encoding="utf-8"))
        assert len(entries) == 1
        assert entries[0]["new_checkpoint"] == "v9"
        assert entries[0]["reason_code"] == "first_baseline"

        # 3. A WARNING was emitted explaining the rotation.
        warning_msgs = [
            r.message for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any("corrupt" in m for m in warning_msgs)


class TestDaemonLoggerFailureFallthrough:
    """The daemon's ``try/except Exception`` around ``log_decision`` must not
    cause silent success-path fallthrough in downstream bookkeeping.

    This is the exact pattern from ``feedback_silent_exception_fallthrough.md``:
    a try/except inside a loop that only logs on catch lets the code *after*
    the try run as if work succeeded. For the daemon that means curriculum
    advancement must still happen even if the logger itself raises, and the
    exception must be visible at ERROR level (so the Alerts tab surfaces it).
    """

    def _make_daemon_settings(self, tmp_path: Path) -> Any:  # noqa: ANN401
        from bots.v0.config import Settings

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

    def test_logger_exception_does_not_break_curriculum_advance(
        self, tmp_path: Path, caplog: Any
    ) -> None:
        from bots.v0.learning.daemon import DaemonConfig, TrainingDaemon

        settings = self._make_daemon_settings(tmp_path)
        starting_difficulty = 1
        daemon = TrainingDaemon(
            settings,
            DaemonConfig(
                check_interval_seconds=1,
                current_difficulty=starting_difficulty,
                max_difficulty=10,
                win_rate_threshold=0.8,
            ),
        )

        mock_orchestrator = MagicMock()
        mock_orchestrator.run.return_value = {
            "cycles_completed": 1,
            "final_difficulty": starting_difficulty,
            "cycle_results": [
                {"checkpoint": "v8", "difficulty": starting_difficulty, "win_rate": 0.9},
            ],
            "total_games": 10,
            "stopped": False,
            "stop_reason": "",
        }

        # A promoted decision that WOULD trigger curriculum advance
        # (win_rate 0.9 >= threshold 0.8, current < max).
        mock_decision = PromotionDecision(
            new_checkpoint="v8",
            old_best="v7",
            new_eval=_make_eval_result("v8", 0.9),
            old_eval=_make_eval_result("v7", 0.5),
            promoted=True,
            reason="win rate gate",
            difficulty=starting_difficulty,
            reason_code="win_rate_gate",
        )
        mock_pm = MagicMock()
        mock_pm.evaluate_and_promote.return_value = mock_decision
        daemon._promotion_manager = mock_pm

        # Install a PromotionLogger whose log_decision raises.
        exploding_logger = MagicMock()
        exploding_logger.log_decision.side_effect = RuntimeError("disk full")
        daemon._promotion_logger = exploding_logger

        with (
            caplog.at_level(logging.ERROR, logger="bots.v0.learning.daemon"),
            patch(
                "bots.v0.learning.trainer.TrainingOrchestrator",
                return_value=mock_orchestrator,
            ),
        ):
            # Must not propagate.
            daemon._run_training()

        # 1. log_decision was attempted.
        exploding_logger.log_decision.assert_called_once_with(mock_decision)

        # 2. Curriculum advancement still ran: difficulty advanced by 1.
        assert daemon._config.current_difficulty == starting_difficulty + 1
        assert daemon._last_advancement is not None

        # 3. The daemon logged the failure at ERROR level with a traceback
        # (``_log.exception`` routes through ERROR).
        exc_records = [
            r for r in caplog.records
            if r.levelno == logging.ERROR and "Failed to persist" in r.message
        ]
        assert len(exc_records) == 1

        # 4. The training result is still recorded as successful.
        status = daemon.get_status()
        assert status["last_error"] is None
        assert status["runs_completed"] == 1


class TestPromotionHistoryWriterCompat:
    """Both writers (``PromotionLogger.log_decision`` and
    ``RollbackMonitor._log_rollback``) must produce a schema that can coexist
    in the same ``promotion_history.json``.

    This doesn't assert atomicity under true concurrency (that would need
    threaded stress tests); it pins the contract that after one writer runs,
    the other can append without tripping on the other's shape.
    """

    def test_rollback_then_promotion_both_visible_and_valid(
        self, tmp_path: Path
    ) -> None:
        from bots.v0.learning.database import TrainingDB
        from bots.v0.learning.rollback import (
            RollbackConfig,
            RollbackDecision,
            RollbackMonitor,
        )

        history_path = tmp_path / "promotion_history.json"
        cp_dir = tmp_path / "checkpoints"
        cp_dir.mkdir()
        # Manifest with previous_best so execute_rollback succeeds.
        (cp_dir / "manifest.json").write_text(
            json.dumps(
                {"checkpoints": [], "best": "v5", "previous_best": "v4"},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        # 1. Writer A: rollback monitor appends a rollback entry.
        db = TrainingDB(tmp_path / "t.db")
        monitor = RollbackMonitor(
            db=db,
            config=RollbackConfig(),
            checkpoint_dir=cp_dir,
            history_path=history_path,
        )
        rb_decision = RollbackDecision(
            current_model="v5",
            revert_to="v4",
            current_win_rate=0.20,
            promotion_win_rate=0.80,
            games_played=15,
            reason="regression detected",
        )
        monitor.execute_rollback(rb_decision)
        db.close()

        # Sanity: the file is valid JSON after the rollback write.
        entries_after_rollback = json.loads(
            history_path.read_text(encoding="utf-8")
        )
        assert len(entries_after_rollback) == 1
        assert entries_after_rollback[0]["reason_code"] == "rollback"
        assert entries_after_rollback[0]["new_checkpoint"] == "v4"

        # 2. Writer B: PromotionLogger appends a first-baseline decision
        # to the same file. This is the exact flow the daemon takes.
        logger = PromotionLogger(
            history_path=history_path,
            wiki_path=tmp_path / "_no_wiki.md",  # non-existent -> no-op
        )
        promo_decision = PromotionDecision(
            new_checkpoint="v6",
            old_best="v4",
            new_eval=_make_eval_result("v6", 0.7),
            old_eval=_make_eval_result("v4", 0.4),
            promoted=True,
            reason="win rate gate",
            difficulty=1,
            reason_code="win_rate_gate",
        )
        logger.log_decision(promo_decision)

        # 3. The file is still valid JSON and contains BOTH entries.
        raw = history_path.read_text(encoding="utf-8")
        entries = json.loads(raw)  # must not raise
        assert isinstance(entries, list)
        assert len(entries) == 2

        # Rollback entry preserved unchanged.
        rb_entry = entries[0]
        assert rb_entry["reason_code"] == "rollback"
        assert rb_entry["new_checkpoint"] == "v4"
        assert rb_entry["old_best"] == "v5"
        assert rb_entry["promoted"] is False

        # Promotion entry present with its reason_code.
        promo_entry = entries[1]
        assert promo_entry["reason_code"] == "win_rate_gate"
        assert promo_entry["new_checkpoint"] == "v6"
        assert promo_entry["promoted"] is True

        # Schema consistency: both entries share the shape fields the
        # dashboard needs for classification + display.
        for e in entries:
            assert "timestamp" in e
            assert "new_checkpoint" in e
            assert "old_best" in e
            assert "promoted" in e
            assert "reason" in e
            assert "reason_code" in e
