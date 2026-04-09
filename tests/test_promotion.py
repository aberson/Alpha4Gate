"""Tests for the model promotion gate."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from alpha4gate.learning.checkpoints import (
    get_best_name,
    promote_checkpoint,
    save_checkpoint,
)
from alpha4gate.learning.evaluator import EvalResult
from alpha4gate.learning.promotion import (
    PromotionConfig,
    PromotionDecision,
    PromotionManager,
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
    checkpoint: str, win_rate: float, games: int = 20
) -> EvalResult:
    wins = int(games * win_rate)
    return EvalResult(
        checkpoint=checkpoint,
        games_played=games,
        wins=wins,
        losses=games - wins,
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
            checkpoint: str, n_games: int, difficulty: int
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
            checkpoint: str, n_games: int, difficulty: int
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
            checkpoint: str, n_games: int, difficulty: int
        ) -> EvalResult:
            if checkpoint == "v2":
                return _make_eval_result("v2", 1.0, games=2)
            return _make_eval_result("v1", 0.0, games=2)

        evaluator.evaluate.side_effect = eval_side_effect

        pm = PromotionManager(evaluator, PromotionConfig(eval_games=2))
        decision = pm.evaluate_and_promote("v2", difficulty=1)

        assert decision.promoted is False
        assert "insufficient eval games" in decision.reason


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

    @patch("alpha4gate.learning.trainer.TrainingOrchestrator._make_env")
    @patch("alpha4gate.learning.trainer.TrainingOrchestrator._init_or_resume_model")
    def test_trainer_saves_is_best_false(
        self, mock_init: MagicMock, mock_env: MagicMock, tmp_path: Path
    ) -> None:
        from alpha4gate.learning.database import TrainingDB
        from alpha4gate.learning.trainer import TrainingOrchestrator

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
        from fastapi.testclient import TestClient

        from alpha4gate.api import app, configure

        data_dir = tmp_path / "data"
        log_dir = tmp_path / "logs"
        replay_dir = tmp_path / "replays"
        data_dir.mkdir()
        log_dir.mkdir()
        replay_dir.mkdir()
        configure(data_dir, log_dir, replay_dir)

        # Reset the module-level promotion manager to avoid state leaks
        import alpha4gate.api as api_mod

        api_mod._promotion_manager = None

        client = TestClient(app)
        resp = client.get("/api/training/promotions")
        assert resp.status_code == 200
        assert resp.json()["promotions"] == []

    def test_manual_promote_requires_checkpoint(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from alpha4gate.api import app, configure

        data_dir = tmp_path / "data"
        log_dir = tmp_path / "logs"
        replay_dir = tmp_path / "replays"
        data_dir.mkdir()
        log_dir.mkdir()
        replay_dir.mkdir()
        configure(data_dir, log_dir, replay_dir)

        import alpha4gate.api as api_mod

        api_mod._promotion_manager = None

        client = TestClient(app)
        resp = client.post("/api/training/promote", json={})
        assert resp.status_code == 400
        assert "checkpoint is required" in resp.json()["error"]

    def test_manual_promote_success(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from alpha4gate.api import app, configure

        data_dir = tmp_path / "data"
        log_dir = tmp_path / "logs"
        replay_dir = tmp_path / "replays"
        data_dir.mkdir()
        log_dir.mkdir()
        replay_dir.mkdir()
        configure(data_dir, log_dir, replay_dir)

        import alpha4gate.api as api_mod

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
