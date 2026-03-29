"""Tests for the RL training orchestrator."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from alpha4gate.learning.database import TrainingDB
from alpha4gate.learning.trainer import TrainingOrchestrator


def _mock_model() -> MagicMock:
    model = MagicMock()
    model.device = "cpu"

    def side_effect(path: str) -> None:
        Path(path).touch()

    model.save.side_effect = side_effect
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

    @patch("alpha4gate.learning.trainer.TrainingOrchestrator._init_or_resume_model")
    def test_run_cycles(self, mock_init: MagicMock, tmp_path: Path) -> None:
        mock_init.return_value = _mock_model()
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

    @patch("alpha4gate.learning.trainer.TrainingOrchestrator._init_or_resume_model")
    def test_disk_guard_stops_run(self, mock_init: MagicMock, tmp_path: Path) -> None:
        mock_init.return_value = _mock_model()
        db_path = tmp_path / "train.db"
        db = TrainingDB(db_path)
        # Insert enough data to make the file non-trivial
        db.store_game("g1", "Simple64", 1, "win", 300.0, 5.0, "v0")
        for i in range(100):
            state = np.zeros(14, dtype=np.float32)
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


class TestCrashRecovery:
    @patch("alpha4gate.learning.trainer.TrainingOrchestrator._init_or_resume_model")
    def test_resume_flag(self, mock_init: MagicMock, tmp_path: Path) -> None:
        mock_init.return_value = _mock_model()
        db = TrainingDB(tmp_path / "train.db")
        db.close()

        orch = TrainingOrchestrator(
            checkpoint_dir=str(tmp_path / "cp"),
            db_path=str(tmp_path / "train.db"),
        )
        orch.run(n_cycles=1, games_per_cycle=1, resume=True)
        mock_init.assert_called_once_with(True)
