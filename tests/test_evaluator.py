"""Tests for the model evaluator."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from alpha4gate.config import Settings
from alpha4gate.learning.database import TrainingDB
from alpha4gate.learning.evaluator import (
    ComparisonResult,
    EvalJob,
    EvalResult,
    ModelEvaluator,
)
from alpha4gate.learning.features import FEATURE_DIM


def _make_settings(tmp_path: Path) -> Settings:
    """Create a Settings instance pointing to tmp_path."""
    return Settings(
        sc2_path=Path("."),
        log_dir=tmp_path / "logs",
        replay_dir=tmp_path / "replays",
        data_dir=tmp_path / "data",
        web_ui_port=0,
        anthropic_api_key="",
        spawning_tool_api_key="",
    )


def _mock_env(outcome_info: dict[str, Any] | None = None) -> MagicMock:
    """Create a mock SC2Env that finishes in one step."""
    env = MagicMock()
    obs = np.zeros(FEATURE_DIM, dtype=np.float32)
    env.reset.return_value = (obs, {})
    info = {"game_time": 200.0}
    if outcome_info:
        info.update(outcome_info)
    env.step.return_value = (obs, 1.0, True, False, info)
    return env


@pytest.fixture()
def db(tmp_path: Path) -> TrainingDB:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    return TrainingDB(data_dir / "training.db")


@pytest.fixture()
def evaluator(tmp_path: Path, db: TrainingDB) -> ModelEvaluator:
    settings = _make_settings(tmp_path)
    return ModelEvaluator(settings, db)


class TestEvalResult:
    def test_dataclass_fields(self) -> None:
        result = EvalResult(
            checkpoint="v5",
            games_played=10,
            wins=7,
            losses=3,
            crashed=0,
            win_rate=0.7,
            avg_reward=5.5,
            avg_duration=300.0,
            difficulty=3,
            action_distribution=[0.2, 0.3, 0.1, 0.15, 0.15, 0.1],
        )
        assert result.checkpoint == "v5"
        assert result.games_played == 10
        assert result.wins == 7
        assert result.losses == 3
        assert result.crashed == 0
        assert result.win_rate == 0.7
        assert result.avg_reward == 5.5
        assert result.difficulty == 3
        assert result.action_distribution is not None
        assert len(result.action_distribution) == 6

    def test_serializable(self) -> None:
        result = EvalResult(
            checkpoint="v1",
            games_played=5,
            wins=3,
            losses=2,
            crashed=0,
            win_rate=0.6,
            avg_reward=2.0,
            avg_duration=200.0,
            difficulty=1,
            action_distribution=None,
        )
        d = asdict(result)
        assert d["checkpoint"] == "v1"
        assert d["action_distribution"] is None


class TestComparisonResult:
    def test_dataclass_fields(self) -> None:
        a = EvalResult("v1", 10, 6, 4, 0, 0.6, 3.0, 300.0, 1, None)
        b = EvalResult("v2", 10, 8, 2, 0, 0.8, 5.0, 250.0, 1, None)
        comp = ComparisonResult(
            a=a,
            b=b,
            winner="v2",
            win_rate_delta=-0.2,
            significant=True,
        )
        assert comp.winner == "v2"
        assert comp.win_rate_delta == pytest.approx(-0.2)
        assert comp.significant is True


class TestEvalJob:
    def test_initial_state(self) -> None:
        job = EvalJob(
            job_id="abc123",
            status="pending",
            checkpoint="v5",
            n_games=10,
            difficulty=3,
        )
        assert job.status == "pending"
        assert job.result is None
        assert job.error is None
        assert job.games_completed == 0


class TestAverageActionProbs:
    def test_empty_returns_none(self) -> None:
        assert ModelEvaluator._average_action_probs([]) is None

    def test_single_entry(self) -> None:
        probs = [0.1, 0.2, 0.3, 0.2, 0.1, 0.1]
        result = ModelEvaluator._average_action_probs([probs])
        assert result is not None
        assert len(result) == 6
        for a, b in zip(result, probs, strict=True):
            assert a == pytest.approx(b)

    def test_multiple_entries(self) -> None:
        p1 = [0.2, 0.3, 0.1, 0.1, 0.2, 0.1]
        p2 = [0.4, 0.1, 0.1, 0.1, 0.2, 0.1]
        result = ModelEvaluator._average_action_probs([p1, p2])
        assert result is not None
        assert result[0] == pytest.approx(0.3)
        assert result[1] == pytest.approx(0.2)


class TestEvaluateWithMockedEnv:
    """Test evaluate() by mocking _load_model and _create_env."""

    def test_evaluate_all_wins(self, evaluator: ModelEvaluator) -> None:
        """All games win => 100% win rate."""
        model = MagicMock()
        model.predict.return_value = (np.array(0), None)

        env = _mock_env({"action_probs": [0.2, 0.3, 0.1, 0.1, 0.2, 0.1]})

        with (
            patch.object(evaluator, "_load_model", return_value=model),
            patch.object(evaluator, "_create_env", return_value=env),
            patch.object(evaluator, "_get_game_result", return_value="win"),
        ):
            result = evaluator.evaluate("v1", 3, difficulty=1)

        assert result.checkpoint == "v1"
        assert result.games_played == 3
        assert result.wins == 3
        assert result.losses == 0
        assert result.win_rate == pytest.approx(1.0)
        assert result.avg_reward == pytest.approx(1.0)
        assert result.difficulty == 1
        assert result.action_distribution is not None

    def test_evaluate_mixed_results(self, evaluator: ModelEvaluator) -> None:
        """Mix of wins and losses."""
        model = MagicMock()
        model.predict.return_value = (np.array(0), None)

        env = _mock_env()

        outcomes = iter(["win", "loss", "win", "loss"])
        with (
            patch.object(evaluator, "_load_model", return_value=model),
            patch.object(evaluator, "_create_env", return_value=env),
            patch.object(
                evaluator,
                "_get_game_result",
                side_effect=lambda _gid: next(outcomes),
            ),
        ):
            result = evaluator.evaluate("v2", 4, difficulty=2)

        assert result.games_played == 4
        assert result.wins == 2
        assert result.losses == 2
        assert result.win_rate == pytest.approx(0.5)
        assert result.avg_reward == pytest.approx(1.0)
        assert result.difficulty == 2

    def test_evaluate_no_action_probs(self, evaluator: ModelEvaluator) -> None:
        """When no action_probs in info, action_distribution should be None."""
        model = MagicMock()
        model.predict.return_value = (np.array(0), None)

        env = _mock_env()  # no action_probs in default info

        with (
            patch.object(evaluator, "_load_model", return_value=model),
            patch.object(evaluator, "_create_env", return_value=env),
            patch.object(evaluator, "_get_game_result", return_value="win"),
        ):
            result = evaluator.evaluate("v1", 1, difficulty=1)

        assert result.action_distribution is None

    def test_evaluate_env_crash_counts_as_crashed_not_loss(
        self,
        evaluator: ModelEvaluator,
    ) -> None:
        """When env.reset() crashes, the game counts as crashed, NOT loss.

        This is the Phase 4.5 blocker #67 behavior: crashed games must be
        surfaced as ``crashed`` so the promotion gate can refuse. The old
        behavior silently converted every crash into a fake loss.
        """
        model = MagicMock()

        env = MagicMock()
        env.reset.side_effect = RuntimeError("SC2 crashed")

        with (
            patch.object(evaluator, "_load_model", return_value=model),
            patch.object(evaluator, "_create_env", return_value=env),
            # _get_game_result should NOT be consulted at all when the
            # inference loop raises -- _run_single_game returns "crashed"
            # directly. We assert that below.
            patch.object(
                evaluator,
                "_get_game_result",
                side_effect=AssertionError("_get_game_result must not be called after a crash"),
            ),
        ):
            result = evaluator.evaluate("v1", 1, difficulty=1)

        assert result.losses == 0
        assert result.wins == 0
        assert result.crashed == 1
        assert result.games_played == 0
        assert result.win_rate == 0.0

    def test_evaluate_multi_step_game(self, evaluator: ModelEvaluator) -> None:
        """Game that takes multiple steps before done."""
        model = MagicMock()
        model.predict.return_value = (np.array(2), None)

        env = MagicMock()
        obs = np.zeros(FEATURE_DIM, dtype=np.float32)
        env.reset.return_value = (obs, {})

        # Three steps: not done, not done, done
        step_returns = [
            (obs, 0.5, False, False, {"game_time": 100.0}),
            (obs, 0.5, False, False, {"game_time": 200.0}),
            (obs, 1.0, True, False, {"game_time": 300.0}),
        ]
        env.step.side_effect = step_returns

        with (
            patch.object(evaluator, "_load_model", return_value=model),
            patch.object(evaluator, "_create_env", return_value=env),
            patch.object(evaluator, "_get_game_result", return_value="win"),
        ):
            result = evaluator.evaluate("v3", 1, difficulty=1)

        assert result.wins == 1
        assert result.avg_reward == pytest.approx(2.0)
        assert result.avg_duration == pytest.approx(300.0)
        assert model.predict.call_count == 3


class TestCompare:
    def test_compare_a_better(self, evaluator: ModelEvaluator) -> None:
        """Checkpoint A wins more games => A is winner."""
        model = MagicMock()
        model.predict.return_value = (np.array(0), None)

        env = _mock_env()

        # A wins all 5, B wins none
        call_count = [0]

        def game_result(_gid: str) -> str:
            call_count[0] += 1
            if call_count[0] <= 5:
                return "win"
            return "loss"

        with (
            patch.object(evaluator, "_load_model", return_value=model),
            patch.object(evaluator, "_create_env", return_value=env),
            patch.object(evaluator, "_get_game_result", side_effect=game_result),
        ):
            comp = evaluator.compare("vA", "vB", n_games=5, difficulty=1)

        assert comp.winner == "vA"
        assert comp.win_rate_delta == pytest.approx(1.0)
        assert comp.significant is True
        assert comp.a.wins == 5
        assert comp.b.wins == 0

    def test_compare_tie(self, evaluator: ModelEvaluator) -> None:
        """Equal win rates => tie."""
        model = MagicMock()
        model.predict.return_value = (np.array(0), None)

        env = _mock_env()

        with (
            patch.object(evaluator, "_load_model", return_value=model),
            patch.object(evaluator, "_create_env", return_value=env),
            patch.object(evaluator, "_get_game_result", return_value="win"),
        ):
            comp = evaluator.compare("vA", "vB", n_games=5, difficulty=1)

        assert comp.winner == "tie"
        assert comp.win_rate_delta == pytest.approx(0.0)
        assert comp.significant is False

    def test_compare_not_significant_with_few_games(
        self,
        evaluator: ModelEvaluator,
    ) -> None:
        """Small sample (<10 total games) => not significant even with big delta."""
        model = MagicMock()
        model.predict.return_value = (np.array(0), None)

        env = _mock_env()

        # 2 games each (total 4 < 10)
        call_count = [0]

        def game_result(_gid: str) -> str:
            call_count[0] += 1
            if call_count[0] <= 2:
                return "win"
            return "loss"

        with (
            patch.object(evaluator, "_load_model", return_value=model),
            patch.object(evaluator, "_create_env", return_value=env),
            patch.object(evaluator, "_get_game_result", side_effect=game_result),
        ):
            comp = evaluator.compare("vA", "vB", n_games=2, difficulty=1)

        assert comp.significant is False

    def test_compare_b_better(self, evaluator: ModelEvaluator) -> None:
        """B wins more => B is winner with negative delta."""
        model = MagicMock()
        model.predict.return_value = (np.array(0), None)

        env = _mock_env()

        call_count = [0]

        def game_result(_gid: str) -> str:
            call_count[0] += 1
            # A wins 2/5, B wins 5/5
            if call_count[0] <= 5:
                return "win" if call_count[0] <= 2 else "loss"
            return "win"

        with (
            patch.object(evaluator, "_load_model", return_value=model),
            patch.object(evaluator, "_create_env", return_value=env),
            patch.object(evaluator, "_get_game_result", side_effect=game_result),
        ):
            comp = evaluator.compare("vA", "vB", n_games=5, difficulty=1)

        assert comp.winner == "vB"
        assert comp.win_rate_delta < 0
        assert comp.significant is True


class TestJobManagement:
    def test_submit_and_get_job(self, evaluator: ModelEvaluator) -> None:
        job_id = evaluator.submit_job("v5", 10, 3)
        job = evaluator.get_job(job_id)
        assert job is not None
        assert job.status == "pending"
        assert job.checkpoint == "v5"
        assert job.n_games == 10
        assert job.difficulty == 3

    def test_get_nonexistent_job(self, evaluator: ModelEvaluator) -> None:
        assert evaluator.get_job("nonexistent") is None

    def test_run_job_completes(self, evaluator: ModelEvaluator) -> None:
        model = MagicMock()
        model.predict.return_value = (np.array(0), None)
        env = _mock_env()

        job_id = evaluator.submit_job("v1", 2, 1)
        with (
            patch.object(evaluator, "_load_model", return_value=model),
            patch.object(evaluator, "_create_env", return_value=env),
            patch.object(evaluator, "_get_game_result", return_value="win"),
        ):
            evaluator.run_job(job_id)

        job = evaluator.get_job(job_id)
        assert job is not None
        assert job.status == "completed"
        assert job.result is not None
        assert job.result.games_played == 2
        assert job.games_completed == 2

    def test_run_job_nonexistent(self, evaluator: ModelEvaluator) -> None:
        """Running a nonexistent job should not raise."""
        evaluator.run_job("nonexistent")  # Should be a no-op

    def test_run_job_failure(self, evaluator: ModelEvaluator) -> None:
        """Job failure is recorded."""
        job_id = evaluator.submit_job("missing", 1, 1)
        with patch.object(
            evaluator,
            "_load_model",
            side_effect=FileNotFoundError("no checkpoint"),
        ):
            evaluator.run_job(job_id)

        job = evaluator.get_job(job_id)
        assert job is not None
        assert job.status == "failed"
        assert job.error is not None
        assert "no checkpoint" in job.error


class TestGetGameResult:
    def test_existing_game(self, evaluator: ModelEvaluator, db: TrainingDB) -> None:
        db.store_game("eval_test_1", "Simple64", 1, "win", 300.0, 5.0, "v1")
        result = evaluator._get_game_result("eval_test_1")
        assert result == "win"

    def test_missing_game_returns_none(
        self,
        evaluator: ModelEvaluator,
    ) -> None:
        """Phase 4.5 blocker #67: missing rows now return None, not "loss".

        The old silent-default-to-loss behavior converted crashed games
        into fake losses and corrupted promotion decisions.
        """
        result = evaluator._get_game_result("nonexistent_game")
        assert result is None


class TestRunSingleGameCrashHandling:
    """Regression tests for Phase 4.5 blocker #67.

    The old ``_run_single_game`` swallowed inference-loop exceptions,
    fell through, and then called ``_get_game_result`` which silently
    defaulted to ``"loss"``. The new behavior returns ``outcome="crashed"``
    so the caller can distinguish crashes from real losses.
    """

    def test_marks_crashed_when_inference_raises(
        self,
        evaluator: ModelEvaluator,
    ) -> None:
        """When ``model.predict`` raises, outcome must be "crashed"."""
        model = MagicMock()
        call_count = [0]

        def predict_side_effect(*_args: Any, **_kw: Any) -> tuple[Any, Any]:
            call_count[0] += 1
            if call_count[0] >= 2:
                raise RuntimeError("simulated mid-game failure")
            return (np.array(0), None)

        model.predict.side_effect = predict_side_effect

        env = MagicMock()
        obs = np.zeros(FEATURE_DIM, dtype=np.float32)
        env.reset.return_value = (obs, {})
        # First step OK, second step never reached because predict raises.
        env.step.return_value = (obs, 0.5, False, False, {"game_time": 50.0})

        with patch.object(evaluator, "_create_env", return_value=env):
            result = evaluator._run_single_game(
                model,
                game_id="eval_crash_1",
                checkpoint_name="v1",
                difficulty=1,
                all_action_probs=[],
            )

        assert result["outcome"] == "crashed"
        # env.close() must still run (the finally block).
        env.close.assert_called_once()

    def test_marks_crashed_when_no_db_row(
        self,
        evaluator: ModelEvaluator,
        db: TrainingDB,
    ) -> None:
        """Inference loop completes normally, but no DB row was written.

        This simulates the case where the SC2 game ended without the
        reward writer hitting ``store_game`` (e.g., the game thread died
        between the last step and the result recording). The old code
        silently returned ``"loss"``; the new code returns ``"crashed"``.
        """
        model = MagicMock()
        model.predict.return_value = (np.array(0), None)
        env = _mock_env()

        with patch.object(evaluator, "_create_env", return_value=env):
            result = evaluator._run_single_game(
                model,
                game_id="eval_missing_row",
                checkpoint_name="v1",
                difficulty=1,
                all_action_probs=[],
            )

        assert result["outcome"] == "crashed"
        env.close.assert_called_once()

    def test_marks_win_when_row_present(
        self,
        evaluator: ModelEvaluator,
        db: TrainingDB,
    ) -> None:
        """Sanity check: a normal game with a "win" row still returns "win"."""
        db.store_game("eval_ok_1", "Simple64", 1, "win", 300.0, 5.0, "v1")

        model = MagicMock()
        model.predict.return_value = (np.array(0), None)
        env = _mock_env()

        with patch.object(evaluator, "_create_env", return_value=env):
            result = evaluator._run_single_game(
                model,
                game_id="eval_ok_1",
                checkpoint_name="v1",
                difficulty=1,
                all_action_probs=[],
            )

        assert result["outcome"] == "win"

    def test_crashed_games_do_not_pollute_action_probs(
        self,
        evaluator: ModelEvaluator,
    ) -> None:
        """A crashed game must not contribute to the shared action_probs list.

        Reproduces the iteration-1 finding: action_probs collected mid-game
        were leaking into the eval result even when the game crashed,
        biasing EvalResult.action_distribution toward early-game states
        of crashed runs.
        """
        model = MagicMock()
        call_count = [0]

        def predict_side_effect(*_args: Any, **_kw: Any) -> tuple[Any, Any]:
            call_count[0] += 1
            if call_count[0] >= 3:
                raise RuntimeError("simulated mid-game failure")
            return (np.array(0), None)

        model.predict.side_effect = predict_side_effect

        env = MagicMock()
        obs = np.zeros(FEATURE_DIM, dtype=np.float32)
        env.reset.return_value = (obs, {})
        # First two steps succeed and report action_probs. The third
        # predict() raises, so those two probs should be discarded.
        env.step.return_value = (
            obs,
            0.5,
            False,
            False,
            {
                "game_time": 50.0,
                "action_probs": [0.2, 0.3, 0.1, 0.1, 0.2, 0.1],
            },
        )

        all_probs: list[list[float]] = []
        with patch.object(evaluator, "_create_env", return_value=env):
            result = evaluator._run_single_game(
                model,
                game_id="eval_pollute_crash",
                checkpoint_name="v1",
                difficulty=1,
                all_action_probs=all_probs,
            )

        assert result["outcome"] == "crashed"
        # Nothing leaked from the crashed game, even though the env
        # produced action_probs on the steps that DID run.
        assert all_probs == []
        env.close.assert_called_once()

    def test_no_db_row_does_not_pollute_action_probs(
        self,
        evaluator: ModelEvaluator,
    ) -> None:
        """The completed-but-no-row branch must also discard action_probs.

        The inference loop completes normally, action_probs were
        collected step-by-step, but ``_get_game_result`` returns None
        because no row was ever recorded. That path returns "crashed",
        so the collected probs must NOT leak into the shared list.
        """
        model = MagicMock()
        model.predict.return_value = (np.array(0), None)
        env = _mock_env({"action_probs": [0.2, 0.3, 0.1, 0.1, 0.2, 0.1]})

        all_probs: list[list[float]] = []
        with patch.object(evaluator, "_create_env", return_value=env):
            result = evaluator._run_single_game(
                model,
                game_id="eval_pollute_no_row",
                checkpoint_name="v1",
                difficulty=1,
                all_action_probs=all_probs,
            )

        assert result["outcome"] == "crashed"
        assert all_probs == []
        env.close.assert_called_once()


class TestEvaluateSeparatesCrashes:
    """Regression tests for Phase 4.5 blocker #67.

    ``EvalResult.crashed`` tracks crashed games separately from wins and
    losses. ``win_rate`` is computed over VALID games only, so a partially
    crashed eval run does not report a fraudulently inflated (or deflated)
    win rate.
    """

    def test_evaluate_separates_crashed_from_losses(
        self,
        evaluator: ModelEvaluator,
    ) -> None:
        """3 wins + 2 crashes => wins=3, losses=0, crashed=2, win_rate=1.0."""
        outcomes = iter(
            [
                {"outcome": "win", "reward": 1.0, "duration": 200.0},
                {"outcome": "crashed", "reward": 0.0, "duration": 0.0},
                {"outcome": "win", "reward": 1.0, "duration": 200.0},
                {"outcome": "crashed", "reward": 0.0, "duration": 0.0},
                {"outcome": "win", "reward": 1.0, "duration": 200.0},
            ]
        )

        model = MagicMock()
        with (
            patch.object(evaluator, "_load_model", return_value=model),
            patch.object(
                evaluator,
                "_run_single_game",
                side_effect=lambda *_a, **_kw: next(outcomes),
            ),
        ):
            result = evaluator.evaluate("v1", 5, difficulty=1)

        assert result.wins == 3
        assert result.losses == 0
        assert result.crashed == 2
        assert result.games_played == 3  # crashes excluded
        assert result.win_rate == pytest.approx(1.0)  # 3/3, not 3/5
        # Averages computed over VALID games only.
        assert result.avg_reward == pytest.approx(1.0)
        assert result.avg_duration == pytest.approx(200.0)

    def test_evaluate_all_crashed(self, evaluator: ModelEvaluator) -> None:
        """All games crash => zero valid games, win_rate=0.0 defensively."""
        model = MagicMock()
        with (
            patch.object(evaluator, "_load_model", return_value=model),
            patch.object(
                evaluator,
                "_run_single_game",
                return_value={
                    "outcome": "crashed",
                    "reward": 0.0,
                    "duration": 0.0,
                },
            ),
        ):
            result = evaluator.evaluate("v1", 3, difficulty=1)

        assert result.wins == 0
        assert result.losses == 0
        assert result.crashed == 3
        assert result.games_played == 0
        assert result.win_rate == 0.0
        assert result.avg_reward == 0.0
        assert result.avg_duration == 0.0


class TestEvalAPIEndpoints:
    """Test the evaluation API endpoints."""

    @pytest.fixture()
    def client(self, tmp_path: Path) -> Any:
        from fastapi.testclient import TestClient

        from alpha4gate.api import app, configure

        data_dir = tmp_path / "data"
        log_dir = tmp_path / "logs"
        replay_dir = tmp_path / "replays"
        data_dir.mkdir()
        log_dir.mkdir()
        replay_dir.mkdir()
        configure(data_dir, log_dir, replay_dir)
        return TestClient(app)

    def test_start_evaluation_missing_checkpoint(self, client: Any) -> None:
        resp = client.post("/api/training/evaluate", json={"games": 5, "difficulty": 1})
        assert resp.status_code == 400
        assert "checkpoint" in resp.json()["error"]

    def test_get_nonexistent_job(self, client: Any) -> None:
        resp = client.get("/api/training/evaluate/nonexistent")
        assert resp.status_code == 404

    @patch("alpha4gate.api._get_evaluator")
    def test_start_and_poll_evaluation(
        self,
        mock_get_eval: MagicMock,
        client: Any,
    ) -> None:
        """Submit a job and poll for status."""
        mock_evaluator = MagicMock()
        mock_evaluator.submit_job.return_value = "job123"
        mock_get_eval.return_value = mock_evaluator

        resp = client.post(
            "/api/training/evaluate",
            json={"checkpoint": "v5", "games": 3, "difficulty": 2},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["job_id"] == "job123"
        assert data["status"] == "pending"

    @patch("alpha4gate.api._get_evaluator")
    def test_poll_completed_job(
        self,
        mock_get_eval: MagicMock,
        client: Any,
    ) -> None:
        """Poll a completed job returns the result."""
        result = EvalResult(
            checkpoint="v5",
            games_played=3,
            wins=2,
            losses=1,
            crashed=0,
            win_rate=0.667,
            avg_reward=3.0,
            avg_duration=250.0,
            difficulty=2,
            action_distribution=None,
        )
        job = EvalJob(
            job_id="job456",
            status="completed",
            checkpoint="v5",
            n_games=3,
            difficulty=2,
            result=result,
            games_completed=3,
        )
        mock_evaluator = MagicMock()
        mock_evaluator.get_job.return_value = job
        mock_get_eval.return_value = mock_evaluator

        resp = client.get("/api/training/evaluate/job456")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["result"]["wins"] == 2
        assert data["result"]["win_rate"] == pytest.approx(0.667)
