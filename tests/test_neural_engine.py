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

    def test_hybrid_defend_override_real_threat(self) -> None:
        """Non-trivial threat near base + can't counterattack -> DEFEND."""
        mock_model = _make_mock_model(action=2)  # would be ATTACK
        with patch("stable_baselines3.PPO") as mock_ppo_cls:
            mock_ppo_cls.load.return_value = mock_model
            engine = NeuralDecisionEngine("fake.zip", mode=DecisionMode.HYBRID)

        # 30 supply threat, our army only 10 — both trivial-raid and
        # counterattack-safe conditions fail, so the override should fire.
        snap = GameSnapshot(
            enemy_army_near_base=True,
            enemy_army_supply_visible=30,
            army_supply=10,
        )
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
        """Enemy not near base -> override never fires, PPO is trusted.

        Also asserts the PPO model was actually consulted, so a passing
        result of ATTACK cannot be a coincidence of the override path.
        """
        mock_model = _make_mock_model(action=2)  # ATTACK
        with patch("stable_baselines3.PPO") as mock_ppo_cls:
            mock_ppo_cls.load.return_value = mock_model
            engine = NeuralDecisionEngine("fake.zip", mode=DecisionMode.HYBRID)

        snap = GameSnapshot(enemy_army_near_base=False)
        result = engine.predict(snap)
        assert result == StrategicState.ATTACK
        mock_model.predict.assert_called_once()

    def test_hybrid_override_fires_on_hidden_threat_near_base(self) -> None:
        """enemy_army_near_base=True + enemy_vis=0 -> unknown threat, fire DEFEND.

        Hidden enemies (cloaked / burrowed / out of sensor) near our base
        are MORE dangerous than small known raids, not less. The override
        must fire even with enemy_vis=0.
        """
        mock_model = _make_mock_model(action=2)  # would be ATTACK
        with patch("stable_baselines3.PPO") as mock_ppo_cls:
            mock_ppo_cls.load.return_value = mock_model
            engine = NeuralDecisionEngine("fake.zip", mode=DecisionMode.HYBRID)

        snap = GameSnapshot(
            enemy_army_near_base=True,
            enemy_army_supply_visible=0,  # hidden — unknown composition
            army_supply=100,  # plenty of army, but we can't see what to fight
        )
        result = engine.predict(snap)
        assert result == StrategicState.DEFEND
        # PPO must NOT have been consulted — override short-circuits.
        mock_model.predict.assert_not_called()

    def test_hybrid_suppressed_on_trivial_raid(self) -> None:
        """Trivial raid (enemy_vis=5) near base -> override suppressed, PPO runs."""
        mock_model = _make_mock_model(action=2)  # ATTACK
        with patch("stable_baselines3.PPO") as mock_ppo_cls:
            mock_ppo_cls.load.return_value = mock_model
            engine = NeuralDecisionEngine("fake.zip", mode=DecisionMode.HYBRID)

        snap = GameSnapshot(
            enemy_army_near_base=True,
            enemy_army_supply_visible=5,
            army_supply=100,
        )
        result = engine.predict(snap)
        assert result == StrategicState.ATTACK
        # PPO path must have been taken
        mock_model.predict.assert_called_once()

    def test_hybrid_suppressed_when_can_counterattack(self) -> None:
        """Big threat but we out-supply 1.5x -> override suppressed, PPO runs."""
        mock_model = _make_mock_model(action=2)  # ATTACK
        with patch("stable_baselines3.PPO") as mock_ppo_cls:
            mock_ppo_cls.load.return_value = mock_model
            engine = NeuralDecisionEngine("fake.zip", mode=DecisionMode.HYBRID)

        # 30 enemy, 50 army — 50 >= 30*1.5 = 45, so we can counterattack.
        snap = GameSnapshot(
            enemy_army_near_base=True,
            enemy_army_supply_visible=30,
            army_supply=50,
        )
        result = engine.predict(snap)
        assert result == StrategicState.ATTACK
        mock_model.predict.assert_called_once()

    def test_hybrid_override_fires_when_army_below_min_counterattack(self) -> None:
        """Non-trivial threat + army below MIN_COUNTERATTACK_ARMY -> DEFEND.

        Even when the raw ratio would allow counterattack (e.g. 11 vs 8),
        an army under MIN_COUNTERATTACK_ARMY (12) is too small to commit.
        """
        mock_model = _make_mock_model(action=2)  # would be ATTACK
        with patch("stable_baselines3.PPO") as mock_ppo_cls:
            mock_ppo_cls.load.return_value = mock_model
            engine = NeuralDecisionEngine("fake.zip", mode=DecisionMode.HYBRID)

        # enemy=8 (just hits non-trivial), army=11 (below 12 minimum).
        snap = GameSnapshot(
            enemy_army_near_base=True,
            enemy_army_supply_visible=8,
            army_supply=11,
        )
        result = engine.predict(snap)
        assert result == StrategicState.DEFEND

