"""Tests for imitation pre-training pipeline."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from alpha4gate.learning.database import TrainingDB
from alpha4gate.learning.features import FEATURE_DIM
from alpha4gate.learning.imitation import run_imitation_training


@pytest.fixture()
def populated_db(tmp_path: Path) -> TrainingDB:
    """Create a DB with fake rule-based transitions for training."""
    db = TrainingDB(tmp_path / "train.db")
    db.store_game("g1", "Simple64", 1, "win", 300.0, 5.0, "rules")

    # Generate 200 transitions with consistent state→action mapping
    # This simulates the rule-based engine always picking the same action
    # for similar states, so the neural net can learn the pattern
    rng = np.random.RandomState(42)
    for i in range(200):
        state = rng.randint(0, 100, size=FEATURE_DIM).astype(np.float32)
        # Deterministic action based on army_supply (index 4):
        # low army → EXPAND (1), high army → ATTACK (2)
        action = 1 if state[4] < 50 else 2
        db.store_transition(
            game_id="g1",
            step_index=i,
            game_time=float(i * 22),
            state=state,
            action=action,
            reward=0.1,
        )
    return db


class TestImitationTraining:
    def test_training_reduces_loss(self, populated_db: TrainingDB, tmp_path: Path) -> None:
        """Training should reduce loss and achieve reasonable agreement."""
        cp_dir = tmp_path / "checkpoints"
        result = run_imitation_training(
            db=populated_db,
            checkpoint_dir=cp_dir,
            max_epochs=20,
            batch_size=32,
            learning_rate=1e-3,
            agreement_threshold=0.99,  # high so we run all 20 epochs
            checkpoint_name="test_model",
        )
        assert result["epochs"] <= 20
        assert result["final_loss"] < 2.0  # should improve from random (~1.6)
        assert result["agreement"] > 0.4  # at least better than random 20%
        assert result["transitions"] == 200

    def test_checkpoint_saved(self, populated_db: TrainingDB, tmp_path: Path) -> None:
        cp_dir = tmp_path / "checkpoints"
        result = run_imitation_training(
            db=populated_db,
            checkpoint_dir=cp_dir,
            max_epochs=5,
            checkpoint_name="v0_test",
        )
        saved_path = Path(result["saved_path"])
        assert saved_path.exists()
        assert saved_path.name == "v0_test.zip"

    def test_empty_db_raises(self, tmp_path: Path) -> None:
        db = TrainingDB(tmp_path / "empty.db")
        with pytest.raises(ValueError, match="No transitions"):
            run_imitation_training(db=db, checkpoint_dir=tmp_path / "cp")

    def test_early_stop_on_agreement(self, populated_db: TrainingDB, tmp_path: Path) -> None:
        """With a low threshold, training should stop early."""
        cp_dir = tmp_path / "checkpoints"
        result = run_imitation_training(
            db=populated_db,
            checkpoint_dir=cp_dir,
            max_epochs=100,
            batch_size=32,
            learning_rate=1e-2,  # aggressive LR to converge fast
            agreement_threshold=0.50,  # easy threshold
            checkpoint_name="early_stop",
        )
        # Should stop before 100 epochs
        assert result["epochs"] < 100
        assert result["agreement"] >= 0.50
