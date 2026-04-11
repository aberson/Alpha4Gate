"""Tests for the RL training orchestrator."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from alpha4gate.learning.database import TrainingDB
from alpha4gate.learning.features import FEATURE_DIM
from alpha4gate.learning.trainer import (
    TrainingOrchestrator,
    compute_adjusted_win_rate,
)


def _mock_model() -> MagicMock:
    model = MagicMock()
    model.device = "cpu"

    def save_side_effect(path: str) -> None:
        Path(path).touch()

    model.save.side_effect = save_side_effect
    # model.learn() and model.set_env() are auto-mocked as MagicMock
    return model


class TestCurriculum:
    def test_should_increase_at_threshold(self) -> None:
        orch = TrainingOrchestrator(
            checkpoint_dir="fake",
            db_path="fake.db",
            win_rate_threshold=0.8,
        )
        assert orch.should_increase_difficulty(0.85) is True
        assert orch.should_increase_difficulty(0.80) is True

    def test_should_not_increase_below_threshold(self) -> None:
        orch = TrainingOrchestrator(
            checkpoint_dir="fake",
            db_path="fake.db",
            win_rate_threshold=0.8,
        )
        assert orch.should_increase_difficulty(0.5) is False
        assert orch.should_increase_difficulty(0.79) is False

    def test_should_not_increase_at_max(self) -> None:
        orch = TrainingOrchestrator(
            checkpoint_dir="fake",
            db_path="fake.db",
            max_difficulty=3,
        )
        orch._difficulty = 3
        assert orch.should_increase_difficulty(1.0) is False

    def test_increase_difficulty(self) -> None:
        orch = TrainingOrchestrator(
            checkpoint_dir="fake",
            db_path="fake.db",
            initial_difficulty=1,
            max_difficulty=5,
        )
        assert orch.increase_difficulty() == 2
        assert orch.increase_difficulty() == 3
        assert orch.difficulty == 3

    def test_increase_caps_at_max(self) -> None:
        orch = TrainingOrchestrator(
            checkpoint_dir="fake",
            db_path="fake.db",
            initial_difficulty=4,
            max_difficulty=5,
        )
        orch.increase_difficulty()  # 5
        orch.increase_difficulty()  # still 5
        assert orch.difficulty == 5


class TestDiskGuard:
    def test_passes_when_no_file(self, tmp_path: Path) -> None:
        orch = TrainingOrchestrator(
            checkpoint_dir=str(tmp_path / "cp"),
            db_path=str(tmp_path / "nonexistent.db"),
            disk_limit_gb=1.0,
        )
        assert orch.check_disk_guard() is True
        assert not orch.stopped

    def test_passes_when_under_limit(self, tmp_path: Path) -> None:
        db_path = tmp_path / "small.db"
        db_path.write_bytes(b"x" * 1000)
        orch = TrainingOrchestrator(
            checkpoint_dir=str(tmp_path / "cp"),
            db_path=str(db_path),
            disk_limit_gb=1.0,
        )
        assert orch.check_disk_guard() is True

    def test_fails_when_over_limit(self, tmp_path: Path) -> None:
        db_path = tmp_path / "big.db"
        # Create a file that reports as "over limit" by using a tiny limit
        db_path.write_bytes(b"x" * 2000)
        orch = TrainingOrchestrator(
            checkpoint_dir=str(tmp_path / "cp"),
            db_path=str(db_path),
            disk_limit_gb=0.000001,  # ~1 KB limit
        )
        assert orch.check_disk_guard() is False
        assert orch.stopped
        assert "Disk limit" in orch.stop_reason


class TestCycleTracking:
    def test_initial_state(self) -> None:
        orch = TrainingOrchestrator(checkpoint_dir="fake", db_path="fake.db")
        assert orch.cycle == 0
        assert orch.total_games == 0
        assert not orch.stopped

    @patch("alpha4gate.learning.trainer.TrainingOrchestrator._make_env")
    @patch("alpha4gate.learning.trainer.TrainingOrchestrator._init_or_resume_model")
    def test_run_cycles(
        self, mock_init: MagicMock, mock_env: MagicMock, tmp_path: Path
    ) -> None:
        mock_init.return_value = _mock_model()
        mock_env.return_value = MagicMock()
        db = TrainingDB(tmp_path / "train.db")
        db.close()

        orch = TrainingOrchestrator(
            checkpoint_dir=str(tmp_path / "cp"),
            db_path=str(tmp_path / "train.db"),
        )
        result = orch.run(n_cycles=3, games_per_cycle=2)
        assert result["cycles_completed"] == 3
        assert orch.cycle == 3
        assert orch.total_games == 6
        # Verify model.learn() was called each cycle
        model = mock_init.return_value
        assert model.learn.call_count == 3
        # Regression guard for the trainer's ``isinstance(raw_failed, int)``
        # fallback branch: ``mock_env.return_value`` is a bare ``MagicMock``
        # whose ``game_store_failed_count`` attribute is itself a
        # ``MagicMock`` (not an int). The trainer must still record
        # ``failed_games == 0`` for those cycles rather than passing a
        # ``MagicMock`` into the cycle_result dict.
        for cr in result["cycle_results"]:
            assert cr["failed_games"] == 0

    @patch("alpha4gate.learning.trainer.TrainingOrchestrator._make_env")
    @patch("alpha4gate.learning.trainer.TrainingOrchestrator._init_or_resume_model")
    def test_disk_guard_stops_run(
        self, mock_init: MagicMock, mock_env: MagicMock, tmp_path: Path
    ) -> None:
        mock_init.return_value = _mock_model()
        mock_env.return_value = MagicMock()
        db_path = tmp_path / "train.db"
        db = TrainingDB(db_path)
        # Insert enough data to make the file non-trivial
        db.store_game("g1", "Simple64", 1, "win", 300.0, 5.0, "v0")
        for i in range(100):
            state = np.zeros(FEATURE_DIM, dtype=np.float32)
            db.store_transition("g1", i, float(i), state, action=0, reward=0.1)
        db.close()

        orch = TrainingOrchestrator(
            checkpoint_dir=str(tmp_path / "cp"),
            db_path=str(db_path),
            disk_limit_gb=0.000001,  # tiny limit
        )
        result = orch.run(n_cycles=5, games_per_cycle=2)
        assert result["stopped"] is True
        assert result["cycles_completed"] == 0


class TestBestCheckpoint:
    @patch("alpha4gate.learning.trainer.TrainingOrchestrator._make_env")
    @patch("alpha4gate.learning.trainer.TrainingOrchestrator._init_or_resume_model")
    def test_trainer_never_marks_best(
        self, mock_init: MagicMock, mock_env: MagicMock, tmp_path: Path
    ) -> None:
        """Trainer saves with is_best=False; promotion gate decides best."""
        mock_init.return_value = _mock_model()
        mock_env.return_value = MagicMock()
        db = TrainingDB(tmp_path / "train.db")
        db.close()

        orch = TrainingOrchestrator(
            checkpoint_dir=str(tmp_path / "cp"),
            db_path=str(tmp_path / "train.db"),
        )
        orch.run(n_cycles=3, games_per_cycle=1)

        from alpha4gate.learning.checkpoints import get_best_name

        # Trainer no longer marks any checkpoint as best —
        # the promotion gate is responsible for that.
        assert get_best_name(tmp_path / "cp") is None

    @patch("alpha4gate.learning.trainer.TrainingOrchestrator._make_env")
    @patch("alpha4gate.learning.trainer.TrainingOrchestrator._init_or_resume_model")
    def test_checkpoints_saved_without_best(
        self, mock_init: MagicMock, mock_env: MagicMock, tmp_path: Path
    ) -> None:
        """All checkpoints should be saved even without best marking."""
        mock_init.return_value = _mock_model()
        mock_env.return_value = MagicMock()
        db_path = tmp_path / "train.db"
        db = TrainingDB(db_path)
        db.store_game("g1", "Simple64", 1, "win", 60.0, 1.0, "v0")
        db.store_game("g2", "Simple64", 1, "win", 60.0, 1.0, "v0")
        db.close()

        orch = TrainingOrchestrator(
            checkpoint_dir=str(tmp_path / "cp"),
            db_path=str(db_path),
        )
        orch.run(n_cycles=1, games_per_cycle=1)

        from alpha4gate.learning.checkpoints import get_best_name, list_checkpoints

        # Checkpoint is saved but not marked as best
        assert get_best_name(tmp_path / "cp") is None
        cps = list_checkpoints(tmp_path / "cp")
        assert len(cps) == 1
        assert cps[0]["name"] == "v1"


class TestCrashRecovery:
    @patch("alpha4gate.learning.trainer.TrainingOrchestrator._make_env")
    @patch("alpha4gate.learning.trainer.TrainingOrchestrator._init_or_resume_model")
    def test_resume_flag(
        self, mock_init: MagicMock, mock_env: MagicMock, tmp_path: Path
    ) -> None:
        mock_init.return_value = _mock_model()
        mock_env.return_value = MagicMock()
        db = TrainingDB(tmp_path / "train.db")
        db.close()

        orch = TrainingOrchestrator(
            checkpoint_dir=str(tmp_path / "cp"),
            db_path=str(tmp_path / "train.db"),
        )
        orch.run(n_cycles=1, games_per_cycle=1, resume=True)
        mock_init.assert_called_once_with(True)


class TestModelInitMatchesEnv:
    """Phase 4.5 F1+F6 regression guard.

    A freshly initialised PPO model must use the same observation and action
    spaces as ``SC2Env``. Two prior incidents (F1: obs space drift 15→17,
    F6: action space drift 5→6) shipped because ``_init_or_resume_model``
    hardcoded space dimensions instead of reading them from the env.
    """

    def test_fresh_model_uses_sc2env_spaces(self, tmp_path: Path) -> None:
        from alpha4gate.learning.environment import SC2Env

        orch = TrainingOrchestrator(
            checkpoint_dir=str(tmp_path / "cp"),
            db_path=str(tmp_path / "train.db"),
        )
        model = orch._init_or_resume_model(resume=False)

        # Spaces must equal the SC2Env class-level spaces, not hardcoded values
        assert model.observation_space == SC2Env.observation_space
        assert model.action_space == SC2Env.action_space


class TestCrashedCycleHandling:
    """Crashed cycles must NOT advance the curriculum or save phantom checkpoints.

    Regression guard for Phase 4.5 Step 2 finding F2: previously a crash in
    `model.set_env`/`model.learn` was caught and silently ignored, then the
    success-path code below the try block ran anyway — reading stale win
    rates, advancing the curriculum, and saving a checkpoint of an unchanged
    model. The trainer reported the cycle as successful.
    """

    @patch("alpha4gate.learning.trainer.TrainingOrchestrator._make_env")
    @patch("alpha4gate.learning.trainer.TrainingOrchestrator._init_or_resume_model")
    def test_crashed_cycle_records_status_and_skips_post_training(
        self, mock_init: MagicMock, mock_env: MagicMock, tmp_path: Path
    ) -> None:
        model = _mock_model()
        # Simulate the real failure: SB3's check_for_correct_spaces raising
        # when set_env is called with a mismatched observation space.
        model.set_env.side_effect = ValueError(
            "Observation spaces do not match: Box((15,)) != Box((17,))"
        )
        mock_init.return_value = model
        mock_env.return_value = MagicMock()
        db = TrainingDB(tmp_path / "train.db")
        db.close()

        orch = TrainingOrchestrator(
            checkpoint_dir=str(tmp_path / "cp"),
            db_path=str(tmp_path / "train.db"),
            initial_difficulty=3,
        )
        result = orch.run(n_cycles=2, games_per_cycle=4)

        # Result must report 2 cycles, both crashed
        assert result["cycles_completed"] == 2
        cycle_results = result["cycle_results"]
        assert len(cycle_results) == 2
        for cr in cycle_results:
            assert cr["status"] == "crashed"
            assert "Observation spaces do not match" in cr["error"]
            assert "checkpoint" not in cr  # no phantom checkpoint key

        # Curriculum must NOT have advanced
        assert orch.difficulty == 3, "crashed cycles must not advance curriculum"

        # No real games were played
        assert orch.total_games == 0, "crashed cycles must not credit games"

        # No checkpoints written to disk
        from alpha4gate.learning.checkpoints import list_checkpoints

        cps = list_checkpoints(tmp_path / "cp")
        assert cps == [], "crashed cycles must not save phantom checkpoints"

        # model.learn was attempted but never reached past set_env
        assert model.set_env.call_count == 2
        assert model.learn.call_count == 0

    @patch("alpha4gate.learning.trainer.TrainingOrchestrator._make_env")
    @patch("alpha4gate.learning.trainer.TrainingOrchestrator._init_or_resume_model")
    def test_mixed_success_and_crash_cycles(
        self, mock_init: MagicMock, mock_env: MagicMock, tmp_path: Path
    ) -> None:
        """One successful cycle followed by one crashed cycle leaves only
        the successful cycle's checkpoint and curriculum state intact.
        """
        model = _mock_model()
        # First call succeeds, second raises
        model.set_env.side_effect = [None, ValueError("space mismatch")]
        mock_init.return_value = model
        mock_env.return_value = MagicMock()
        db = TrainingDB(tmp_path / "train.db")
        db.close()

        orch = TrainingOrchestrator(
            checkpoint_dir=str(tmp_path / "cp"),
            db_path=str(tmp_path / "train.db"),
            initial_difficulty=2,
        )
        result = orch.run(n_cycles=2, games_per_cycle=3)

        cycle_results = result["cycle_results"]
        assert len(cycle_results) == 2
        # First cycle is the success path (has a checkpoint)
        assert cycle_results[0].get("status") != "crashed"
        assert cycle_results[0]["checkpoint"] == "v1"
        # Second cycle is crashed
        assert cycle_results[1]["status"] == "crashed"
        assert "checkpoint" not in cycle_results[1]

        # Only one checkpoint written (from cycle 1)
        from alpha4gate.learning.checkpoints import list_checkpoints

        cps = list_checkpoints(tmp_path / "cp")
        assert len(cps) == 1
        assert cps[0]["name"] == "v1"

        # total_games = 3 (one successful cycle) not 4 (no +1 for the crash)
        assert orch.total_games == 3


class TestAdjustedWinRate:
    """Bug B (soak-2026-04-11 cycle 5): games whose ``store_game``
    raised never got a row in the ``games`` table, so a plain
    ``get_recent_win_rate(N)`` call silently absorbed older games in
    the tail window and drifted the cycle win-rate away from what
    actually happened. ``compute_adjusted_win_rate`` shrinks the
    tail window so only live (DB-written) games count toward the
    denominator.
    """

    def test_excludes_failed_games_from_denominator(self) -> None:
        """With ``failed_games=3``, the helper must query the DB for
        ``expected_games - failed_games`` rows instead of
        ``expected_games`` rows — so the stale filler rows from older
        cycles don't contaminate the denominator.
        """
        db = MagicMock()
        db.get_recent_win_rate.return_value = 0.5

        rate = compute_adjusted_win_rate(
            db, expected_games=10, failed_games=3
        )

        assert rate == 0.5
        # The helper asked the DB for the LIVE game count, not the
        # expected count. That's the point: the denominator excludes
        # the 3 crashed games.
        db.get_recent_win_rate.assert_called_once_with(7)

    def test_no_failures_queries_full_expected_window(self) -> None:
        """Happy path: failed_games=0 means the helper should ask the
        DB for the full expected window (same behavior as before)."""
        db = MagicMock()
        db.get_recent_win_rate.return_value = 0.75

        rate = compute_adjusted_win_rate(
            db, expected_games=8, failed_games=0
        )
        assert rate == 0.75
        db.get_recent_win_rate.assert_called_once_with(8)

    def test_cycle_failed_games_flows_from_env_to_cycle_result(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """End-to-end integration test for the Bug B wiring.

        Every piece (env counter, trainer ``finally`` block,
        ``compute_adjusted_win_rate`` helper, cycle_result dict) is
        tested in isolation elsewhere. THIS test drives one full
        cycle and asserts that an ``SC2Env.game_store_failed_count = 2``
        actually flows through the trainer's ``finally`` block, is
        read as a real int (surviving the ``isinstance`` guard),
        shrinks the win-rate query window by 2, and lands in
        ``results[0]["failed_games"] == 2``. This is the exact
        integration seam identified in the soak-2026-04-11 postmortem.
        """

        class FakeEnv:
            """Minimal stand-in for SC2Env. Real int attribute, real close()."""

            def __init__(self) -> None:
                self.game_store_failed_count: int = 2
                self.closed: bool = False

            def close(self) -> None:
                self.closed = True

        fake_env = FakeEnv()
        fake_db = MagicMock()
        fake_db.get_recent_win_rate.return_value = 0.42

        games_per_cycle = 5
        expected_base_window = games_per_cycle * 2  # 10
        expected_shrunken_window = expected_base_window - 2  # 8

        with (
            patch(
                "alpha4gate.learning.trainer.TrainingOrchestrator._make_env",
                return_value=fake_env,
            ),
            patch(
                "alpha4gate.learning.trainer.TrainingOrchestrator._init_or_resume_model",
                return_value=_mock_model(),
            ),
            patch(
                "alpha4gate.learning.database.TrainingDB",
                return_value=fake_db,
            ),
            caplog.at_level("ERROR", logger="alpha4gate.learning.trainer"),
        ):
            orch = TrainingOrchestrator(
                checkpoint_dir=str(tmp_path / "cp"),
                db_path=str(tmp_path / "train.db"),
            )
            result = orch.run(n_cycles=1, games_per_cycle=games_per_cycle)

        # 1) The failed count flowed into the cycle_result dict as a real int.
        cycle_results = result["cycle_results"]
        assert len(cycle_results) == 1
        assert cycle_results[0]["failed_games"] == 2
        assert isinstance(cycle_results[0]["failed_games"], int)

        # 2) The DB was queried with the SHRUNKEN window, not the base window.
        fake_db.get_recent_win_rate.assert_called_once_with(
            expected_shrunken_window
        )

        # 3) The win_rate result from the adjusted call made it into cycle_result.
        assert cycle_results[0]["win_rate"] == 0.42

        # 4) The ERROR-level log line fired so the soak watchdog can see it.
        error_records = [
            r for r in caplog.records
            if r.levelname == "ERROR"
            and "failed to store" in r.getMessage()
        ]
        assert len(error_records) == 1, (
            f"expected exactly one 'failed to store' ERROR log, got "
            f"{[r.getMessage() for r in caplog.records]}"
        )

        # 5) env.close() was called (the trainer's ``finally`` block ran).
        assert fake_env.closed is True

    def test_all_games_failed_returns_zero(self) -> None:
        """When every game in the cycle crashed, the helper must
        return 0.0 without asking the DB — a 0-game window is not a
        meaningful curriculum signal and we don't want to trigger
        difficulty advancement off of it."""
        db = MagicMock()
        rate = compute_adjusted_win_rate(
            db, expected_games=4, failed_games=4
        )
        assert rate == 0.0
        db.get_recent_win_rate.assert_not_called()
