"""Tests for the neural decision engine and hyperparams module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
from bots.v0.decision_engine import GameSnapshot, StrategicState
from bots.v0.learning.hyperparams import load_hyperparams, save_hyperparams, to_ppo_kwargs
from bots.v0.learning.neural_engine import DecisionMode, NeuralDecisionEngine

from orchestrator.registry import resolve_data_path

# Resolve via registry so the test follows the hot-data move from
# ``data/`` -> ``bots/v0/data/`` without knowing which location is active.
HYPERPARAMS_PATH = resolve_data_path("hyperparams.json")


# ---------- Hyperparams tests ----------


class TestHyperparams:
    def test_load_hyperparams(self) -> None:
        params = load_hyperparams(HYPERPARAMS_PATH)
        assert "learning_rate" in params
        assert "net_arch" in params
        assert params["net_arch"] == [128, 128]

    def test_to_ppo_kwargs_extracts_net_arch(self) -> None:
        params = load_hyperparams(HYPERPARAMS_PATH)
        kwargs = to_ppo_kwargs(params)
        assert "policy_kwargs" in kwargs
        assert kwargs["policy_kwargs"]["net_arch"] == [128, 128]
        assert "learning_rate" in kwargs
        assert "net_arch" not in kwargs  # should be inside policy_kwargs

    def test_to_ppo_kwargs_ignores_unknown(self, tmp_path: Path) -> None:
        params = {"learning_rate": 1e-3, "unknown_key": 42}
        kwargs = to_ppo_kwargs(params)
        assert "learning_rate" in kwargs
        assert "unknown_key" not in kwargs

    def test_save_and_reload(self, tmp_path: Path) -> None:
        params = {"learning_rate": 1e-3, "net_arch": [64, 64]}
        path = tmp_path / "hp.json"
        save_hyperparams(params, path)
        loaded = load_hyperparams(path)
        assert loaded["learning_rate"] == 1e-3
        assert loaded["net_arch"] == [64, 64]


# ---------- Neural engine tests ----------


def _make_mock_model(action: int = 2) -> MagicMock:
    """Create a mock SB3 PPO model that always predicts the given action."""
    model = MagicMock()
    model.predict.return_value = (np.array(action), None)
    model.device = "cpu"
    return model


class TestNeuralDecisionEngine:
    def test_predict_returns_strategic_state(self) -> None:
        mock_model = _make_mock_model(action=2)  # ATTACK
        with patch("stable_baselines3.PPO") as mock_ppo_cls:
            mock_ppo_cls.load.return_value = mock_model
            engine = NeuralDecisionEngine("fake_path.zip", mode=DecisionMode.NEURAL)

        result = engine.predict(GameSnapshot())
        assert result == StrategicState.ATTACK

    def test_predict_all_actions(self) -> None:
        expected = [
            StrategicState.OPENING,
            StrategicState.EXPAND,
            StrategicState.ATTACK,
            StrategicState.DEFEND,
            StrategicState.LATE_GAME,
        ]
        for action_idx, expected_state in enumerate(expected):
            mock_model = _make_mock_model(action=action_idx)
            with patch("stable_baselines3.PPO") as mock_ppo_cls:
                mock_ppo_cls.load.return_value = mock_model
                engine = NeuralDecisionEngine("fake.zip", mode=DecisionMode.NEURAL)
            result = engine.predict(GameSnapshot())
            assert result == expected_state

    def test_hybrid_defend_override(self) -> None:
        mock_model = _make_mock_model(action=2)  # would be ATTACK
        with patch("stable_baselines3.PPO") as mock_ppo_cls:
            mock_ppo_cls.load.return_value = mock_model
            engine = NeuralDecisionEngine("fake.zip", mode=DecisionMode.HYBRID)

        snap = GameSnapshot(enemy_army_near_base=True)
        result = engine.predict(snap)
        assert result == StrategicState.DEFEND

        # Probability vector should be one-hot on DEFEND, sized to the
        # canonical action list (Phase 4.5 F9 fix: was hardcoded to 5
        # elements, now derived from ACTION_TO_STATE)
        from bots.v0.decision_engine import ACTION_TO_STATE

        assert len(engine.last_probabilities) == len(ACTION_TO_STATE)
        defend_idx = ACTION_TO_STATE.index(StrategicState.DEFEND)
        for i, p in enumerate(engine.last_probabilities):
            assert p == (1.0 if i == defend_idx else 0.0)

    def test_hybrid_no_override_when_safe(self) -> None:
        mock_model = _make_mock_model(action=2)  # ATTACK
        with patch("stable_baselines3.PPO") as mock_ppo_cls:
            mock_ppo_cls.load.return_value = mock_model
            engine = NeuralDecisionEngine("fake.zip", mode=DecisionMode.HYBRID)

        snap = GameSnapshot(enemy_army_near_base=False)
        result = engine.predict(snap)
        assert result == StrategicState.ATTACK

