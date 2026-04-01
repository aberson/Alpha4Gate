"""End-to-end integration test: full training pipeline without SC2.

Exercises: feature encoding → DB storage → imitation training → checkpoint
management → reward calculation → neural engine inference → API endpoints.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from alpha4gate.api import app, configure
from alpha4gate.decision_engine import GameSnapshot, StrategicState
from alpha4gate.learning.checkpoints import get_best_name, list_checkpoints
from alpha4gate.learning.database import TrainingDB
from alpha4gate.learning.features import FEATURE_DIM, decode, encode
from alpha4gate.learning.imitation import run_imitation_training
from alpha4gate.learning.neural_engine import DecisionMode, NeuralDecisionEngine
from alpha4gate.learning.rewards import RewardCalculator


@pytest.fixture()
def training_dir(tmp_path: Path) -> Path:
    """Set up a training directory with DB, checkpoints, and reward rules."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "checkpoints").mkdir()

    # Copy reward rules
    import shutil

    rules_src = Path(__file__).parent.parent / "data" / "reward_rules.json"
    if rules_src.exists():
        shutil.copy(rules_src, data_dir / "reward_rules.json")

    return data_dir


class TestFullPipeline:
    """Test the complete training pipeline without SC2."""

    def test_encode_store_train_infer(self, training_dir: Path) -> None:
        """Full pipeline: encode → store → train → infer."""
        db_path = training_dir / "training.db"
        cp_dir = training_dir / "checkpoints"

        # 1. Create DB and store rule-based transitions
        db = TrainingDB(db_path)
        db.store_game("g1", "Simple64", 1, "win", 300.0, 5.0, "rules")

        rng = np.random.RandomState(42)
        for i in range(200):
            snap = GameSnapshot(
                supply_used=rng.randint(10, 200),
                supply_cap=200,
                minerals=rng.randint(0, 2000),
                vespene=rng.randint(0, 1000),
                army_supply=rng.randint(0, 100),
                worker_count=rng.randint(5, 50),
                base_count=rng.randint(1, 4),
                enemy_army_near_base=bool(rng.randint(0, 2)),
                enemy_army_supply_visible=rng.randint(0, 50),
                game_time_seconds=float(i * 22),
                gateway_count=rng.randint(0, 8),
                robo_count=rng.randint(0, 3),
                forge_count=rng.randint(0, 2),
                upgrade_count=rng.randint(0, 5),
                enemy_structure_count=rng.randint(0, 20),
            )

            # Rule-based action: DEFEND if enemy near, ATTACK if army > 20, else EXPAND
            if snap.enemy_army_near_base:
                action = 3  # DEFEND
            elif snap.army_supply >= 20:
                action = 2  # ATTACK
            else:
                action = 1  # EXPAND

            # Encode and store raw state
            vec = encode(snap)
            assert vec.shape == (FEATURE_DIM,)
            assert np.all(vec >= 0) and np.all(vec <= 1)

            raw = np.array([
                snap.supply_used, snap.supply_cap, snap.minerals, snap.vespene,
                snap.army_supply, snap.worker_count, snap.base_count,
                int(snap.enemy_army_near_base), snap.enemy_army_supply_visible,
                snap.game_time_seconds, snap.gateway_count, snap.robo_count,
                snap.forge_count, snap.upgrade_count, snap.enemy_structure_count,
                snap.cannon_count, snap.battery_count,
            ], dtype=np.float32)

            db.store_transition("g1", i, float(i * 22), raw, action=action, reward=0.1)

        assert db.get_transition_count() == 200
        assert db.get_game_count() == 1
        assert db.get_recent_win_rate(10) == 1.0

        # 2. Run imitation training
        result = run_imitation_training(
            db=db,
            checkpoint_dir=cp_dir,
            max_epochs=30,
            batch_size=32,
            learning_rate=1e-3,
            agreement_threshold=0.60,
            checkpoint_name="v0_e2e",
        )
        db.close()

        assert result["epochs"] > 0
        assert result["agreement"] > 0.3  # better than random
        assert Path(result["saved_path"]).exists()

        # 3. Verify checkpoint management
        cps = list_checkpoints(cp_dir)
        assert len(cps) == 1
        assert cps[0]["name"] == "v0_e2e"
        assert get_best_name(cp_dir) == "v0_e2e"

        # 4. Load model and run inference
        model_path = cp_dir / "v0_e2e.zip"
        engine = NeuralDecisionEngine(
            model_path=model_path,
            mode=DecisionMode.HYBRID,
        )

        # Test inference
        snap_attack = GameSnapshot(
            supply_used=100, supply_cap=200, minerals=1000, vespene=500,
            army_supply=50, worker_count=30, base_count=2,
            enemy_army_near_base=False, game_time_seconds=300.0,
        )
        state = engine.predict(snap_attack)
        assert isinstance(state, StrategicState)

        # Test hybrid DEFEND override
        snap_defend = GameSnapshot(
            supply_used=50, supply_cap=100, enemy_army_near_base=True,
        )
        state = engine.predict(snap_defend)
        assert state == StrategicState.DEFEND  # hybrid override

    def test_reward_calculator_with_rules_file(self, training_dir: Path) -> None:
        """Test reward calculator loads and evaluates rules from file."""
        rules_path = training_dir / "reward_rules.json"
        if not rules_path.exists():
            pytest.skip("reward_rules.json not found")

        calc = RewardCalculator(rules_path)
        assert len(calc.rules) >= 3

        # Scout-early should fire
        reward = calc.compute_step_reward({
            "game_time_seconds": 120.0,
            "has_scouted": True,
            "supply_used": 50,
            "supply_cap": 100,
        })
        assert reward > 0.001  # base + scout bonus

    def test_api_endpoints_with_training_data(self, training_dir: Path) -> None:
        """Test API endpoints reflect training state."""
        configure(training_dir, training_dir, training_dir)
        client = TestClient(app)

        # Initially empty
        resp = client.get("/api/training/status")
        assert resp.status_code == 200

        resp = client.get("/api/training/checkpoints")
        assert resp.status_code == 200

        resp = client.get("/api/reward-rules")
        assert resp.status_code == 200

    def test_feature_encode_decode_all_states(self) -> None:
        """Verify encode/decode round-trip for many different states."""
        rng = np.random.RandomState(99)
        for _ in range(50):
            snap = GameSnapshot(
                supply_used=int(rng.randint(0, 200)),
                supply_cap=int(rng.randint(0, 200)),
                minerals=int(rng.randint(0, 2000)),
                vespene=int(rng.randint(0, 2000)),
                army_supply=int(rng.randint(0, 200)),
                worker_count=int(rng.randint(0, 80)),
                base_count=int(rng.randint(1, 5)),
                enemy_army_near_base=bool(rng.randint(0, 2)),
                enemy_army_supply_visible=int(rng.randint(0, 200)),
                game_time_seconds=float(rng.randint(0, 1200)),
                gateway_count=int(rng.randint(0, 10)),
                robo_count=int(rng.randint(0, 4)),
                forge_count=int(rng.randint(0, 2)),
                upgrade_count=int(rng.randint(0, 10)),
            )
            vec = encode(snap)
            restored = decode(vec)
            # Check key fields survived round-trip
            assert restored.supply_used == snap.supply_used
            assert restored.minerals == snap.minerals
            assert restored.enemy_army_near_base == snap.enemy_army_near_base
